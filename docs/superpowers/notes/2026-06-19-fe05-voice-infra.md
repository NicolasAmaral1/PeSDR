# FE-05 Voice I/O — MinIO + Infra Runbook

**Branch:** `dev/nicolas-fe05-voice`
**Date:** 2026-06-19
**Spec:** `docs/superpowers/specs/2026-06-19-elevenlabs-voice-io-design.md`

This runbook covers every manual operator step required to bring the FE-05 voice
feature live on vps-nova. Execute steps in order. Do NOT skip the migration step.

---

## 1. MinIO — Add to the ai_sdr compose

> **IMPORTANT:** Confirm the network name against the running stack before applying.
> Run `docker network ls` and inspect the ai_sdr compose to find the internal network
> name (commonly `proxy` or `ai_sdr_default`). Replace `<internal_network>` below with
> the actual name. **Do not blind-apply this block.**

Add the following service and volume to the existing ai_sdr compose file on the VPS:

```yaml
services:
  # --- FE-05: MinIO object storage (audio binaries) ---
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    restart: unless-stopped
    environment:
      MINIO_ROOT_USER: "${MINIO_ROOT_USER}"
      MINIO_ROOT_PASSWORD: "${MINIO_ROOT_PASSWORD}"
    volumes:
      - minio_data:/data
    ports:
      # Expose console on 9001 only for local/VPN access — do NOT expose to public internet.
      # The S3 API port 9000 must NOT be published externally; the worker reaches it via
      # the internal Docker network as http://minio:9000.
      - "127.0.0.1:9001:9001"
    networks:
      - <internal_network>   # ← replace with the network shared by the ai_sdr worker

volumes:
  minio_data:
```

Then bring up the new service:

```bash
docker compose -f <ai_sdr_compose_file>.yml up -d minio
```

Verify the worker container can reach it:

```bash
docker exec <worker_container_name> curl -sf http://minio:9000/minio/health/live && echo "OK"
```

---

## 2. Bucket Bootstrap

Install and configure the MinIO client (`mc`) on the VPS, then create the bucket:

```bash
# Install mc (if not already present)
curl -sSLo /usr/local/bin/mc https://dl.min.io/client/mc/release/linux-amd64/mc
chmod +x /usr/local/bin/mc

# Point mc at the MinIO instance (run from the VPS host)
mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

# Create the tenant bucket
mc mb local/avelum-media

# Verify
mc ls local/
```

The bucket name (`avelum-media`) is per-tenant. If additional tenants are added later,
create a separate bucket for each and configure `storage.bucket` accordingly in their
`tenant.yaml`.

**Bucket policy:** keep the bucket **private** (no public access). Audio is delivered
to WhatsApp via the Meta media upload endpoint (returns a `media_id`), not via a public
URL. Presigned URLs are generated only for internal UI access.

---

## 3. Tenant Config (`tenant.yaml`)

Add the following `voice` and `storage` blocks to the pilot tenant's
`tenants/<slug>/tenant.yaml`. All field names are verified against
`src/ai_sdr/schemas/tenant_yaml.py` — use them exactly as shown.

```yaml
voice:
  response_mode: match_lead          # always | match_lead | never | context_driven
  fallback_to_text_on_failure: true
  synthesis:
    provider: elevenlabs
    credentials_ref: secrets/elevenlabs_api_key
    voice_id: "ABC123xyz"            # ← replace with the real voice ID from ElevenLabs dashboard
    format: ogg_opus
    timeout_seconds: 8
    default_emotion: null            # optional: "excited", "calm", etc.
  transcription:
    provider: elevenlabs             # or "whisper" — provider is pluggable
    credentials_ref: secrets/elevenlabs_api_key
    language: pt-BR
    min_confidence: 0.5

storage:
  provider: minio
  bucket: "avelum-media"
  endpoint_ref: secrets/minio_endpoint
  access_key_ref: secrets/minio_access_key
  secret_key_ref: secrets/minio_secret_key
```

**Field notes:**

- `voice.synthesis.credentials_ref` and `voice.transcription.credentials_ref` both
  resolve to the same ElevenLabs API key — this is intentional if using ElevenLabs
  for both TTS and STT (Scribe). If STT uses a different provider (e.g. Whisper),
  point `transcription.credentials_ref` at the corresponding secret.
- `voice.synthesis.default_emotion` is optional (`null` = no emotion hint). Valid
  values are provider-defined (ElevenLabs style names).
- `storage.endpoint_ref: secrets/minio_endpoint` resolves at runtime to
  `http://minio:9000` (the internal Docker hostname). The `MinioStorageAdapter` strips
  the `secrets/` prefix and looks up the value from the tenant's decrypted secrets map.
- `response_mode: match_lead` — the worker responds in voice only when the lead's last
  inbound was an audio message. Start here for the pilot; switch to `always` only after
  confirming audio delivery end-to-end.
- Both `voice` and `storage` blocks are **optional** in `TenantConfig`; omitting them
  leaves the tenant in text-only mode with no behavior change.

**Cross-validation:** `response_mode != "never"` requires `synthesis` to be set
(enforced by the `VoiceConfig` model validator). A `voice` block with audio input
enabled requires `storage` to be set (enforced at runtime by the normalizer).

---

## 4. Secrets

Add the four secrets to the pilot tenant's `secrets.enc.yaml` via sops:

```bash
# Decrypt, edit, re-encrypt
sops tenants/<slug>/secrets.enc.yaml
```

Inside the file, add:

```yaml
elevenlabs_api_key: "<key from ElevenLabs dashboard — API Keys section>"
minio_endpoint: "http://minio:9000"
minio_access_key: "<MINIO_ROOT_USER value or a dedicated MinIO service account>"
minio_secret_key: "<MINIO_ROOT_PASSWORD value or matching service account secret>"
```

**Where to find the ElevenLabs credentials:**

- **API key:** ElevenLabs dashboard → Profile → API Keys → create or copy key.
- **voice_id:** ElevenLabs dashboard → Voices → select or clone the desired voice →
  copy the Voice ID from the voice detail page. Paste it into `tenant.yaml` under
  `voice.synthesis.voice_id` (plain text, not a secret).

**Security note:** `minio_endpoint` is technically not a credential, but keeping all
four MinIO values in `secrets.enc.yaml` is simpler and consistent. Alternatively,
hardcode the endpoint as a plain value in `tenant.yaml` if you prefer (the
`StorageConfig.endpoint_ref` field accepts `None`, meaning no ref resolution; however,
the current `minio.py` implementation resolves it via `endpoint_ref`, so keep it as a
secrets ref for now).

---

## 5. Database Migration

Run the Alembic migration **before** restarting the worker. Migration `0030` drops and
recreates the `ck_outbound_message_type` and `ck_outbound_body_consistency` CHECK
constraints on `outbound_messages` to allow `message_type = 'audio'`. Without this,
any audio send attempt will fail at the DB level with a constraint violation.

```bash
# On the VPS, inside the container or with the DB URL set:
alembic upgrade head
```

Confirm the migration applied:

```bash
alembic current
# Should show: 0030_extend_outbound_message_type_audio (head)
```

Migration file: `migrations/versions/0030_extend_outbound_message_type_audio.py`
Revises: `0029_leads_inbound_channel_label`

---

## 6. Worker Restart

After applying the config and migration, restart the worker so the `AdapterRegistry`
and tenant loader pick up the new `voice`/`storage` configuration:

```bash
docker compose -f <ai_sdr_compose_file>.yml restart worker
# or, if the worker is a separate service:
docker restart <worker_container_name>
```

Watch startup logs to confirm no config parse errors:

```bash
docker logs -f <worker_container_name> 2>&1 | grep -E "(voice|storage|minio|elevenlabs|error|Error)" | head -30
```

---

## 7. Live Smoke Checklist

Execute after the worker is back up:

- [ ] **Send:** from a test WhatsApp number, send a voice note (audio message) to the
  pilot tenant's WhatsApp number.
- [ ] **Worker inbound log:** confirm the worker logs `normalize_inbound` with
  `outcome=processed` (or `low_confidence` if audio quality is poor — use clear speech
  for the test).
- [ ] **Transcription:** confirm the worker logs the transcribed text.
- [ ] **Voice reply:** confirm a voice note (audio) is delivered back to the test
  WhatsApp number.
- [ ] **DB audit — inbound:** query `inbound_messages` for the test lead; confirm
  `media_type = 'audio'`, `transcription IS NOT NULL`, `transcription_confidence > 0`,
  `transcription_provider IS NOT NULL`, `media_storage_key IS NOT NULL`.
- [ ] **DB audit — outbound:** query `outbound_messages` for the same lead; confirm
  `media_type = 'audio'`, `synthesis_voice_id IS NOT NULL`,
  `media_storage_key IS NOT NULL`.
- [ ] **MinIO:** verify both audio objects exist in the bucket:
  ```bash
  mc ls local/avelum-media/inbound/
  mc ls local/avelum-media/outbound/
  ```

If `response_mode: match_lead` is set and the pilot number sends a text message first,
the reply will be text. Send an audio message to trigger the voice path.

---

## 8. Rollback Caveat

**Downgrading past migration 0030 will fail if any `outbound_messages` rows with
`message_type = 'audio'` exist.** The `downgrade()` recreates the constraint as
`IN ('text', 'template')`, which the DB will reject if audio rows are present.

Before downgrading:

```sql
-- Check for audio rows
SELECT COUNT(*) FROM outbound_messages WHERE message_type = 'audio';

-- If any exist, purge them (confirm this is safe for your data before running)
DELETE FROM outbound_messages WHERE message_type = 'audio';
```

Then run:

```bash
alembic downgrade 0029_leads_inbound_channel_label
```

Also remove or comment out the `voice` and `storage` blocks from the tenant's
`tenant.yaml` and restart the worker to revert to text-only mode.

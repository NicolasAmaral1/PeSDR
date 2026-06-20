# FE-05 (v1) — Camada de I/O por modalidade: voz ElevenLabs (entrada + saída)

> **Status:** design aprovado no brainstorming (2026-06-19), aguardando review da spec.
> **Branch:** `dev/nicolas-fe05-voice` (worktree `/Users/nicolasamaral/dev/PeSDR-fe05-voice`, base `main` @ 647b8ad).
> **Escopo:** primeira fatia do FE-05. Áudio entrada (STT) + saída (TTS) + texto. Imagem = assento reservado.
> **Spec-mãe:** `docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md` §13 (adapters) + §5 (TurnDecision) + §13.5 (voz). Este doc refina aquela intenção contra o estado real do `main`.

## 1. Motivação

Tenants diferentes querem se comunicar em modalidades diferentes: um prefere responder em **áudio**, outro em **texto**. Além disso, leads mandam áudios no WhatsApp e hoje esses áudios são **descartados** (`handle_inbound` retorna `[]` para mensagens não-texto).

Em vez de pendurar áudio como caso especial espalhado pelo pipeline, introduzimos uma **costura de modalidade** nas bordas do turn: o coração do FlowEngine (a chamada única de LLM por turn) continua 100% texto; duas camadas finas traduzem modalidade na entrada e na saída.

## 2. Decisões do brainstorming

| Dimensão | Decisão |
|---|---|
| Escopo v1 | Áudio entrada (STT) + saída (TTS) + texto. Imagem = assento reservado no normalizer (não implementado). |
| Política de saída | 4 modos: `always` / `match_lead` / `never` / `context_driven` (LLM decide via `TurnDecision.response_format`). |
| Storage | Persistir binários de áudio (entrada e saída) em **MinIO** na vps-nova (S3-compat via boto3). |
| TTS | ElevenLabs. |
| STT | Agnóstico: interface genérica `SpeechTranscriber`, pluga ElevenLabs Scribe ou Whisper na implementação. |
| Abordagem | **A** — duas etapas explícitas (Inbound Normalizer + Outbound Renderer) envolvendo o `run_turn`, com categorias de adapter novas (voz, storage) espelhando o padrão `messaging/`. |

## 3. O que o `main` já reservou (não precisa criar)

A fundação FE-01a/b já deixou os assentos prontos. **Confirmado por leitura do `origin/main`:**

- **DB — `inbound_messages`** já tem: `media_type` (default `text`), `media_storage_key`, `audio_url`, `transcription`, `transcription_confidence`, `transcription_provider`.
- **DB — `outbound_messages`** já tem: `media_type`, `media_storage_key`, `audio_url`, `audio_duration_ms`, `synthesis_voice_id`, `voice_emotion`.
- **`TurnDecision`** (`flowengine/decision.py`) já tem `response_format: Literal["text","voice","both"] | None` e `voice_emotion`.
- **`flowengine/pipeline.py`** já lê `inbound_text = (inbound.text or inbound.transcription or "")` — o turn consome transcrição transparentemente.
- **`flowengine/sender.py`** já tem o slot explícito de voz:
  ```python
  if decision.response_format in ("voice", "both"):
      logger.warning("voice_format_not_implemented_fe03b ... falling back to text")
  ```
  e `humanize(..., is_voice=...)` (voz não fragmenta em chunks).
- **`humanization.apply_to_voice`** já existe em `tenant_yaml.py`.

**Consequência:** zero migration de schema de mensagem. O trabalho é preencher slots desenhados.

## 4. Arquitetura

```
            ┌─────────────── INBOUND NORMALIZER (worker, antes do run_turn) ───┐
 lead  →    │ texto   → passa direto                                           │
(wpp)       │ áudio   → download_media → storage.upload → transcribe           │ → InboundMessageRow.transcription
            │ imagem  → [assento reservado: hoje passa como texto vazio + log] │
            └──────────────────────────────────────────────────────────────────┘
                                  ↓
                    [ run_turn — INALTERADO, só texto ]
                                  ↓
            ┌─────────────── OUTBOUND RENDERER (substitui slot em sender.py) ──┐
 lead  ←    │ decide_modality(voice_cfg, decision.response_format, last_in)    │
(wpp)       │  texto → humanize + send_text (atual)                            │ ← TurnDecision.response_text
            │  voz   → synthesize → storage.upload → send_audio                │
            └──────────────────────────────────────────────────────────────────┘
```

Duas categorias novas de adapter, cada uma com seu registry `@register_provider` espelhando `messaging/factory.py` + `messaging/registry.py` (cache por `(tenant_id, provider)` para evitar redecrypt SOPS):

- **Voz** (`src/ai_sdr/voice/`): dividida em dois protocolos estreitos para que STT seja trocável sem tocar o TTS.
- **Storage** (`src/ai_sdr/storage/`).

## 5. Contratos

### 5.1 Voz (`src/ai_sdr/voice/base.py`)

```python
@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    content_type: str          # ex.: "audio/ogg; codecs=opus"
    voice_id: str
    char_count: int            # custo
    duration_ms: int | None

@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    confidence: float          # 0..1
    provider: str
    duration_ms: int | None    # custo (segundos de áudio)

class SpeechSynthesizer(ABC):  # TTS
    @abstractmethod
    async def synthesize(self, text: str, voice_id: str, *,
                         emotion: str | None = None,
                         fmt: str = "ogg_opus") -> SynthesisResult: ...

class SpeechTranscriber(ABC):  # STT (agnóstico)
    @abstractmethod
    async def transcribe(self, audio: bytes, *, language: str = "pt-BR") -> TranscriptionResult: ...
```

Implementações: `voice/elevenlabs.py` (`SpeechSynthesizer`; opcionalmente `SpeechTranscriber` via Scribe), `voice/fake.py` (ambos, determinísticos para teste). Provider de STT é selecionável separadamente do TTS via config.

### 5.2 Storage (`src/ai_sdr/storage/base.py`)

```python
class StorageAdapter(ABC):
    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str) -> str: ...  # → URL
    @abstractmethod
    async def get_url(self, key: str, expires_in: int = 3600) -> str: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...
```

Implementações: `storage/minio.py` (boto3 com `endpoint_url`; chaves via secrets), `storage/fake.py` (in-memory dict).

### 5.3 Extensões do `MessagingAdapter` (`messaging/base.py`)

```python
async def send_audio(self, to: str, audio: bytes, content_type: str) -> SendResult: ...
#   WhatsApp Cloud: POST /{phone_id}/media (upload) → media_id → POST /messages type=audio
async def download_media(self, media_ref: str) -> tuple[bytes, str]: ...
#   baixa mídia inbound da CDN da Meta usando access_token; retorna (bytes, mime)
```

Value-type `InboundMessage` ganha `media_type: str = "text"` e `media_ref: str | None` (id da mídia Meta). `handle_inbound` do WhatsApp passa a emitir `InboundMessage(media_type="audio", media_ref=<id>)` em vez de descartar; `messaging/ingest.py` persiste `media_type` (+ `raw` com o ref) na `InboundMessageRow`. `fake.py` implementa ambos os métodos.

> **Decisão de entrega:** a saída usa **upload pro endpoint de mídia da Meta** (media_id), não link público. A cópia no MinIO é nosso arquivo/auditoria (replay, futura UI de Chat), desacoplada da entrega.

### 5.4 Costura (`src/ai_sdr/voice/`)

```python
# normalizer.py
async def normalize_inbound(session, inbound_row, *, messaging, transcriber, storage, voice_cfg) -> NormalizeOutcome
#   NormalizeOutcome ∈ {processed, low_confidence, unprocessable}

# renderer.py
def decide_modality(decision, voice_cfg, last_inbound_media_type) -> Literal["text", "voice"]
async def render_and_send(*, decision, lead, voice_cfg, synthesizer, storage, messaging,
                          humanization, audit_sink, ...) -> RenderResult
```

`render_and_send` substitui o corpo do slot em `sender.py`; o caminho de texto atual (humanize + chunks) fica intacto dentro dele.

## 6. Fluxo de dados

### 6.1 Entrada (worker, antes do `run_turn`)

```
webhook → handle_inbound (reconhece áudio) → InboundMessageRow{media_type=audio, raw=media_ref}
  ↓ worker/jobs/inbound.py
normalize_inbound:
  media_type==audio:
    messaging.download_media(media_ref) → bytes
    storage.upload("inbound/{id}.ogg", bytes, mime) → grava media_storage_key + audio_url
    transcriber.transcribe(bytes) → grava transcription + transcription_confidence + transcription_provider
    se confidence < min_confidence → outcome=low_confidence
  media_type==text → outcome=processed (passthrough)
  ↓
  low_confidence → messaging.send_text(fallback "não entendi o áudio, manda escrito?") + marca inbound tratado, NÃO chama run_turn
  processed      → run_turn(inbound)   # inalterado; lê inbound.transcription
```

### 6.2 Saída (passo [12], dentro do `sender.py`)

```
TurnDecision{response_text, response_format, voice_emotion}
  ↓
modalidade = decide_modality(voice_cfg.response_mode, decision.response_format, last_inbound_media_type)
  always → voice
  never  → text
  match_lead → voice se o último inbound foi áudio, senão text
  context_driven → decision.response_format ("voice"/"both" → voice; senão text)
  ↓ voice
synthesizer.synthesize(response_text, voice_cfg.voice_id, emotion=voice_emotion) [timeout]
  ok    → storage.upload("outbound/{id}.ogg") → messaging.send_audio(bytes)
        → OutboundMessage{media_type=audio, audio_url, media_storage_key,
                          synthesis_voice_id, voice_emotion, audio_duration_ms}
  falha → fallback_to_text_on_failure ? caminho texto : escala (requires_review)
  ↓ text
humanize + chunks (atual). Voz NÃO fragmenta: 1 áudio por turn.
```

### 6.3 Custo

`flowengine/usage.py` / `accumulate_tokens` ganham `voice_synthesis_chars` e `voice_transcription_ms`, acumulados em `talk.tokens_consumed` (já é o dict de custo geral).

## 7. Config do tenant (`schemas/tenant_yaml.py`)

Blocos novos, **opcionais** (tenant sem eles = texto puro, comportamento atual 100% intacto). `extra="forbid"`, refs com prefixo `secrets/`.

```yaml
voice:
  response_mode: match_lead         # always | match_lead | never | context_driven  (default: never)
  fallback_to_text_on_failure: true
  synthesis:
    provider: elevenlabs
    credentials_ref: secrets/elevenlabs_api_key
    voice_id: "ABC123xyz"
    format: ogg_opus
    timeout_seconds: 8
  transcription:
    provider: elevenlabs            # ou whisper — agnóstico
    credentials_ref: secrets/elevenlabs_api_key
    language: pt-BR
    min_confidence: 0.5
storage:
  provider: minio
  endpoint_ref: secrets/minio_endpoint
  bucket: "avelum-media"
  access_key_ref: secrets/minio_access_key
  secret_key_ref: secrets/minio_secret_key
```

Modelos Pydantic: `VoiceConfig`, `SpeechSynthesisConfig`, `SpeechTranscriptionConfig`, `StorageConfig`. Validações:
- `response_mode != "never"` → `synthesis` obrigatório.
- `voice` configurado com entrada de áudio → `storage` obrigatório (binário precisa ir pra algum lugar).
- Adicionar `voice: VoiceConfig | None = None` e `storage: StorageConfig | None = None` em `TenantConfig`.

## 8. Tratamento de erros

| Falha | Comportamento |
|---|---|
| Síntese timeout/erro | `fallback_to_text_on_failure` → caminho texto; senão escala `requires_review` (`escalation_category="system_exhausted"`) |
| Transcrição confiança baixa | fallback "manda escrito?", **não** roda o turn |
| `download_media` falha (CDN Meta expira em 24h) | inbound não-processável → fallback + log; marca inbound com erro |
| `storage.upload` falha na **saída** | sem arquivo não há `send_audio` → fallback texto |
| `storage.upload` falha na **entrada** | guarda `transcription` mesmo assim, loga miss, **não** bloqueia o turn |
| `send_audio` terminal (Auth/Policy) | mesma classificação `TerminalError` do messaging atual |

- **Idempotência:** `media_storage_key` determinístico por id (`inbound/{id}.ogg`, `outbound/{id}.ogg`) → re-upload é overwrite seguro em retry do worker.
- **Circuit breaker:** reservado (spec-mãe §13.4). v1 = só timeout + fallback.
- **Registry:** voz/storage espelham `AdapterRegistry` (cache por `(tenant, categoria)`), guardado em `app.state` (API) e módulo-level (worker).

## 9. Testes

Espelhando `tests/integration/test_adapter_compliance.py`:

- **Compliance parametrizado:** `SpeechSynthesizer` / `SpeechTranscriber` / `StorageAdapter` rodam a mesma suíte contra `fake` + stub real.
- **Unit:**
  - `decide_modality`: matriz 4 modos × {último inbound texto, último inbound áudio} × `response_format`.
  - `normalize_inbound`: áudio ok / confiança baixa / `download_media` falha / storage falha (não bloqueia).
  - `render_and_send`: voz ok / síntese falha→fallback texto / storage falha→fallback.
- **Integration:** turn ponta-a-ponta com fakes (inbound áudio → transcrição → `run_turn` → resposta voz → `send_audio` fake) no DB de teste.
- Sem credenciais reais nos testes (fakes). Um teste "live" opcional marcado `skip` por padrão.

## 10. Layout de arquivos

**Novos:**
```
src/ai_sdr/voice/{__init__,base,factory,registry,elevenlabs,fake,normalizer,renderer}.py
src/ai_sdr/storage/{__init__,base,factory,registry,minio,fake}.py
tests/unit/test_voice_*.py · test_storage_*.py · test_renderer_modality.py · test_normalizer.py
tests/integration/test_voice_compliance.py · test_storage_compliance.py · test_turn_voice_e2e.py
```
**Editados:**
```
messaging/{base,whatsapp_cloud,fake,ingest}.py   (+send_audio, +download_media, +InboundMessage.media_type/media_ref)
flowengine/sender.py        (slot de voz → render_and_send)
flowengine/system_prompt.py (instrução response_format só quando response_mode=context_driven)
flowengine/usage.py         (custo de voz)
worker/jobs/inbound.py      (chama normalize_inbound antes do run_turn)
observability/outbound_audit.py (persiste campos de áudio)
schemas/tenant_yaml.py      (VoiceConfig, StorageConfig + campos em TenantConfig)
```
**Infra:** container MinIO no compose da vps-nova + bucket + secrets (`minio_endpoint`, `minio_access_key`, `minio_secret_key`, `elevenlabs_api_key`) no `secrets.enc.yaml` do tenant.

**Sem migration nova** de schema de mensagem (campos já existem no `main`).

## 11. Fora de escopo (assentos reservados)

- **Imagem/visão/OCR** — normalizer já nasce com o ramo desenhado, mas não-implementado.
- **Chamada de voz real (PSTN)** — spec-mãe declara fora; voz aqui é mensagem de áudio assíncrona.
- **Circuit breaker / rate-limit cross-cutting** — só timeout+fallback na v1.
- **`send_image` / outras modalidades de saída** — slot no `MessagingAdapter`.
- **Enforcement de budget por tenant** — só registramos custo; teto fica pro futuro.

## 12. Riscos / pontos a confirmar na implementação

- **Formato de áudio do WhatsApp:** Cloud API aceita `audio/ogg; codecs=opus` (voice note) — confirmar que o ElevenLabs entrega `ogg_opus` compatível; senão transcodar.
- **`download_media` da Meta:** o `media_id` exige duas chamadas (GET metadata → GET URL com bearer). Encapsular no adapter.
- **MinIO público vs privado:** como o áudio vai pra Meta por upload (media_id), o bucket pode ser privado; URLs assinadas só pra UI interna.
- **`handle_inbound` value-type:** mudar `InboundMessage` (frozen dataclass) é breaking pra qualquer consumidor — auditar usos antes.

## 13. Notas de implementação (pós-execução, 2026-06-20)

Correção ao §3/§10 ("sem migration"): as **colunas** de mídia já existiam, mas duas **CHECK constraints** precisaram de migration:
- **0030** — estende `ck_outbound_message_type` (+`ck_outbound_body_consistency`) para aceitar `message_type='audio'` (linhas de áudio carregam `body_text`=transcript). Sem ela, o INSERT de áudio falha no DB.
- **0031** — estende `ck_talks_requires_review_reason` para aceitar `voice_synthesis_failed` (usado quando síntese falha e `fallback_to_text_on_failure: false` → escala em vez de virar poison message; ver §8).

Caveat de rollback: downgrade de 0030 falha se houver linhas `media_type='audio'` (purgar antes).

Follow-ups deixados para o time (não-bloqueantes): custo de transcrição (`voice_transcription_ms`, §6.3) ainda não acumulado; cache de instância de adapter por `(tenant, provider)` não implementado (rebuild por drain); migrations 0025/0031 importam `ALL_REASONS` dinamicamente (afeta só replay histórico em DB novo, não o estado final).

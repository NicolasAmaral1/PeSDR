# FE-05 (v1) — Voice I/O (ElevenLabs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a modality I/O seam around the (unchanged) FlowEngine turn so tenants can receive/send WhatsApp voice messages: inbound audio is transcribed to text (STT) before the turn, outbound text is synthesized to audio (ElevenLabs TTS) after the turn, per a tenant policy.

**Architecture:** Two new adapter categories (`voice/`, `storage/`) mirroring the existing `messaging/` factory+registry pattern, plus two thin seam functions — an **Inbound Normalizer** (worker, before `run_turn`) and an **Outbound Renderer** (inside `sender.py`). The text-only `run_turn` core is untouched; it already reads `inbound.transcription` and already routes `response_format in ("voice","both")` to a fallback slot we replace.

**Tech Stack:** Python 3.12, async, Pydantic v2 (`extra="forbid"`), SQLAlchemy async, httpx, tenacity, boto3 (MinIO/S3), pytest (`pytest.mark.integration`), uv.

## Global Constraints

- **No new message-schema migration.** `inbound_messages` (`media_type, media_storage_key, audio_url, transcription, transcription_confidence, transcription_provider`) and `outbound_messages` (`media_type, media_storage_key, audio_url, audio_duration_ms, synthesis_voice_id, voice_emotion`) already exist on `main`. Verify with `git grep` before assuming a column is missing.
- **Secret refs** in `tenant.yaml` MUST start with `secrets/`; resolve by stripping that prefix against the SOPS dict (`ref.removeprefix("secrets/")`). Match `messaging/factory.py::_resolve_secret`.
- **Adapter purity:** adapters know nothing about `leads`/`tenants` tables; receive config+secrets at construction; never read secrets at request time. Retry transient/rate-limit internally; surface only terminal errors.
- **Tenants without `voice`/`storage` blocks keep current behavior** (text-only). Every new code path must be a no-op when `tenant_cfg.voice is None`.
- **Registry caching:** mirror `messaging/registry.py` — cache instances per `(tenant_id, provider)` to avoid re-decrypting SOPS.
- **TDD:** write the failing test first, watch it fail, minimal impl, watch it pass, commit. Commit messages: `feat(fe05): …` / `test(fe05): …`.
- **Run tests** from the worktree root `/Users/nicolasamaral/dev/PeSDR-fe05-voice` with `uv run pytest …`. Unit and integration are run separately (conftest clobber — see CLAUDE/memory).

---

### Task 1: Tenant config models (`voice` + `storage`)

**Files:**
- Modify: `src/ai_sdr/schemas/tenant_yaml.py` (add classes after `MessagingConfig`; add fields to `TenantConfig`)
- Test: `tests/unit/test_tenant_yaml_voice.py`

**Interfaces:**
- Produces: `SpeechSynthesisConfig{provider:str, credentials_ref:str, voice_id:str, format:str="ogg_opus", timeout_seconds:int=8, default_emotion:str|None=None}`; `SpeechTranscriptionConfig{provider:str, credentials_ref:str, language:str="pt-BR", min_confidence:float=0.5}`; `VoiceConfig{response_mode:Literal["always","match_lead","never","context_driven"]="never", fallback_to_text_on_failure:bool=True, synthesis:SpeechSynthesisConfig|None=None, transcription:SpeechTranscriptionConfig|None=None}`; `StorageConfig{provider:str, bucket:str, endpoint_ref:str|None=None, access_key_ref:str|None=None, secret_key_ref:str|None=None}`; `TenantConfig.voice:VoiceConfig|None=None`, `TenantConfig.storage:StorageConfig|None=None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tenant_yaml_voice.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import (
    SpeechSynthesisConfig,
    StorageConfig,
    TenantConfig,
    VoiceConfig,
)


def _base_tenant(**extra) -> dict:
    return {
        "id": "avelum",
        "display_name": "Avelum",
        "timezone": "America/Sao_Paulo",
        **extra,
    }


def test_voice_defaults_to_never_and_no_synthesis():
    v = VoiceConfig()
    assert v.response_mode == "never"
    assert v.fallback_to_text_on_failure is True
    assert v.synthesis is None


def test_voice_requires_synthesis_when_mode_not_never():
    with pytest.raises(ValidationError, match="synthesis"):
        VoiceConfig(response_mode="always")


def test_synthesis_ref_must_use_secrets_prefix():
    with pytest.raises(ValidationError, match="secrets/"):
        SpeechSynthesisConfig(
            provider="elevenlabs", credentials_ref="elevenlabs_key", voice_id="v1"
        )


def test_storage_ref_must_use_secrets_prefix():
    with pytest.raises(ValidationError, match="secrets/"):
        StorageConfig(provider="minio", bucket="b", endpoint_ref="minio")


def test_tenant_accepts_full_voice_and_storage_block():
    cfg = TenantConfig.model_validate(
        _base_tenant(
            voice={
                "response_mode": "match_lead",
                "synthesis": {
                    "provider": "elevenlabs",
                    "credentials_ref": "secrets/elevenlabs_api_key",
                    "voice_id": "ABC123",
                },
                "transcription": {
                    "provider": "elevenlabs",
                    "credentials_ref": "secrets/elevenlabs_api_key",
                },
            },
            storage={
                "provider": "minio",
                "bucket": "avelum-media",
                "endpoint_ref": "secrets/minio_endpoint",
                "access_key_ref": "secrets/minio_access_key",
                "secret_key_ref": "secrets/minio_secret_key",
            },
        )
    )
    assert cfg.voice.response_mode == "match_lead"
    assert cfg.voice.synthesis.voice_id == "ABC123"
    assert cfg.storage.bucket == "avelum-media"


def test_tenant_without_voice_is_text_only():
    cfg = TenantConfig.model_validate(_base_tenant())
    assert cfg.voice is None
    assert cfg.storage is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tenant_yaml_voice.py -q`
Expected: FAIL — `ImportError: cannot import name 'VoiceConfig'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ai_sdr/schemas/tenant_yaml.py`, add a shared validator helper and the new classes after `MessagingConfig`:

```python
def _require_secrets_prefix(value: str | None) -> str | None:
    if value is not None and not value.startswith("secrets/"):
        raise ValueError(f"ref must start with 'secrets/' (got {value!r})")
    return value


class SpeechSynthesisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    credentials_ref: str
    voice_id: str
    format: str = "ogg_opus"
    timeout_seconds: int = Field(default=8, ge=1, le=60)
    default_emotion: str | None = None

    @field_validator("credentials_ref")
    @classmethod
    def _check_ref(cls, v: str) -> str:
        return _require_secrets_prefix(v)  # type: ignore[return-value]


class SpeechTranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    credentials_ref: str
    language: str = "pt-BR"
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("credentials_ref")
    @classmethod
    def _check_ref(cls, v: str) -> str:
        return _require_secrets_prefix(v)  # type: ignore[return-value]


class VoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_mode: Literal["always", "match_lead", "never", "context_driven"] = "never"
    fallback_to_text_on_failure: bool = True
    synthesis: SpeechSynthesisConfig | None = None
    transcription: SpeechTranscriptionConfig | None = None

    @model_validator(mode="after")
    def _check_synthesis_present(self) -> Self:
        if self.response_mode != "never" and self.synthesis is None:
            raise ValueError("voice.synthesis is required when response_mode != 'never'")
        return self


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    bucket: str
    endpoint_ref: str | None = None
    access_key_ref: str | None = None
    secret_key_ref: str | None = None

    @field_validator("endpoint_ref", "access_key_ref", "secret_key_ref")
    @classmethod
    def _check_refs(cls, v: str | None) -> str | None:
        return _require_secrets_prefix(v)
```

Then add to `TenantConfig` (after the `humanization` field):

```python
    voice: VoiceConfig | None = None
    storage: StorageConfig | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tenant_yaml_voice.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_tenant_yaml_voice.py
git commit -m "feat(fe05): tenant.yaml voice + storage config models"
```

---

### Task 2: Storage adapter (base + fake + MinIO + factory/registry)

**Files:**
- Create: `src/ai_sdr/storage/__init__.py`, `src/ai_sdr/storage/base.py`, `src/ai_sdr/storage/fake.py`, `src/ai_sdr/storage/minio.py`, `src/ai_sdr/storage/factory.py`
- Test: `tests/unit/test_storage_fake.py`, `tests/integration/test_storage_compliance.py`

**Interfaces:**
- Consumes: `StorageConfig` (Task 1).
- Produces: `StorageAdapter` ABC with `async upload(key:str, data:bytes, content_type:str) -> str`, `async get_url(key:str, expires_in:int=3600) -> str`, `async delete(key:str) -> None`; `FakeStorageAdapter` (in-mem, `.objects: dict[str,bytes]`); `build_storage_adapter(cfg:StorageConfig, secrets:Mapping[str,str]) -> StorageAdapter`; decorator `register_storage_provider(name)`.

- [ ] **Step 1: Write the failing test (fake unit)**

```python
# tests/unit/test_storage_fake.py
from __future__ import annotations

import pytest

from ai_sdr.storage.fake import FakeStorageAdapter


async def test_upload_then_get_url_roundtrips():
    s = FakeStorageAdapter(base_url="https://fake.local")
    url = await s.upload("outbound/123.ogg", b"\x00\x01", "audio/ogg")
    assert url == "https://fake.local/outbound/123.ogg"
    assert s.objects["outbound/123.ogg"] == b"\x00\x01"
    assert await s.get_url("outbound/123.ogg") == url


async def test_delete_removes_object():
    s = FakeStorageAdapter()
    await s.upload("k", b"x", "text/plain")
    await s.delete("k")
    assert "k" not in s.objects
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_storage_fake.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_sdr.storage'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_sdr/storage/__init__.py
```

```python
# src/ai_sdr/storage/base.py
"""StorageAdapter contract — opaque blob storage for media (audio).

Keys are caller-chosen, deterministic per message id (e.g.
'inbound/{id}.ogg') so retries overwrite idempotently. Adapters know
nothing about tenants/leads; config+secrets injected at construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageAdapter(ABC):
    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes under key; return a URL referencing the object."""

    @abstractmethod
    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a URL for an existing object (presigned if applicable)."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove the object. No-op if absent."""
```

```python
# src/ai_sdr/storage/fake.py
"""In-memory StorageAdapter for tests + simulate CLI. No I/O."""

from __future__ import annotations

from ai_sdr.storage.base import StorageAdapter


class FakeStorageAdapter(StorageAdapter):
    def __init__(self, base_url: str = "https://fake.storage.local") -> None:
        self._base_url = base_url.rstrip("/")
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = data
        self.content_types[key] = content_type
        return f"{self._base_url}/{key}"

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._base_url}/{key}"

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.content_types.pop(key, None)
```

```python
# src/ai_sdr/storage/minio.py
"""MinIO/S3 StorageAdapter via boto3 (S3-compatible).

Runs blocking boto3 calls in a thread (asyncio.to_thread) — boto3 has no
native async. endpoint_url points at the MinIO container; for AWS S3 omit
endpoint_ref in tenant.yaml and the SDK uses the default endpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import boto3

from ai_sdr.schemas.tenant_yaml import StorageConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.storage.factory import register_storage_provider


class MinioStorageAdapter(StorageAdapter):
    def __init__(self, cfg: StorageConfig, secrets: Mapping[str, str]) -> None:
        def _sec(ref: str | None) -> str | None:
            return secrets[ref.removeprefix("secrets/")] if ref else None

        self._bucket = cfg.bucket
        self._endpoint = _sec(cfg.endpoint_ref)
        self._client = boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=_sec(cfg.access_key_ref),
            aws_secret_access_key=_sec(cfg.secret_key_ref),
        )

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return await self.get_url(key)

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self._bucket, Key=key
        )


@register_storage_provider("minio")
def _build_minio(cfg: StorageConfig, secrets: Mapping[str, str]) -> StorageAdapter:
    return MinioStorageAdapter(cfg, secrets)
```

```python
# src/ai_sdr/storage/factory.py
"""Dispatch a StorageAdapter from StorageConfig. Mirrors messaging/factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdr.schemas.tenant_yaml import StorageConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.storage.fake import FakeStorageAdapter

_REGISTRY: dict[str, Callable[[StorageConfig, Mapping[str, str]], StorageAdapter]] = {}


def register_storage_provider(name: str):
    def _wrap(builder):
        if name in _REGISTRY:
            raise RuntimeError(f"storage provider already registered: {name}")
        _REGISTRY[name] = builder
        return builder

    return _wrap


@register_storage_provider("fake")
def _build_fake(cfg: StorageConfig, secrets: Mapping[str, str]) -> StorageAdapter:
    return FakeStorageAdapter()


def build_storage_adapter(cfg: StorageConfig, secrets: Mapping[str, str]) -> StorageAdapter:
    if cfg.provider == "minio" and "minio" not in _REGISTRY:
        from ai_sdr.storage import minio  # noqa: F401  (triggers registration)
    builder = _REGISTRY.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown storage provider: {cfg.provider!r}")
    return builder(cfg, secrets)
```

- [ ] **Step 4: Run fake test to verify it passes**

Run: `uv run pytest tests/unit/test_storage_fake.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the compliance test (fake + minio-mocked)**

```python
# tests/integration/test_storage_compliance.py
"""Same contract against every StorageAdapter impl."""

from __future__ import annotations

import pytest

from ai_sdr.schemas.tenant_yaml import StorageConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.storage.minio import MinioStorageAdapter

pytestmark = pytest.mark.integration


class _StubS3:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType):  # noqa: N803
        self.store[f"{Bucket}/{Key}"] = Body

    def generate_presigned_url(self, op, *, Params, ExpiresIn):  # noqa: N803
        return f"https://minio.local/{Params['Bucket']}/{Params['Key']}"

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        self.store.pop(f"{Bucket}/{Key}", None)


@pytest.fixture(params=["fake", "minio"])
def storage_under_test(request) -> StorageAdapter:
    if request.param == "fake":
        return FakeStorageAdapter()
    cfg = StorageConfig(provider="minio", bucket="b", endpoint_ref="secrets/ep")
    a = MinioStorageAdapter.__new__(MinioStorageAdapter)
    a._bucket = "b"
    a._endpoint = "https://minio.local"
    a._client = _StubS3()
    return a


async def test_upload_returns_nonempty_url(storage_under_test):
    url = await storage_under_test.upload("outbound/1.ogg", b"\x01\x02", "audio/ogg")
    assert isinstance(url, str) and url


async def test_delete_is_idempotent(storage_under_test):
    await storage_under_test.delete("missing-key")  # must not raise
```

- [ ] **Step 6: Run compliance test**

Run: `uv run pytest tests/integration/test_storage_compliance.py -q`
Expected: PASS (4 passed — 2 tests × 2 params).

- [ ] **Step 7: Add boto3 dependency + commit**

```bash
uv add boto3
git add src/ai_sdr/storage tests/unit/test_storage_fake.py tests/integration/test_storage_compliance.py pyproject.toml uv.lock
git commit -m "feat(fe05): storage adapter (base/fake/minio) + compliance suite"
```

---

### Task 3: Voice adapters (base + fake + ElevenLabs + factory/registry)

**Files:**
- Create: `src/ai_sdr/voice/__init__.py`, `src/ai_sdr/voice/base.py`, `src/ai_sdr/voice/fake.py`, `src/ai_sdr/voice/elevenlabs.py`, `src/ai_sdr/voice/factory.py`
- Test: `tests/unit/test_voice_fake.py`, `tests/integration/test_voice_compliance.py`

**Interfaces:**
- Consumes: `SpeechSynthesisConfig`, `SpeechTranscriptionConfig` (Task 1).
- Produces: dataclasses `SynthesisResult{audio:bytes, content_type:str, voice_id:str, char_count:int, duration_ms:int|None}`, `TranscriptionResult{text:str, confidence:float, provider:str, duration_ms:int|None}`; ABCs `SpeechSynthesizer.synthesize(text:str, voice_id:str, *, emotion:str|None=None, fmt:str="ogg_opus") -> SynthesisResult` and `SpeechTranscriber.transcribe(audio:bytes, *, language:str="pt-BR") -> TranscriptionResult`; `FakeSynthesizer`, `FakeTranscriber`; `build_synthesizer(cfg:SpeechSynthesisConfig, secrets) -> SpeechSynthesizer`, `build_transcriber(cfg:SpeechTranscriptionConfig, secrets) -> SpeechTranscriber`; decorators `register_synthesizer(name)`, `register_transcriber(name)`.

- [ ] **Step 1: Write the failing test (fakes unit)**

```python
# tests/unit/test_voice_fake.py
from __future__ import annotations

from ai_sdr.voice.base import SynthesisResult, TranscriptionResult
from ai_sdr.voice.fake import FakeSynthesizer, FakeTranscriber


async def test_fake_synthesizer_returns_bytes_and_char_count():
    s = FakeSynthesizer()
    r = await s.synthesize("olá mundo", "voice-1")
    assert isinstance(r, SynthesisResult)
    assert r.audio  # non-empty bytes
    assert r.voice_id == "voice-1"
    assert r.char_count == len("olá mundo")


async def test_fake_transcriber_echoes_scripted_text_with_confidence():
    t = FakeTranscriber(text="oi tudo bem", confidence=0.92)
    r = await t.transcribe(b"\x00\x01")
    assert isinstance(r, TranscriptionResult)
    assert r.text == "oi tudo bem"
    assert r.confidence == 0.92
    assert r.provider == "fake"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_voice_fake.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_sdr.voice'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_sdr/voice/__init__.py
```

```python
# src/ai_sdr/voice/base.py
"""Voice adapter contracts — split into two narrow protocols so the STT
provider can differ from (and be swapped without touching) the TTS one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    content_type: str
    voice_id: str
    char_count: int
    duration_ms: int | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    confidence: float
    provider: str
    duration_ms: int | None = None


class SpeechSynthesizer(ABC):
    @abstractmethod
    async def synthesize(
        self, text: str, voice_id: str, *, emotion: str | None = None, fmt: str = "ogg_opus"
    ) -> SynthesisResult: ...


class SpeechTranscriber(ABC):
    @abstractmethod
    async def transcribe(self, audio: bytes, *, language: str = "pt-BR") -> TranscriptionResult: ...
```

```python
# src/ai_sdr/voice/fake.py
"""Deterministic fakes for tests + simulate CLI."""

from __future__ import annotations

from ai_sdr.voice.base import (
    SpeechSynthesizer,
    SpeechTranscriber,
    SynthesisResult,
    TranscriptionResult,
)


class FakeSynthesizer(SpeechSynthesizer):
    def __init__(self, content_type: str = "audio/ogg; codecs=opus") -> None:
        self._content_type = content_type
        self.calls: list[tuple[str, str, str | None]] = []

    async def synthesize(
        self, text: str, voice_id: str, *, emotion: str | None = None, fmt: str = "ogg_opus"
    ) -> SynthesisResult:
        self.calls.append((text, voice_id, emotion))
        return SynthesisResult(
            audio=b"FAKEOGG" + text.encode("utf-8"),
            content_type=self._content_type,
            voice_id=voice_id,
            char_count=len(text),
            duration_ms=len(text) * 60,
        )


class FakeTranscriber(SpeechTranscriber):
    def __init__(self, text: str = "transcrição fake", confidence: float = 0.95) -> None:
        self._text = text
        self._confidence = confidence
        self.calls: list[bytes] = []

    async def transcribe(self, audio: bytes, *, language: str = "pt-BR") -> TranscriptionResult:
        self.calls.append(audio)
        return TranscriptionResult(
            text=self._text, confidence=self._confidence, provider="fake", duration_ms=1000
        )
```

```python
# src/ai_sdr/voice/elevenlabs.py
"""ElevenLabs SpeechSynthesizer (TTS) + optional Scribe transcriber.

HTTP via httpx with bounded tenacity retry on 5xx/429. Synthesis returns
raw audio bytes; the caller stores them + sends via the messaging adapter.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx
import tenacity

from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, SpeechTranscriptionConfig
from ai_sdr.voice.base import (
    SpeechSynthesizer,
    SpeechTranscriber,
    SynthesisResult,
    TranscriptionResult,
)
from ai_sdr.voice.factory import register_synthesizer, register_transcriber

_OUTPUT_FORMAT = {"ogg_opus": "opus_48000", "mp3": "mp3_44100_128"}
_CONTENT_TYPE = {"ogg_opus": "audio/ogg; codecs=opus", "mp3": "audio/mpeg"}
_WAIT = tenacity.wait_exponential(multiplier=1, min=1, max=4)
_MAX_ATTEMPTS = 3


def _build_http_client(timeout: float) -> httpx.AsyncClient:  # test seam
    return httpx.AsyncClient(timeout=timeout)


class ElevenLabsSynthesizer(SpeechSynthesizer):
    def __init__(self, cfg: SpeechSynthesisConfig, secrets: Mapping[str, str]) -> None:
        self._api_key = secrets[cfg.credentials_ref.removeprefix("secrets/")]
        self._timeout = float(cfg.timeout_seconds)
        self._fmt = cfg.format

    async def synthesize(
        self, text: str, voice_id: str, *, emotion: str | None = None, fmt: str = "ogg_opus"
    ) -> SynthesisResult:
        out_fmt = _OUTPUT_FORMAT.get(fmt, "opus_48000")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={out_fmt}"
        body = {"text": text, "model_id": "eleven_multilingual_v2"}
        headers = {"xi-api-key": self._api_key, "accept": "audio/ogg"}
        retryer = tenacity.AsyncRetrying(
            stop=tenacity.stop_after_attempt(_MAX_ATTEMPTS),
            wait=_WAIT,
            retry=tenacity.retry_if_exception_type(httpx.HTTPStatusError),
            reraise=True,
        )
        async for attempt in retryer:
            with attempt:
                async with _build_http_client(self._timeout) as client:
                    resp = await client.post(url, json=body, headers=headers)
                if resp.status_code >= 500 or resp.status_code == 429:
                    resp.raise_for_status()
                if resp.status_code != 200:
                    raise RuntimeError(f"elevenlabs synth failed: {resp.status_code} {resp.text}")
                return SynthesisResult(
                    audio=resp.content,
                    content_type=_CONTENT_TYPE.get(fmt, "audio/ogg; codecs=opus"),
                    voice_id=voice_id,
                    char_count=len(text),
                    duration_ms=None,
                )
        raise RuntimeError("unreachable: tenacity exhausted")


class ElevenLabsTranscriber(SpeechTranscriber):
    def __init__(self, cfg: SpeechTranscriptionConfig, secrets: Mapping[str, str]) -> None:
        self._api_key = secrets[cfg.credentials_ref.removeprefix("secrets/")]
        self._language = cfg.language

    async def transcribe(self, audio: bytes, *, language: str = "pt-BR") -> TranscriptionResult:
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {"xi-api-key": self._api_key}
        files = {"file": ("audio.ogg", audio, "audio/ogg")}
        data = {"model_id": "scribe_v1"}
        async with _build_http_client(30.0) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
        resp.raise_for_status()
        payload = resp.json()
        return TranscriptionResult(
            text=payload.get("text", ""),
            confidence=float(payload.get("language_probability", 1.0) or 1.0),
            provider="elevenlabs",
            duration_ms=None,
        )


@register_synthesizer("elevenlabs")
def _build_synth(cfg: SpeechSynthesisConfig, secrets: Mapping[str, str]) -> SpeechSynthesizer:
    return ElevenLabsSynthesizer(cfg, secrets)


@register_transcriber("elevenlabs")
def _build_transcriber(
    cfg: SpeechTranscriptionConfig, secrets: Mapping[str, str]
) -> SpeechTranscriber:
    return ElevenLabsTranscriber(cfg, secrets)
```

```python
# src/ai_sdr/voice/factory.py
"""Dispatch synthesizers + transcribers by provider name."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, SpeechTranscriptionConfig
from ai_sdr.voice.base import SpeechSynthesizer, SpeechTranscriber
from ai_sdr.voice.fake import FakeSynthesizer, FakeTranscriber

_SYNTH: dict[str, Callable[[SpeechSynthesisConfig, Mapping[str, str]], SpeechSynthesizer]] = {}
_TRANS: dict[str, Callable[[SpeechTranscriptionConfig, Mapping[str, str]], SpeechTranscriber]] = {}


def register_synthesizer(name: str):
    def _wrap(builder):
        if name in _SYNTH:
            raise RuntimeError(f"synthesizer already registered: {name}")
        _SYNTH[name] = builder
        return builder

    return _wrap


def register_transcriber(name: str):
    def _wrap(builder):
        if name in _TRANS:
            raise RuntimeError(f"transcriber already registered: {name}")
        _TRANS[name] = builder
        return builder

    return _wrap


@register_synthesizer("fake")
def _fake_synth(cfg, secrets) -> SpeechSynthesizer:
    return FakeSynthesizer()


@register_transcriber("fake")
def _fake_trans(cfg, secrets) -> SpeechTranscriber:
    return FakeTranscriber()


def _ensure_elevenlabs(provider: str) -> None:
    if provider == "elevenlabs" and "elevenlabs" not in _SYNTH:
        from ai_sdr.voice import elevenlabs  # noqa: F401


def build_synthesizer(
    cfg: SpeechSynthesisConfig, secrets: Mapping[str, str]
) -> SpeechSynthesizer:
    _ensure_elevenlabs(cfg.provider)
    builder = _SYNTH.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown synthesizer provider: {cfg.provider!r}")
    return builder(cfg, secrets)


def build_transcriber(
    cfg: SpeechTranscriptionConfig, secrets: Mapping[str, str]
) -> SpeechTranscriber:
    _ensure_elevenlabs(cfg.provider)
    builder = _TRANS.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown transcriber provider: {cfg.provider!r}")
    return builder(cfg, secrets)
```

- [ ] **Step 4: Run fake test to verify it passes**

Run: `uv run pytest tests/unit/test_voice_fake.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the compliance test (fake + elevenlabs-mocked)**

```python
# tests/integration/test_voice_compliance.py
from __future__ import annotations

import httpx
import pytest

from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, SpeechTranscriptionConfig
from ai_sdr.voice import elevenlabs as el
from ai_sdr.voice.base import SpeechSynthesizer
from ai_sdr.voice.fake import FakeSynthesizer

pytestmark = pytest.mark.integration


def _mock_client(response: httpx.Response):
    return lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response), timeout=timeout
    )


@pytest.fixture(params=["fake", "elevenlabs"])
def synth_under_test(request, monkeypatch) -> SpeechSynthesizer:
    if request.param == "fake":
        return FakeSynthesizer()
    cfg = SpeechSynthesisConfig(
        provider="elevenlabs", credentials_ref="secrets/k", voice_id="v"
    )
    monkeypatch.setattr(el, "_build_http_client", _mock_client(httpx.Response(200, content=b"OGGDATA")))
    return el.ElevenLabsSynthesizer(cfg, {"k": "xi-key"})


async def test_synthesize_returns_audio_bytes(synth_under_test):
    r = await synth_under_test.synthesize("oi", "v")
    assert r.audio
    assert r.char_count == 2


async def test_elevenlabs_transcriber_parses_text(monkeypatch):
    cfg = SpeechTranscriptionConfig(provider="elevenlabs", credentials_ref="secrets/k")
    monkeypatch.setattr(
        el, "_build_http_client",
        _mock_client(httpx.Response(200, json={"text": "oi tudo bem", "language_probability": 0.9})),
    )
    t = el.ElevenLabsTranscriber(cfg, {"k": "xi-key"})
    r = await t.transcribe(b"\x00")
    assert r.text == "oi tudo bem"
    assert r.confidence == 0.9
    assert r.provider == "elevenlabs"
```

- [ ] **Step 6: Run compliance test**

Run: `uv run pytest tests/integration/test_voice_compliance.py -q`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/voice tests/unit/test_voice_fake.py tests/integration/test_voice_compliance.py
git commit -m "feat(fe05): voice adapters (synth/transcribe, fake/elevenlabs) + compliance"
```

---

### Task 4: Inbound message value-type gains media fields + fake media methods

**Files:**
- Modify: `src/ai_sdr/messaging/base.py` (extend `InboundMessage`; add abstract `send_audio` + `download_media` with safe defaults)
- Modify: `src/ai_sdr/messaging/fake.py` (implement the two new methods + media scripting)
- Test: `tests/unit/test_messaging_base_media.py`

**Interfaces:**
- Produces: `InboundMessage` gains `media_type:str="text"` and `media_ref:str|None=None`; `MessagingAdapter.send_audio(self, to:str, audio:bytes, content_type:str) -> SendResult` (abstract) and `MessagingAdapter.download_media(self, media_ref:str) -> tuple[bytes,str]` (abstract). `FakeMessagingAdapter.sent_audio: list[dict]`, `.media_blobs: dict[str,tuple[bytes,str]]`, `.stage_media(media_ref, data, content_type)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_messaging_base_media.py
from __future__ import annotations

from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.fake import FakeMessagingAdapter


def test_inbound_message_defaults_to_text_modality():
    m = InboundMessage(
        external_id="x", from_address="+1", text="hi",
        received_at_iso="2026-06-19T00:00:00+00:00", raw={},
    )
    assert m.media_type == "text"
    assert m.media_ref is None


async def test_fake_send_audio_records_payload():
    a = FakeMessagingAdapter()
    r = await a.send_audio("+5511999998888", b"OGG", "audio/ogg")
    assert r.external_id
    assert a.sent_audio == [{"to": "+5511999998888", "content_type": "audio/ogg", "n_bytes": 3}]


async def test_fake_download_media_returns_staged_blob():
    a = FakeMessagingAdapter()
    a.stage_media("media-123", b"VOICEBYTES", "audio/ogg")
    data, ct = await a.download_media("media-123")
    assert data == b"VOICEBYTES"
    assert ct == "audio/ogg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_messaging_base_media.py -q`
Expected: FAIL — `TypeError: InboundMessage.__init__() ... media_type` is absent / `AttributeError: send_audio`.

- [ ] **Step 3: Write minimal implementation**

In `src/ai_sdr/messaging/base.py`, add two fields to the `InboundMessage` frozen dataclass (after `raw`):

```python
    media_type: str = "text"
    media_ref: str | None = None
```

Add two abstract methods to `MessagingAdapter` (after `send_template`):

```python
    @abstractmethod
    async def send_audio(self, to: str, audio: bytes, content_type: str) -> SendResult:
        """Deliver an audio message. Implementations upload the bytes to the
        provider's media endpoint then send by media id. Same retry/error
        contract as send_text."""

    @abstractmethod
    async def download_media(self, media_ref: str) -> tuple[bytes, str]:
        """Fetch inbound media bytes by the provider-native reference.
        Returns (bytes, mime_type)."""
```

In `src/ai_sdr/messaging/fake.py`, extend `__init__` and add methods:

```python
        # in __init__:
        self.sent_audio: list[dict[str, object]] = []
        self.media_blobs: dict[str, tuple[bytes, str]] = {}

    def stage_media(self, media_ref: str, data: bytes, content_type: str) -> None:
        self.media_blobs[media_ref] = (data, content_type)

    async def send_audio(self, to: str, audio: bytes, content_type: str) -> SendResult:
        if self._pending_failure is not None:
            exc = self._pending_failure
            self._pending_failure = None
            raise exc
        self.sent_audio.append({"to": to, "content_type": content_type, "n_bytes": len(audio)})
        return SendResult(
            external_id=f"fakeaud_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(UTC).isoformat(),
        )

    async def download_media(self, media_ref: str) -> tuple[bytes, str]:
        return self.media_blobs[media_ref]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_messaging_base_media.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/base.py src/ai_sdr/messaging/fake.py tests/unit/test_messaging_base_media.py
git commit -m "feat(fe05): InboundMessage media fields + fake send_audio/download_media"
```

---

### Task 5: WhatsApp adapter — parse inbound audio, send_audio, download_media

**Files:**
- Modify: `src/ai_sdr/messaging/whatsapp_cloud.py`
- Test: `tests/integration/test_whatsapp_audio.py`

**Interfaces:**
- Consumes: `InboundMessage.media_type/media_ref` (Task 4), `SendResult`.
- Produces: WhatsApp `handle_inbound` emits `InboundMessage(media_type="audio", media_ref=<meta media id>)` for audio messages; `send_audio` uploads bytes to `/{phone_id}/media` then POSTs `type=audio` with the returned id; `download_media` does the two-step Meta fetch (GET media metadata → GET binary URL).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_whatsapp_audio.py
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

pytestmark = pytest.mark.integration


def _adapter() -> WhatsAppCloudAPIAdapter:
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/p",
        access_token_ref="secrets/t",
        webhook_verify_token_ref="secrets/v",
        app_secret_ref="secrets/s",
    )
    return WhatsAppCloudAPIAdapter(cfg, {"p": "111", "t": "TOK", "v": "vt", "s": "appsecret"})


def _sign(body: bytes, secret: str = "appsecret") -> dict:
    return {"x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


async def test_handle_inbound_audio_yields_media_ref():
    a = _adapter()
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{
            "id": "wamid.AUDIO1", "from": "5511988887777", "timestamp": "1716638400",
            "type": "audio", "audio": {"id": "media-xyz", "mime_type": "audio/ogg"},
        }]}}]}]
    }
    body = json.dumps(payload).encode()
    msgs = await a.handle_inbound(body, _sign(body))
    assert len(msgs) == 1
    assert msgs[0].media_type == "audio"
    assert msgs[0].media_ref == "media-xyz"
    assert msgs[0].text == ""


async def test_send_audio_uploads_then_sends(monkeypatch):
    a = _adapter()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "uploaded-media-1"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.AUDOUT"}]})

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15.0),
    )
    r = await a.send_audio("+5511988887777", b"OGGBYTES", "audio/ogg")
    assert r.external_id == "wamid.AUDOUT"


async def test_download_media_two_step(monkeypatch):
    a = _adapter()

    def handler(req: httpx.Request) -> httpx.Response:
        if "/media-xyz" in req.url.path or req.url.path.endswith("media-xyz"):
            return httpx.Response(200, json={"url": "https://lookaside.fbcdn.net/blob", "mime_type": "audio/ogg"})
        return httpx.Response(200, content=b"VOICE")

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15.0),
    )
    data, mime = await a.download_media("media-xyz")
    assert data == b"VOICE"
    assert mime == "audio/ogg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_whatsapp_audio.py -q`
Expected: FAIL — audio branch missing (`media_type` AttributeError on returned msg) / `AttributeError: send_audio`.

- [ ] **Step 3: Write minimal implementation**

In `handle_inbound`, replace the `if m.get("type") != "text": continue` block so audio is captured:

```python
                for m in value.get("messages", []):
                    mtype = m.get("type")
                    if mtype == "text":
                        text_body = (m.get("text") or {}).get("body", "")
                        media_ref = None
                    elif mtype == "audio":
                        text_body = ""
                        media_ref = (m.get("audio") or {}).get("id")
                    else:
                        continue  # image/document — reserved seat (FE-05 v2)
                    received_dt = dt.datetime.fromtimestamp(int(m["timestamp"]), tz=dt.UTC)
                    out.append(
                        InboundMessage(
                            external_id=m["id"],
                            from_address="+" + m["from"],
                            text=text_body,
                            received_at_iso=received_dt.isoformat(),
                            raw=m,
                            media_type=("audio" if mtype == "audio" else "text"),
                            media_ref=media_ref,
                        )
                    )
```

Add the two methods to `WhatsAppCloudAPIAdapter` (after `send_template`):

```python
    async def send_audio(self, to: str, audio: bytes, content_type: str) -> SendResult:
        upload_url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/media"
        send_url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/messages"
        auth = {"Authorization": f"Bearer {self._access_token}"}
        async with _build_http_client() as client:
            up = await client.post(
                upload_url,
                data={"messaging_product": "whatsapp", "type": content_type},
                files={"file": ("audio.ogg", audio, content_type)},
                headers=auth,
            )
            if up.status_code != 200:
                raise _classify_error(up.status_code, (up.json() or {}).get("error"), None)
            media_id = up.json()["id"]
            body = {
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "audio",
                "audio": {"id": media_id},
            }
            resp = await client.post(send_url, json=body, headers=auth)
        if resp.status_code != 200:
            raise _classify_error(resp.status_code, (resp.json() or {}).get("error"), None)
        return SendResult(
            external_id=resp.json()["messages"][0]["id"],
            sent_at_iso=datetime.now(dt.UTC).isoformat(),
        )

    async def download_media(self, media_ref: str) -> tuple[bytes, str]:
        meta_url = f"https://graph.facebook.com/{self._api_version}/{media_ref}"
        auth = {"Authorization": f"Bearer {self._access_token}"}
        async with _build_http_client() as client:
            meta = await client.get(meta_url, headers=auth)
            if meta.status_code != 200:
                raise _classify_error(meta.status_code, (meta.json() or {}).get("error"), None)
            info = meta.json()
            blob = await client.get(info["url"], headers=auth)
            if blob.status_code != 200:
                raise _classify_error(blob.status_code, None, None)
        return blob.content, info.get("mime_type", "audio/ogg")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_whatsapp_audio.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Re-run the existing compliance suite (no regressions)**

Run: `uv run pytest tests/integration/test_adapter_compliance.py -q`
Expected: PASS (unchanged count). The `WhatsAppCloudAPIAdapter` now satisfies the extended ABC.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/whatsapp_cloud.py tests/integration/test_whatsapp_audio.py
git commit -m "feat(fe05): whatsapp inbound audio parse + send_audio + download_media"
```

---

### Task 6: Persist `media_type` + `media_ref` on ingest

**Files:**
- Modify: `src/ai_sdr/messaging/ingest.py` (`ingest_inbound_message` — write `media_type`; stash `media_ref` so the normalizer can fetch)
- Test: `tests/integration/test_ingest_audio.py`

**Interfaces:**
- Consumes: `InboundMessage.media_type/media_ref` (Task 4), `InboundMessageRow` (already has `media_type`, `audio_url`).
- Produces: `InboundMessageRow.media_type` reflects the inbound modality; the Meta `media_ref` is retrievable for the normalizer (carried in the persisted `raw` JSON, which for audio already contains `audio.id`).

> **Note:** `media_ref` is already inside `raw` for WhatsApp (`raw["audio"]["id"]`). We persist `media_type` explicitly and rely on `raw` for the ref, so no schema change. The normalizer (Task 7) reads `media_ref` from `raw`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_ingest_audio.py
from __future__ import annotations

import pytest
from sqlalchemy import select

from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.ingest import ingest_inbound_message
from ai_sdr.models.inbound_message import InboundMessageRow

pytestmark = pytest.mark.integration


async def test_ingest_persists_audio_media_type(db_session, seeded_tenant):
    msg = InboundMessage(
        external_id="wamid.A1", from_address="+5511988887777", text="",
        received_at_iso="2026-06-19T12:00:00+00:00",
        raw={"id": "wamid.A1", "type": "audio", "audio": {"id": "media-xyz"}},
        media_type="audio", media_ref="media-xyz",
    )
    await ingest_inbound_message(db_session, seeded_tenant, "whatsapp_cloud", msg)
    row = (
        await db_session.execute(
            select(InboundMessageRow).where(InboundMessageRow.external_id == "wamid.A1")
        )
    ).scalar_one()
    assert row.media_type == "audio"
    assert row.raw["audio"]["id"] == "media-xyz"
```

> Reuse the existing integration fixtures for `db_session` and a seeded tenant. If the repo's fixture names differ, match `tests/integration/conftest.py` (grep for `async def seeded_tenant` / `db_session`). If no such fixtures exist, add a minimal one mirroring `tests/integration/test_ingest*.py` patterns already in the repo.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_ingest_audio.py -q`
Expected: FAIL — `row.media_type == "text"` (default) since ingest never set it.

- [ ] **Step 3: Write minimal implementation**

In `ingest_inbound_message`, add `media_type` to the `.values(...)` of the `pg_insert`:

```python
        .values(
            tenant_id=tenant.id,
            provider=provider,
            external_id=msg.external_id,
            lead_id=lead.id,
            from_address=msg.from_address,
            text=msg.text,
            received_at=received_at,
            raw=dict(msg.raw),
            status="queued",
            media_type=msg.media_type,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_ingest_audio.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/ingest.py tests/integration/test_ingest_audio.py
git commit -m "feat(fe05): persist media_type on inbound ingest"
```

---

### Task 7: Inbound Normalizer

**Files:**
- Create: `src/ai_sdr/voice/normalizer.py`
- Test: `tests/unit/test_normalizer.py`

**Interfaces:**
- Consumes: `MessagingAdapter.download_media` (Task 5), `SpeechTranscriber` (Task 3), `StorageAdapter` (Task 2), `SpeechTranscriptionConfig` (Task 1), `InboundMessageRow`.
- Produces: enum-ish `NormalizeOutcome = Literal["processed","low_confidence","unprocessable"]`; `async normalize_inbound(inbound:InboundMessageRow, *, messaging:MessagingAdapter, transcriber:SpeechTranscriber, storage:StorageAdapter, transcription_cfg:SpeechTranscriptionConfig) -> NormalizeOutcome`. Mutates the row in place (`transcription`, `transcription_confidence`, `transcription_provider`, `media_storage_key`, `audio_url`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_normalizer.py
from __future__ import annotations

import pytest

from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechTranscriptionConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeTranscriber
from ai_sdr.voice.normalizer import normalize_inbound


class _Row:
    """Minimal stand-in for InboundMessageRow (attribute bag)."""

    def __init__(self, **kw):
        self.id = kw.get("id", "row-1")
        self.media_type = kw["media_type"]
        self.text = kw.get("text", "")
        self.raw = kw.get("raw", {})
        self.transcription = None
        self.transcription_confidence = None
        self.transcription_provider = None
        self.media_storage_key = None
        self.audio_url = None


def _cfg(min_conf=0.5):
    return SpeechTranscriptionConfig(provider="fake", credentials_ref="secrets/k", min_confidence=min_conf)


async def test_text_inbound_is_processed_untouched():
    row = _Row(media_type="text", text="oi")
    outcome = await normalize_inbound(
        row, messaging=FakeMessagingAdapter(), transcriber=FakeTranscriber(),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(),
    )
    assert outcome == "processed"
    assert row.transcription is None


async def test_audio_inbound_transcribes_and_stores():
    messaging = FakeMessagingAdapter()
    messaging.stage_media("media-xyz", b"VOICE", "audio/ogg")
    row = _Row(media_type="audio", raw={"audio": {"id": "media-xyz"}})
    outcome = await normalize_inbound(
        row, messaging=messaging, transcriber=FakeTranscriber(text="quero saber o preço", confidence=0.9),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(),
    )
    assert outcome == "processed"
    assert row.transcription == "quero saber o preço"
    assert row.transcription_confidence == 0.9
    assert row.transcription_provider == "fake"
    assert row.media_storage_key == "inbound/row-1.ogg"
    assert row.audio_url


async def test_low_confidence_returns_low_confidence():
    messaging = FakeMessagingAdapter()
    messaging.stage_media("m", b"V", "audio/ogg")
    row = _Row(media_type="audio", raw={"audio": {"id": "m"}})
    outcome = await normalize_inbound(
        row, messaging=messaging, transcriber=FakeTranscriber(text="??", confidence=0.2),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(min_conf=0.5),
    )
    assert outcome == "low_confidence"


async def test_download_failure_returns_unprocessable():
    row = _Row(media_type="audio", raw={"audio": {"id": "absent"}})
    outcome = await normalize_inbound(
        row, messaging=FakeMessagingAdapter(), transcriber=FakeTranscriber(),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(),
    )
    assert outcome == "unprocessable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_normalizer.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_sdr.voice.normalizer`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_sdr/voice/normalizer.py
"""Inbound modality normalizer — runs in the worker BEFORE run_turn.

Translates an inbound audio message into text the text-only turn can
consume: download media → archive to storage → transcribe. Text inbound
is a passthrough. The row is mutated in place; the caller commits.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechTranscriptionConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.voice.base import SpeechTranscriber

logger = logging.getLogger(__name__)

NormalizeOutcome = Literal["processed", "low_confidence", "unprocessable"]


class _InboundRowProto(Protocol):
    id: object
    media_type: str
    raw: dict
    transcription: str | None
    transcription_confidence: float | None
    transcription_provider: str | None
    media_storage_key: str | None
    audio_url: str | None


async def normalize_inbound(
    inbound: _InboundRowProto,
    *,
    messaging: MessagingAdapter,
    transcriber: SpeechTranscriber,
    storage: StorageAdapter,
    transcription_cfg: SpeechTranscriptionConfig,
) -> NormalizeOutcome:
    if inbound.media_type != "audio":
        return "processed"

    media_ref = (inbound.raw.get("audio") or {}).get("id")
    if not media_ref:
        logger.warning("normalize_inbound.no_media_ref inbound=%s", inbound.id)
        return "unprocessable"

    try:
        audio, content_type = await messaging.download_media(media_ref)
    except Exception as exc:  # CDN expiry / transient — treat as unprocessable
        logger.warning("normalize_inbound.download_failed inbound=%s err=%s", inbound.id, exc)
        return "unprocessable"

    key = f"inbound/{inbound.id}.ogg"
    try:
        url = await storage.upload(key, audio, content_type)
        inbound.media_storage_key = key
        inbound.audio_url = url
    except Exception as exc:  # archive miss must not block the turn
        logger.warning("normalize_inbound.storage_failed inbound=%s err=%s", inbound.id, exc)

    result = await transcriber.transcribe(audio, language=transcription_cfg.language)
    inbound.transcription = result.text
    inbound.transcription_confidence = result.confidence
    inbound.transcription_provider = result.provider

    if result.confidence < transcription_cfg.min_confidence:
        return "low_confidence"
    return "processed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_normalizer.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/voice/normalizer.py tests/unit/test_normalizer.py
git commit -m "feat(fe05): inbound voice normalizer (download/store/transcribe)"
```

---

### Task 8: Outbound modality decision

**Files:**
- Create: `src/ai_sdr/voice/renderer.py` (only `decide_modality` in this task; `render_and_send` lands in Task 9)
- Test: `tests/unit/test_renderer_modality.py`

**Interfaces:**
- Consumes: `VoiceConfig` (Task 1), `TurnDecision.response_format` (existing).
- Produces: `decide_modality(response_mode:str, response_format:str|None, last_inbound_media_type:str) -> Literal["text","voice"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_renderer_modality.py
from __future__ import annotations

import pytest

from ai_sdr.voice.renderer import decide_modality


@pytest.mark.parametrize(
    "mode,fmt,last_in,expected",
    [
        ("never", "voice", "audio", "text"),
        ("always", None, "text", "voice"),
        ("match_lead", None, "audio", "voice"),
        ("match_lead", None, "text", "text"),
        ("context_driven", "voice", "text", "voice"),
        ("context_driven", "both", "text", "voice"),
        ("context_driven", "text", "audio", "text"),
        ("context_driven", None, "audio", "text"),
    ],
)
def test_decide_modality_matrix(mode, fmt, last_in, expected):
    assert decide_modality(mode, fmt, last_in) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_renderer_modality.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_sdr.voice.renderer`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_sdr/voice/renderer.py
"""Outbound modality renderer — replaces the voice fallback slot in
flowengine.sender. decide_modality picks text vs voice per tenant policy;
render_and_send (Task 9) performs the synthesis + send.
"""

from __future__ import annotations

from typing import Literal


def decide_modality(
    response_mode: str,
    response_format: str | None,
    last_inbound_media_type: str,
) -> Literal["text", "voice"]:
    if response_mode == "always":
        return "voice"
    if response_mode == "never":
        return "text"
    if response_mode == "match_lead":
        return "voice" if last_inbound_media_type == "audio" else "text"
    if response_mode == "context_driven":
        return "voice" if response_format in ("voice", "both") else "text"
    return "text"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_renderer_modality.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/voice/renderer.py tests/unit/test_renderer_modality.py
git commit -m "feat(fe05): decide_modality policy (always/match_lead/never/context_driven)"
```

---

### Task 9: `render_and_send` + voice cost accumulation

**Files:**
- Modify: `src/ai_sdr/voice/renderer.py` (add `render_and_send` + `RenderResult`)
- Modify: `src/ai_sdr/flowengine/usage.py` (add `accumulate_voice_usage`)
- Test: `tests/unit/test_render_and_send.py`, `tests/unit/test_usage_voice.py`

**Interfaces:**
- Consumes: `decide_modality` (Task 8), `SpeechSynthesizer` (Task 3), `StorageAdapter` (Task 2), `MessagingAdapter.send_audio` (Task 5), `humanize`/`HumanizationConfig`/`Chunk` (existing), `VoiceConfig`.
- Produces: `RenderResult{external_id:str|None, modality:Literal["text","voice"], media_type:str, audio_url:str|None, media_storage_key:str|None, synthesis_voice_id:str|None, voice_emotion:str|None, audio_duration_ms:int|None, synthesis_chars:int}`; `async render_and_send(*, response_text:str, response_format:str|None, voice_emotion:str|None, to:str, message_id:str, voice_cfg:VoiceConfig|None, last_inbound_media_type:str, synthesizer:SpeechSynthesizer|None, storage:StorageAdapter|None, messaging:MessagingAdapter, humanization:HumanizationConfig) -> RenderResult`; `accumulate_voice_usage(running:dict, *, synthesis_chars:int=0, transcription_ms:int=0) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_usage_voice.py
from ai_sdr.flowengine.usage import accumulate_voice_usage


def test_accumulate_voice_usage_sums_in_place():
    running = {}
    accumulate_voice_usage(running, synthesis_chars=120)
    accumulate_voice_usage(running, synthesis_chars=30, transcription_ms=2000)
    assert running["voice_synthesis_chars"] == 150
    assert running["voice_transcription_ms"] == 2000
```

```python
# tests/unit/test_render_and_send.py
from __future__ import annotations

import pytest

from ai_sdr.flowengine.humanizer import HumanizationConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, VoiceConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeSynthesizer
from ai_sdr.voice.renderer import render_and_send


def _voice_cfg(mode="always", fallback=True) -> VoiceConfig:
    return VoiceConfig(
        response_mode=mode,
        fallback_to_text_on_failure=fallback,
        synthesis=SpeechSynthesisConfig(provider="fake", credentials_ref="secrets/k", voice_id="v1"),
    )


async def test_text_path_sends_text_and_no_audio():
    messaging = FakeMessagingAdapter()
    r = await render_and_send(
        response_text="olá", response_format=None, voice_emotion=None,
        to="+5511", message_id="m1", voice_cfg=None, last_inbound_media_type="text",
        synthesizer=None, storage=None, messaging=messaging,
        humanization=HumanizationConfig(enabled=False),
    )
    assert r.modality == "text"
    assert messaging.sent_messages and not messaging.sent_audio


async def test_voice_path_synthesizes_stores_and_sends_audio():
    messaging = FakeMessagingAdapter()
    storage = FakeStorageAdapter()
    r = await render_and_send(
        response_text="bom dia", response_format=None, voice_emotion="happy",
        to="+5511", message_id="out-1", voice_cfg=_voice_cfg(), last_inbound_media_type="audio",
        synthesizer=FakeSynthesizer(), storage=storage, messaging=messaging,
        humanization=HumanizationConfig(),
    )
    assert r.modality == "voice"
    assert r.media_type == "audio"
    assert r.synthesis_voice_id == "v1"
    assert r.voice_emotion == "happy"
    assert r.synthesis_chars == len("bom dia")
    assert messaging.sent_audio and not messaging.sent_messages
    assert storage.objects["outbound/out-1.ogg"]
    assert r.audio_url


async def test_synthesis_failure_falls_back_to_text():
    class _BoomSynth(FakeSynthesizer):
        async def synthesize(self, *a, **k):
            raise RuntimeError("eleven down")

    messaging = FakeMessagingAdapter()
    r = await render_and_send(
        response_text="oi", response_format=None, voice_emotion=None,
        to="+5511", message_id="m2", voice_cfg=_voice_cfg(fallback=True),
        last_inbound_media_type="audio", synthesizer=_BoomSynth(),
        storage=FakeStorageAdapter(), messaging=messaging, humanization=HumanizationConfig(),
    )
    assert r.modality == "text"
    assert messaging.sent_messages and not messaging.sent_audio


async def test_synthesis_failure_without_fallback_raises():
    class _BoomSynth(FakeSynthesizer):
        async def synthesize(self, *a, **k):
            raise RuntimeError("eleven down")

    with pytest.raises(RuntimeError):
        await render_and_send(
            response_text="oi", response_format=None, voice_emotion=None,
            to="+5511", message_id="m3", voice_cfg=_voice_cfg(fallback=False),
            last_inbound_media_type="audio", synthesizer=_BoomSynth(),
            storage=FakeStorageAdapter(), messaging=FakeMessagingAdapter(),
            humanization=HumanizationConfig(),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_render_and_send.py tests/unit/test_usage_voice.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_and_send'` / `accumulate_voice_usage`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ai_sdr/flowengine/usage.py`:

```python
def accumulate_voice_usage(
    running: dict[str, Any], *, synthesis_chars: int = 0, transcription_ms: int = 0
) -> None:
    """Add voice cost counters into the running Talk.tokens_consumed dict."""
    running["voice_synthesis_chars"] = int(running.get("voice_synthesis_chars", 0) or 0) + int(
        synthesis_chars
    )
    running["voice_transcription_ms"] = int(running.get("voice_transcription_ms", 0) or 0) + int(
        transcription_ms
    )
```

Append to `src/ai_sdr/voice/renderer.py` (add imports at top):

```python
import contextlib
import logging
from dataclasses import dataclass

from ai_sdr.flowengine.humanizer import HumanizationConfig, humanize
from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.schemas.tenant_yaml import VoiceConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.voice.base import SpeechSynthesizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderResult:
    external_id: str | None
    modality: Literal["text", "voice"]
    media_type: str
    audio_url: str | None = None
    media_storage_key: str | None = None
    synthesis_voice_id: str | None = None
    voice_emotion: str | None = None
    audio_duration_ms: int | None = None
    synthesis_chars: int = 0


async def _send_text(messaging, to, response_text, humanization) -> str | None:
    chunks = humanize(response_text, humanization, is_voice=False)
    last_id: str | None = None
    for chunk in chunks:
        out = await messaging.send_text(to, chunk.text)
        last_id = out.external_id
    return last_id


async def render_and_send(
    *,
    response_text: str,
    response_format: str | None,
    voice_emotion: str | None,
    to: str,
    message_id: str,
    voice_cfg: VoiceConfig | None,
    last_inbound_media_type: str,
    synthesizer: SpeechSynthesizer | None,
    storage: StorageAdapter | None,
    messaging: MessagingAdapter,
    humanization: HumanizationConfig,
) -> RenderResult:
    modality = (
        decide_modality(voice_cfg.response_mode, response_format, last_inbound_media_type)
        if voice_cfg is not None
        else "text"
    )

    if modality == "text" or voice_cfg is None or synthesizer is None or storage is None:
        last_id = await _send_text(messaging, to, response_text, humanization)
        return RenderResult(external_id=last_id, modality="text", media_type="text")

    assert voice_cfg.synthesis is not None  # guaranteed by VoiceConfig validator
    try:
        synth = await synthesizer.synthesize(
            response_text,
            voice_cfg.synthesis.voice_id,
            emotion=voice_emotion or voice_cfg.synthesis.default_emotion,
            fmt=voice_cfg.synthesis.format,
        )
        key = f"outbound/{message_id}.ogg"
        url = await storage.upload(key, synth.audio, synth.content_type)
        send_out = await messaging.send_audio(to, synth.audio, synth.content_type)
    except Exception as exc:
        if voice_cfg.fallback_to_text_on_failure:
            logger.warning("render.voice_failed_fallback_text msg=%s err=%s", message_id, exc)
            last_id = await _send_text(messaging, to, response_text, humanization)
            return RenderResult(external_id=last_id, modality="text", media_type="text")
        raise

    return RenderResult(
        external_id=send_out.external_id,
        modality="voice",
        media_type="audio",
        audio_url=url,
        media_storage_key=key,
        synthesis_voice_id=synth.voice_id,
        voice_emotion=voice_emotion,
        audio_duration_ms=synth.duration_ms,
        synthesis_chars=synth.char_count,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_render_and_send.py tests/unit/test_usage_voice.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/voice/renderer.py src/ai_sdr/flowengine/usage.py tests/unit/test_render_and_send.py tests/unit/test_usage_voice.py
git commit -m "feat(fe05): render_and_send (voice/text) + voice cost accumulation"
```

---

### Task 10: Audit row records audio metadata

**Files:**
- Modify: `src/ai_sdr/flowengine/audit.py` (`record_outbound_audit` accepts a `RenderResult`-shaped media payload)
- Test: `tests/integration/test_audit_audio.py`

**Interfaces:**
- Consumes: `RenderResult` (Task 9), `OutboundMessage` (has the media columns).
- Produces: `record_outbound_audit(..., media_type:str="text", audio_url:str|None=None, media_storage_key:str|None=None, synthesis_voice_id:str|None=None, voice_emotion:str|None=None, audio_duration_ms:int|None=None)` persists those columns; `message_type` becomes `"audio"` when `media_type == "audio"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_audit_audio.py
from __future__ import annotations

import pytest
from sqlalchemy import select

from ai_sdr.flowengine.audit import record_outbound_audit
from ai_sdr.flowengine.sender import SendResult
from ai_sdr.models.outbound_message import OutboundMessage

pytestmark = pytest.mark.integration


async def test_audit_persists_audio_fields(db_session, seeded_talk, seeded_inbound):
    await record_outbound_audit(
        db_session, talk=seeded_talk, inbound=seeded_inbound,
        response_text="bom dia", turn_index=1,
        send_result=SendResult(external_id="wamid.AUD", status="sent"),
        provider="whatsapp_cloud", sent_at=seeded_inbound.received_at,
        media_type="audio", audio_url="https://minio.local/outbound/x.ogg",
        media_storage_key="outbound/x.ogg", synthesis_voice_id="v1",
        voice_emotion="calm", audio_duration_ms=4200,
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(OutboundMessage).where(OutboundMessage.external_id == "wamid.AUD")
        )
    ).scalar_one()
    assert row.media_type == "audio"
    assert row.message_type == "audio"
    assert row.synthesis_voice_id == "v1"
    assert row.audio_duration_ms == 4200
    assert row.audio_url.endswith("/outbound/x.ogg")
```

> Reuse existing integration fixtures (`db_session`, plus a seeded `Talk` and `InboundMessageRow`). Grep `tests/integration/conftest.py` for the actual fixture names used by the FE pipeline tests (e.g. `test_pipeline_smoke_3_turns.py`) and match them; rename `seeded_talk`/`seeded_inbound` accordingly.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_audit_audio.py -q`
Expected: FAIL — `TypeError: record_outbound_audit() got an unexpected keyword argument 'media_type'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ai_sdr/flowengine/audit.py`, extend the signature (after `chunk_index: int = 0`):

```python
    chunk_index: int = 0,
    media_type: str = "text",
    audio_url: str | None = None,
    media_storage_key: str | None = None,
    synthesis_voice_id: str | None = None,
    voice_emotion: str | None = None,
    audio_duration_ms: int | None = None,
```

Replace the hard-coded media fields in the `OutboundMessage(...)` construction:

```python
        message_type=("audio" if media_type == "audio" else "text"),
        ...
        media_type=media_type,
        media_storage_key=media_storage_key,
        audio_url=audio_url,
        audio_duration_ms=audio_duration_ms,
        synthesis_voice_id=synthesis_voice_id,
        voice_emotion=voice_emotion,
```

(Leave `body_text=response_text` as-is — the transcript text is still recorded for audio rows.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_audit_audio.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/audit.py tests/integration/test_audit_audio.py
git commit -m "feat(fe05): outbound audit records audio metadata"
```

---

### Task 11: Wire the renderer into `sender.py` + thread voice deps through `run_turn`

**Files:**
- Modify: `src/ai_sdr/flowengine/sender.py` (`send_response_text` delegates to `render_and_send`; returns media metadata)
- Modify: `src/ai_sdr/flowengine/pipeline.py` (`run_turn` gains optional `voice_cfg`, `synthesizer`, `storage`; passes them + `inbound.media_type` to the sender; forwards media metadata + voice cost to audit)
- Test: `tests/unit/test_sender_voice.py`

**Interfaces:**
- Consumes: `render_and_send` + `RenderResult` (Task 9), `accumulate_voice_usage` (Task 9), `record_outbound_audit` media kwargs (Task 10).
- Produces: `send_response_text(*, adapter, lead, decision, humanization_config, voice_cfg=None, synthesizer=None, storage=None, last_inbound_media_type="text") -> SendResult` where `SendResult` gains media fields mirrored from `RenderResult`. `run_turn(..., voice_cfg=None, synthesizer=None, storage=None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sender_voice.py
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.humanizer import HumanizationConfig
from ai_sdr.flowengine.sender import send_response_text
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, VoiceConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeSynthesizer


class _Lead:
    id = "lead-1"
    whatsapp_e164 = "+5511988887777"


def _decision(fmt=None) -> TurnDecision:
    return TurnDecision(
        response_text="bom dia", response_format=fmt, collected_fields={}, reasoning="x",
    )


async def test_sender_text_only_when_no_voice_cfg():
    adapter = FakeMessagingAdapter()
    r = await send_response_text(
        adapter=adapter, lead=_Lead(), decision=_decision(),
        humanization_config=HumanizationConfig(enabled=False),
    )
    assert r.media_type == "text"
    assert adapter.sent_messages and not adapter.sent_audio


async def test_sender_voice_when_always_mode():
    adapter = FakeMessagingAdapter()
    vcfg = VoiceConfig(
        response_mode="always",
        synthesis=SpeechSynthesisConfig(provider="fake", credentials_ref="secrets/k", voice_id="v1"),
    )
    r = await send_response_text(
        adapter=adapter, lead=_Lead(), decision=_decision(),
        humanization_config=HumanizationConfig(), voice_cfg=vcfg,
        synthesizer=FakeSynthesizer(), storage=FakeStorageAdapter(),
        last_inbound_media_type="text",
    )
    assert r.media_type == "audio"
    assert r.synthesis_voice_id == "v1"
    assert adapter.sent_audio
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sender_voice.py -q`
Expected: FAIL — `TypeError: send_response_text() got an unexpected keyword argument 'voice_cfg'` / `SendResult` has no `media_type`.

- [ ] **Step 3: Write minimal implementation**

Rewrite `src/ai_sdr/flowengine/sender.py`'s `SendResult` dataclass to carry media metadata, and replace the body of `send_response_text` to delegate:

```python
@dataclass
class SendResult:
    external_id: str | None
    status: str
    error_detail: str | None = None
    media_type: str = "text"
    audio_url: str | None = None
    media_storage_key: str | None = None
    synthesis_voice_id: str | None = None
    voice_emotion: str | None = None
    audio_duration_ms: int | None = None
    synthesis_chars: int = 0


async def send_response_text(
    *,
    adapter: MessagingAdapter,
    lead: Lead,
    decision: TurnDecision,
    humanization_config: HumanizationConfig,
    voice_cfg=None,
    synthesizer=None,
    storage=None,
    last_inbound_media_type: str = "text",
) -> SendResult:
    from ai_sdr.voice.renderer import render_and_send

    render = await render_and_send(
        response_text=decision.response_text,
        response_format=decision.response_format,
        voice_emotion=decision.voice_emotion,
        to=lead.whatsapp_e164,
        message_id=str(lead.id),  # replaced with a per-turn id in pipeline wiring below
        voice_cfg=voice_cfg,
        last_inbound_media_type=last_inbound_media_type,
        synthesizer=synthesizer,
        storage=storage,
        messaging=adapter,
        humanization=humanization_config,
    )
    return SendResult(
        external_id=render.external_id,
        status="sent",
        media_type=render.media_type,
        audio_url=render.audio_url,
        media_storage_key=render.media_storage_key,
        synthesis_voice_id=render.synthesis_voice_id,
        voice_emotion=render.voice_emotion,
        audio_duration_ms=render.audio_duration_ms,
        synthesis_chars=render.synthesis_chars,
    )
```

> Keep the imports `TurnDecision`, `HumanizationConfig`, `MessagingAdapter`, `Lead` already present in the file. Drop the now-unused `humanize`/`asyncio`/`contextlib` imports only if unreferenced (the renderer owns chunking now).

In `src/ai_sdr/flowengine/pipeline.py`:

1. Extend `run_turn` signature with three optional params after `now`:

```python
    voice_cfg=None,
    synthesizer=None,
    storage=None,
```

2. Replace the step [12] `send_response_text(...)` call to pass voice deps + the current inbound modality, and use a per-turn message id (`inbound.id`) for deterministic storage keys:

```python
        send_result = await send_response_text(
            adapter=adapter,
            lead=ctx.lead,
            decision=decision,
            humanization_config=humanization,
            voice_cfg=voice_cfg,
            synthesizer=synthesizer,
            storage=storage,
            last_inbound_media_type=inbound.media_type,
        )
```

3. In step [11] token bookkeeping, accumulate voice cost when audio was synthesized:

```python
        from ai_sdr.flowengine.usage import accumulate_voice_usage

        if send_result.synthesis_chars:
            accumulate_voice_usage(tokens, synthesis_chars=send_result.synthesis_chars)
        ctx.talk.tokens_consumed = tokens
```

4. In step [13] audit call, forward media metadata:

```python
        await record_outbound_audit(
            session,
            talk=ctx.talk,
            inbound=inbound,
            response_text=decision.response_text,
            turn_index=ctx.talk.turn_count,
            send_result=send_result,
            provider=inbound.provider,
            sent_at=now,
            media_type=send_result.media_type,
            audio_url=send_result.audio_url,
            media_storage_key=send_result.media_storage_key,
            synthesis_voice_id=send_result.synthesis_voice_id,
            voice_emotion=send_result.voice_emotion,
            audio_duration_ms=send_result.audio_duration_ms,
        )
```

> The deterministic storage key should be `outbound/{inbound.id}.ogg`. Update `send_response_text` to accept `message_id` and pass `str(inbound.id)` from the pipeline instead of `str(lead.id)`; thread it through as a parameter. (Add `message_id: str` to `send_response_text` and pass `message_id=str(inbound.id)` in the pipeline call; default it to `str(lead.id)` only in unit tests that don't have an inbound.)

- [ ] **Step 4: Run unit + smoke to verify they pass**

Run: `uv run pytest tests/unit/test_sender_voice.py -q`
Expected: PASS (2 passed).
Run: `uv run pytest tests/integration/test_pipeline_smoke_3_turns.py -q`
Expected: PASS (unchanged — text path still default since `voice_cfg=None`).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/sender.py src/ai_sdr/flowengine/pipeline.py tests/unit/test_sender_voice.py
git commit -m "feat(fe05): wire renderer into sender + thread voice deps through run_turn"
```

---

### Task 12: Worker wires the normalizer + builds voice/storage deps

**Files:**
- Modify: `src/ai_sdr/worker/jobs/inbound.py` (build transcriber/synthesizer/storage from tenant_cfg; call `normalize_inbound` before `run_turn`; on `low_confidence` send fallback + skip turn; pass voice deps to `run_turn`)
- Test: `tests/integration/test_worker_voice.py`

**Interfaces:**
- Consumes: `normalize_inbound` (Task 7), `build_transcriber`/`build_synthesizer` (Task 3), `build_storage_adapter` (Task 2), `run_turn(voice_cfg, synthesizer, storage)` (Task 11), `tenant_cfg.voice`/`tenant_cfg.storage`.
- Produces: end-to-end audio handling in the live worker path. A module-level helper `_build_voice_stack(tenant_cfg, secrets) -> tuple[synth|None, transcriber|None, storage|None]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_worker_voice.py
"""Audio inbound → transcription populates the row before the turn runs."""

from __future__ import annotations

import pytest

from ai_sdr.schemas.tenant_yaml import (
    SpeechSynthesisConfig,
    SpeechTranscriptionConfig,
    StorageConfig,
    VoiceConfig,
)
from ai_sdr.worker.jobs.inbound import _build_voice_stack

pytestmark = pytest.mark.integration


def test_build_voice_stack_returns_none_when_no_voice():
    synth, trans, storage = _build_voice_stack(_cfg_without_voice(), {})
    assert (synth, trans, storage) == (None, None, None)


def test_build_voice_stack_builds_all_three():
    cfg = _cfg_with_voice()
    secrets = {"elevenlabs_api_key": "k", "minio_endpoint": "https://m", "minio_access_key": "a", "minio_secret_key": "s"}
    synth, trans, storage = _build_voice_stack(cfg, secrets)
    assert synth is not None and trans is not None and storage is not None


def _cfg_without_voice():
    from ai_sdr.schemas.tenant_yaml import TenantConfig
    return TenantConfig(id="avelum", display_name="A", timezone="America/Sao_Paulo")


def _cfg_with_voice():
    from ai_sdr.schemas.tenant_yaml import TenantConfig
    return TenantConfig(
        id="avelum", display_name="A", timezone="America/Sao_Paulo",
        voice=VoiceConfig(
            response_mode="match_lead",
            synthesis=SpeechSynthesisConfig(provider="elevenlabs", credentials_ref="secrets/elevenlabs_api_key", voice_id="v1"),
            transcription=SpeechTranscriptionConfig(provider="elevenlabs", credentials_ref="secrets/elevenlabs_api_key"),
        ),
        storage=StorageConfig(provider="minio", bucket="b", endpoint_ref="secrets/minio_endpoint", access_key_ref="secrets/minio_access_key", secret_key_ref="secrets/minio_secret_key"),
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_worker_voice.py -q`
Expected: FAIL — `ImportError: cannot import name '_build_voice_stack'`.

- [ ] **Step 3: Write minimal implementation**

Add the helper near the top of `src/ai_sdr/worker/jobs/inbound.py` (module level):

```python
def _build_voice_stack(tenant_cfg, secrets):
    """Return (synthesizer|None, transcriber|None, storage|None) for a tenant."""
    from ai_sdr.storage.factory import build_storage_adapter
    from ai_sdr.voice.factory import build_synthesizer, build_transcriber

    voice = getattr(tenant_cfg, "voice", None)
    storage_cfg = getattr(tenant_cfg, "storage", None)
    if voice is None:
        return None, None, None
    synth = build_synthesizer(voice.synthesis, secrets) if voice.synthesis else None
    trans = build_transcriber(voice.transcription, secrets) if voice.transcription else None
    storage = build_storage_adapter(storage_cfg, secrets) if storage_cfg else None
    return synth, trans, storage
```

Then, in the message-processing block right before the `run_turn(...)` call (around `src/ai_sdr/worker/jobs/inbound.py:389`), insert normalization + thread deps. The exact surrounding variables are `tenant`, `tenant_cfg`, `secrets`, `adapter`, and the inbound row (the head message merged for the turn). Add:

```python
            synth, transcriber, storage = _build_voice_stack(tenant_cfg, secrets)
            if transcriber is not None and storage is not None and head_inbound.media_type == "audio":
                from ai_sdr.voice.normalizer import normalize_inbound

                outcome = await normalize_inbound(
                    head_inbound,
                    messaging=adapter,
                    transcriber=transcriber,
                    storage=storage,
                    transcription_cfg=tenant_cfg.voice.transcription,
                )
                if outcome != "processed":
                    await adapter.send_text(
                        head_inbound.from_address,
                        "Não consegui entender o áudio, pode mandar por escrito?",
                    )
                    head_inbound.status = "processed"
                    head_inbound.processed_at = datetime.now(UTC)
                    await session.commit()
                    continue  # skip run_turn for this message
```

And pass the voice deps into the existing `run_turn(...)` call:

```python
            result = await run_turn(
                ...,  # existing args unchanged
                voice_cfg=tenant_cfg.voice,
                synthesizer=synth,
                storage=storage,
            )
```

> Match the real local variable names in `inbound.py` (the head inbound row, `session`, `adapter`, `tenant_cfg`, `secrets`). Grep the function around line 360-389 to confirm: the merged head message variable + how `secrets` is obtained (likely via the `AdapterRegistry`/`SopsLoader` already in scope). If `secrets` isn't already loaded in that scope, load it once via the same `SopsLoader` the registry uses.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_worker_voice.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/worker/jobs/inbound.py tests/integration/test_worker_voice.py
git commit -m "feat(fe05): worker normalizes inbound audio + threads voice deps to run_turn"
```

---

### Task 13: System prompt instructs the LLM on `response_format` (context_driven only)

**Files:**
- Modify: `src/ai_sdr/flowengine/system_prompt.py` (append a voice-mode instruction to the fresh layer when `response_mode == "context_driven"`)
- Test: `tests/unit/test_system_prompt_voice.py`

**Interfaces:**
- Consumes: `build_fresh_layer` (existing) — add an optional `voice_response_mode: str | None = None` parameter; when `"context_driven"`, the rendered prompt includes guidance to set `response_format`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_system_prompt_voice.py
from __future__ import annotations

from ai_sdr.flowengine.system_prompt import voice_mode_instruction


def test_no_instruction_for_non_context_modes():
    assert voice_mode_instruction("always") == ""
    assert voice_mode_instruction("never") == ""
    assert voice_mode_instruction("match_lead") == ""
    assert voice_mode_instruction(None) == ""


def test_context_driven_emits_response_format_guidance():
    text = voice_mode_instruction("context_driven")
    assert "response_format" in text
    assert "voice" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_system_prompt_voice.py -q`
Expected: FAIL — `ImportError: cannot import name 'voice_mode_instruction'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ai_sdr/flowengine/system_prompt.py`:

```python
def voice_mode_instruction(response_mode: str | None) -> str:
    """Guidance appended to the fresh layer so the LLM can choose audio in
    context_driven mode. Empty for all other modes (the runtime decides)."""
    if response_mode != "context_driven":
        return ""
    return (
        "\n\nMODALIDADE DE RESPOSTA: você pode responder em áudio quando for mais "
        "natural (ex.: o lead mandou áudio, ou a mensagem é longa/emocional). "
        "Para isso, defina response_format='voice'. Caso contrário use "
        "response_format='text' ou deixe nulo."
    )
```

Then include it where the fresh layer text is assembled. In `build_fresh_layer`, add a parameter `voice_response_mode: str | None = None` and append `voice_mode_instruction(voice_response_mode)` to the rendered instruction string. In `pipeline.py`'s `_fresh(...)` builder, pass `voice_response_mode=(voice_cfg.response_mode if voice_cfg else None)`.

> If `build_fresh_layer` composes its text from a list/template, append the instruction to the same string that carries node guidance. Keep the change additive — an empty string for non-context modes changes nothing.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_system_prompt_voice.py -q`
Expected: PASS (2 passed).
Run: `uv run pytest tests/unit/test_system_prompt*.py -q`
Expected: PASS (existing prompt tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/system_prompt.py src/ai_sdr/flowengine/pipeline.py tests/unit/test_system_prompt_voice.py
git commit -m "feat(fe05): context_driven response_format prompt instruction"
```

---

### Task 14: End-to-end integration test (audio in → voice out)

**Files:**
- Test: `tests/integration/test_turn_voice_e2e.py`

**Interfaces:**
- Consumes: everything above through `run_turn` with fakes (`FakeMessagingAdapter`, `FakeSynthesizer`, `FakeStorageAdapter`) + a stub `Runnable` LLM that returns a `TurnDecision`.

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_turn_voice_e2e.py
"""Audio inbound transcribed → run_turn → voice outbound, all via fakes."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, VoiceConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeSynthesizer

pytestmark = pytest.mark.integration


class _StubLLM:
    """Runnable-like: returns a fixed TurnDecision regardless of input."""

    async def ainvoke(self, messages):
        return TurnDecision(
            response_text="claro, posso te ajudar com isso",
            response_format=None, collected_fields={}, reasoning="stub",
        )


async def test_match_lead_audio_inbound_yields_voice_outbound(
    db_session, seeded_tenant, seeded_treeflow, audio_inbound_row
):
    """audio_inbound_row.media_type == 'audio' and .transcription preset by
    the normalizer step (we set it here to isolate the outbound path)."""
    from ai_sdr.flowengine.pipeline import run_turn

    audio_inbound_row.transcription = "qual o valor?"
    messaging = FakeMessagingAdapter()
    voice_cfg = VoiceConfig(
        response_mode="match_lead",
        synthesis=SpeechSynthesisConfig(provider="fake", credentials_ref="secrets/k", voice_id="v1"),
    )

    result = await run_turn(
        db_session,
        tenant=seeded_tenant.model,
        tenant_cfg=seeded_tenant.cfg,
        treeflow=seeded_treeflow.definition,
        treeflow_version=seeded_treeflow.version,
        inbound=audio_inbound_row,
        llm=_StubLLM(),
        adapter=messaging,
        opt_out_keywords=[],
        guardrail_cfg=seeded_tenant.guardrail_cfg,
        voice_cfg=voice_cfg,
        synthesizer=FakeSynthesizer(),
        storage=FakeStorageAdapter(),
    )

    assert result.outcome == "sent"
    assert messaging.sent_audio, "expected a voice message because last inbound was audio"
    assert not messaging.sent_messages
```

> This test reuses the same fixtures the existing `tests/integration/test_pipeline_smoke_3_turns.py` uses to build a seeded tenant/treeflow/guardrails and an inbound row. Grep that file for the exact fixture names + how it constructs `tenant`, `tenant_cfg`, `treeflow`, `treeflow_version`, `guardrail_cfg`, and an `InboundMessageRow`; mirror them, adding `media_type="audio"` to the inbound row fixture (`audio_inbound_row`). If the smoke test inlines construction rather than using fixtures, inline the same here.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/test_turn_voice_e2e.py -q`
Expected: PASS (1 passed). If a fixture name mismatch fails collection, align names with the smoke test and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_turn_voice_e2e.py
git commit -m "test(fe05): e2e audio-in → voice-out turn via fakes"
```

---

### Task 15: Infra — MinIO on the VPS + tenant secrets + example config

**Files:**
- Create: `docs/superpowers/notes/2026-06-19-fe05-voice-infra.md` (runbook)
- Modify: the VPS compose for the ai_sdr stack (document the MinIO service block; do NOT apply blind — coordinate with the operator)
- Modify (example, not committed secrets): `tenants/avelum/tenant.yaml` (add `voice` + `storage` blocks) — only if Avelum is the pilot; otherwise leave as documented example in the runbook.

**Interfaces:**
- Consumes: `StorageConfig`/`VoiceConfig` shapes (Task 1).
- Produces: a running MinIO bucket + the four secrets (`elevenlabs_api_key`, `minio_endpoint`, `minio_access_key`, `minio_secret_key`) in the tenant's `secrets.enc.yaml`.

- [ ] **Step 1: Write the runbook**

Create `docs/superpowers/notes/2026-06-19-fe05-voice-infra.md` documenting:
- MinIO compose service (image `minio/minio`, `command: server /data --console-address ":9001"`, volume, `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` from env), on the existing `proxy`/internal network used by `ai_sdr` containers. Endpoint reachable from the worker container as `http://minio:9000`.
- `mc mb local/avelum-media` (or per-tenant bucket) bootstrap.
- `tenant.yaml` `voice` + `storage` blocks (copy from spec §7).
- Adding the four secrets via `sops tenants/<slug>/secrets.enc.yaml`.
- ElevenLabs: where the API key + `voice_id` come from (ElevenLabs dashboard).

- [ ] **Step 2: Apply MinIO on the VPS (operator step)**

This is an infra change — coordinate, do not auto-apply. Add the MinIO service to the ai_sdr compose, `docker compose up -d minio`, create the bucket. Verify the worker container resolves `http://minio:9000`.

- [ ] **Step 3: Seed secrets + enable a pilot tenant**

Add the four secrets to the pilot tenant's `secrets.enc.yaml`; add `voice`/`storage` to its `tenant.yaml`; set `voice.response_mode` (start with `match_lead`). Restart the worker so the `AdapterRegistry`/tenant loader picks up the new config.

- [ ] **Step 4: Live smoke (manual)**

Send a WhatsApp voice note to the pilot number → confirm the worker logs `normalize_inbound` → a voice reply arrives. Check `outbound_messages` has a row with `media_type='audio'` + `synthesis_voice_id`.

- [ ] **Step 5: Commit the runbook**

```bash
git add docs/superpowers/notes/2026-06-19-fe05-voice-infra.md
git commit -m "docs(fe05): MinIO + voice infra runbook"
```

---

## Self-Review

**Spec coverage** (spec §-by-§ → task):
- §3 reserved fields (no migration) → Global Constraints + Tasks 6/10 use existing columns. ✓
- §4 architecture (two seams + two adapter categories) → Tasks 2,3 (categories), 7 (normalizer), 8/9/11 (renderer). ✓
- §5.1 voice contracts → Task 3. §5.2 storage → Task 2. §5.3 messaging ext (send_audio/download_media/InboundMessage) → Tasks 4,5. §5.4 seam → Tasks 7,8,9. ✓
- §6.1 inbound flow → Tasks 5,6,7,12. §6.2 outbound flow → Tasks 8,9,11. §6.3 cost → Task 9 + 11. ✓
- §7 config → Task 1. ✓
- §8 error matrix → Task 7 (download/storage/low-confidence), Task 9 (synth fail/fallback), Task 12 (low-confidence fallback message). ✓
- §9 tests → every task is TDD; compliance suites in Tasks 2,3; e2e in Task 14. ✓
- §10 file layout → matches Tasks 2,3,7,8,9 (new) + 1,4,5,6,10,11,12,13 (edits). ✓
- §11 reserved seats → image branch left as `continue` in Task 5; documented. ✓
- §12 risks → addressed: ogg_opus content-type (Task 3/5), two-step download (Task 5), private bucket (Task 15), InboundMessage breaking change (Task 4 keeps defaults → non-breaking). ✓

**Placeholder scan:** No "TBD"/"implement later". Two tasks (6, 10, 14) instruct grepping existing integration fixtures by name rather than inventing them — this is deliberate (the repo's conftest is the source of truth) and each gives the exact pattern file to mirror. Worker wiring (Task 12) and prompt assembly (Task 13) reference real anchors (`inbound.py:389`, `build_fresh_layer`) with the additive code shown.

**Type consistency:** `SendResult` (flowengine) gains media fields used identically in Tasks 9/10/11. `RenderResult` field names match `record_outbound_audit` kwargs (Task 10) and `OutboundMessage` columns. `decide_modality` signature identical in Tasks 8 and 9. `normalize_inbound` outcome literal identical in Tasks 7 and 12. `build_synthesizer`/`build_transcriber`/`build_storage_adapter` names identical across Tasks 2,3,12.

## Open items the implementer must resolve against live code
1. Exact local variable names in `worker/jobs/inbound.py` around line 360-389 (head inbound row, how `secrets` is obtained). Task 12 flags this.
2. Integration fixture names in `tests/integration/conftest.py` (Tasks 6,10,14).
3. `build_fresh_layer` internal text assembly point (Task 13).

"""What ``BedrockGemmaProvider`` actually puts on the wire.

The multimodal payload shape is the thing the PDF-classification bug was originally suspected to be,
and it turned out not to be: the deployed model answered ``cannot identify image file <_io.BytesIO
...>``, a Pillow error raised *inside the model container*, which proves the endpoint had already
parsed ``messages``, found the ``image_url`` part and base64-decoded it. The envelope was right; the
bytes were a PDF. These tests pin the envelope so that stays true, and pin the guard that now drops a
non-image part instead of failing the whole request over it.

No AWS: the boto3 client is replaced by a capturing stub, following the injection pattern used
throughout the AI suite.
"""

from __future__ import annotations

import base64
import io
import json

import pytest

from app.ai.core.errors import ProviderNotConfiguredError
from app.ai.providers.llm.bedrock import BedrockGemmaProvider

MODEL = "google.gemma-3-12b-it"


class _CapturingBedrockClient:
    """Records each ``invoke_model`` request body and replays one canned generation."""

    def __init__(self, generation: str = '{"documentType": "receipt"}') -> None:
        self.requests: list[dict] = []
        self._generation = generation

    def invoke_model(self, *, modelId: str, body: str, accept: str, contentType: str):
        self.requests.append({"modelId": modelId, "body": json.loads(body)})
        return {"body": io.BytesIO(json.dumps({"generation": self._generation}).encode("utf-8"))}


def _provider(monkeypatch, client: _CapturingBedrockClient) -> BedrockGemmaProvider:
    provider = BedrockGemmaProvider(
        model=MODEL, kind="CLASSIFICATION", model_setting_name="AI_CLASSIFICATION_MODEL"
    )
    monkeypatch.setattr(provider, "_bedrock", lambda: client)
    return provider


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_text_only_request_sends_a_plain_string_content(monkeypatch) -> None:
    client = _CapturingBedrockClient()
    _provider(monkeypatch, client).generate_text("classify this")

    message = client.requests[0]["body"]["messages"][0]
    assert message["role"] == "user"
    assert message["content"] == "classify this"


def test_image_request_sends_a_base64_data_url_part(monkeypatch) -> None:
    client = _CapturingBedrockClient()
    data = png_bytes()

    _provider(monkeypatch, client).generate_text(
        "classify this", images=[{"bytes": data, "mime_type": "image/png"}]
    )

    parts = client.requests[0]["body"]["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"]
    assert parts[0]["text"] == "classify this"

    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # The decoded payload must be the exact image bytes handed in — this is the assertion that the
    # PDF bug violated, arriving as a PDF wearing an image MIME type.
    assert base64.b64decode(url.split(",", 1)[1]) == data


def test_multiple_images_become_multiple_parts(monkeypatch) -> None:
    """Supported by the envelope; whether the deployed model accepts >1 is a separate question,
    which is why AI_CLASSIFICATION_MAX_PDF_PAGES defaults to 1.
    """
    client = _CapturingBedrockClient()
    data = png_bytes()

    _provider(monkeypatch, client).generate_text(
        "classify", images=[{"bytes": data, "mime_type": "image/png"}] * 3
    )

    parts = client.requests[0]["body"]["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "image_url", "image_url"]


def test_pdf_part_is_dropped_rather_than_sent(monkeypatch) -> None:
    """The regression guard: a PDF must never reach the model's image decoder again.

    Dropping it degrades to a text-only answer, which is useful; sending it failed the request.
    """
    client = _CapturingBedrockClient()

    _provider(monkeypatch, client).generate_text(
        "classify", images=[{"bytes": b"%PDF-1.4 ...", "mime_type": "application/pdf"}]
    )

    # Content falls back to the plain string: no image part survived.
    assert client.requests[0]["body"]["messages"][0]["content"] == "classify"


def test_part_with_no_mime_type_is_dropped(monkeypatch) -> None:
    client = _CapturingBedrockClient()

    _provider(monkeypatch, client).generate_text(
        "classify", images=[{"bytes": png_bytes(), "mime_type": None}]
    )

    assert client.requests[0]["body"]["messages"][0]["content"] == "classify"


def test_empty_image_bytes_are_skipped(monkeypatch) -> None:
    client = _CapturingBedrockClient()

    _provider(monkeypatch, client).generate_text(
        "classify", images=[{"bytes": b"", "mime_type": "image/png"}]
    )

    assert client.requests[0]["body"]["messages"][0]["content"] == "classify"


def test_valid_image_survives_alongside_a_dropped_one(monkeypatch) -> None:
    client = _CapturingBedrockClient()
    data = png_bytes()

    _provider(monkeypatch, client).generate_text(
        "classify",
        images=[
            {"bytes": b"%PDF-1.4 ...", "mime_type": "application/pdf"},
            {"bytes": data, "mime_type": "image/jpeg"},
        ],
    )

    parts = client.requests[0]["body"]["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_output_budget_and_temperature_are_sent(monkeypatch) -> None:
    client = _CapturingBedrockClient()
    provider = BedrockGemmaProvider(
        model=MODEL,
        max_output_tokens=1024,
        temperature=0.0,
        kind="CLASSIFICATION",
        model_setting_name="AI_CLASSIFICATION_MODEL",
    )
    monkeypatch.setattr(provider, "_bedrock", lambda: client)

    provider.generate_text("classify")

    body = client.requests[0]["body"]
    assert body["max_tokens"] == 1024
    assert body["temperature"] == 0.0
    assert client.requests[0]["modelId"] == MODEL


def test_missing_model_names_the_setting_to_fix(monkeypatch) -> None:
    provider = BedrockGemmaProvider(
        model=None, kind="CLASSIFICATION", model_setting_name="AI_CLASSIFICATION_MODEL"
    )
    monkeypatch.setattr(provider, "_bedrock", lambda: _CapturingBedrockClient())

    with pytest.raises(ProviderNotConfiguredError) as excinfo:
        provider.generate_text("classify")

    assert "AI_CLASSIFICATION_MODEL" in str(excinfo.value)

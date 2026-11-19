import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.detectors.llm import OllamaDetector
from app.main import create_app

SAMPLE_PDF = Path(__file__).resolve().parent.parent / "frontend" / "samples" / "contrato_ficticio.pdf"

# What a well-behaved model returns for the sample contract, plus the two failure modes
# the pipeline must survive: a hallucinated entity and a structured ID mislabelled as PERSON.
FAKE_LLM_ENTITIES = [
    {"text": "Lucía Fernández Ortega", "type": "PERSON"},
    {"text": "Javier Morales Ruiz", "type": "PERSON"},
    {"text": "Elena Ruiz Navarro", "type": "PERSON"},
    {"text": "Navalia Logística Integral S.L", "type": "ORGANIZATION"},
    {"text": "Calle de Alcalá 214, 3º B, 28028 Madrid", "type": "ADDRESS"},
    {"text": "Avenida de la Constitución 45, 2º izquierda, 41001 Sevilla", "type": "ADDRESS"},
    {"text": "14 de julio de 1991", "type": "DATE_OF_BIRTH"},
    {"text": "hernia discal lumbar", "type": "HEALTH"},
    {"text": "Pedro Inventado Gómez", "type": "PERSON"},  # hallucination
    {"text": "DNI 12345678Z", "type": "PERSON"},  # wrong type, regex must win
]


def fake_ollama(entities: list[dict] | None = None, fail: bool = False) -> httpx.MockTransport:
    entities = FAKE_LLM_ENTITIES if entities is None else entities

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})
        body = json.loads(request.content)
        chunk = body["messages"][-1]["content"]
        present = [e for e in entities if e["text"].split()[0] in chunk or e["text"].startswith("Pedro")]
        return httpx.Response(200, json={"message": {"content": json.dumps({"entities": present})}})

    return httpx.MockTransport(handler)


@pytest.fixture
def settings() -> Settings:
    return Settings(ollama_model="test-model", _env_file=None)


@pytest.fixture
def sample_pdf() -> bytes:
    return SAMPLE_PDF.read_bytes()


def make_client(settings: Settings, **fake_kwargs) -> TestClient:
    llm = OllamaDetector(settings, transport=fake_ollama(**fake_kwargs))
    return TestClient(create_app(settings, llm))


@pytest.fixture
def client(settings) -> TestClient:
    with make_client(settings) as c:
        yield c

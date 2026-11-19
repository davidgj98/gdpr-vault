"""Semantic-PII detection with a local LLM served by Ollama.

Design decisions:
- The document is split into overlapping chunks so long documents are fully covered.
- The model is constrained with a JSON schema (Ollama structured outputs), not just
  asked nicely to return JSON.
- The model returns *strings*, never offsets: LLMs are bad at counting characters.
  Offsets are computed here by searching the strings in the source text, which also
  discards hallucinated entities that do not appear in the document.
- Each detected string is masked everywhere in the document, even in chunks where the
  model missed it.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings
from app.entities import SEMANTIC_TYPES, PIIType, Span

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]

SYSTEM_PROMPT = """You are a GDPR data-protection engine that finds personal data in Spanish business documents.

Extract every span of text that identifies or relates to a natural person:
- PERSON: full or partial names of people (e.g. "Juan Pérez García", "Sra. López").
- ADDRESS: street addresses (street, number, floor, city). Not just a city or country on its own.
- ORGANIZATION: names of companies or institutions that are parties in the document.
- DATE_OF_BIRTH: birth dates (only when the text says or implies it is a birth date).
- HEALTH: medical conditions, diagnoses, disabilities, sick leave reasons.

Rules:
- Copy each entity EXACTLY as it appears in the text, character by character.
- Do NOT extract emails, phone numbers, DNI/NIE, IBAN or social security numbers.
- Do NOT extract job titles, generic roles ("el trabajador", "la empresa"), laws or public bodies' regulations.
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "type": {"type": "string", "enum": sorted(t.value for t in SEMANTIC_TYPES)},
                },
                "required": ["text", "type"],
            },
        }
    },
    "required": ["entities"],
}


class LLMUnavailableError(RuntimeError):
    pass


def chunk_text(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    """Split text into (offset, chunk) pairs, cutting at line breaks when possible."""
    if len(text) <= size:
        return [(0, text)]
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut != -1:
                end = cut + 1
        chunks.append((start, text[start:end]))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _entity_pattern(entity: str) -> re.Pattern[str]:
    """Whitespace-tolerant, word-bounded pattern (PDF text breaks lines arbitrarily)."""
    tokens = [re.escape(t) for t in entity.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(tokens) + r"(?!\w)", re.IGNORECASE)


def locate(text: str, entity: str, pii_type: PIIType) -> list[Span]:
    return [Span(m.start(), m.end(), m.group(), pii_type, "llm") for m in _entity_pattern(entity).finditer(text)]


def _clean(value: str) -> str:
    """Trim punctuation the model drags along, but keep the dot of 'S.L.' / 'S.A.'."""
    value = value.strip(" \t\n,;:")
    while value.endswith(".") and not re.search(r"(?:^|[\s.])[A-Z]\.$", value):
        value = value[:-1].rstrip()
    return value


_HONORIFIC = r"(?:Sr|Sra|Srta|Dr|Dra|D|Dña|Don|Doña)\.?"


def honorific_variants(text: str, people: list[str]) -> list[Span]:
    """Once the LLM has found 'Javier Morales Ruiz', also catch 'el Sr. Morales' or
    'Dña. Fernández' anywhere in the document, which small models often miss."""
    spans: list[Span] = []
    surnames = {tok for name in people for tok in name.split()[1:] if len(tok) >= 3 and tok[0].isupper()}
    for surname in surnames:
        pattern = re.compile(rf"(?<!\w){_HONORIFIC}\s+{re.escape(surname)}(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?(?!\w)")
        spans += [Span(m.start(), m.end(), m.group(), PIIType.PERSON, "rule") for m in pattern.finditer(text)]
    return spans


class OllamaDetector:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport  # injectable for tests

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.ollama_host,
            timeout=timeout or self.settings.ollama_timeout,
            transport=self._transport,
        )

    async def status(self) -> dict:
        """Is Ollama reachable and is the configured model pulled?"""
        try:
            async with self._client(timeout=3.0) as client:
                r = await client.get("/api/tags")
                r.raise_for_status()
            names = {m["name"] for m in r.json().get("models", [])}
            model = self.settings.ollama_model
            pulled = model in names or f"{model}:latest" in names
            return {"available": pulled, "reachable": True, "model": model}
        except (httpx.HTTPError, ValueError, KeyError):
            return {"available": False, "reachable": False, "model": self.settings.ollama_model}

    async def _extract(self, client: httpx.AsyncClient, chunk: str) -> list[dict]:
        r = await client.post(
            "/api/chat",
            json={
                "model": self.settings.ollama_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Extract all personal data from this document:\n\n<document>\n{chunk}\n</document>",
                    },
                ],
                "format": RESPONSE_SCHEMA,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_ctx": 8192},
            },
        )
        r.raise_for_status()
        content = r.json()["message"]["content"]
        try:
            return json.loads(content).get("entities", [])
        except json.JSONDecodeError:
            log.warning("LLM returned invalid JSON for a chunk; skipping it")
            return []

    async def detect(self, text: str, on_progress: ProgressCallback | None = None) -> tuple[list[Span], int]:
        """Return (spans, number_of_discarded_hallucinations)."""
        chunks = chunk_text(text, self.settings.llm_chunk_size, self.settings.llm_chunk_overlap)
        found: dict[tuple[str, PIIType], None] = {}  # ordered set

        try:
            async with self._client() as client:
                for i, (_, chunk) in enumerate(chunks):
                    for item in await self._extract(client, chunk):
                        value = _clean(str(item.get("text", "")))
                        try:
                            pii_type = PIIType(item.get("type"))
                        except ValueError:
                            continue
                        if len(value) >= 3 and pii_type in SEMANTIC_TYPES:
                            found[(value, pii_type)] = None
                    if on_progress:
                        await on_progress(i + 1, len(chunks))
        except httpx.HTTPError as e:
            raise LLMUnavailableError(f"Ollama request failed: {e!r}") from e

        spans: list[Span] = []
        hallucinated = 0
        for value, pii_type in found:
            located = locate(text, value, pii_type)
            if not located:
                hallucinated += 1
                log.debug("Discarding entity not present in text: %r", value)
            spans.extend(located)
        people = [v for v, t in found if t is PIIType.PERSON]
        return spans + honorific_variants(text, people), hallucinated

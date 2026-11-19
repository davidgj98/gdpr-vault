"""Span-based pseudonymization.

Replacement happens in a single pass over non-overlapping spans, so a label can never
be re-matched by a later entity (the classic bug of chained ``str.replace`` calls),
and offsets for both texts are kept so the UI can highlight them side by side.

Two output styles share this code:

- labels: ``[NOMBRE_1]``, ``[DNI_1]``… unambiguous and obviously redacted.
- pseudonyms: realistic fake values (see ``app.surrogates``) that keep the text natural.

Either way each entity has a stable id (``[NOMBRE_1]``) for the audit, and the mapping
goes from whatever was written into the text back to the original value.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.entities import LABEL_PREFIX, PIIType, Span
from app.surrogates import SurrogateFactory


def resolve_overlaps(spans: list[Span]) -> list[Span]:
    """Pick a non-overlapping set of spans.

    - Checksum-validated regex hits win over LLM output: if the model labels
      "DNI 12345678Z" as a PERSON, the regex DNI is kept and the LLM span dropped.
    - Exception: an LLM ADDRESS may absorb the postal codes it contains.
    - Otherwise the longest span wins.
    """
    by_length = sorted(spans, key=lambda s: (-len(s), s.start))
    kept: list[Span] = []
    for span in (s for s in by_length if s.method == "regex"):
        if not any(span.overlaps(k) for k in kept):
            kept.append(span)

    for span in (s for s in by_length if s.method != "regex"):
        clashes = [k for k in kept if span.overlaps(k)]
        absorbable = span.type is PIIType.ADDRESS and all(
            k.type is PIIType.POSTAL_CODE and span.start <= k.start and k.end <= span.end for k in clashes
        )
        if not clashes or absorbable:
            kept = [k for k in kept if k not in clashes]
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def preview(value: str) -> str:
    """Non-reversible preview for the audit log: 'Juan Pérez' -> 'J*** P****'."""
    words = value.split()
    return " ".join(w[0] + "*" * max(len(w) - 1, 1) if len(w) > 1 else "*" for w in words)


@dataclass
class Replacement:
    label: str  # stable id, e.g. [NOMBRE_1]
    value: str  # what was written into the text: the label itself or a pseudonym
    type: PIIType
    method: str
    start: int  # offsets in the original text
    end: int
    out_start: int  # offsets in the anonymized text
    out_end: int


@dataclass
class AnonymizationResult:
    text: str
    replacements: list[Replacement]
    mapping: dict[str, str]  # written value -> original value (the "vault key")
    entities: list[dict] = field(default_factory=list)  # audit summary, no raw PII


def anonymize(text: str, spans: list[Span], surrogates: SurrogateFactory | None = None) -> AnonymizationResult:
    """Replace every span with a label, or with a realistic pseudonym when `surrogates` is given."""
    spans = resolve_overlaps(spans)

    counters: dict[PIIType, int] = defaultdict(int)
    labels: dict[str, str] = {}  # normalized original -> label
    values: dict[str, str] = {}  # label -> text written in its place
    mapping: dict[str, str] = {}
    summary: dict[str, dict] = {}

    out: list[str] = []
    replacements: list[Replacement] = []
    cursor = 0
    out_len = 0

    for span in spans:
        key = normalize(span.text)
        label = labels.get(key)
        if label is None:
            counters[span.type] += 1
            label = f"[{LABEL_PREFIX[span.type]}_{counters[span.type]}]"
            labels[key] = label
            original = re.sub(r"\s+", " ", span.text)  # PDF line breaks are layout, not data
            values[label] = (surrogates and surrogates.make(span.type, original)) or label
            mapping[values[label]] = original
            summary[label] = {
                "label": label,
                "replacement": values[label],
                "type": span.type.value,
                "methods": set(),
                "occurrences": 0,
                "preview": preview(span.text),
            }
        summary[label]["methods"].add(span.method)
        summary[label]["occurrences"] += 1

        chunk = text[cursor : span.start]
        out.append(chunk)
        out_len += len(chunk)
        value = values[label]
        out.append(value)
        replacements.append(
            Replacement(label, value, span.type, span.method, span.start, span.end, out_len, out_len + len(value))
        )
        out_len += len(value)
        cursor = span.end

    out.append(text[cursor:])

    entities = [{**e, "methods": sorted(e["methods"])} for e in summary.values()]
    return AnonymizationResult("".join(out), replacements, mapping, entities)


def _key_pattern(key: str) -> str:
    # Whitespace-tolerant (an external tool may rewrap lines) and word-bounded, so the fake
    # surname "Gil" never matches inside "Gilberto".
    return r"(?<!\w)" + r"\s+".join(re.escape(part) for part in key.split()) + r"(?!\w)"


def deanonymize(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Restore original values in a (possibly LLM-edited) pseudonymized text.

    Works for both labels and pseudonyms. All keys are matched in one pass, longest first,
    so "Sr. Navarro" is restored as a whole before "Navarro" alone could be considered.
    """
    keys = sorted((k for k in mapping if k.strip()), key=len, reverse=True)
    if not keys:
        return text, 0
    lookup = {re.sub(r"\s+", " ", k).casefold(): v for k, v in mapping.items()}
    pattern = re.compile("|".join(_key_pattern(k) for k in keys), re.IGNORECASE)
    restored = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal restored
        restored += 1
        return lookup[re.sub(r"\s+", " ", m.group()).casefold()]

    return pattern.sub(sub, text), restored

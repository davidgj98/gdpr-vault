"""Span-based pseudonymization.

Replacement happens in a single pass over non-overlapping spans, so a label can never
be re-matched by a later entity (the classic bug of chained ``str.replace`` calls),
and offsets for both texts are kept so the UI can highlight them side by side.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.entities import LABEL_PREFIX, PIIType, Span


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
    label: str
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
    mapping: dict[str, str]  # label -> original value (the "vault key")
    entities: list[dict] = field(default_factory=list)  # audit summary, no raw PII


def anonymize(text: str, spans: list[Span]) -> AnonymizationResult:
    spans = resolve_overlaps(spans)

    counters: dict[PIIType, int] = defaultdict(int)
    labels: dict[str, str] = {}  # normalized original -> label
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
            mapping[label] = re.sub(r"\s+", " ", span.text)  # PDF line breaks are layout, not data
            summary[label] = {
                "label": label,
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
        out.append(label)
        replacements.append(
            Replacement(label, span.type, span.method, span.start, span.end, out_len, out_len + len(label))
        )
        out_len += len(label)
        cursor = span.end

    out.append(text[cursor:])

    entities = [{**e, "methods": sorted(e["methods"])} for e in summary.values()]
    return AnonymizationResult("".join(out), replacements, mapping, entities)


_LABEL_RE = re.compile(r"\[[A-Z_]+_\d+\]")


def deanonymize(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Restore original values in a (possibly LLM-edited) anonymized text."""
    restored = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal restored
        if m.group() in mapping:
            restored += 1
            return mapping[m.group()]
        return m.group()

    return _LABEL_RE.sub(sub, text), restored

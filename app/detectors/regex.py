"""Structured-PII detection: regular expressions + checksum validation.

Every identifier that carries a control digit is validated before being reported.
That is what keeps precision high: a random 8-digit number followed by a letter is
not a DNI unless the letter matches ``number mod 23``.
"""

import re
from collections.abc import Callable, Iterator

from app.entities import PIIType, Span

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"

# Official IBAN lengths for the countries most likely to appear in Spanish documents.
_IBAN_LENGTHS = {
    "ES": 24,
    "PT": 25,
    "FR": 27,
    "DE": 22,
    "IT": 27,
    "GB": 22,
    "IE": 22,
    "NL": 18,
    "BE": 16,
    "LU": 20,
    "AT": 20,
    "CH": 21,
    "AD": 24,
    "PL": 28,
}


# ── Validators ──────────────────────────────────────────────────────────────────


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def is_valid_dni(value: str) -> bool:
    v = re.sub(r"[\s-]", "", value).upper()
    if not re.fullmatch(r"\d{8}[A-Z]", v):
        return False
    return _DNI_LETTERS[int(v[:8]) % 23] == v[8]


def is_valid_nie(value: str) -> bool:
    v = re.sub(r"[\s-]", "", value).upper()
    if not re.fullmatch(r"[XYZ]\d{7}[A-Z]", v):
        return False
    number = str("XYZ".index(v[0])) + v[1:8]
    return _DNI_LETTERS[int(number) % 23] == v[8]


def is_valid_iban(value: str) -> bool:
    v = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", v):
        return False
    expected = _IBAN_LENGTHS.get(v[:2])
    if expected and len(v) != expected:
        return False
    rearranged = v[4:] + v[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


def is_valid_nss(value: str) -> bool:
    """Spanish Social Security number: PP NNNNNNNN DD, DD = (PP·NNNNNNNN) mod 97."""
    d = _digits(value)
    if len(d) != 12:
        return False
    province, number, control = int(d[:2]), int(d[2:10]), int(d[10:])
    if not 1 <= province <= 53:
        return False
    base = province * 10**8 + number if number >= 10**7 else int(d[:10])
    return base % 97 == control


def is_valid_card(value: str) -> bool:
    """Luhn checksum."""
    d = _digits(value)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


# ── Patterns ────────────────────────────────────────────────────────────────────

_BOUNDARY_BEFORE = r"(?<![\w])"
_BOUNDARY_AFTER = r"(?![\w])"

_PATTERNS: list[tuple[PIIType, re.Pattern[str], Callable[[str], bool] | None]] = [
    (
        PIIType.DNI,
        re.compile(rf"{_BOUNDARY_BEFORE}\d{{8}}[\s-]?[A-Za-z]{_BOUNDARY_AFTER}"),
        is_valid_dni,
    ),
    (
        PIIType.NIE,
        re.compile(rf"{_BOUNDARY_BEFORE}[XYZxyz][\s-]?\d{{7}}[\s-]?[A-Za-z]{_BOUNDARY_AFTER}"),
        is_valid_nie,
    ),
    (
        PIIType.IBAN,
        re.compile(rf"{_BOUNDARY_BEFORE}[A-Z]{{2}}\d{{2}}(?:[ ]?[A-Z0-9]){{10,30}}{_BOUNDARY_AFTER}"),
        is_valid_iban,
    ),
    (
        PIIType.NSS,
        re.compile(r"(?<![\d/-])\d{2}[ /-]?\d{8}[ /-]?\d{2}(?![\d/-])"),
        is_valid_nss,
    ),
    (
        PIIType.CREDIT_CARD,
        re.compile(r"(?<![\d-])[3-6]\d{3}(?:[ -]?\d){9,15}(?![\d-])"),
        is_valid_card,
    ),
    (
        PIIType.EMAIL,
        re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w])"),
        None,
    ),
    (
        # Spanish phone numbers: optional +34/0034, 9 digits starting with 6-9,
        # any grouping (612 345 678 · 91 234 56 78 · 612345678 · +34-612-345-678)
        PIIType.PHONE,
        re.compile(r"(?<![\w+])(?:(?:\+|00)34[\s.-]?)?[6-9](?:[\s.-]?\d){8}(?![\w])"),
        None,
    ),
    (
        # Postal codes need address context (see _has_postal_context), otherwise any
        # 5-digit amount would match.
        PIIType.POSTAL_CODE,
        re.compile(r"(?<![\d.,])(?:0[1-9]|[1-4]\d|5[0-2])\d{3}(?![.,]?\d)"),
        None,
    ),
]

_POSTAL_PREFIX = re.compile(r"(?:c\.\s?p\.?|cp|c[óo]digo postal)[\s:.]*$", re.IGNORECASE)
_POSTAL_CITY = re.compile(r",?[ ]+(?!(?:Euros?|EUR|Km|Kg|Horas?)\b)[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+")


def _has_postal_context(text: str, start: int, end: int) -> bool:
    """'C.P. 28013', 'código postal: 28013' or '28013 Madrid'."""
    return bool(_POSTAL_PREFIX.search(text[max(0, start - 20) : start]) or _POSTAL_CITY.match(text, end))


def _trim_iban(value: str) -> str:
    """The greedy pattern may swallow a trailing uppercase word ("... 1332 BANCO").
    Cut the candidate to the official length of its country."""
    expected = _IBAN_LENGTHS.get(value[:2].upper())
    if not expected:
        return value
    count = 0
    for i, ch in enumerate(value):
        if ch.isalnum():
            count += 1
            if count == expected:
                return value[: i + 1]
    return value


def _iter_matches(text: str) -> Iterator[Span]:
    for pii_type, pattern, validator in _PATTERNS:
        for m in pattern.finditer(text):
            value = m.group()
            if pii_type is PIIType.IBAN:
                value = _trim_iban(value)
            if validator and not validator(value):
                continue
            if pii_type is PIIType.POSTAL_CODE and not _has_postal_context(text, *m.span()):
                continue
            yield Span(m.start(), m.start() + len(value), value, pii_type, "regex")


def detect_regex(text: str) -> list[Span]:
    return list(_iter_matches(text))

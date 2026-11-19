import pytest

from app.detectors.regex import (
    detect_regex,
    is_valid_card,
    is_valid_dni,
    is_valid_iban,
    is_valid_nie,
    is_valid_nss,
)
from app.entities import PIIType


def found(text: str) -> list[tuple[str, str]]:
    return [(s.type.value, s.text) for s in detect_regex(text)]


# ── Checksum validators ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["12345678Z", "48291037F", "48291037-f", "12345678 Z"])
def test_valid_dni(value):
    assert is_valid_dni(value)


@pytest.mark.parametrize("value", ["12345678A", "1234567Z", "ABCDEFGHZ"])
def test_invalid_dni(value):
    assert not is_valid_dni(value)


def test_nie():
    assert is_valid_nie("X5821937Q")
    assert not is_valid_nie("X5821937A")


@pytest.mark.parametrize(
    "value", ["ES91 2100 0418 4502 0005 1332", "ES9121000418450200051332", "DE89370400440532013000"]
)
def test_valid_iban(value):
    assert is_valid_iban(value)


def test_invalid_iban():
    assert not is_valid_iban("ES91 2100 0418 4502 0005 1333")  # one digit off
    assert not is_valid_iban("ES91 2100 0418 4502 0005 13")  # wrong length for ES


def test_nss():
    assert is_valid_nss("28/12345678/40")
    assert is_valid_nss("281234567840")
    assert not is_valid_nss("28/12345678/41")


def test_card_luhn():
    assert is_valid_card("4111 1111 1111 1111")
    assert not is_valid_card("4111 1111 1111 1112")


# ── Detection in context ────────────────────────────────────────────────────────


def test_detects_structured_identifiers():
    text = (
        "Juan, con DNI 12345678Z y NIE X5821937Q, cobra en ES91 2100 0418 4502 0005 1332. "
        "NSS 28/12345678/40. Tarjeta 4111-1111-1111-1111. Email juan.perez@empresa.es."
    )
    types = {t for t, _ in found(text)}
    assert types == {"DNI", "NIE", "IBAN", "NSS", "CREDIT_CARD", "EMAIL"}


def test_invalid_checksums_are_ignored():
    assert found("Expediente 12345678A, cuenta ES00 0000 0000 0000 0000 0000") == []


@pytest.mark.parametrize(
    "phone", ["612 345 678", "612345678", "+34 612 345 678", "0034612345678", "91 456 78 90", "+34-912-345-678"]
)
def test_phone_formats(phone):
    assert found(f"Llamar al {phone} mañana") == [("PHONE", phone)]


def test_phone_inside_longer_number_is_ignored():
    assert found("Pedido 1612345678901") == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Calle Mayor 1, 28013 Madrid", ["28013"]),
        ("C.P. 08001", ["08001"]),
        ("código postal: 41001", ["41001"]),
        ("Un importe de 42500 euros", []),
        ("Ref. 28013 del expediente", []),
        ("Año 2016/679", []),
        ("Precio 42.500 Euros", []),
    ],
)
def test_postal_code_needs_context(text, expected):
    assert [v for t, v in found(text) if t == "POSTAL_CODE"] == expected


def test_iban_trailing_word_is_trimmed():
    spans = detect_regex("Cuenta ES91 2100 0418 4502 0005 1332 BANCO")
    assert spans[0].type is PIIType.IBAN
    assert spans[0].text == "ES91 2100 0418 4502 0005 1332"


def test_offsets_are_exact():
    text = "DNI: 12345678Z."
    (span,) = detect_regex(text)
    assert text[span.start : span.end] == "12345678Z"

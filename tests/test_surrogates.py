import re

from app.anonymizer import anonymize, deanonymize
from app.detectors.llm import honorific_variants, locate
from app.detectors.regex import (
    detect_regex,
    is_valid_card,
    is_valid_dni,
    is_valid_iban,
    is_valid_nie,
    is_valid_nss,
)
from app.entities import PIIType
from app.surrogates import SurrogateFactory

TEXT = (
    "Contrato entre Navalia Logística S.L. y Javier Morales Ruiz, DNI 12345678Z, NIE X5821937Q, "
    "NSS 28/12345678/40, IBAN ES91 2100 0418 4502 0005 1332, tarjeta 4111 1111 1111 1111, "
    "tel. +34 612 345 678, correo javier.morales@correo-ejemplo.es. Firmado por el Sr. Morales y "
    "por Dña. Lucía Fernández. Diagnóstico: hernia discal."
)


def spans_for(text: str) -> list:
    spans = detect_regex(text)
    for value, pii_type in [
        ("Javier Morales Ruiz", PIIType.PERSON),
        ("Lucía Fernández", PIIType.PERSON),
        ("Navalia Logística S.L.", PIIType.ORGANIZATION),
        ("hernia discal", PIIType.HEALTH),
    ]:
        spans += locate(text, value, pii_type)
    return spans + honorific_variants(text, ["Javier Morales Ruiz"])


def test_structured_surrogates_keep_valid_checksums_and_layout():
    f = SurrogateFactory(TEXT, seed=7)
    cases = [
        (PIIType.DNI, "12345678Z", is_valid_dni),
        (PIIType.NIE, "X5821937Q", is_valid_nie),
        (PIIType.NSS, "28/12345678/40", is_valid_nss),
        (PIIType.IBAN, "ES91 2100 0418 4502 0005 1332", is_valid_iban),
        (PIIType.CREDIT_CARD, "4111 1111 1111 1111", is_valid_card),
    ]
    for pii_type, original, valid in cases:
        fake = f.make(pii_type, original)
        assert fake != original and valid(fake), (pii_type, fake)
        assert re.sub(r"\w", "x", fake) == re.sub(r"\w", "x", original)  # same grouping


def test_phone_keeps_format_and_line_type():
    fake = SurrogateFactory("", seed=1).make(PIIType.PHONE, "+34 612 345 678")
    assert re.fullmatch(r"\+34 6\d\d \d{3} \d{3}", fake) and fake != "+34 612 345 678"


def test_email_uses_reserved_domain():
    assert SurrogateFactory("", seed=1).make(PIIType.EMAIL, "ana@empresa.es").endswith("@example.com")


def test_surnames_stay_consistent_across_honorific_variants():
    f = SurrogateFactory(TEXT, seed=3)
    full = f.make(PIIType.PERSON, "Javier Morales Ruiz")
    short = f.make(PIIType.PERSON, "Sr. Morales")
    assert short == "Sr. " + full.split()[1]


def test_honorific_and_gender_are_kept():
    f = SurrogateFactory("", seed=5)
    fake = f.make(PIIType.PERSON, "Dña. Lucía Fernández")
    assert fake.startswith("Dña. ")
    from app.surrogates import FEMALE

    assert fake.split()[1] in FEMALE


def test_surrogates_never_collide_with_document_words():
    # Every candidate surname except one is already in the text: the factory must pick that one.
    from app.surrogates import SURNAMES

    text = " ".join(SURNAMES[1:]) + " Morales"
    fake = SurrogateFactory(text, seed=0).make(PIIType.PERSON, "Sr. Morales")
    assert fake == f"Sr. {SURNAMES[0]}"


def test_health_data_keeps_a_label():
    assert SurrogateFactory("", seed=0).make(PIIType.HEALTH, "hernia discal") is None


def test_pseudonymized_text_hides_originals_and_restores_exactly():
    result = anonymize(TEXT, spans_for(TEXT), SurrogateFactory(TEXT, seed=11))
    for secret in ["Javier", "Morales", "12345678Z", "X5821937Q", "ES91", "612 345 678", "Lucía", "Navalia"]:
        assert secret not in result.text, secret
    assert "[SALUD_1]" in result.text  # Art. 9 data is labelled, not faked
    assert "Sr. " in result.text and "Dña. " in result.text

    for r in result.replacements:
        assert result.text[r.out_start : r.out_end] == r.value
        assert result.mapping[r.value] == TEXT[r.start : r.end]

    restored, n = deanonymize(result.text, result.mapping)
    assert restored == TEXT and n == len(result.replacements)


def test_restore_tolerates_rewrapped_and_recased_pseudonyms():
    mapping = {"Pablo Navarro Gil": "Javier Morales Ruiz", "Sr. Navarro": "Sr. Morales", "Gil": "X"}
    text = "Resumen: PABLO NAVARRO\nGIL firma; el Sr. Navarro confirma. Gilberto no cambia."
    restored, n = deanonymize(text, mapping)
    assert restored == "Resumen: Javier Morales Ruiz firma; el Sr. Morales confirma. Gilberto no cambia."
    assert n == 2


def test_email_follows_the_persons_pseudonym():
    f = SurrogateFactory("", seed=2)
    person = f.make(PIIType.PERSON, "Javier Morales Ruiz")
    email = f.make(PIIType.EMAIL, "javier.morales.ruiz@empresa.es")
    from app.surrogates import fold

    assert email == ".".join(fold(w) for w in person.split()) + "@example.com"

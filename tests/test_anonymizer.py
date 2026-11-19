import itertools

from app.anonymizer import anonymize, deanonymize, preview, resolve_overlaps
from app.detectors.llm import _clean, chunk_text, honorific_variants, locate
from app.detectors.regex import detect_regex
from app.entities import PIIType, Span


def span(text: str, value: str, pii_type: PIIType, method: str = "llm") -> Span:
    start = text.index(value)
    return Span(start, start + len(value), value, pii_type, method)


def test_consistent_labels_for_repeated_entities():
    text = "Ana López firma. Luego ANA LÓPEZ cobra. Pedro Ruiz observa."
    spans = locate(text, "Ana López", PIIType.PERSON) + locate(text, "Pedro Ruiz", PIIType.PERSON)
    result = anonymize(text, spans)
    assert result.text == "[NOMBRE_1] firma. Luego [NOMBRE_1] cobra. [NOMBRE_2] observa."
    assert result.mapping == {"[NOMBRE_1]": "Ana López", "[NOMBRE_2]": "Pedro Ruiz"}
    assert result.entities[0]["occurrences"] == 2


def test_no_partial_word_replacement():
    text = "Ana vendrá mañana con Ana."
    result = anonymize(text, locate(text, "Ana", PIIType.PERSON))
    assert result.text == "[NOMBRE_1] vendrá mañana con [NOMBRE_1]."


def test_entity_split_across_lines_is_found():
    text = "firmado por Javier\nMorales Ruiz en Madrid"
    (s,) = locate(text, "Javier Morales Ruiz", PIIType.PERSON)
    assert s.text == "Javier\nMorales Ruiz"


def test_regex_beats_mislabelled_llm_span():
    text = "Identificado con DNI 12345678Z en el registro"
    spans = [*detect_regex(text), span(text, "DNI 12345678Z", PIIType.PERSON)]
    (kept,) = resolve_overlaps(spans)
    assert kept.type is PIIType.DNI


def test_address_absorbs_postal_code():
    text = "Vive en Calle Mayor 1, 28013 Madrid."
    spans = [*detect_regex(text), span(text, "Calle Mayor 1, 28013 Madrid", PIIType.ADDRESS)]
    (kept,) = resolve_overlaps(spans)
    assert kept.type is PIIType.ADDRESS


def test_replacement_offsets_point_to_labels():
    text = "Llame a Ana López al 612345678."
    spans = detect_regex(text) + locate(text, "Ana López", PIIType.PERSON)
    result = anonymize(text, spans)
    for r in result.replacements:
        assert result.text[r.out_start : r.out_end] == r.label
        assert text[r.start : r.end] == result.mapping[r.label]


def test_roundtrip():
    text = "Contrato de Ana López, DNI 12345678Z, tel. 612 345 678."
    spans = detect_regex(text) + locate(text, "Ana López", PIIType.PERSON)
    result = anonymize(text, spans)
    assert "Ana" not in result.text and "12345678Z" not in result.text
    restored, n = deanonymize(result.text, result.mapping)
    assert restored == text and n == 3


def test_deanonymize_ignores_unknown_labels():
    text, n = deanonymize("Hola [NOMBRE_1] y [NOMBRE_9]", {"[NOMBRE_1]": "Ana"})
    assert text == "Hola Ana y [NOMBRE_9]" and n == 1


def test_audit_preview_hides_value():
    assert preview("Javier Morales") == "J***** M******"
    assert "12345678Z" not in preview("12345678Z")


def test_honorific_variants():
    text = "Javier Morales Ruiz firma. Después el Sr. Morales y la Dña. Ruiz Pérez llegan."
    found = {s.text for s in honorific_variants(text, ["Javier Morales Ruiz"])}
    assert found == {"Sr. Morales", "Dña. Ruiz Pérez"}


def test_llm_output_cleanup():
    assert _clean("Navalia Logística S.L.") == "Navalia Logística S.L."  # keep company suffix dot
    assert _clean("Javier Morales.") == "Javier Morales"
    assert _clean("Hola Marta,") == "Marta"
    assert _clean("Gracias, Raúl") == "Raúl"


def test_chunks_cover_text_with_overlap():
    text = "\n".join(f"Línea {i} con algo de texto" for i in range(300))
    chunks = chunk_text(text, size=500, overlap=50)
    assert chunks[0][0] == 0
    assert chunks[-1][0] + len(chunks[-1][1]) == len(text)
    for (o1, c1), (o2, _) in itertools.pairwise(chunks):
        assert o2 < o1 + len(c1)  # consecutive chunks overlap
        assert text[o1 : o1 + len(c1)] == c1

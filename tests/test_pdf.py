import pymupdf
import pytest

from app.pdf import TEXT_FLAGS, redact_pdf

MAPPING = {"[NOMBRE_1]": "Elena Ruiz Navarro", "[NIE_1]": "X5821937Q"}


def line_words(pdf: bytes, first_word: str) -> list[tuple[float, str]]:
    words = pymupdf.open(stream=pdf)[0].get_text("words", flags=TEXT_FLAGS)
    y = next(w[1] for w in words if w[4].startswith(first_word))
    return [(round(w[0], 1), w[4]) for w in words if abs(w[1] - y) < 2]


@pytest.mark.parametrize("pseudonyms", [False, True])
def test_redaction_keeps_the_rest_of_the_line_in_place(sample_pdf, pseudonyms):
    # Regression: PyMuPDF 2.x shifted every glyph after a redaction to the left, so the
    # following words slid under the drawn boxes. Surviving words must not move.
    before = dict((w, x) for x, w in line_words(sample_pdf, "contactar"))
    after = line_words(redact_pdf(sample_pdf, MAPPING, pseudonyms), "contactar")
    survivors = [(x, w) for x, w in after if w in before]
    assert {"teléfono", "(NIE", "contactará"} <= {w for _, w in survivors}
    for x, w in survivors:
        assert abs(x - before[w]) < 0.5, (w, before[w], x)


def test_punctuation_glued_to_the_data_survives(sample_pdf):
    text = pymupdf.open(stream=redact_pdf(sample_pdf, MAPPING))[0].get_text()
    assert "X5821937Q" not in text and "Elena" not in text
    assert "(NIE" in text and ")," in text  # only the identifier itself was removed

"""PDF text extraction and true redaction with PyMuPDF.

Redaction physically removes the underlying text (it is not a black box drawn on top
that can be copy-pasted from), and document metadata such as author is scrubbed.
"""

import re

import pymupdf


class PDFError(ValueError):
    pass


# Expand ligatures ("ﬁ" -> "fi") so that extracted text is searchable and matches the
# strings the detectors return.
TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES


def extract_pages(pdf_bytes: bytes, max_pages: int) -> list[str]:
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PDFError("The file is not a valid PDF.") from e
    with doc:
        if doc.needs_pass:
            raise PDFError("The PDF is password-protected.")
        if doc.page_count > max_pages:
            raise PDFError(f"The PDF has {doc.page_count} pages; the limit is {max_pages}.")
        return [page.get_text("text", flags=TEXT_FLAGS) for page in doc]


_EDGE_PUNCT = "\"'«»()[]{}.,;:¡!¿?"


def _norm(token: str) -> str:
    return token.strip(_EDGE_PUNCT).casefold()


def _word_matches(words: list[tuple], tokens: list[str]) -> list[list[pymupdf.Rect]]:
    """Find a token sequence in the page's word list.

    Word-level matching avoids substring hits ('Ana' inside 'mañana'). Each match is
    returned as one rectangle per text line it spans, so the redaction reads as a
    single block instead of one box per word."""
    matches: list[list[pymupdf.Rect]] = []
    n = len(tokens)
    norms = [_norm(w[4]) for w in words]
    for i in range(len(words) - n + 1):
        if norms[i : i + n] != tokens:
            continue
        lines: dict[tuple[int, int], pymupdf.Rect] = {}
        for w in words[i : i + n]:
            key = (w[5], w[6])  # (block, line)
            lines[key] = lines[key] | pymupdf.Rect(w[:4]) if key in lines else pymupdf.Rect(w[:4])
        matches.append(list(lines.values()))
    return matches


def redact_pdf(pdf_bytes: bytes, mapping: dict[str, str]) -> bytes:
    """Return a copy of the PDF with every original value replaced by its label."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    with doc:
        for page in doc:
            words = page.get_text("words", flags=TEXT_FLAGS)
            page_text = re.sub(r"\s+", " ", page.get_text("text", flags=TEXT_FLAGS)).casefold()
            for label, original in mapping.items():
                tokens = [_norm(t) for t in original.split() if _norm(t)]
                if not tokens:
                    continue
                matches = _word_matches(words, tokens)
                if not matches and re.sub(r"\s+", " ", original).casefold() in page_text:
                    # PII glued to other text ("Tel:612345678"): fall back to substring search
                    matches = [[r] for r in page.search_for(original)]
                for rects in matches:
                    for i, rect in enumerate(rects):
                        # Word boxes include line spacing, and redaction removes every glyph
                        # that touches the rect: without this inset, a redaction could eat
                        # letters from the line above or below.
                        inset = rect.height * 0.18
                        page.add_redact_annot(
                            pymupdf.Rect(rect.x0, rect.y0 + inset, rect.x1, rect.y1 - inset),
                            text=label if i == 0 else None,  # label once per occurrence
                            fontname="helv",
                            fontsize=max(4, min(8.5, rect.height * 0.62)),
                            fill=(0.07, 0.09, 0.12),
                            text_color=(1, 1, 1),
                            align=pymupdf.TEXT_ALIGN_LEFT,
                        )
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        doc.scrub(metadata=True, xml_metadata=True)
        return doc.tobytes(garbage=4, deflate=True)

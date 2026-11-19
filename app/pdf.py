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


def _word_matches(words: list[tuple], tokens: list[str]) -> list[list[tuple]]:
    """Find a token sequence in the page's word list and return each match as its words.

    Word-level matching avoids substring hits ('Ana' inside 'mañana')."""
    n = len(tokens)
    norms = [_norm(w[4]) for w in words]
    return [words[i : i + n] for i in range(len(words) - n + 1) if norms[i : i + n] == tokens]


def _glyph_rect(page: pymupdf.Page, word: tuple) -> pymupdf.Rect:
    """The part of a word box that holds the data itself: 'X5821937Q),' -> 'X5821937Q', so
    the bracket and comma glued to it are not redacted along with it."""
    rect = pymupdf.Rect(word[:4])
    core = word[4].strip(_EDGE_PUNCT)
    if core and core != word[4]:
        hits = page.search_for(core, clip=rect)
        if hits:
            rect = pymupdf.Rect(hits[0])
            for h in hits[1:]:
                rect |= h
    return rect


def _inset(rect: pymupdf.Rect, ratio: float) -> pymupdf.Rect:
    d = rect.height * ratio
    return pymupdf.Rect(rect.x0, rect.y0 + d, rect.x1, rect.y1 - d)


def redact_pdf(pdf_bytes: bytes, mapping: dict[str, str], pseudonyms: bool = False) -> bytes:
    """Return a copy of the PDF with every original value removed and replaced by its
    label (white on a dark box) or by its pseudonym (dark text on a light tint, so the page
    still reads naturally while it stays visible what was changed).

    Two passes per page:

    1. Glyph removal, one redaction per *word*. Redacting the spaces between words too
       would break justified text: PDF word spacing (``Tw``) is attached to space
       characters, so deleting them shifts the rest of the line to the left.
       Rects are also inset vertically, because redaction deletes every glyph a rect
       touches and word boxes include line spacing.
    2. Drawing: one box per text line of each occurrence, with the replacement written
       once at a font size that fits the box.
    """
    fill, color = ((0.87, 0.92, 1.0), (0.04, 0.1, 0.3)) if pseudonyms else ((0.07, 0.09, 0.12), (1, 1, 1))
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    with doc:
        for page in doc:
            words = page.get_text("words", flags=TEXT_FLAGS)
            page_text = re.sub(r"\s+", " ", page.get_text("text", flags=TEXT_FLAGS)).casefold()
            boxes: list[tuple[pymupdf.Rect, str | None]] = []
            for written, original in mapping.items():
                tokens = [_norm(t) for t in original.split() if _norm(t)]
                if not tokens:
                    continue
                occurrences = [
                    [((w[5], w[6]), _glyph_rect(page, w)) for w in match] for match in _word_matches(words, tokens)
                ]
                if not occurrences and re.sub(r"\s+", " ", original).casefold() in page_text:
                    # PII glued to other text ("Tel:612345678"): fall back to substring search
                    occurrences = [[((0, i), pymupdf.Rect(r))] for i, r in enumerate(page.search_for(original))]
                for occurrence in occurrences:
                    lines: dict[tuple, pymupdf.Rect] = {}
                    for line, rect in occurrence:
                        page.add_redact_annot(_inset(rect, 0.18), fill=False)
                        lines[line] = lines[line] | rect if line in lines else pymupdf.Rect(rect)
                    boxes += [(r, written if i == 0 else None) for i, r in enumerate(lines.values())]
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

            for rect, text in boxes:
                page.draw_rect(_inset(rect, 0.12 if pseudonyms else 0.16), color=None, fill=fill, overlay=True)
                if text:
                    size = rect.height * (0.72 if pseudonyms else 0.5)
                    width = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
                    if width > rect.width - 2:
                        size = max(4.0, size * (rect.width - 2) / width)
                    baseline = (rect.y0 + rect.y1) / 2 + size * 0.35
                    page.insert_text((rect.x0 + 1, baseline), text, fontsize=size, fontname="helv", color=color)
        doc.scrub(metadata=True, xml_metadata=True)
        return doc.tobytes(garbage=4, deflate=True)

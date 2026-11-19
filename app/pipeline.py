"""Orchestrates extraction → detection (regex + LLM) → pseudonymization → redaction."""

import asyncio
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from app.anonymizer import anonymize
from app.config import Settings
from app.detectors.llm import LLMUnavailableError, OllamaDetector
from app.detectors.regex import detect_regex
from app.jobs import Job, JobStatus
from app.pdf import PDFError, extract_pages, redact_pdf

log = logging.getLogger(__name__)


@dataclass
class Options:
    use_regex: bool = True
    use_llm: bool = True


class ProcessingError(Exception):
    pass


async def run_job(job: Job, text: str | None, options: Options, settings: Settings, llm: OllamaDetector) -> None:
    try:
        await _run(job, text, options, settings, llm)
    except (ProcessingError, PDFError) as e:
        job.status, job.error, job.progress = JobStatus.ERROR, str(e), "Failed"
    except Exception:
        log.exception("Unexpected error processing job %s", job.id)
        job.status, job.error, job.progress = JobStatus.ERROR, "Internal error while processing the document.", "Failed"
    finally:
        job.pdf_bytes = None  # keep only the redacted copy


async def _run(job: Job, text: str | None, options: Options, settings: Settings, llm: OllamaDetector) -> None:
    started = time.perf_counter()
    warnings: list[str] = []
    pages = None

    # 1. Extraction
    if job.source == "pdf":
        job.status, job.progress = JobStatus.EXTRACTING, "Extracting text from PDF"
        page_texts = await asyncio.to_thread(extract_pages, job.pdf_bytes, settings.max_pages)
        pages = len(page_texts)
        text = "\n".join(page_texts).strip()
        if not text:
            raise ProcessingError("No text layer found. This looks like a scanned PDF; OCR is not supported yet.")
    assert text is not None
    if len(text) > settings.max_text_chars:
        raise ProcessingError(f"Document too long ({len(text):,} characters).")

    # 2. Detection
    job.status = JobStatus.DETECTING
    spans = []
    regex_count = 0
    if options.use_regex:
        job.progress = "Running regex + checksum validators"
        regex_spans = detect_regex(text)
        regex_count = len(regex_spans)
        spans += regex_spans

    llm_info: dict = {"enabled": options.use_llm, "model": settings.ollama_model}
    if options.use_llm:

        async def on_progress(done: int, total: int) -> None:
            job.progress = f"LLM analysis · chunk {done}/{total}"

        job.progress = "LLM analysis"
        try:
            llm_spans, hallucinated = await llm.detect(text, on_progress)
            spans += llm_spans
            llm_info.update(status="ok", occurrences=len(llm_spans), discarded_hallucinations=hallucinated)
        except LLMUnavailableError as e:
            log.warning("%s", e)
            llm_info["status"] = "unavailable"
            warnings.append(
                "The LLM layer was unavailable: names, addresses, companies and health data were NOT "
                "detected. Only structured identifiers were masked. Review before sharing."
            )
    else:
        llm_info["status"] = "disabled"

    # 3. Pseudonymization
    job.status, job.progress = JobStatus.ANONYMIZING, "Pseudonymizing"
    result = anonymize(text, spans)

    if job.source == "pdf" and result.mapping:
        job.progress = "Redacting PDF"
        job.redacted_pdf = await asyncio.to_thread(redact_pdf, job.pdf_bytes, result.mapping)

    by_type = Counter(r.type.value for r in result.replacements)
    audit = {
        "document": job.filename,
        "source": job.source,
        "processed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "pages": pages,
        "characters": len(text),
        "layers": {
            "regex": {"enabled": options.use_regex, "occurrences": regex_count},
            "llm": llm_info,
        },
        "entities_found": len(result.entities),
        "occurrences_masked": len(result.replacements),
        "by_type": dict(by_type.most_common()),
        "entities": result.entities,
        "warnings": warnings,
        "note": "This log contains no raw personal data. Original values are only in the mapping file.",
    }

    job.result = {
        "original_text": text,
        "anonymized_text": result.text,
        "replacements": [
            {k: (v.value if k == "type" else v) for k, v in asdict(r).items()} for r in result.replacements
        ],
        "mapping": result.mapping,
        "audit": audit,
    }
    job.status, job.progress = JobStatus.DONE, "Done"

import asyncio
import json
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.anonymizer import deanonymize
from app.jobs import Job, JobStatus
from app.pipeline import Options, run_job

router = APIRouter(prefix="/api")


def _job_or_404(request: Request, job_id: str) -> Job:
    job = request.app.state.store.get(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found or expired.")
    return job


@router.get("/health", tags=["system"])
async def health(request: Request) -> dict:
    llm = await request.app.state.llm.status()
    return {"status": "ok", "llm": llm, "active_jobs": len(request.app.state.store)}


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED, tags=["jobs"])
async def create_job(
    request: Request,
    file: Annotated[UploadFile | None, File(description="Native (text-layer) PDF")] = None,
    text: Annotated[str | None, Form(description="Plain text, as an alternative to a PDF")] = None,
    use_regex: Annotated[bool, Form()] = True,
    use_llm: Annotated[bool, Form()] = True,
    style: Annotated[
        Literal["labels", "pseudonyms"],
        Form(description="`labels` ([NOMBRE_1]) or `pseudonyms` (realistic fake values)"),
    ] = "labels",
) -> dict:
    """Start an anonymization job. Poll `GET /api/jobs/{job_id}` for the result."""
    settings = request.app.state.settings
    if not use_regex and not use_llm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enable at least one detection layer.")

    if file is not None and file.filename:
        max_bytes = settings.max_upload_mb * 1024 * 1024
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Max file size is {settings.max_upload_mb} MB."
            )
        if not data.startswith(b"%PDF"):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF files are supported.")
        job = request.app.state.store.create(file.filename, "pdf")
        job.pdf_bytes = data
        text = None
    elif text and text.strip():
        if len(text) > settings.max_text_chars:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Text too long.")
        job = request.app.state.store.create("pasted-text.txt", "text")
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send a PDF `file` or a `text` field.")

    options = Options(use_regex, use_llm, pseudonyms=style == "pseudonyms")
    task = asyncio.create_task(run_job(job, text, options, settings, request.app.state.llm))
    tasks: set = request.app.state.tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"job_id": job.id, "status": job.status.value}


@router.get("/jobs/{job_id}", tags=["jobs"])
async def get_job(request: Request, job_id: str) -> dict:
    return _job_or_404(request, job_id).public()


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["jobs"])
async def delete_job(request: Request, job_id: str) -> Response:
    """Erase a job and all its data immediately (right to erasure, Art. 17)."""
    if not request.app.state.store.delete(job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found or expired.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


FileKind = Literal["anonymized.txt", "audit.json", "mapping.json", "redacted.pdf"]


@router.get("/jobs/{job_id}/files/{kind}", tags=["jobs"])
async def download(request: Request, job_id: str, kind: FileKind, inline: bool = False) -> Response:
    job = _job_or_404(request, job_id)
    if job.status != JobStatus.DONE or not job.result:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not finished.")

    stem = job.filename.rsplit(".", 1)[0]
    match kind:
        case "anonymized.txt":
            body, media = job.result["anonymized_text"].encode(), "text/plain; charset=utf-8"
        case "audit.json":
            body, media = json.dumps(job.result["audit"], indent=2, ensure_ascii=False).encode(), "application/json"
        case "mapping.json":
            payload = {
                "document": job.filename,
                "warning": "Contains personal data. Store securely.",
                "style": job.result["audit"]["replacement_style"],
                "mapping": job.result["mapping"],
            }
            body, media = json.dumps(payload, indent=2, ensure_ascii=False).encode(), "application/json"
        case "redacted.pdf":
            if not job.redacted_pdf:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No redacted PDF for this job.")
            body, media = job.redacted_pdf, "application/pdf"

    disposition = "inline" if inline else "attachment"
    return Response(body, media_type=media, headers={"Content-Disposition": f'{disposition}; filename="{stem}.{kind}"'})


class DeanonymizeRequest(BaseModel):
    text: str = Field(max_length=500_000)
    mapping: dict[str, str]


@router.post("/deanonymize", tags=["tools"])
async def deanonymize_text(body: DeanonymizeRequest) -> dict:
    """Re-identify a text (e.g. a reply from an external AI) using a job's mapping file.
    Stateless: the mapping travels with the request and is not stored."""
    text, restored = deanonymize(body.text, body.mapping)
    return {"text": text, "restored": restored}

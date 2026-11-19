"""In-memory job store with expiry.

Nothing is written to disk. Results (which contain the original personal data and the
re-identification mapping) are purged after ``JOB_TTL_SECONDS`` or on explicit DELETE,
following the storage-limitation principle (GDPR Art. 5(1)(e)).
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    DETECTING = "detecting"
    ANONYMIZING = "anonymizing"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    id: str
    filename: str
    source: str  # "pdf" | "text"
    expires_at: float
    status: JobStatus = JobStatus.QUEUED
    progress: str = "Queued"
    error: str | None = None
    result: dict[str, Any] | None = None
    pdf_bytes: bytes | None = None
    redacted_pdf: bytes | None = None
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "job_id": self.id,
            "filename": self.filename,
            "source": self.source,
            "status": self.status.value,
            "progress": self.progress,
            "expires_in": max(0, int(self.expires_at - time.time())),
            "has_redacted_pdf": self.redacted_pdf is not None,
        }
        if self.error:
            data["error"] = self.error
        if self.result:
            data.update(self.result)
        return data


class JobStore:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}

    def create(self, filename: str, source: str) -> Job:
        self.purge_expired()
        job = Job(id=uuid.uuid4().hex, filename=filename, source=source, expires_at=time.time() + self.ttl)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        self.purge_expired()
        return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        return self._jobs.pop(job_id, None) is not None

    def purge_expired(self) -> int:
        now = time.time()
        expired = [k for k, j in self._jobs.items() if j.expires_at <= now]
        for k in expired:
            del self._jobs[k]
        return len(expired)

    def __len__(self) -> int:
        return len(self._jobs)

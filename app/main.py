import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import Settings, get_settings
from app.detectors.llm import OllamaDetector
from app.jobs import JobStore

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Everything is served from this origin: the browser is not allowed to contact any
# third party (no CDNs, no web fonts, no analytics).
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
)
# Swagger UI / ReDoc load their assets from a CDN; they are developer tools, not the app.
DOCS_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}


def create_app(settings: Settings | None = None, llm: OllamaDetector | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def janitor() -> None:
            while True:
                await asyncio.sleep(30)
                app.state.store.purge_expired()

        cleaner = asyncio.create_task(janitor())
        yield
        cleaner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleaner

    app = FastAPI(
        title="GDPR-Vault",
        description="Local-first PII detection and pseudonymization for Spanish business documents.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = JobStore(settings.job_ttl_seconds)
    app.state.llm = llm or OllamaDetector(settings)
    app.state.tasks = set()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in DOCS_PATHS:
            response.headers["Content-Security-Policy"] = CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"  # responses contain personal data
        return response

    app.include_router(router)
    # Mounted last so it never shadows API routes.
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()

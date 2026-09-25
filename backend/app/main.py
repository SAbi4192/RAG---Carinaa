"""
Carinaa API - application entry point.

    Carinaa — Every Answer, Traceable.

RUNNING IT
----------
    .venv\\Scripts\\python -m uvicorn app.main:app --reload --port 8000
    (from the `backend/` directory, or use scripts/dev.py from the project root)

WHAT HAPPENS AT STARTUP
-----------------------
1. Logging is configured with secret redaction.
2. Data directories are created.
3. The database schema is created (idempotent).
4. The API routers are mounted.
5. The embedding model and vector store are warmed in a BACKGROUND thread.

Step 5 is deliberately off the critical path. Loading an ONNX model takes a few
seconds, and blocking startup on it would make the server appear hung. Warming in
the background means the first user query is fast, but the server is available
immediately.

We do NOT preload the 2 GB local LLM. It is loaded on first use in Offline mode
(or explicitly from the Settings page before a demo), because holding 2 GB of RAM
for a feature the user may never use would be wasteful.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import init_db

configure_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Background warmup
# ---------------------------------------------------------------------------
def _warm_models() -> None:
    """Load the embedding model and open the vector store, off the critical path."""
    try:
        from app.rag.embeddings import get_embedding_service

        service = get_embedding_service()
        if service.warmup():
            logger.info(
                "Embedding model warmed: %s (%d dims)",
                service.model_name,
                service.dimension,
            )
        else:
            logger.warning(
                "The embedding model could not be loaded at startup. Ingestion and "
                "retrieval will fail until it is available. Run "
                "`python scripts/fetch_models.py`."
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("Embedding warmup skipped: %s", exc)

    try:
        from app.rag.vectorstore import get_vector_store

        health = get_vector_store().health()
        logger.info("Vector store ready: %s", health.get("vectors", 0))
    except Exception as exc:  # pragma: no cover
        logger.warning("Vector store warmup skipped: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown."""
    logger.info("=" * 72)
    logger.info("%s v%s — %s", settings.app_name, settings.app_version, settings.app_tagline)
    logger.info("Environment : %s", settings.environment)
    logger.info("Database    : %s", settings.resolved_database_url.split("?", 1)[0])
    logger.info("Vectors     : %s", settings.chroma_dir)
    logger.info("Embeddings  : %s", settings.embedding_model)
    logger.info(
        "Providers   : gemini=%s groq=%s local=%s",
        "configured" if settings.gemini_configured else "not configured",
        "configured" if settings.groq_configured else "not configured",
        f"{settings.local_model_size_gb()} GB on disk"
        if settings.local_model_present
        else "missing",
    )
    logger.info("=" * 72)

    init_db()

    # Warm in a daemon thread so startup returns immediately.
    threading.Thread(target=_warm_models, name="carinaa-warmup", daemon=True).start()

    yield

    logger.info("%s shutting down.", settings.app_name)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title=f"{settings.app_name} API",
    description=(
        "Explainable, educational, secure RAG document intelligence.\n\n"
        "Every answer is grounded in retrieved evidence, cited to a real location, "
        "and traceable through the full pipeline."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)


@app.middleware("http")
async def security_headers(request, call_next):  # noqa: ANN001, ANN201
    """Add conservative security headers to every response.

    `no-store` on API responses matters here: answers contain document content, and
    a shared cache holding them would be a data-leak path.
    """
    response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")

    return response


register_exception_handlers(app)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(api_router)


# Unknown /api/* paths must return JSON, never HTML.
#
# This is registered AFTER the real routers, so it only ever catches paths nothing
# else matched. Without it, a typo such as /api/documnets would fall through to the
# SPA mount below and receive index.html — the caller then fails with a baffling
# "Unexpected token '<'" JSON parse error instead of a clear 404.
@app.api_route(
    "/api/{unknown:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def api_not_found(unknown: str) -> JSONResponse:
    return JSONResponse(
        {"detail": f"No such API endpoint: /api/{unknown}", "code": "not_found"},
        status_code=404,
    )


# ---------------------------------------------------------------------------
# Optional: serve the built frontend
# ---------------------------------------------------------------------------
# In development the Vite dev server serves the UI and proxies /api here. In a
# packaged build there is no dev server, so we serve the compiled assets if they
# exist. Checking for the directory keeps a single entry point working in both.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class SPAStaticFiles(StaticFiles):
        """Static files with a client-side-routing fallback.

        A plain StaticFiles mount answers /dashboard with a 404, because no such
        file exists on disk — that route only exists inside React Router. Returning
        index.html instead lets the router render the page. Without this fallback,
        reloading (or sharing a link to) any page other than the landing page would
        break, which is exactly the bug every SPA hits once it is deployed.
        """

        async def get_response(self, path: str, scope):  # noqa: ANN001, ANN201
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                # Starlette raises rather than returning in some versions, so both
                # paths are handled here.
                if exc.status_code != 404:
                    raise
                response = None

            if response is None or response.status_code == 404:
                response = await super().get_response("index.html", scope)

            # The HTML document must always be revalidated. It is the one file that
            # names the content-hashed JS/CSS bundles: if a browser or an old
            # service worker serves a stale index.html after a rebuild, every chunk
            # 404s and the page is a black screen. no-cache keeps the ETag
            # revalidation (cheap, and it already works - the server sends ETag)
            # but guarantees the shell itself is never answered from a stale cache.
            # Hashed assets under /assets/ stay cacheable because their names change
            # whenever their contents do.
            if response.headers.get("content-type", "").startswith("text/html"):
                response.headers["Cache-Control"] = "no-cache"
            # The service worker script must revalidate too: the browser checks it
            # for updates on navigation, but it must not answer that check from a
            # cache set by the previous worker, or a stale shell never gets replaced.
            if path == "sw.js" or path.endswith("/sw.js"):
                response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount(
        "/",
        SPAStaticFiles(directory=str(_FRONTEND_DIST), html=True),
        name="frontend",
    )
    logger.info("Serving the built frontend from %s", _FRONTEND_DIST)
else:
    # API-only deployment: there is no UI to serve, so "/" describes the service
    # instead of being shadowed by an empty mount.
    @app.get("/", include_in_schema=False)
    def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": settings.app_name,
                "tagline": settings.app_tagline,
                "version": settings.app_version,
                "docs": "/docs",
                "api": "/api/health",
            }
        )

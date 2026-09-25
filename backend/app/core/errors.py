"""
Typed application errors and the handlers that turn them into safe responses.

WHY THIS FILE EXISTS
--------------------
Section 56: "Never expose raw stack traces to normal users."

Every failure path in Carinaa raises one of these. Each carries a machine-readable
`code` (so the UI can react specifically), a human `message`, an HTTP status, and
an optional `detail` payload. The handler pipeline guarantees that whatever
happens - including a genuinely unexpected crash - the client receives a clean,
redacted JSON body and the server logs the full trace for the developer.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, safe_error_message

logger = get_logger(__name__)


class CarinaaError(Exception):
    """Base class for all expected application failures."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "error"
    message: str = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: Any = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.detail is not None:
            payload["error"]["detail"] = self.detail
        return payload


# --------------------------------------------------------------------------
# Auth / access
# --------------------------------------------------------------------------
class AuthError(CarinaaError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "auth_error"
    message = "Authentication required."


class InvalidCredentials(AuthError):
    code = "invalid_credentials"
    message = "Incorrect email or password."


class EmailAlreadyRegistered(CarinaaError):
    status_code = status.HTTP_409_CONFLICT
    code = "email_taken"
    message = "An account with that email already exists."


class WeakPassword(CarinaaError):
    code = "weak_password"
    message = "That password does not meet the requirements."


class Forbidden(CarinaaError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have access to this resource."


class NotFound(CarinaaError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found."


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
class UnsupportedFileType(CarinaaError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_file_type"
    message = "That file type is not supported."


class FileTooLarge(CarinaaError):
    status_code = 413  # Content Too Large
    code = "file_too_large"
    message = "That file is larger than the configured limit."


class ParseFailure(CarinaaError):
    status_code = 422  # Unprocessable Content
    code = "parse_failure"
    message = "We could not read any text from that file."


class EmptyDocument(CarinaaError):
    status_code = 422  # Unprocessable Content
    code = "empty_document"
    message = "That file contains no extractable text."


# --------------------------------------------------------------------------
# RAG
# --------------------------------------------------------------------------
class EmbeddingError(CarinaaError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "embedding_error"
    message = "The embedding model could not process that text."


class VectorStoreError(CarinaaError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "vector_store_error"
    message = "The vector store is unavailable."


class RetrievalError(CarinaaError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "retrieval_error"
    message = "Retrieval failed."


class NoWorkspaceDocuments(CarinaaError):
    status_code = status.HTTP_409_CONFLICT
    code = "no_documents"
    message = "This workspace has no indexed documents yet."


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------
class LLMError(CarinaaError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "llm_error"
    message = "The language model could not produce an answer."


class ProviderNotConfigured(LLMError):
    code = "provider_not_configured"
    message = "That AI provider is not configured on the server."


class ProviderRateLimited(LLMError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "provider_rate_limited"
    message = "The AI provider is rate limiting us. Please retry shortly."


class LocalModelUnavailable(LLMError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "local_model_unavailable"
    message = "The local model is not available."


class AllProvidersFailed(LLMError):
    code = "all_providers_failed"
    message = "No AI provider could complete this request."


# --------------------------------------------------------------------------
# Presentation features
# --------------------------------------------------------------------------
class TranslationError(CarinaaError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "translation_error"
    message = "Translation failed. The original answer is unchanged."


class TranslationUnavailableOffline(CarinaaError):
    status_code = status.HTTP_409_CONFLICT
    code = "translation_unavailable_offline"
    message = (
        "Translation is unavailable in Offline mode because no local translation "
        "engine is configured. The original grounded answer is unchanged."
    )


class ShortenError(CarinaaError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "shorten_error"
    message = "Shortening failed. The original answer is unchanged."


class ShortenRejected(CarinaaError):
    status_code = 422  # Unprocessable Content
    code = "shorten_rejected"
    message = (
        "The shortened version could not be verified as preserving the answer's "
        "meaning and citations, so the original answer has been kept."
    )


class WebSearchDisabled(CarinaaError):
    status_code = status.HTTP_409_CONFLICT
    code = "web_search_disabled"
    message = "Web search is disabled for this request."


class DetailedAnswerError(CarinaaError):
    """A Detailed Answer could not be generated (engine down, no evidence)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "detailed_answer_error"
    message = (
        "The detailed version could not be generated. The original answer is "
        "unchanged."
    )


class DetailedAnswerRejected(CarinaaError):
    """The detailed version failed citation validation; nothing was stored."""

    status_code = 422  # Unprocessable Content
    code = "detailed_answer_rejected"
    message = (
        "The detailed version cited sources that are not in this answer's evidence, "
        "so it was rejected. The original answer is unchanged."
    )


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so no unhandled error ever reaches the browser raw."""

    @app.exception_handler(CarinaaError)
    async def _carinaa_error(_: Request, exc: CarinaaError) -> JSONResponse:
        logger.info("Handled %s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Flatten pydantic's structure into something a form can render.
        fields = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query"))
            fields.append({"field": loc or "request", "message": err.get("msg", "invalid")})
        return JSONResponse(
            status_code=422,  # Unprocessable Content
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Some of the submitted values were not valid.",
                    "detail": {"fields": fields},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Full detail goes to the server log; the client gets a safe summary.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected server error occurred.",
                    "detail": {"hint": safe_error_message(exc)},
                }
            },
        )

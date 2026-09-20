"""
Embeddings.

WHAT AN EMBEDDING IS
--------------------
An embedding model turns text into a list of numbers - a vector - such that texts
with similar *meaning* end up close together. "How do I reset my password?" and
"password recovery steps" share almost no words, but their vectors are close. That
is the whole trick: it lets us search by meaning instead of by keyword.

WHY LOCAL
---------
Spec section 9: the model must run locally so indexing and retrieval work with no
internet. This is what makes Offline mode genuinely offline: the document never
leaves the machine to be turned into vectors.

The model is ONNX (via `fastembed`), so there is no PyTorch dependency. On a CPU
it embeds roughly a few hundred short chunks per second - fast enough that the
ingestion progress bar moves visibly.

THE MODEL-MISMATCH RULE (spec section 9)
----------------------------------------
A vector is only meaningful relative to the model that produced it. Comparing a
vector from model A with one from model B is like comparing metres with feet: the
numbers are real, the comparison is nonsense.

Therefore:
  * every chunk records the model that embedded it (`Chunk.embedding_model`)
  * every document records the same (`Document.embedding_model`)
  * if the configured model changes, those documents are flagged as stale and can
    be re-indexed from SQLite - no re-upload needed

Queries are ALWAYS embedded with the same model as the chunks, because both go
through this one service.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.errors import EmbeddingError
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingService:
    """Process-wide, lazily-loaded local embedding model.

    Loading the ONNX model takes a moment, so we load on first use and keep it
    resident. A lock serialises access because a single ONNX session is not
    guaranteed to be safe under concurrent calls.
    """

    _instance: "EmbeddingService | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, model_name: str | None = None, cache_dir: Path | None = None) -> None:
        self.model_name = model_name or settings.embedding_model
        self.cache_dir = Path(cache_dir or settings.embedding_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._model: Any = None
        self._dimension: int = 0
        self._lock = threading.Lock()
        self._load_error: str | None = None
        self._source: str = "unresolved"

    # ------------------------------------------------------------ model source
    def _resolve_specific_model_path(self) -> str | None:
        """Return a local model directory, if a complete one is present.

        `fastembed` accepts `specific_model_path`, which short-circuits its
        HuggingFace download path completely. We use it when
        `settings.embedding_local_dir` holds a complete model - see the comment on
        that setting for why an environment might need this.
        """
        directory = Path(settings.embedding_local_dir)
        onnx_file = directory / settings.embedding_onnx_filename
        required = (onnx_file, directory / "config.json", directory / "tokenizer.json")

        if all(path.is_file() and path.stat().st_size > 0 for path in required):
            self._source = f"local directory ({directory})"
            return str(directory)

        self._source = f"huggingface cache ({self.cache_dir})"
        return None

    # ---------------------------------------------------------------- singleton
    @classmethod
    def instance(cls) -> "EmbeddingService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the cached model. Used by tests and by a model-change in Settings."""
        with cls._instance_lock:
            cls._instance = None

    # -------------------------------------------------------------------- load
    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from fastembed import TextEmbedding

                specific_path = self._resolve_specific_model_path()
                logger.info(
                    "Loading embedding model '%s' from %s ...", self.model_name, self._source
                )

                kwargs: dict[str, Any] = {"model_name": self.model_name}
                if specific_path:
                    kwargs["specific_model_path"] = specific_path
                else:
                    kwargs["cache_dir"] = str(self.cache_dir)

                self._model = TextEmbedding(**kwargs)
                self._dimension = self._detect_dimension()
                logger.info(
                    "Embedding model ready: %s (%d dimensions, source=%s)",
                    self.model_name,
                    self._dimension,
                    self._source,
                )
            except Exception as exc:
                self._load_error = f"{exc.__class__.__name__}: {exc}"
                logger.error("Could not load embedding model '%s': %s", self.model_name, exc)
                raise EmbeddingError(
                    f"The local embedding model '{self.model_name}' could not be loaded. "
                    f"Run `python scripts/fetch_models.py` to download it, or check "
                    f"EMBEDDING_LOCAL_DIR in your .env file.",
                    detail={"reason": self._load_error},
                ) from exc

    def _detect_dimension(self) -> int:
        try:
            from fastembed import TextEmbedding

            for entry in TextEmbedding.list_supported_models():
                if entry.get("model") == self.model_name:
                    return int(entry.get("dim", 0))
        except Exception:  # pragma: no cover - metadata is best-effort
            pass
        # Fall back to measuring it.
        vector = next(iter(self._model.embed(["dimension probe"])))
        return int(np.asarray(vector).shape[-1])

    # ------------------------------------------------------------------ public
    @property
    def dimension(self) -> int:
        self._ensure_loaded()
        return self._dimension

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed chunks for storage.

        Returns a float32 array of shape (len(texts), dimension), L2-normalised so
        that cosine similarity is just a dot product (which is what the vector
        store computes).
        """
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        self._ensure_loaded()
        cleaned = [t if t.strip() else " " for t in texts]

        try:
            with self._lock:
                vectors = list(
                    self._model.embed(cleaned, batch_size=settings.embedding_batch_size)
                )
        except Exception as exc:
            logger.error("Embedding failed: %s", exc)
            raise EmbeddingError(
                "The embedding model could not process that text.",
                detail={"reason": exc.__class__.__name__},
            ) from exc

        array = np.asarray(vectors, dtype=np.float32)
        return _normalise(array)

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a user question with the SAME model used for the chunks.

        Uses the model's query path (`query_embed`) when it has one. The e5 family
        of models was trained with asymmetric prefixes - "query: " for questions
        and "passage: " for stored text - and fastembed applies those internally,
        which is why we do not add them by hand here. Applying them twice measurably
        degrades retrieval.
        """
        if not query.strip():
            raise EmbeddingError("The question was empty.")

        self._ensure_loaded()
        try:
            with self._lock:
                if hasattr(self._model, "query_embed"):
                    vectors = list(self._model.query_embed([query]))
                else:
                    vectors = list(self._model.embed([query]))
        except Exception as exc:
            logger.error("Query embedding failed: %s", exc)
            raise EmbeddingError(
                "The embedding model could not process that question.",
                detail={"reason": exc.__class__.__name__},
            ) from exc

        array = np.asarray(vectors, dtype=np.float32)
        return _normalise(array)[0]

    def warmup(self) -> bool:
        """Load the model eagerly at startup so the first user query is not slow."""
        try:
            self._ensure_loaded()
            self.embed_query("warmup")
            return True
        except EmbeddingError:
            return False

    def info(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "dimension": self._dimension,
            "loaded": self._model is not None,
            "local": True,
            "source": self._source,
            "cache_dir": str(self.cache_dir),
            "requires_internet_on_first_use_only": True,
            "load_error": self._load_error,
        }


def _normalise(array: np.ndarray) -> np.ndarray:
    """L2-normalise rows so cosine similarity == dot product.

    Guarded against zero-length vectors, which would otherwise produce NaNs and
    silently corrupt every similarity score downstream.
    """
    if array.size == 0:
        return array
    norms = np.linalg.norm(array, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def get_embedding_service() -> EmbeddingService:
    return EmbeddingService.instance()

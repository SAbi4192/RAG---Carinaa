"""
RAG Laboratory routes.

The laboratory exists so a beginner can run ONE stage of the pipeline in isolation
and look at what comes out. Each endpoint here is a thin wrapper over the *same*
code the real pipeline uses - a lab that reimplemented chunking or embedding would
teach the wrong thing, because the thing being taught is what the system actually
does.

WHAT LIVES HERE
---------------
    POST /labs/embed          embed text and inspect the vectors
    GET  /labs/stages         the canonical stage list, so UI and backend agree

The other laboratories reuse existing endpoints rather than duplicating them:

    Chunking Lab    POST /documents/preview-chunks   (the real chunker)
    Retrieval Lab   POST /chat/retrieve              (the real retriever)
    Vector DB Lab   GET  /documents/{id}/chunks      (what is actually stored)
    Context Lab     POST /chat/ask  -> response.context
    Generation Lab  POST /chat/ask  -> response.answer + citations
    Full Pipeline   POST /chat/ask  -> response.trace

That reuse is deliberate. Every one of those already returns measured values, and a
laboratory that invented its own would be a simulation pretending to be the system.

HONESTY RULES APPLIED HERE
--------------------------
  * The 2-D "concept space" is a PCA projection, labelled as such. It is NOT the
    real 384-dimensional space and the UI must say so. Claiming otherwise would
    teach a beginner something false about what embeddings are.
  * Vectors are returned truncated for display, and the response says how many
    dimensions are actually being shown.
  * Nothing is stored, and no language model is called.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.deps import CurrentUser
from app.core.logging import get_logger
from app.rag.embeddings import get_embedding_service
from app.rag.trace import conditional_stage_definitions, stage_definitions

logger = get_logger(__name__)

router = APIRouter(prefix="/labs", tags=["labs"])

# How many of the 384 dimensions to send for display. The full vector would be
# ~1,500 numbers per text, which is unreadable in a UI and pointless over the wire.
PREVIEW_DIMENSIONS = 24

MAX_TEXTS = 12


class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=MAX_TEXTS)
    # Optional: embed a question with the model's QUERY path and rank the texts
    # against it. This is the one place the asymmetry matters - fastembed applies
    # "query: " vs "passage: " prefixes internally, so a question embedded as a
    # document would be scored against the wrong distribution.
    query: str = ""


def _project_to_2d(matrix: np.ndarray) -> tuple[list[dict[str, float]], list[float]]:
    """Project vectors to 2-D with PCA, for a conceptual scatter plot.

    Returns the coordinates and the fraction of variance each axis explains.

    PCA is used rather than t-SNE or UMAP because it is deterministic, needs no
    extra dependency, and - crucially - can report how much of the real structure
    it preserves. A projection that hides its own distortion is worse than none.
    """
    count = matrix.shape[0]
    if count < 2:
        return [{"x": 0.0, "y": 0.0}], [0.0, 0.0]

    centred = matrix - matrix.mean(axis=0, keepdims=True)
    # SVD is more numerically stable than eigen-decomposing the covariance matrix,
    # and for n < d (12 texts, 384 dims) it is also cheaper.
    _, singular, components = np.linalg.svd(centred, full_matrices=False)

    axes = min(2, components.shape[0])
    coords = centred @ components[:axes].T

    total_variance = float((singular**2).sum())
    if total_variance > 0:
        explained = [float(s**2) / total_variance for s in singular[:axes]]
    else:
        explained = [0.0] * axes
    explained += [0.0] * (2 - len(explained))

    points = [
        {"x": round(float(coords[i, 0]), 4), "y": round(float(coords[i, 1]), 4)}
        if axes > 1
        else {"x": round(float(coords[i, 0]), 4), "y": 0.0}
        for i in range(count)
    ]
    return points, [round(v, 4) for v in explained]


@router.post("/embed")
def embed_texts(payload: EmbedRequest, _: CurrentUser) -> dict[str, Any]:
    """Embed text with the real model and return vectors, similarity and a projection.

    Nothing is stored. The response is meant to be read by a human.
    """
    texts = [t for t in (payload.texts or []) if t and t.strip()]
    if not texts:
        return {
            "model": "",
            "dimensions": 0,
            "count": 0,
            "vectors": [],
            "note": "No text was provided, so nothing was embedded.",
        }

    service = get_embedding_service()
    matrix = service.embed_documents(texts)
    dimensions = int(matrix.shape[1]) if matrix.size else 0

    # Cosine similarity is a dot product here because the vectors are L2-normalised
    # on the way out of the embedder. Recomputing cosine by hand would risk
    # disagreeing with what the vector store actually does.
    similarity = (matrix @ matrix.T).astype(float) if matrix.size else np.zeros((0, 0))

    projection, explained = _project_to_2d(matrix) if matrix.size else ([], [0.0, 0.0])

    preview = int(min(PREVIEW_DIMENSIONS, dimensions))
    vectors = [
        [round(float(value), 4) for value in matrix[row, :preview]]
        for row in range(matrix.shape[0])
    ] if matrix.size else []

    response: dict[str, Any] = {
        "model": service.model_name,
        "dimensions": dimensions,
        "preview_dimensions": preview,
        "count": len(texts),
        "vectors": vectors,
        "norms": [round(float(np.linalg.norm(matrix[i])), 4) for i in range(matrix.shape[0])]
        if matrix.size
        else [],
        "similarity": [[round(float(v), 4) for v in row] for row in similarity]
        if matrix.size
        else [],
        "projection": projection,
        "projection_explained_variance": explained,
        "projection_note": (
            "Conceptual 2-D projection (PCA) of the real vectors. This is NOT the "
            "true embedding space - the model uses "
            f"{dimensions} dimensions and the projection keeps only the two that "
            "carry the most variance. Use it to see which texts are near each "
            "other, not to measure exact distances."
        ),
        "note": (
            "These are real vectors from the same model used for retrieval. "
            "Nothing was stored and no language model was called."
        ),
    }

    query = (payload.query or "").strip()
    if query:
        query_vector = service.embed_query(query)
        scores = (matrix @ query_vector).astype(float) if matrix.size else np.zeros(0)
        order = np.argsort(-scores) if scores.size else np.array([], dtype=int)
        response["query"] = {
            "text": query,
            "vector": [round(float(v), 4) for v in query_vector[:preview]],
            "ranking": [
                {"index": int(i), "score": round(float(scores[i]), 4)} for i in order
            ],
            "note": (
                "The question is embedded through the model's query path and scored "
                "against each text by cosine similarity - exactly what the retriever "
                "does before ranking."
            ),
        }

    logger.info(
        "Lab: embedded %d text(s) into %d dimensions (query=%s)",
        len(texts),
        dimensions,
        bool(query),
    )
    return response


@router.get("/stages")
def lab_stages(_: CurrentUser) -> dict[str, Any]:
    """The canonical pipeline stage list, so the UI and the backend cannot disagree.

    Served rather than hard-coded in the frontend: if a stage is added or renamed,
    the laboratory should follow automatically instead of silently omitting it.

    `conditional_stages` are the ones that can appear in a trace without being part
    of the fixed order - web search and the offline fail-safe. They are returned
    separately because drawing them as ordinary steps would show a pipeline that
    never runs, but they still need labelling when they do appear.
    """
    return {
        "stages": stage_definitions(),
        "conditional_stages": conditional_stage_definitions(),
        "note": (
            "These are the stages the pipeline actually runs, in order. A stage that "
            "did not run for a given question is reported as 'skipped' with a reason - "
            "it is never shown as if it had executed."
        ),
    }

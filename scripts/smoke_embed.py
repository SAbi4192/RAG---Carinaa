"""Smoke test: local embedding model + ChromaDB round trip.

Run:  .venv/Scripts/python -m scripts.smoke_embed
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np  # noqa: E402

from app.core.config import settings  # noqa: E402


def main() -> int:
    print(f"model      : {settings.embedding_model}")
    print(f"cache dir  : {settings.embedding_cache_dir}")

    t0 = time.perf_counter()
    from fastembed import TextEmbedding

    model = TextEmbedding(
        model_name=settings.embedding_model, cache_dir=str(settings.embedding_cache_dir)
    )
    print(f"load       : {time.perf_counter() - t0:.1f}s")

    docs = [
        "Virtualization improves resource utilization by abstracting physical hardware.",
        "A neural network learns its weights through backpropagation.",
        "Cloud computing provides on-demand compute resources over the internet.",
        "The placement cell requires a minimum of 75% attendance for eligibility.",
    ]

    t0 = time.perf_counter()
    passage_vectors = np.asarray(list(model.embed(docs, batch_size=8)), dtype=np.float32)
    print(f"embed docs : {time.perf_counter() - t0:.2f}s  shape={passage_vectors.shape}")

    query = "How does virtualization help with resource usage?"
    t0 = time.perf_counter()
    query_vector = np.asarray(list(model.query_embed([query])), dtype=np.float32)[0]
    print(f"embed query: {time.perf_counter() - t0:.2f}s  shape={query_vector.shape}")

    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    print("\nSimilarity of the query to each document:")
    scored = sorted(
        ((cosine(query_vector, v), d) for v, d in zip(passage_vectors, docs)),
        reverse=True,
    )
    for score, doc in scored:
        marker = "  <-- top hit" if score == scored[0][0] else ""
        print(f"  {score:+.4f}  {doc[:62]}{marker}")

    # ---- Chroma round trip -------------------------------------------------
    print("\nChromaDB round trip:")
    import tempfile

    import chromadb

    tmp = Path(tempfile.mkdtemp(prefix="carinaa-chroma-"))
    client = chromadb.PersistentClient(path=str(tmp))
    collection = client.get_or_create_collection(
        name="smoke", metadata={"hnsw:space": "cosine"}
    )
    collection.add(
        ids=[f"c{i}" for i in range(len(docs))],
        embeddings=[v.tolist() for v in passage_vectors],
        documents=docs,
        metadatas=[
            {"workspace_id": 1, "document_id": 10, "page_number": 1},
            {"workspace_id": 1, "document_id": 10, "page_number": 2},
            {"workspace_id": 2, "document_id": 20, "page_number": 3},
            {"workspace_id": 1, "document_id": 10, "page_number": 4},
        ],
    )
    result = collection.query(
        query_embeddings=[query_vector.tolist()],
        n_results=4,
        where={"workspace_id": 1},
        include=["documents", "metadatas", "distances"],
    )
    print(f"  collection count : {collection.count()}")
    print("  filtered query (workspace_id=1) returned:")
    for doc, meta, dist in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        ws = meta.get("workspace_id")
        assert ws == 1, f"LEAK: workspace {ws} returned for workspace 1 query"
        print(f"    sim={1 - dist:+.4f}  ws={ws}  {doc[:52]}")

    leaked = [m for m in result["metadatas"][0] if m.get("workspace_id") != 1]
    print(f"  cross-workspace leakage: {len(leaked)} rows (expected 0)")

    import shutil

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

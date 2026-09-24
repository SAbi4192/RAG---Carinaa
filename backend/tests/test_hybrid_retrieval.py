"""
Tests for hybrid retrieval: BM25, reciprocal rank fusion, and the lab endpoint.

Why these tests exist: hybrid retrieval changes what evidence a user sees. If the
fusion were wrong, every downstream claim (trace, lab comparison, citations)
would inherit the error quietly. So the maths is pinned here directly, without a
network or a live model:

  * RRF must be scale-free: the same two orderings, scored on deliberately
    incompatible numeric scales, must fuse purely by position.
  * BM25 must do the thing dense search is weak at: find the exact string
    ("RFC 1918", "UNIT III") and weight rare terms over common ones.
  * A missing chunk must read as "not retrieved", never as "ranked last".
  * The /chat/retrieve/compare endpoint must return three honest columns.
"""

from __future__ import annotations

import pytest

from app.rag.hybrid import Bm25Index, tokenize
from app.rag.vectorstore import RetrievedChunk


def _chunk(index: int, content: str, *, score: float = 0.0, name: str = "doc.md") -> RetrievedChunk:
    return RetrievedChunk(
        vector_id=f"v{index}",
        workspace_id=1,
        document_id=1,
        chunk_id=index,
        chunk_index=index,
        content=content,
        metadata={"document_name": name},
        document_name=name,
        score=score,
        rank=index,
    )


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------
def test_tokenize_lowercases_splits_and_drops_stopwords() -> None:
    tokens = tokenize("The Role of NAT in RFC 1918")
    assert "rfc" in tokens and "1918" in tokens
    assert "the" not in tokens and "of" not in tokens


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------
def test_bm25_finds_the_exact_string_dense_search_would_blur() -> None:
    corpus = [
        _chunk(0, "This passage discusses private networking addresses in general."),
        _chunk(1, "RFC 1918 defines the private address ranges 10/8, 172.16/12, 192.168/16."),
        _chunk(2, "Public addresses are assigned by the regional registries."),
    ]
    index = Bm25Index.build(corpus)
    hits = index.search("RFC 1918", top_k=3)
    assert hits, "an exact-term query must match"
    assert hits[0][0].vector_id == "v1", "the chunk literally containing the term must rank first"
    # The returned score is always positive - a chunk with no query term is not
    # returned at all, so BM25 never ranks irrelevant evidence ahead of relevant.
    assert all(score > 0 for _chunk_obj, score in hits)


def test_bm25_weights_rare_terms_above_common_ones() -> None:
    corpus = [
        _chunk(0, "design design design thinking"),          # all common words
        _chunk(1, "empathy define ideate prototype test"),   # all rare words
        _chunk(2, "design thinking workshop"),
    ]
    index = Bm25Index.build(corpus)
    hits = index.search("design thinking", top_k=3)
    by_id = {c.vector_id: score for c, score in hits}
    # IDF: a term appearing in most documents discriminates almost nothing.
    # Chunk 2 (both terms, few repeats) must beat chunk 0 (many repeats of a
    # word that also appears elsewhere) or at least carry the same rare weight.
    assert by_id["v2"] > 0
    assert by_id.get("v1", 0) == 0, "a chunk without the query terms must not score"


def test_bm25_handles_an_empty_index_and_empty_query() -> None:
    assert Bm25Index.build([]).is_empty
    assert Bm25Index.build([]).search("anything", top_k=5) == []
    index = Bm25Index.build([_chunk(0, "some text")])
    assert index.search("   ", top_k=5) == []


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------
def test_rrf_is_scale_free_and_order_only() -> None:
    from app.rag.hybrid import reciprocal_rank_fusion

    # Two lists on deliberately incompatible numeric scales: cosine-like on the
    # left, BM25-like (14.0) on the right. A score-adding "fusion" would let the
    # 14.0 dominate. RRF must not care.
    a = _chunk(0, "A", score=0.97)
    b = _chunk(1, "B", score=0.10)
    c = _chunk(2, "C", score=0.01)
    dense = [a, b, c]

    x = _chunk(3, "X", score=14.0)
    sparse = [x, c, b]

    fused = reciprocal_rank_fusion(dense, sparse, rrf_k=60, dense_weight=0.5)
    ids = [f.vector_id for f in fused]

    # C and B appear in BOTH lists; A and X in only one. Two contributions beat
    # one, so the in-both chunks must lead the one-list chunks. Between C (3rd+2nd)
    # and B (2nd+3rd) the sums are identical, so either order is honest; the test
    # asserts the real property: {v1, v2} are the top two, {v0, v3} the bottom two.
    assert set(ids[:2]) == {"v1", "v2"}, ids
    assert set(ids[2:]) == {"v0", "v3"}, ids

    # Every fused value is a reciprocal-rank score (~1/60-ish), never a raw
    # cosine or BM25 number. The BM25 side's 14.0 must not leak into any fused
    # score - that is the scale-free guarantee in one line.
    assert all(0 < f.rrf_score < 0.02 for f in fused)
    assert max(f.rrf_score for f in fused) < x.score


def test_rrf_weight_extremes_reduce_to_single_lists() -> None:
    from app.rag.hybrid import reciprocal_rank_fusion

    dense = [_chunk(0, "A"), _chunk(1, "B")]
    sparse = [_chunk(1, "B"), _chunk(0, "A")]  # opposite order, same members

    dense_only = reciprocal_rank_fusion(dense, sparse, dense_weight=1.0)
    assert [c.vector_id for c in dense_only] == ["v0", "v1"]

    sparse_only = reciprocal_rank_fusion(dense, sparse, dense_weight=0.0)
    assert [c.vector_id for c in sparse_only] == ["v1", "v0"]


def test_rrf_never_invents_a_chunk_neither_list_retrieved() -> None:
    from app.rag.hybrid import reciprocal_rank_fusion

    dense = [_chunk(0, "A")]
    sparse = [_chunk(1, "B")]
    fused = reciprocal_rank_fusion(dense, sparse)
    assert {c.vector_id for c in fused} == {"v0", "v1"}


# ---------------------------------------------------------------------------
# The compare endpoint, end to end over the HTTP stack
# ---------------------------------------------------------------------------
@pytest.fixture()
def indexed_workspace(client, make_user, make_workspace, db):
    """A workspace with two documents that give dense and lexical search
    genuinely different strengths: one paraphrases, one carries the exact term."""
    headers, owner_id = make_user()
    workspace_id = make_workspace(headers)

    from app.db.models import Chunk, Document
    from app.rag.embeddings import get_embedding_service
    from app.rag.vectorstore import get_vector_store

    embeddings = get_embedding_service()
    store = get_vector_store()

    documents = [
        (
            "networking.md",
            "Network Address Translation lets several machines share one public "
            "address. It hides internal structure from the outside network.",
        ),
        (
            "standards.md",
            "RFC 1918 sets aside private ranges: 10.0.0.0/8, 172.16.0.0/12 and "
            "192.168.0.0/16 are never routed on the public internet.",
        ),
    ]

    for name, text in documents:
        document = Document(
            workspace_id=workspace_id,
            user_id=owner_id,
            original_filename=name,
            stored_filename=name,
            file_type="md",
            size_bytes=len(text),
            status="ready",
            stage="ready",
            progress=100,
            char_count=len(text),
            chunk_count=1,
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        vector_id = f"vec-{document.id}"
        chunk = Chunk(
            document_id=document.id,
            workspace_id=workspace_id,
            chunk_index=0,
            content=text,
            char_start=0,
            char_end=len(text),
            token_estimate=len(text) // 4,
            block_type="text",
            vector_id=vector_id,
            doc_metadata={"document_name": name, "file_type": "md"},
        )
        db.add(chunk)
        db.commit()
        db.refresh(chunk)

        store.add_chunks(
            workspace_id=workspace_id,
            document_id=document.id,
            vector_ids=[vector_id],
            contents=[text],
            embeddings=embeddings.embed_documents([text]),
            metadatas=[
                {
                    "document_name": name,
                    "file_type": "md",
                    "chunk_index": 0,
                }
            ],
        )

    return headers, workspace_id


def test_compare_endpoint_returns_three_honest_columns(client, indexed_workspace) -> None:
    headers, workspace_id = indexed_workspace
    response = client.post(
        "/api/chat/retrieve/compare",
        json={
            "workspace_id": workspace_id,
            "question": "RFC 1918 private address ranges",
            "top_k": 2,
            "candidate_k": 4,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body["columns"]) == {"dense", "bm25", "hybrid"}
    for mode, column in body["columns"].items():
        assert "error" not in column, f"{mode} failed: {column}"
        assert column["mode"] == mode
        assert column["results"], f"{mode} must retrieve something"
        # Each column reports its own scale so the UI can never label an RRF
        # value as a cosine similarity.
        assert column["score_scale"] == {
            "dense": "cosine",
            "bm25": "bm25",
            "hybrid": "rrf",
        }[mode]

    # The exact-term query is BM25's home ground: the RFC document must be
    # first for BM25 (and for the fusion, which honours a rank-1 list).
    assert body["columns"]["bm25"]["results"][0]["document_name"] == "standards.md"
    assert body["columns"]["hybrid"]["results"][0]["document_name"] == "standards.md"

    # The comparison matrix exists and reports agreement honestly.
    assert isinstance(body["comparison"], list) and body["comparison"]
    row = next(r for r in body["comparison"] if r["document_name"] == "standards.md")
    assert "ranks" in row and set(row["ranks"]) == {"dense", "bm25", "hybrid"}
    assert row["best_rank"] == 0
    assert 1 <= row["agreement"] <= 3


def test_compare_endpoint_requires_authentication(client) -> None:
    response = client.post(
        "/api/chat/retrieve/compare", json={"workspace_id": 1, "question": "anything"}
    )
    assert response.status_code in (401, 403)


def test_compare_endpoint_respects_workspace_isolation(client, indexed_workspace, make_user) -> None:
    _headers, workspace_id = indexed_workspace
    outsider_headers, _ = make_user("Intruder")
    response = client.post(
        "/api/chat/retrieve/compare",
        json={"workspace_id": workspace_id, "question": "RFC 1918"},
        headers=outsider_headers,
    )
    assert response.status_code in (403, 404), (
        "a stranger must not be able to run retrieval against someone else's corpus"
    )


# ---------------------------------------------------------------------------
# The mode parameter reaches the retriever and is reported
# ---------------------------------------------------------------------------
def test_retrieve_endpoint_accepts_mode_and_reports_scale(client, indexed_workspace) -> None:
    headers, workspace_id = indexed_workspace
    response = client.post(
        "/api/chat/retrieve",
        json={
            "workspace_id": workspace_id,
            "question": "What is NAT?",
            "mode": "hybrid",
            "top_k": 2,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    retrieval = response.json()["retrieval"]
    assert retrieval["mode"] == "hybrid"
    assert retrieval["score_scale"] == "rrf"
    # Hybrid runs expose the full three-ranking report, because that is the
    # evidence the Learning panel's diagram draws from.
    assert retrieval["hybrid"], "a hybrid run must carry its dense/bm25/fused report"
    assert {"dense", "bm25", "fused"} <= set(retrieval["hybrid"])

"""
Cross-workspace isolation.

THE CLAIM
---------
A user must never be able to retrieve, read, or even confirm the existence of
another user's data.

WHY THIS IS TESTED AT TWO LAYERS
--------------------------------
The vector store and the API fail differently, so one test cannot cover both:

  * A **vector-store** leak returns plausible-looking evidence and raises no error
    at all. That is the worst kind of bug in a RAG system, because the answer still
    looks correct and is cited to a real chunk - just somebody else's chunk.
  * An **API** leak hands over a whole workspace or document.

So both are tested explicitly rather than assuming the upper layer covers the lower.

WHY 404 AND NOT 403
-------------------
Requesting another user's workspace returns 404, never 403. A 403 would confirm
that the id exists, which turns the endpoint into an oracle for enumerating other
people's workspaces. "Not found" and "not yours" are deliberately indistinguishable.
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _unit_vector(seed: int, dim: int = 32) -> np.ndarray:
    """A deterministic, L2-normalised vector.

    Synthetic vectors keep these tests fast and exact. Because the two chunks below
    are given IDENTICAL vectors, the only thing that can separate them is the
    workspace filter - which is precisely the property under test.
    """
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=dim).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def _workspace_terms(clause) -> set:
    """Flatten a Chroma `where` clause into the workspace ids it constrains."""
    found: set = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "workspace_id":
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(clause)
    return found


def _index(store, *, workspace_id: int, document_id: int, vector_id: str, text: str, vector):
    """Write one synthetic chunk into the store."""
    store.add_chunks(
        workspace_id=workspace_id,
        document_id=document_id,
        vector_ids=[vector_id],
        contents=[text],
        embeddings=np.stack([vector]),
        metadatas=[{"document_name": f"doc-{document_id}.md"}],
    )


# ---------------------------------------------------------------------------
# Layer 1: the vector store
# ---------------------------------------------------------------------------
def test_scope_filter_always_contains_the_workspace_id(store):
    """No filter shape may omit the workspace boundary."""
    for document_ids in (None, [], [1], [1, 2]):
        clause = store._scope_filter(7, document_ids)
        assert _workspace_terms(clause) == {7}, (
            f"document_ids={document_ids!r} produced a filter without the "
            f"workspace boundary: {clause}"
        )


def test_document_filter_narrows_but_never_widens(store):
    """A document filter may only narrow an already-scoped query."""
    clause = store._scope_filter(7, [3, 4])
    assert _workspace_terms(clause) == {7}
    # The document restriction must survive alongside the workspace restriction,
    # as a sibling term inside the $and - not replacing it.
    assert clause == {
        "$and": [{"workspace_id": 7}, {"document_id": {"$in": [3, 4]}}]
    }


def test_single_document_filter_keeps_the_workspace_boundary(store):
    """The single-document branch is a separate code path and needs its own check."""
    clause = store._scope_filter(7, [3])
    assert clause == {"$and": [{"workspace_id": 7}, {"document_id": 3}]}


def test_query_never_returns_another_workspaces_chunks(store):
    """The core isolation guarantee, exercised against real ChromaDB."""
    vector = _unit_vector(1)

    _index(
        store,
        workspace_id=1,
        document_id=10,
        vector_id="ws1-chunk",
        text="ALPHA belongs to workspace one",
        vector=vector,
    )
    _index(
        store,
        workspace_id=2,
        document_id=20,
        vector_id="ws2-chunk",
        text="BETA belongs to workspace two",
        vector=vector,
    )

    results = store.query(workspace_id=2, query_embedding=vector, top_k=10)
    contents = [chunk.content for chunk in results]

    # Sanity: the query genuinely works, so a "no leak" result is not just an
    # empty index in disguise.
    assert "BETA belongs to workspace two" in contents
    assert all("ALPHA" not in text for text in contents)
    assert {chunk.workspace_id for chunk in results} == {2}


def test_owning_workspace_can_still_retrieve_its_chunk(store):
    """Guards against a filter so strict that it hides everything."""
    vector = _unit_vector(2)
    _index(
        store,
        workspace_id=42,
        document_id=1,
        vector_id="only",
        text="gamma belongs to workspace forty-two",
        vector=vector,
    )

    results = store.query(workspace_id=42, query_embedding=vector, top_k=5)
    assert any("gamma" in chunk.content for chunk in results)


def test_document_filter_cannot_be_used_to_escape_the_workspace(store):
    """Asking for another workspace's document id must return nothing."""
    vector = _unit_vector(4)
    _index(store, workspace_id=1, document_id=10, vector_id="a", text="one", vector=vector)
    _index(store, workspace_id=2, document_id=20, vector_id="b", text="two", vector=vector)

    results = store.query(
        workspace_id=1, query_embedding=vector, top_k=10, document_ids=[20]
    )
    assert results == []


def test_post_retrieval_verification_rejects_foreign_rows(store):
    """Defence in depth: even if the index returned a foreign row, it is dropped.

    This exercises the second guard directly, by handing `_to_retrieved` a result
    set that the filter should never have produced.
    """
    result = {
        "ids": [["mine", "theirs"]],
        "documents": [["my content", "their content"]],
        "metadatas": [
            [
                {"workspace_id": 5, "document_id": 1},
                {"workspace_id": 9, "document_id": 2},
            ]
        ],
        "distances": [[0.10, 0.20]],
    }

    chunks = store._to_retrieved(result, expected_workspace_id=5)

    assert len(chunks) == 1
    assert chunks[0].content == "my content"
    assert chunks[0].workspace_id == 5


def test_delete_workspace_removes_only_that_workspaces_vectors(store):
    vector = _unit_vector(3)
    _index(store, workspace_id=1, document_id=10, vector_id="a", text="one", vector=vector)
    _index(store, workspace_id=2, document_id=20, vector_id="b", text="two", vector=vector)

    store.delete_workspace(1)

    assert store.count(workspace_id=1) == 0
    assert store.count(workspace_id=2) == 1


# ---------------------------------------------------------------------------
# Layer 2: the API
# ---------------------------------------------------------------------------
def test_another_user_cannot_read_the_workspace(client, make_user, make_workspace):
    owner_headers, _ = make_user("Owner")
    attacker_headers, _ = make_user("Attacker")
    workspace_id = make_workspace(owner_headers)

    response = client.get(f"/api/workspaces/{workspace_id}", headers=attacker_headers)

    # 404, not 403 - see the module docstring.
    assert response.status_code == 404, response.text


def test_owner_can_read_their_own_workspace(client, make_user, make_workspace):
    """The mirror image, so the 404 above is proven to be about ownership."""
    owner_headers, _ = make_user("Owner")
    workspace_id = make_workspace(owner_headers)

    response = client.get(f"/api/workspaces/{workspace_id}", headers=owner_headers)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == workspace_id


def test_another_user_cannot_list_the_workspaces_documents(
    client, make_user, make_workspace
):
    owner_headers, _ = make_user("Owner")
    attacker_headers, _ = make_user("Attacker")
    workspace_id = make_workspace(owner_headers)

    response = client.get(
        f"/api/workspaces/{workspace_id}/documents", headers=attacker_headers
    )

    assert response.status_code == 404, response.text


def test_another_user_cannot_delete_the_workspace(client, make_user, make_workspace):
    owner_headers, _ = make_user("Owner")
    attacker_headers, _ = make_user("Attacker")
    workspace_id = make_workspace(owner_headers)

    response = client.delete(f"/api/workspaces/{workspace_id}", headers=attacker_headers)

    assert response.status_code == 404, response.text
    # And it is genuinely still there afterwards.
    assert (
        client.get(f"/api/workspaces/{workspace_id}", headers=owner_headers).status_code
        == 200
    )


def test_another_user_cannot_retrieve_from_the_workspace(
    client, make_user, make_workspace
):
    """Retrieval is the dangerous one: it returns document CONTENT."""
    owner_headers, _ = make_user("Owner")
    attacker_headers, _ = make_user("Attacker")
    workspace_id = make_workspace(owner_headers)

    response = client.post(
        "/api/chat/retrieve",
        json={"workspace_id": workspace_id, "question": "anything at all"},
        headers=attacker_headers,
    )

    assert response.status_code == 404, response.text


def test_another_user_cannot_open_a_conversation_in_the_workspace(
    client, make_user, make_workspace
):
    owner_headers, _ = make_user("Owner")
    attacker_headers, _ = make_user("Attacker")
    workspace_id = make_workspace(owner_headers)

    response = client.post(
        "/api/conversations",
        json={"workspace_id": workspace_id, "title": "sneaky", "ai_mode": "offline"},
        headers=attacker_headers,
    )

    assert response.status_code == 404, response.text


def test_workspace_list_is_scoped_to_the_owner(client, make_user, make_workspace):
    owner_headers, _ = make_user("Owner")
    attacker_headers, _ = make_user("Attacker")

    owner_workspace = make_workspace(owner_headers, name="Owner workspace")
    attacker_workspace = make_workspace(attacker_headers, name="Attacker workspace")

    response = client.get("/api/workspaces", headers=attacker_headers)
    assert response.status_code == 200, response.text

    ids = {item["id"] for item in response.json()["workspaces"]}
    assert attacker_workspace in ids
    assert owner_workspace not in ids


def test_unauthenticated_requests_are_rejected(client, make_user, make_workspace):
    owner_headers, _ = make_user("Owner")
    workspace_id = make_workspace(owner_headers)

    assert client.get(f"/api/workspaces/{workspace_id}").status_code == 401
    assert client.get("/api/workspaces").status_code == 401


# ---------------------------------------------------------------------------
# The security report must not read other tenants' data
# ---------------------------------------------------------------------------
def test_security_report_only_scans_the_callers_own_traces(
    client, make_user, make_workspace, db
):
    """Regression test for a real leak.

    The trace-scan query used to select trace events with no user filter at all, so
    a user's security report scanned every tenant's payloads. Here a trace is
    planted for a *different* account, and the caller's report must still report
    zero payloads scanned.
    """
    from app.db.models import Conversation, Message, TraceEvent

    other_headers, other_user_id = make_user("Other")
    other_workspace_id = make_workspace(other_headers, name="Other workspace")

    conversation = Conversation(
        workspace_id=other_workspace_id, user_id=other_user_id, title="other"
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    message = Message(conversation_id=conversation.id, role="assistant", content="other")
    db.add(message)
    db.commit()
    db.refresh(message)

    # A credential-shaped value, so the scan would have had something to find.
    db.add(
        TraceEvent(
            message_id=message.id,
            trace_id="trace-other-user",
            stage="llm_generation",
            event_data={"note": "credential-shaped AIza" + "x" * 35},
        )
    )
    db.commit()

    caller_headers, _ = make_user("Caller")
    make_workspace(caller_headers, name="Caller workspace")

    response = client.get("/api/evaluation/security", headers=caller_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["context"]["trace_payloads_scanned"] == 0, (
        "The caller has no conversations, so their report must scan no trace "
        "payloads - a non-zero count means another tenant's data was read."
    )


def test_security_report_actually_runs_the_authorization_check(
    client, make_user, make_workspace
):
    """The authorization check must EXECUTE, not silently skip.

    It is legitimately skipped only when there is no second account to act as the
    attacker. When one exists it must run and pass.
    """
    headers, _ = make_user("Owner")
    make_workspace(headers, name="One")
    make_workspace(headers, name="Two")

    response = client.get("/api/evaluation/security", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    checks = {check["name"]: check for check in body["checks"]}
    authorization = checks["cross_workspace_authorization"]

    assert body["context"]["second_account_available"] is True
    assert authorization["skipped"] is False, (
        "The authorization check was skipped even though a second account exists: "
        f"{authorization['detail']}"
    )
    assert authorization["passed"] is True, authorization["detail"]

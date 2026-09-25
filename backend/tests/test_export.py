"""
Tests for the Evidence Pack export (#54).

The export reads only stored state, so these tests build a message + trace row
directly and call the endpoint. The properties that matter are:

  * it renders the STORED answer, question, citations and trace - the pack must
    match what the user saw, because a mismatch is the whole reason the export
    refuses to regenerate;
  * it is scoped to the owner (isolation), and needs authentication;
  * both formats are offered honestly (Markdown downloads; HTML is print-ready,
    and it is NOT labelled a PDF);
  * a citation link points at the exact chunk (`?chunk=`), tying the export back
    to the in-app evidence view.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.db.models import Conversation, Message, TraceEvent, Workspace
from app.db.session import SessionLocal


def _seed_message(*, user_id: int) -> tuple[int, int]:
    """Insert a workspace, conversation, assistant message and its trace.

    Returns (conversation_id, message_id).
    """
    db = SessionLocal()
    try:
        workspace = Workspace(user_id=user_id, name="Export test")
        db.add(workspace)
        db.commit()
        db.refresh(workspace)

        conversation = Conversation(
            workspace_id=workspace.id, user_id=user_id, title="Q", ai_mode="online"
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        question = Message(
            conversation_id=conversation.id, role="user", content="What is a hypervisor?",
            ai_mode="online",
        )
        db.add(question)
        db.commit()

        answer = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="A hypervisor abstracts hardware [1].",
            ai_mode="online",
            provider="groq",
            model="llama-3.3-70b-versatile",
            grounding_status="SUPPORTED",
            grounding_detail={"status": "SUPPORTED", "counts": {"supported": 1, "weak": 0, "uncited": 0}},
            latency_ms=134,
            retrieval={
                "mode": "hybrid",
                "score_scale": "rrf",
                "chunks": [
                    {
                        "label": "1",
                        "chunk_id": 42,
                        "document_id": 7,
                        "document_name": "virtualization.md",
                        "content": "A hypervisor, or VMM, allows multiple VMs to share hardware.",
                        "score": 0.0164,
                    }
                ],
            },
            citations=[
                {
                    "number": 1,
                    "kind": "document",
                    "document_id": 7,
                    "chunk_id": 42,
                    "document_name": "virtualization.md",
                    "section": "Virtualization",
                    "page_number": 3,
                    "snippet": "A hypervisor, or VMM, allows multiple VMs to share hardware.",
                }
            ],
            created_at=dt.datetime(2026, 9, 20, 12, 0, 0, tzinfo=dt.timezone.utc),
        )
        db.add(answer)
        db.commit()
        db.refresh(answer)

        db.add_all(
            [
                TraceEvent(
                    message_id=answer.id,
                    trace_id=answer.trace_id or "t1",
                    seq=1,
                    stage="vector_search",
                    status="ok",
                    duration_ms=8,
                    event_data={"label": "Vector Search"},
                ),
                TraceEvent(
                    message_id=answer.id,
                    trace_id=answer.trace_id or "t1",
                    seq=2,
                    stage="llm_generation",
                    status="ok",
                    duration_ms=96,
                    event_data={"label": "LLM Generation"},
                ),
            ]
        )
        db.commit()
        return conversation.id, answer.id
    finally:
        db.close()


def _headers_for(client, make_user) -> tuple[dict, int]:
    headers, user_id = make_user()
    return headers, user_id


def test_export_markdown_contains_the_stored_evidence(client, make_user) -> None:
    headers, user_id = _headers_for(client, make_user)
    _conv, message_id = _seed_message(user_id=user_id)

    response = client.get(
        f"/api/messages/{message_id}/export?format=md", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text

    # The stored question, answer, provider, citation, evidence and trace are all
    # present - assembled from the row, not regenerated.
    assert "What is a hypervisor?" in body
    assert "A hypervisor abstracts hardware [1]." in body
    # The exported pack is a user-facing artifact: engine identity is a ROLE, never
    # a cloud vendor (the seed row was produced with provider="groq").
    assert "Remote answer engine" in body
    assert "groq" not in body.lower()
    assert "openai/" not in body.lower()
    assert "virtualization.md" in body
    assert "Virtualization" in body
    assert "vector_search" not in body or "Vector Search" in body  # label is used
    assert "LLM Generation" in body
    # The score scale is named, so an RRF number is never read as a cosine.
    assert "rrf" in body.lower()


def test_export_markdown_links_the_exact_chunk(client, make_user) -> None:
    headers, user_id = _headers_for(client, make_user)
    _conv, message_id = _seed_message(user_id=user_id)

    response = client.get(
        f"/api/messages/{message_id}/export?format=md&base_url=http://localhost:8000",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.text
    assert "http://localhost:8000/app/knowledge/7?chunk=42" in body


def test_export_html_is_a_printable_document_not_a_pdf_claim(client, make_user) -> None:
    headers, user_id = _headers_for(client, make_user)
    _conv, message_id = _seed_message(user_id=user_id)

    response = client.get(
        f"/api/messages/{message_id}/export?format=html", headers=headers
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert body.lstrip().startswith("<!doctype html>")
    assert "A hypervisor abstracts hardware" in body
    # It says Save-as-PDF; it does not pretend to BE a PDF.
    assert "Save as PDF" in body
    assert "%PDF" not in body


def test_export_requires_the_message_to_belong_to_caller(client, make_user) -> None:
    owner_headers, owner_id = _headers_for(client, make_user)
    _conv, message_id = _seed_message(user_id=owner_id)

    other_headers, _other_id = _headers_for(client, make_user)
    response = client.get(
        f"/api/messages/{message_id}/export?format=md", headers=other_headers
    )
    # Not the caller's message: a 404 (it is "not found" to them) or 403. Either
    # is acceptable; a 200 leaking the other user's evidence is not.
    assert response.status_code in (403, 404), response.text


def test_export_requires_authentication(client, make_user) -> None:
    headers, user_id = _headers_for(client, make_user)
    _conv, message_id = _seed_message(user_id=user_id)
    response = client.get(f"/api/messages/{message_id}/export")
    assert response.status_code in (401, 403)


def test_export_of_unknown_message_is_404(client, make_user) -> None:
    headers, _ = _headers_for(client, make_user)
    response = client.get("/api/messages/999999/export?format=md", headers=headers)
    assert response.status_code == 404

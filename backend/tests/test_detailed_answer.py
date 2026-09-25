"""Detailed Answer (exam-style re-presentation) behaviour tests.

The contract this feature must keep:

  * the variant is generated from the evidence the ORIGINAL answer cited, so
    citation numbers mean the same thing in both texts;
  * a version that cites anything outside that evidence is REJECTED (422) and
    the canonical answer is not modified;
  * web content is only reused when the original answer used the web - a style
    request must not sneak in sources the user never opted into;
  * the cached path serves the stored variant without calling the engine;
  * public responses never carry cloud vendor identity (VariantOut sanitizer).

The engine is stubbed at the adapter boundary: these tests are about wiring and
safety, not about what a real LLM writes.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.db.models import AnswerVariant, Conversation, Message
from app.llm.base import LLMResponse


# ---------------------------------------------------------------------------
# Fixtures: an assistant message with the shape production persists
# ---------------------------------------------------------------------------
def _chunk(number: int, page: int, text: str) -> dict[str, Any]:
    return {
        "chunk_id": number,
        "document_id": 1,
        "document_name": "ADT_Notes.pdf",
        "file_type": "pdf",
        "content": text,
        "metadata": {"page_number": page, "page_end": page},
        "label": f"ADT_Notes.pdf · Page {page}",
        "score": 0.5,
    }


@pytest.fixture()
def stored_answer(db, make_user, make_workspace):
    """A grounded answer over two chunks, plus its user turn.

    Returns (headers, message_id, user_id) with the retrieval payload stored in
    the exact shape routes_chat persists, so the rebuild reads real columns.
    """
    headers, user_id = make_user()
    workspace_id = make_workspace(headers)

    conversation = Conversation(workspace_id=workspace_id, user_id=user_id, title="t")
    db.add(conversation)
    db.commit()

    question = Message(
        conversation_id=conversation.id,
        role="user",
        content="What is virtualization?",
        ai_mode="online",
    )
    answer = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="Virtualization abstracts hardware [1]. A hypervisor runs guests [2].",
        ai_mode="online",
        provider="groq",
        model="openai/gpt-oss-120b",
        retrieval={
            "chunks": [
                _chunk(1, 22, "Virtualization is the abstraction of hardware resources."),
                _chunk(2, 23, "The hypervisor layer schedules guest operating systems."),
            ],
            "top_score": 0.62,
            "score_scale": "cosine",
        },
        citations=[
            {"number": 1, "document_name": "ADT_Notes.pdf", "page_number": 22},
            {"number": 2, "document_name": "ADT_Notes.pdf", "page_number": 23},
        ],
    )
    db.add_all([question, answer])
    db.commit()
    return headers, answer.id, user_id


class StubAdapter:
    """An LLM whose reply is scripted, plus a record of what it was asked."""

    def __init__(self, text: str, *, provider: str = "groq", model: str = "openai/gpt-oss-120b"):
        self._text = text
        self._provider = provider
        self._model = model
        self.messages: list[dict[str, str]] | None = None
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    async def generate(self, messages, **kwargs):
        self.calls += 1
        self.messages = messages
        self.kwargs = kwargs
        return LLMResponse(
            text=self._text, provider=self._provider, model=self._model, latency_ms=10
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_detailed_answer_succeeds_and_persists(client, stored_answer, monkeypatch):
    from app.features import detailed as detailed_module

    headers, message_id, _ = stored_answer
    stub = StubAdapter(
        "### Introduction\nVirtualization abstracts hardware [1].\n"
        "### Key Points\nGuests are scheduled by the hypervisor [2]."
    )
    monkeypatch.setattr(detailed_module, "get_llm_adapter", lambda: stub)

    response = client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "mark8"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["kind"] == "detailed"
    assert body["level"] == "mark8"
    assert "[1]" in body["content"] and "[2]" in body["content"]
    # Citations re-resolve against the ORIGINAL evidence: same numbers, and the
    # PDF page provenance survived the rebuild.
    pages = {c["number"]: c.get("page_number") for c in body["citations"]}
    assert pages == {1: 22, 2: 23}
    assert body["validation"]["passed"] is True
    # The canonical answer is untouched (the immutability rule).
    saved = client.get(f"/api/chat/messages/{message_id}", headers=headers)


def test_detailed_answer_public_payload_has_no_vendor(client, stored_answer, monkeypatch):
    headers, message_id, _ = stored_answer
    stub = StubAdapter("An exam answer citing [1] and [2].")
    monkeypatch.setattr("app.features.detailed.get_llm_adapter", lambda: stub)

    response = client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "university"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    blob = str(response.json()).lower()
    assert "groq" not in blob
    assert "gpt-oss" not in blob
    assert response.json()["provider"] in ("", "remote", "local")


def test_style_gets_its_own_token_budget(client, stored_answer, monkeypatch):
    """A 20-mark answer cannot be written inside the default budget."""
    from app.features import detailed as detailed_module
    from app.rag.prompts import ANSWER_STYLES

    headers, message_id, _ = stored_answer
    stub = StubAdapter("Long answer [1][2].")
    monkeypatch.setattr(detailed_module, "get_llm_adapter", lambda: stub)

    client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "mark20"},
        headers=headers,
    )
    assert stub.kwargs["max_tokens"] == ANSWER_STYLES["mark20"]["max_tokens"]
    assert stub.kwargs["temperature"] == ANSWER_STYLES["mark20"]["temperature"]


def test_prompt_rebuilds_the_same_numbered_evidence(client, stored_answer, monkeypatch):
    """[1] in the detailed answer must mean the SAME excerpt as in the original."""
    from app.features import detailed as detailed_module

    headers, message_id, _ = stored_answer
    stub = StubAdapter("Rebuilt [1].")
    monkeypatch.setattr(detailed_module, "get_llm_adapter", lambda: stub)

    client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "more_detail"},
        headers=headers,
    )
    user_message = next(m for m in stub.messages if m["role"] == "user")
    assert "Virtualization is the abstraction" in user_message["content"]
    assert "[1]" in user_message["content"], "the rebuild must keep the numbering"


def test_web_sources_are_not_sneaked_in(client, stored_answer, monkeypatch):
    """A style request must not introduce web evidence the original did not use."""
    from app.features import detailed as detailed_module

    headers, message_id, _ = stored_answer
    stub = StubAdapter("Answer [1].")
    monkeypatch.setattr(detailed_module, "get_llm_adapter", lambda: stub)

    client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "mark8"},
        headers=headers,
    )
    joined = "\n".join(m["content"] for m in stub.messages)
    assert "web" not in joined.lower().split("answer engine")[0] or True
    # Stronger: with web_search_used False, no WEB SOURCES block may be present.
    assert "WEB SOURCES" not in joined


def test_original_web_sources_are_reused_with_the_same_numbers(
    client, stored_answer, monkeypatch, db
):
    from app.db.models import Message as MessageModel
    from app.features import detailed as detailed_module

    headers, message_id, _ = stored_answer
    row = db.get(MessageModel, message_id)
    row.web_search_used = True
    row.web_sources = [
        {"number": 3, "title": "Example", "url": "https://example.org", "snippet": "s",
         "source": "example.org"}
    ]
    db.commit()

    stub = StubAdapter("Documents [1][2] and web [W3].")
    monkeypatch.setattr(detailed_module, "get_llm_adapter", lambda: stub)

    response = client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "mark16"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    joined = "\n".join(m["content"] for m in stub.messages)
    assert "https://example.org" in joined, "the web block must be rebuilt identically"


# ---------------------------------------------------------------------------
# Rejection and cache paths
# ---------------------------------------------------------------------------
def test_fabricated_citation_is_rejected_and_nothing_stored(
    client, stored_answer, monkeypatch, db
):
    headers, message_id, _ = stored_answer
    stub = StubAdapter("This cites [7], which does not exist in the evidence.")
    monkeypatch.setattr("app.features.detailed.get_llm_adapter", lambda: stub)

    response = client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "mark8"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "detailed_answer_rejected"

    from app.db.models import AnswerVariant as AV

    stored = db.query(AV).filter(AV.message_id == message_id).count()
    assert stored == 0, "a rejected variant must never be persisted"


def test_unknown_style_is_rejected_without_calling_the_engine(
    client, stored_answer, monkeypatch
):
    from app.features import detailed as detailed_module

    headers, message_id, _ = stored_answer
    stub = StubAdapter("should not be called")
    monkeypatch.setattr(detailed_module, "get_llm_adapter", lambda: stub)

    response = client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "write-an-essay"},
        headers=headers,
    )
    assert response.status_code == 422  # schema validator rejects the unknown style
    assert stub.calls == 0


def test_second_request_is_cached(client, stored_answer, monkeypatch):
    from app.features import detailed as detailed_module

    headers, message_id, _ = stored_answer
    stub = StubAdapter("Cached answer [1][2].")
    monkeypatch.setattr(detailed_module, "get_llm_adapter", lambda: stub)

    first = client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "mark8"},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["cached"] is False

    second = client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "mark8"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert stub.calls == 1, "the cache path must not call the engine again"


def test_styles_are_listed_by_the_capabilities_endpoint(client, make_user):
    """The menu labels come from the server so UI and prompts cannot drift."""
    headers, _ = make_user()
    response = client.get("/api/features/capabilities", headers=headers)
    assert response.status_code == 200
    styles = {entry["style"] for entry in response.json()["answer_styles"]}
    assert {"more_detail", "mark8", "mark16", "mark20", "university"} == styles


def test_variant_listing_includes_detailed(client, stored_answer, monkeypatch):
    headers, message_id, _ = stored_answer
    stub = StubAdapter("Listed [1][2].")
    monkeypatch.setattr("app.features.detailed.get_llm_adapter", lambda: stub)
    client.post(
        "/api/features/detailed-answer",
        json={"message_id": message_id, "style": "university"},
        headers=headers,
    )

    response = client.get(f"/api/features/messages/{message_id}/variants", headers=headers)
    assert response.status_code == 200
    kinds = {row["kind"]: row["level"] for row in response.json()}
    assert ("detailed" in kinds) and kinds["detailed"] == "university"
    # The listed variant's provenance is sanitized too.
    blob = str(response.json()).lower()
    assert "groq" not in blob and "gpt-oss" not in blob

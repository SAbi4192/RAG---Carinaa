"""Chat-scoped documents.

The feature exists so a user can say "answer only from PDF 3". Two properties
matter, and both are tested here:

1. **The scope is enforced at retrieval time, not in the prompt.** A prompt-level
   instruction is a request a model may ignore; a filter applied before vector
   search cannot be ignored. The cross-contamination tests below are the point of
   the whole feature.

2. **Attaching and detaching are references, never copies or deletions.** The same
   document can be attached to several conversations, and removing it from a chat
   must leave it untouched in the workspace. A user who thinks they deleted a file
   and did not - or thinks they did not and did - has been misled either way.
"""

from __future__ import annotations

import io
import time

import pytest


def _ingest(client, headers, workspace_id: int, filename: str, text: str) -> int:
    """Upload and wait for ingestion. Returns the document id."""
    response = client.post(
        f"/api/workspaces/{workspace_id}/documents",
        headers=headers,
        files={"file": (filename, io.BytesIO(text.encode()), "text/markdown")},
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["document"]["id"]

    deadline = time.time() + 180
    while time.time() < deadline:
        progress = client.get(f"/api/documents/{document_id}/progress", headers=headers)
        if progress.json().get("status") in ("ready", "failed"):
            break
        time.sleep(0.4)
    assert progress.json()["status"] == "ready", progress.text
    return document_id


@pytest.fixture()
def scoped_setup(client, make_user, make_workspace):
    """A workspace with two clearly unrelated documents, and one conversation."""
    headers, _ = make_user()
    workspace_id = make_workspace(headers, name="Scope workspace")

    biology = _ingest(
        client,
        headers,
        workspace_id,
        "biology.md",
        "# Photosynthesis\n"
        "Photosynthesis converts light energy into chemical energy in plants. "
        "Chlorophyll absorbs light in the thylakoid membrane, and the Calvin cycle "
        "fixes carbon dioxide into glucose using ATP and NADPH.\n",
    )
    cloud = _ingest(
        client,
        headers,
        workspace_id,
        "cloud.md",
        "# Virtualization\n"
        "A hypervisor creates and runs virtual machines on a single physical host. "
        "Virtualization abstracts hardware so many workloads share one server, which "
        "improves utilisation and reduces cost.\n",
    )

    conversation = client.post(
        "/api/conversations",
        headers=headers,
        json={"workspace_id": workspace_id, "title": "Scope test"},
    ).json()["id"]

    return {
        "headers": headers,
        "workspace_id": workspace_id,
        "conversation_id": conversation,
        "biology": biology,
        "cloud": cloud,
    }


# ---------------------------------------------------------------------------
# Scope state
# ---------------------------------------------------------------------------
def test_new_conversation_has_no_scope_and_falls_back_to_the_workspace(client, scoped_setup):
    """No attachments must mean "search everything", not "search nothing".

    Forcing the user to attach a file before asking anything would break the
    general knowledge-base use of the product.
    """
    response = client.get(
        f"/api/conversations/{scoped_setup['conversation_id']}/documents",
        headers=scoped_setup["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "workspace"
    assert body["active_document_ids"] == []
    assert body["documents"] == []
    assert "whole workspace" in body["note"]


def test_attaching_switches_the_scope_to_chat(client, scoped_setup):
    response = client.post(
        f"/api/conversations/{scoped_setup['conversation_id']}/documents",
        headers=scoped_setup["headers"],
        json={"document_id": scoped_setup["biology"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["scope"] == "chat"
    assert body["active_document_ids"] == [scoped_setup["biology"]]
    assert body["documents"][0]["original_filename"] == "biology.md"


def test_attaching_twice_is_a_no_op_not_an_error(client, scoped_setup):
    """A repeat click on "attach" should not fail; the intent is already met."""
    url = f"/api/conversations/{scoped_setup['conversation_id']}/documents"
    first = client.post(
        url, headers=scoped_setup["headers"], json={"document_id": scoped_setup["biology"]}
    )
    second = client.post(
        url, headers=scoped_setup["headers"], json={"document_id": scoped_setup["biology"]}
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert len(second.json()["documents"]) == 1


def test_deactivating_keeps_the_document_attached_but_out_of_scope(client, scoped_setup):
    base = f"/api/conversations/{scoped_setup['conversation_id']}/documents"
    client.post(base, headers=scoped_setup["headers"], json={"document_id": scoped_setup["biology"]})
    client.post(base, headers=scoped_setup["headers"], json={"document_id": scoped_setup["cloud"]})

    response = client.patch(
        f"{base}/{scoped_setup['cloud']}",
        headers=scoped_setup["headers"],
        json={"is_active": False},
    )
    body = response.json()
    assert body["active_document_ids"] == [scoped_setup["biology"]]
    # Still attached, just not searched - the distinction the UI must not blur.
    assert len(body["documents"]) == 2
    assert {d["document_id"] for d in body["documents"] if not d["is_active"]} == {
        scoped_setup["cloud"]
    }


def test_detaching_returns_to_workspace_scope_and_deletes_nothing(client, scoped_setup):
    base = f"/api/conversations/{scoped_setup['conversation_id']}/documents"
    client.post(base, headers=scoped_setup["headers"], json={"document_id": scoped_setup["biology"]})

    response = client.delete(f"{base}/{scoped_setup['biology']}", headers=scoped_setup["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "workspace"
    assert body["removed_from_chat_only"] is True

    # THE POINT: the document is still in the workspace.
    documents = client.get(
        f"/api/workspaces/{scoped_setup['workspace_id']}/documents",
        headers=scoped_setup["headers"],
    ).json()["documents"]
    names = {d["original_filename"] for d in documents}
    assert "biology.md" in names, "detaching from a chat must not delete the document"


def test_cannot_attach_a_document_from_another_workspace(client, scoped_setup, make_user, make_workspace):
    """Scope must not become a way to reach another workspace's documents."""
    other_headers, _ = make_user("Other User")
    other_workspace = make_workspace(other_headers, name="Other workspace")
    other_document = _ingest(
        client, other_headers, other_workspace, "secret.md", "# Secret\nPrivate notes.\n"
    )

    response = client.post(
        f"/api/conversations/{scoped_setup['conversation_id']}/documents",
        headers=scoped_setup["headers"],
        json={"document_id": other_document},
    )
    assert response.status_code == 404


def test_scope_endpoints_require_authentication(client, scoped_setup):
    url = f"/api/conversations/{scoped_setup['conversation_id']}/documents"
    assert client.get(url).status_code == 401
    assert client.post(url, json={"document_id": 1}).status_code == 401


# ---------------------------------------------------------------------------
# The scope is actually applied to retrieval
# ---------------------------------------------------------------------------
def test_retrieval_is_limited_to_the_active_scope(client, scoped_setup):
    """Cross-contamination guard: an out-of-scope document must not appear."""
    client.post(
        f"/api/conversations/{scoped_setup['conversation_id']}/documents",
        headers=scoped_setup["headers"],
        json={"document_id": scoped_setup["biology"]},
    )

    response = client.post(
        "/api/chat/retrieve",
        headers=scoped_setup["headers"],
        json={
            "workspace_id": scoped_setup["workspace_id"],
            "conversation_id": scoped_setup["conversation_id"],
            "question": "How does photosynthesis work?",
        },
    )
    assert response.status_code == 200
    chunks = response.json()["retrieval"].get("chunks", [])
    assert chunks, "expected the scoped document to match"
    assert {c["document_name"] for c in chunks} == {"biology.md"}


def test_excluding_a_document_removes_it_from_retrieval(client, scoped_setup):
    base = f"/api/conversations/{scoped_setup['conversation_id']}/documents"
    client.post(base, headers=scoped_setup["headers"], json={"document_id": scoped_setup["biology"]})
    client.post(base, headers=scoped_setup["headers"], json={"document_id": scoped_setup["cloud"]})
    client.patch(
        f"{base}/{scoped_setup['cloud']}",
        headers=scoped_setup["headers"],
        json={"is_active": False},
    )

    response = client.post(
        "/api/chat/retrieve",
        headers=scoped_setup["headers"],
        json={
            "workspace_id": scoped_setup["workspace_id"],
            "conversation_id": scoped_setup["conversation_id"],
            "question": "What is a hypervisor and how does virtualization work?",
        },
    )
    chunks = response.json()["retrieval"].get("chunks", [])
    names = {c["document_name"] for c in chunks}
    assert "cloud.md" not in names, "a deactivated document must not be retrieved"
    assert names <= {"biology.md"}


def test_no_scope_searches_the_whole_workspace(client, scoped_setup):
    response = client.post(
        "/api/chat/retrieve",
        headers=scoped_setup["headers"],
        json={
            "workspace_id": scoped_setup["workspace_id"],
            "question": "What is a hypervisor and how does photosynthesis work?",
        },
    )
    names = {c["document_name"] for c in response.json()["retrieval"].get("chunks", [])}
    assert len(names) >= 1, "workspace fallback must return something"

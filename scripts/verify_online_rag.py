"""Verify the three headline RAG features end to end against a live server.

Kept as a file because the project path contains '&', which bash mis-parses.

Usage:
    .venv/Scripts/python.exe scripts/verify_online_rag.py

Checks:
  1. unit -> section index filter ("UNIT III" returns only UNIT III)
  2. page filter ("what is on page 5")
  3. hybrid conversation memory (name recalled, and document retrieval not displaced)
"""

from __future__ import annotations

import time
import uuid

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from e2e_test import Client  # noqa: E402

BASE = "http://127.0.0.1:8000"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")


def main() -> int:
    client = Client(BASE)
    token = client.request(
        "POST",
        "/api/auth/register",
        json_body={
            "email": f"rag_{uuid.uuid4().hex[:8]}@carinaa-e2e.dev",
            "password": "TestPass123",
            "display_name": "RAG Verify",
        },
    )[1]["access_token"]
    client.token = token

    workspace = client.request("POST", "/api/workspaces", json_body={"name": "RAG Verify"})[1]["id"]
    document = client.upload(
        f"/api/workspaces/{workspace}/documents",
        "test_manual.pdf",
        (PROJECT_ROOT / "data" / "test_manual.pdf").read_bytes(),
    )[1]["document"]["id"]

    for _ in range(300):
        status = client.request("GET", f"/api/documents/{document}/progress")[1].get("status")
        if status in ("ready", "failed"):
            break
        time.sleep(0.4)

    chunks = client.request("GET", f"/api/documents/{document}")[1].get("chunk_count", 0)
    print(f"\nFixture: workspace {workspace}, document {document}, {chunks} chunks.\n")

    # ---- 1. unit -> section index filter ---------------------------------
    print("1. Unit references resolve to their section and filter correctly")
    for query, expected_prefix in (
        ("What is the name of UNIT III?", "UNIT III"),
        ("What does the third unit say?", "UNIT III"),
        ("What is the name of UNIT IV?", "UNIT IV"),
    ):
        status, body = client.request(
            "POST",
            "/api/chat/retrieve",
            json_body={"workspace_id": workspace, "question": query},
        )
        sections = sorted(
            {
                (chunk.get("metadata") or {}).get("section", "")
                for chunk in (body.get("retrieval") or {}).get("chunks", [])
            }
        )
        # Every returned chunk must be in the requested unit. Compare on a prefix,
        # because the stored title includes the rest of the heading.

        ok = status == 200 and bool(sections) and all(section.startswith(expected_prefix) for section in sections)
        check(f"{query!r} -> only {expected_prefix}", ok, f"sections={[s[:30] for s in sections]}")

    # ---- 2. page filter --------------------------------------------------
    print("\n2. Page references filter retrieval")
    status, body = client.request(
        "POST",
        "/api/chat/retrieve",
        json_body={"workspace_id": workspace, "question": "Tell me what is on page number 3"},
    )
    retrieved = (body.get("retrieval") or {}).get("chunks", [])
    page_starts = sorted(
        {
            (chunk.get("metadata") or {}).get("page_number")
            for chunk in retrieved
        }
    )
    covers_page_3 = bool(retrieved) and all(
        (chunk.get("metadata") or {}).get("page_number", 0) <= 3
        <= ((chunk.get("metadata") or {}).get("page_end")
            or (chunk.get("metadata") or {}).get("page_number", 0))
        for chunk in retrieved
    )
    check(
        "page 3 query returns only chunks covering page 3",
        status == 200 and covers_page_3,
        f"page starts={page_starts}",
    )

    # ---- 3. hybrid memory -----------------------------------------------
    print("\n3. Conversation memory (hybrid providers)")
    status, first = client.request(
        "POST",
        "/api/chat/ask",
        json_body={
            "workspace_id": workspace,
            "question": "My name is Abishek and I study design thinking.",
            "mode": "online",
            "language": "en",
        },
        timeout=900,
    )
    check("introducing message answered", status == 200, f"HTTP {status} provider={first.get('provider_label')}")

    # Both turns MUST share one conversation, or there is no history to recall from.
    # Passing only workspace_id would silently create a new conversation per turn and
    # the recall check would fail for the wrong reason (a test bug, not a product bug).

    conversation_id = (first or {}).get("conversation_id")
    check("conversation id returned", bool(conversation_id), f"conversation={conversation_id}")

    status, second = client.request(
        "POST",
        "/api/chat/ask",
        json_body={
            "workspace_id": workspace,
            "conversation_id": conversation_id,
            "question": "What is my name?",
            "mode": "online",
            "language": "en",
        },
        timeout=900,
    )
    answer = (second or {}).get("answer", "")
    check(
        "name recalled from the conversation",
        status == 200 and "abishek" in answer.lower(),
        f"HTTP {status} provider={(second or {}).get('provider_label')} answer={answer[:90]!r}",
    )

    # Document retrieval must still work alongside memory.
    status, doc = client.request(
        "POST",
        "/api/chat/ask",
        json_body={
            "workspace_id": workspace,
            "question": "What does UNIT III - CONCEPT GENERATION say?",
            "mode": "online",
            "language": "en",
        },
        timeout=900,
    )
    citations = (doc or {}).get("citations", []) or []
    check(
        "document retrieval not displaced by memory",
        status == 200 and len(citations) > 0,
        f"HTTP {status} citations={len(citations)} provider={(doc or {}).get('provider_label')}",
    )

    passed = sum(1 for _, ok, _ in results if ok)

    print("\n" + "=" * 70)
    print(f"RESULT: {passed} passed, {len(results) - passed} failed, {len(results)} checks")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

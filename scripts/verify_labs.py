"""Verify that every laboratory bench has working data behind it.

The labs are frontend components, so they cannot be unit-tested from here. What CAN
be verified is the thing that actually matters: that each bench's backend call
returns real, correctly-shaped data. A bench with no working endpoint behind it is a
mock-up, and a mock-up in a teaching tool is worse than nothing.

This walks the seven benches in the order the dashboard presents them and asserts
the properties each one depends on:

    1. Chunking      the real chunker responds to size and overlap
    2. Embeddings    vectors, similarity, projection, query ranking
    3. Vector store  documents and their stored chunks
    4. Retrieval     scored candidates, no model called
    5. Context       a numbered evidence block with a budget
    6. Generation    an answer with grounding and citations
    7. Full pipeline a trace with real stages and durations

Usage:
    .venv/Scripts/python.exe scripts/verify_labs.py
    .venv/Scripts/python.exe scripts/verify_labs.py --mode offline
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from e2e_test import Client  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    results.append((name, bool(condition), detail))
    print(f"  [{PASS if condition else FAIL}] {name}")
    if detail:
        print(f"           {detail}")
    return bool(condition)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", default="online", choices=["online", "offline"])
    parser.add_argument("--document", default="samples/cloud_computing_notes.md")
    args = parser.parse_args()

    client = Client(args.base_url)
    run_id = uuid.uuid4().hex[:8]

    print("=" * 78)
    print(f"RAG Laboratory verification   (mode={args.mode})")
    print("=" * 78)

    # ---- setup ---------------------------------------------------------
    status, body = client.request(
        "POST",
        "/api/auth/register",
        json_body={
            "email": f"lab_{run_id}@carinaa-e2e.dev",
            "password": "TestPass123",
            "display_name": "Lab Check",
        },
    )
    if status != 201:
        print(f"FAIL could not register: HTTP {status} {body}")
        return 1
    client.token = body["access_token"]

    _, workspace = client.request("POST", "/api/workspaces", json_body={"name": f"Lab {run_id}"})
    workspace_id = workspace["id"]

    document_path = PROJECT_ROOT / args.document
    status, uploaded = client.upload(
        f"/api/workspaces/{workspace_id}/documents",
        document_path.name,
        document_path.read_bytes(),
    )
    if status != 201:
        print(f"FAIL could not upload: HTTP {status} {uploaded}")
        return 1
    document_id = uploaded["document"]["id"]

    deadline = time.time() + 240
    progress: dict = {}
    while time.time() < deadline:
        _, progress = client.request("GET", f"/api/documents/{document_id}/progress")
        if progress.get("status") in ("ready", "failed"):
            break
        time.sleep(0.6)
    print(f"  setup: workspace {workspace_id}, document {document_id} ({progress.get('status')})\n")

    # ---- 1. Chunking ---------------------------------------------------
    print("1. Chunking bench")
    sample = (PROJECT_ROOT / args.document).read_text(encoding="utf-8")

    sizes = [(300, 40), (700, 100), (1400, 200)]
    counts: list[int] = []
    for size, overlap in sizes:
        status, out = client.request(
            "POST",
            "/api/documents/preview-chunks",
            json_body={"text": sample, "chunk_size": size, "chunk_overlap": overlap},
        )
        if status != 200:
            check(f"preview-chunks at size={size}", False, f"HTTP {status}")
            continue
        counts.append(out.get("count", 0))
        first = (out.get("chunks") or [{}])[0]
        check(
            f"chunker responds at size={size}, overlap={overlap}",
            out.get("count", 0) > 0
            and first.get("characters", 0) > 0
            and "overlap_blocks" in first,
            f"{out.get('count')} chunks, first is {first.get('characters')} chars, "
            f"{first.get('tokens_estimated')} tokens",
        )

    check(
        "smaller chunks produce more chunks",
        len(counts) >= 2 and counts[0] >= counts[-1],
        f"counts for sizes {[s for s, _ in sizes]}: {counts}",
    )

    # ---- 2. Embeddings -------------------------------------------------
    print("\n2. Embedding bench")
    status, embed = client.request(
        "POST",
        "/api/labs/embed",
        json_body={
            "texts": [
                "Virtualization lets one physical server host many virtual machines.",
                "A hypervisor creates and runs virtual machines.",
                "Photosynthesis converts light energy into chemical energy.",
            ],
            "query": "How do hypervisors work?",
        },
    )
    if status != 200:
        check("labs/embed", False, f"HTTP {status}: {json.dumps(embed)[:200]}")
    else:
        check(
            "vectors returned, truncated for display and honest about it",
            len(embed["vectors"]) == 3
            and len(embed["vectors"][0]) == embed["preview_dimensions"]
            and embed["preview_dimensions"] <= embed["dimensions"],
            f"{embed['dimensions']} dims, showing {embed['preview_dimensions']}, "
            f"model {embed['model'].split('/')[-1]}",
        )
        check(
            "norms are 1.0 so cosine is a dot product",
            all(abs(n - 1.0) < 1e-3 for n in embed["norms"]),
            f"norms={embed['norms']}",
        )
        matrix = embed["similarity"]
        check(
            "similarity matrix is symmetric with a unit diagonal",
            all(abs(matrix[i][i] - 1.0) < 1e-3 for i in range(3))
            and all(abs(matrix[i][j] - matrix[j][i]) < 1e-4 for i in range(3) for j in range(3)),
            "diagonal 1.000, off-diagonal mirrored",
        )
        check(
            "related texts score above unrelated ones",
            matrix[0][1] > matrix[0][2],
            f"virtualization pair {matrix[0][1]:.3f} > virtualization/photosynthesis {matrix[0][2]:.3f}",
        )
        check(
            "projection is labelled as a projection, with its variance reported",
            "not" in embed["projection_note"].lower()
            and len(embed["projection"]) == 3
            and sum(embed["projection_explained_variance"]) <= 1.0001,
            f"explains {embed['projection_explained_variance']} of the variance",
        )
        ranking = (embed.get("query") or {}).get("ranking", [])
        check(
            "query ranking orders by score and finds the right text",
            len(ranking) == 3
            and ranking[0]["score"] >= ranking[-1]["score"]
            and ranking[0]["index"] == 1,
            f"top is text {ranking[0]['index'] + 1} at {ranking[0]['score']:.3f}"
            if ranking
            else "no ranking",
        )

    # ---- 3. Vector store -----------------------------------------------
    print("\n3. Vector store bench")
    status, documents = client.request("GET", f"/api/workspaces/{workspace_id}/documents")
    docs = documents.get("documents", []) if status == 200 else []
    check(
        "documents list shows what is indexed",
        status == 200 and len(docs) == 1 and docs[0]["chunk_count"] > 0,
        f"{len(docs)} document(s), {docs[0]['chunk_count'] if docs else 0} chunks",
    )

    status, chunks = client.request("GET", f"/api/documents/{document_id}/chunks?limit=20")
    check(
        "stored chunks are readable with provenance",
        status == 200 and len(chunks) > 0 and bool(chunks[0].get("doc_metadata")),
        f"{len(chunks)} chunks, first has document_name="
        f"{chunks[0]['doc_metadata'].get('document_name') if chunks else '—'}",
    )

    status, stats = client.request("GET", f"/api/documents/{document_id}/chunk-stats")
    check(
        "chunk statistics are computed",
        status == 200 and stats.get("count", 0) > 0 and "characters" in stats,
        f"count={stats.get('count')}, mean={stats.get('characters', {}).get('mean')} chars",
    )

    # ---- 4. Retrieval --------------------------------------------------
    print("\n4. Retrieval bench")
    question = "How does virtualization improve resource utilization, and what is a hypervisor?"
    status, retrieved = client.request(
        "POST",
        "/api/chat/retrieve",
        json_body={"workspace_id": workspace_id, "question": question},
    )
    retrieval = retrieved.get("retrieval", {}) if status == 200 else {}
    check(
        "retrieval returns scored candidates without calling a model",
        status == 200
        and retrieval.get("returned", 0) > 0
        and retrieved.get("generated") is False,
        f"returned={retrieval.get('returned')}, top_score={retrieval.get('top_score')}, "
        f"generated={retrieved.get('generated')}",
    )

    # ---- 5-7. Context, Generation, Pipeline ----------------------------
    print("\n5-7. Context, Generation and Pipeline benches")
    status, answer = client.request(
        "POST",
        "/api/chat/ask",
        json_body={"workspace_id": workspace_id, "question": question, "mode": args.mode},
        timeout=900.0,
    )
    if status != 200:
        check("ask the pipeline", False, f"HTTP {status}: {json.dumps(answer)[:250]}")
    else:
        context = answer.get("context") or {}
        excerpts = context.get("excerpts") or []
        check(
            "context bench: numbered evidence block with a budget",
            context.get("excerpt_count", 0) > 0
            and len(excerpts) > 0
            and context.get("budget", 0) > 0
            and all("number" in e and "content" in e and "label" in e for e in excerpts),
            f"{context.get('excerpt_count')} excerpts, "
            f"{context.get('characters')}/{context.get('budget')} chars, "
            f"dropped={context.get('dropped')}",
        )
        check(
            "context bench: excerpts are numbered from 1 with no gaps",
            [e["number"] for e in excerpts] == list(range(1, len(excerpts) + 1)),
            f"numbers={[e['number'] for e in excerpts]}",
        )
        check(
            "generation bench: an answer with a provider label",
            bool(answer.get("answer")) and bool(answer.get("provider_label")),
            f"{len(answer.get('answer', ''))} chars via {answer.get('provider_label')}",
        )
        check(
            "generation bench: grounding verdict and citations present",
            isinstance(answer.get("grounding"), dict)
            and "status" in answer["grounding"]
            and isinstance(answer.get("citations"), list),
            f"grounding={answer['grounding'].get('status')}, "
            f"{len(answer.get('citations', []))} citation(s)",
        )

        trace = answer.get("trace") or {}
        stages = trace.get("stages") or []
        check(
            "pipeline bench: a trace with real stages and durations",
            len(stages) >= 6
            and all("status" in s and "duration_ms" in s for s in stages)
            and trace.get("total_ms", 0) > 0,
            f"{len(stages)} stages, {trace.get('total_ms')} ms total: "
            + ", ".join(f"{s['stage']}={s['status']}" for s in stages[:5]),
        )
        check(
            "pipeline bench: skipped stages are marked, not faked",
            all(s["status"] in ("ok", "skipped", "error") for s in stages),
            "statuses: " + ", ".join(sorted({s["status"] for s in stages})),
        )
        # Executed stages, de-duplicated in order of first appearance.
        #
        # A stage can legitimately appear more than once: when the primary provider
        # fails and the fallback answers, `llm_generation` is recorded twice - once
        # with status=error for the failed attempt and once with status=ok for the
        # one that worked. That duplication IS the fallback disclosure, so the check
        # must tolerate it rather than treat it as a fault.
        executed: list[str] = []
        for stage in stages:
            if stage["status"] != "skipped" and stage["stage"] not in executed:
                executed.append(stage["stage"])

        canonical = [
            "query_analysis",
            "query_embedding",
            "vector_search",
            "candidate_retrieval",
            "reranking",
            "context_building",
            "llm_generation",
            "citation_resolution",
            "grounding",
        ]
        expected = [stage for stage in canonical if stage in set(executed)]
        check(
            "pipeline bench: executed stages follow the canonical order",
            executed == expected,
            f"executed={executed}",
        )

        # Wall-clock timestamps: the "Live RAG Trace" reads the run as a log, which
        # needs WHEN a stage finished, not only how long it took. Every stage in a
        # real run must be stamped, and the stamps must be distinct - if they all
        # shared one second, the persistence would still be using the INSERT-time
        # default and the log would be a lie.
        stamps = [s.get("created_at") for s in stages]
        check(
            "pipeline bench: every stage carries a real completion timestamp",
            all(stamps) and len(set(stamps)) == len(stamps),
            f"{len(stamps)} stages, {len(set(stamps))} distinct timestamps "
            f"({stamps[0][11:23] if stamps[0] else '—'} → "
            f"{stamps[-1][11:23] if stamps[-1] else '—'})",
        )
        retried = len([s for s in stages if s["stage"] == "llm_generation"]) > 1
        if retried:
            failed_attempt = next(
                (s for s in stages if s["stage"] == "llm_generation" and s["status"] == "error"),
                None,
            )
            check(
                "pipeline bench: a provider retry is recorded, not hidden",
                failed_attempt is not None,
                f"llm_generation recorded twice; the failed attempt reports "
                f"{failed_attempt['data'].get('error') if failed_attempt else '—'}",
            )

    # ---- summary -------------------------------------------------------
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print("\n" + "=" * 78)
    print(f"RESULT: {passed} passed, {failed} failed, {len(results)} checks")
    print("=" * 78)
    if failed:
        print("\nFailures:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}\n      {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

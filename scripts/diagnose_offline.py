"""Diagnose why the local 3B model refuses questions it should be able to answer.

The observation: with the SAME retrieved chunks, Groq (a large model) answers
"Tell me about the PDF" in detail, while Qwen2.5-3B replies

    "I could not find this in the documents in this workspace."

Retrieval is identical in both modes - embeddings and the vector store are local
either way. Only the model differs. So the question is whether the *prompt* is
built for a model that cannot handle it.

This script runs the real prompt and a small-model-oriented prompt over the same
context and compares, so the conclusion is measured rather than assumed.

Usage:
    .venv/Scripts/python.exe scripts/diagnose_offline.py
    .venv/Scripts/python.exe scripts/diagnose_offline.py --workspace 21 --question "Tell me about the PDF"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", type=int, default=7, help="PDF document to draw chunks from")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=400)
    args = parser.parse_args()

    from sqlalchemy import select

    from app.core.config import settings
    from app.db.models import Chunk
    from app.db.session import SessionLocal
    from app.llm.local import LocalProvider
    from app.rag.prompts import build_generation_messages

    print("=" * 74)
    print("Offline diagnosis: production prompt vs small-model prompt")
    print("=" * 74)

    provider = LocalProvider()
    print(f"  model      : {settings.local_model_label}")
    print(f"  n_ctx      : {provider.n_ctx}")

    if not provider.load():
        print(f"FAIL the local model could not be loaded: {provider._load_error}")
        return 1

    # --- build a realistic context from the real document -------------------
    with SessionLocal() as db:
        chunks = list(
            db.scalars(
                select(Chunk).where(Chunk.document_id == args.document_id).limit(args.top_k)
            ).all()
        )
    if not chunks:
        print(f"FAIL no chunks for document {args.document_id}")
        return 1

    excerpts = [
        {
            "number": index + 1,
            "label": str(
                (c.doc_metadata or {}).get("document_name")
                or (c.doc_metadata or {}).get("section")
                or "document"
            ),
            "content": c.content,
            "chunk_id": c.id,
            "document_id": c.document_id,
        }
        for index, c in enumerate(chunks)
    ]
    total_chars = sum(len(e["content"]) for e in excerpts)
    print(f"  chunks     : {len(excerpts)} ({total_chars} chars of context)")
    print()

    def run(label: str, messages: list[dict[str, str]]) -> str:
        prompt_chars = sum(len(m["content"]) for m in messages)
        print(f"    {label} (prompt {prompt_chars} chars)")
        started = time.time()
        response = provider._complete(messages, 0.2, args.max_tokens)
        text = str(response.get("text", "")).strip()
        print(f"      {time.time() - started:.1f}s finish={response.get('finish_reason')}")
        print("      " + text[:520].replace("\n", "\n      "))
        print()
        return text

    # Two questions, because a prompt that answers everything is as broken as one
    # that refuses everything. The second MUST still be refused: the document does
    # not mention any such person, and inventing one would be a fabrication.
    cases = [
        ("ANSWERABLE  ", "Tell me about the PDF"),
        ("UNANSWERABLE", "Who is Donald Triumph?"),
    ]

    outcomes: dict[str, dict[str, bool]] = {}

    for kind, question in cases:
        print("-" * 74)
        print(f"{kind}: {question!r}")
        print("-" * 74)
        production = run(
            "production",
            build_generation_messages(question, excerpts, mode="online"),
        )
        offline = run(
            "offline   ",
            build_generation_messages(question, excerpts, mode="offline"),
        )
        outcomes[question] = {
            "production_refused": "could not find this in the documents" in production.lower(),
            "offline_refused": "could not find this in the documents" in offline.lower(),
            "production_len": len(production),
            "offline_len": len(offline),
        }

    print("=" * 74)
    print("RESULTS")
    print("=" * 74)
    for question, result in outcomes.items():
        print(f"  {question!r}")
        for key, value in result.items():
            print(f"      {key:20} = {value}")
    print()

    answerable = outcomes[cases[0][1]]
    unanswerable = outcomes[cases[1][1]]

    ok = True
    if answerable["offline_refused"]:
        print("FAIL the offline prompt still refuses an answerable question")
        ok = False
    else:
        print("PASS offline prompt answers the answerable question")

    if not unanswerable["offline_refused"]:
        print("FAIL the offline prompt answered a question the documents do not cover")
        print("     (that is a fabrication risk - the refusal must survive)")
        ok = False
    else:
        print("PASS offline prompt still refuses the unanswerable question")

    print("=" * 74)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

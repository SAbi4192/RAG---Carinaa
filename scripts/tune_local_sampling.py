"""Measure how the repeat penalty affects local translation quality.

The local 3B model degenerated when translating a 552-character answer into Tamil:
it produced 2082 characters (a 3.8x expansion), mixing English words into Tamil and
looping. Raising the repeat penalty is the standard remedy for that failure mode, so
this measures it rather than guessing a value.

`length_ratio` is the signal: a faithful translation of this content lands near
1.5-2.5x for Tamil, so anything far above that is degeneration, not translation.

Usage:
    .venv/Scripts/python.exe scripts/tune_local_sampling.py
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

PENALTIES = (1.1, 1.2, 1.3, 1.45)
LANGUAGE = "ta"
BUDGET = 1024


def main() -> int:
    from app.core.config import settings
    from app.llm.local import LocalProvider
    from app.rag.prompts import build_translation_messages

    database = PROJECT_ROOT / "data" / "carinaa.db"
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "select content from messages where role='assistant' and ai_mode='offline' "
            "and length(content) > 300 order by id desc limit 1"
        ).fetchone()
    if row is None:
        print("FAIL no suitable offline answer found in the database")
        return 1
    source = row["content"]

    provider = LocalProvider()
    if not provider.load():
        print(f"FAIL local model unavailable: {provider._load_error}")
        return 1

    messages = build_translation_messages(source, LANGUAGE, target_language_name="Tamil")

    print("=" * 74)
    print("Local sampling: repeat penalty vs translation quality")
    print("=" * 74)
    print(f"  source : {len(source)} chars")
    print(f"  budget : {BUDGET} tokens")
    print()
    print(f"{'penalty':>8} | {'chars':>6} | {'ratio':>6} | {'tokens':>6} | {'finish':>9} | cites")
    print("-" * 74)

    rows = []
    for penalty in PENALTIES:
        settings.local_repeat_penalty = penalty
        result = provider._complete(messages, 0.0, BUDGET)
        text = str(result.get("text", "")).strip()
        usage = result.get("usage") or {}
        ratio = len(text) / max(1, len(source))
        cites = sorted({m for m in re.findall(r"\[(\d{1,3})\]", text)})
        rows.append((penalty, len(text), ratio, usage.get("completion_tokens"), cites))
        print(
            f"{penalty:8} | {len(text):6} | {ratio:6.2f} | "
            f"{usage.get('completion_tokens'):6} | {str(result.get('finish_reason')):>9} | {cites}",
            flush=True,
        )

    print("-" * 74)
    # A plausible Tamil translation of this content sits near 1.5-2.5x.
    plausible = [r for r in rows if 1.2 <= r[2] <= 3.0]
    if plausible:
        best = min(plausible, key=lambda r: abs(r[2] - 2.0))
        print(f"PASS plausible ratio at repeat_penalty={best[0]} (ratio {best[2]:.2f})")
        return 0
    print("FAIL every penalty produced an implausible length ratio")
    print("     the 3B model may not be able to translate this content into Tamil")
    return 1


if __name__ == "__main__":
    sys.exit(main())

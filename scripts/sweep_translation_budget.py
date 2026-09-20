"""Find the token budget at which the local model finishes a translation cleanly.

Earlier runs showed a narrow band: at 1024 tokens the Tamil translation was cut off
mid-sentence (finish=length), while at 1932 it ran on to a 3.8x expansion and
degenerated. This sweeps the budget to find where the model actually stops on its own.

`finish=stop` with a plausible length ratio is the target. `finish=length` means the
budget cut it off; a ratio far above ~2.5x means it rambled.

Usage:
    .venv/Scripts/python.exe scripts/sweep_translation_budget.py
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

BUDGETS = (1280, 1536, 1792, 2048)
LANGUAGE = "ta"


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
        print("FAIL no suitable offline answer found")
        return 1
    source = row["content"]

    provider = LocalProvider()
    if not provider.load():
        print(f"FAIL local model unavailable: {provider._load_error}")
        return 1

    messages = build_translation_messages(source, LANGUAGE, target_language_name="Tamil")
    print("=" * 76)
    print("Translation budget sweep (repeat_penalty kept at the shipped value)")
    print("=" * 76)
    print(f"  source        : {len(source)} chars")
    print(f"  repeat_penalty: {settings.local_repeat_penalty}")
    print()
    print(f"{'budget':>7} | {'chars':>6} | {'ratio':>6} | {'tokens':>6} | {'finish':>7} | cites")
    print("-" * 76)

    good: list[int] = []
    for budget in BUDGETS:
        result = provider._complete(messages, 0.0, budget)
        text = str(result.get("text", "")).strip()
        usage = result.get("usage") or {}
        ratio = len(text) / max(1, len(source))
        cites = sorted({m for m in re.findall(r"\[(\d{1,3})\]", text)})
        finish = str(result.get("finish_reason"))
        print(
            f"{budget:7} | {len(text):6} | {ratio:6.2f} | "
            f"{usage.get('completion_tokens'):6} | {finish:>7} | {cites}",
            flush=True,
        )
        if finish == "stop" and ratio <= 2.5:
            good.append(budget)

    print("-" * 76)
    if good:
        print(f"PASS clean finish at budget(s): {good}")
        print(f"     suggested factor for this language: "
              f"{good[0] / max(1, len(source)):.2f} tokens per source character")
        return 0
    print("FAIL no budget produced a clean finish with a plausible length")
    print("     the 3B model cannot translate this content into Tamil in one pass")
    return 1


if __name__ == "__main__":
    sys.exit(main())

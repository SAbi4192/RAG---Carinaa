"""Probe which provider models are reachable right now.

Guessing model IDs is how a project ships a broken default that 404s for
everyone. Both providers expose a live model list, so ask the API.

Run from the project root with the venv python:
    .venv/Scripts/python.exe scripts/probe_models.py

Diagnostic only - it prints, it modifies nothing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.llm.gemini import GeminiProvider  # noqa: E402
from app.llm.groq import GroqProvider  # noqa: E402


async def probe(label: str, provider, key_setting: str | None) -> None:
    print("=" * 70)
    print(f"{label}: key {'SET' if key_setting else 'MISSING'}")
    if not key_setting:
        return

    try:
        models = await provider.list_models()
    except Exception as exc:  # noqa: BLE001
        print(f"  list_models failed: {exc.__class__.__name__}: {exc}")
        return

    print(f"  {len(models)} usable text models")
    for name in models:
        print(f"    - {name}")

    if not models:
        return

    # Separate "the model exists" from "the model actually answers".
    for probe_model in models[:3]:
        try:
            response = await provider.generate(
                [{"role": "user", "content": "Reply with the single word: ok"}],
                temperature=0.0,
                max_tokens=8,
            )
            print(f"  generate on {probe_model!r}: OK -> {response.text[:40]!r}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  generate on {probe_model!r}: {exc.__class__.__name__}: {exc}")


async def main() -> int:
    await probe("Gemini", GeminiProvider(), getattr(settings, "gemini_api_key", None))
    await probe("Groq", GroqProvider(), getattr(settings, "groq_api_key", None))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

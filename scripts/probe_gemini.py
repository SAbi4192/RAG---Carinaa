"""Diagnose why Gemini returns an empty response.

Calls the Gemini REST API directly and prints the raw JSON: the real finishReason, the
parts, and whether thinking tokens consumed the output budget.

Usage:
    .venv/Scripts/python.exe scripts/probe_gemini.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402


async def probe(model: str, thinking: int | None, max_tokens: int, label: str = "") -> None:
    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with the single word: ok"}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": max_tokens},
    }
    if thinking is not None:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": thinking}

    url = f"{settings.gemini_base_url}/models/{model}:generateContent"
    print(f"\n=== {label or model} | thinking={thinking} maxOutputTokens={max_tokens}")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, params={"key": settings.gemini_api_key})
            print(f"HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError:
                print("  non-JSON body:", response.text[:400])
                return

            candidates = data.get("candidates") or []
            if not candidates:
                print("  NO candidates. Raw (truncated):", json.dumps(data)[:700])
                return

            candidate = candidates[0]
            parts = ((candidate.get("content") or {}).get("parts") or [])
            text = "".join(str(p.get("text") or "") for p in parts)
            usage = data.get("usageMetadata") or {}
            print(f"  finishReason : {candidate.get('finishReason')!r}")
            print(f"  parts        : {len(parts)}")
            print(f"  text         : {text[:120]!r}")
            print(
                f"  tokens       : thoughts={usage.get('thoughtsTokenCount')} "
                f"candidates={usage.get('candidatesTokenCount')}"
            )
            if not text.strip():
                print("  -> EMPTY: dumping raw candidate")
                print("    " + json.dumps(candidate)[:600])
    except Exception as exc:  # noqa: BLE001
        print(f"  request failed: {exc.__class__.__name__}: {exc}")


async def main() -> int:
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY missing")
        return 1

    print(f"configured model: {settings.gemini_model}")

    await probe(settings.gemini_model, 0, 2048, label="configured, thinking=0")
    await probe(settings.gemini_model, -1, 2048, label="configured, thinking=-1")
    await probe("gemini-3.5-flash", 0, 2048, label="gemini-3.5-flash, thinking=0")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

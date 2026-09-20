"""Focused probe: does a translation preserve the canonical answer's citations?

This isolates one behaviour that used to fail against real Gemini. A thinking
model charges its reasoning tokens against `maxOutputTokens`, so a long chain of
thought can silently truncate the visible answer - which is exactly how a Tamil
translation ended up dropping citation marker [2].

The probe runs the minimum real journey needed to reach the translate endpoint:

    register -> workspace -> upload -> ingest -> ask (online) -> translate

It prints the source/target citation sets, the token usage reported by the
provider, and PASS/FAIL. Nothing is mocked.

Usage:
    .venv/Scripts/python scripts/probe_translate.py
    .venv/Scripts/python scripts/probe_translate.py --language ta
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = ""

    def request(self, method, path, *, json_body=None, raw=None,
                content_type="application/json", timeout=600.0):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        try:
            return status, json.loads(body) if body else {}
        except ValueError:
            return status, body

    def upload(self, path: str, filename: str, content: bytes):
        boundary = f"----carinaa{uuid.uuid4().hex}"
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        return self.request("POST", path, raw=body,
                            content_type=f"multipart/form-data; boundary={boundary}")


def markers(text: str) -> set[str]:
    """Citation markers as they appear in the answer, e.g. {'1', '2'}."""
    return set(re.findall(r"\[(\d{1,3})\]", text or ""))


def numbers(text: str) -> set[str]:
    """Numeric literals, used to check the translation did not lose figures."""
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", default="online", choices=["online", "offline"])
    parser.add_argument("--language", default="ta")
    parser.add_argument("--document", default="samples/cloud_computing_notes.md")
    parser.add_argument("--question", default=(
        "How does virtualization improve resource utilization, and what is a hypervisor?"
    ))
    args = parser.parse_args()

    client = Client(args.base_url)
    run_id = uuid.uuid4().hex[:8]

    print("=" * 74)
    print(f"Carinaa translation probe   (run {run_id}, mode={args.mode}, lang={args.language})")
    print("=" * 74)

    status, _ = client.request("GET", "/api/health")
    if status != 200:
        print(f"FAIL server unreachable (HTTP {status})")
        return 1

    status, body = client.request("POST", "/api/auth/register", json_body={
        "email": f"probe_{run_id}@carinaa-e2e.dev",
        "password": "TestPass123",
        "display_name": "Probe",
    })
    if status != 201:
        print(f"FAIL register HTTP {status}: {body}")
        return 1
    client.token = body["access_token"]

    status, workspace = client.request("POST", "/api/workspaces",
                                       json_body={"name": f"Probe {run_id}"})
    if status != 201:
        print(f"FAIL workspace HTTP {status}: {workspace}")
        return 1
    workspace_id = workspace["id"]

    document_path = PROJECT_ROOT / args.document
    status, body = client.upload(f"/api/workspaces/{workspace_id}/documents",
                                 document_path.name, document_path.read_bytes())
    if status != 201:
        print(f"FAIL upload HTTP {status}: {body}")
        return 1
    document_id = body["document"]["id"]

    deadline = time.time() + 240
    progress: dict = {}
    while time.time() < deadline:
        _, progress = client.request("GET", f"/api/documents/{document_id}/progress")
        if progress.get("status") in ("ready", "failed"):
            break
        time.sleep(0.6)
    if progress.get("status") != "ready":
        print(f"FAIL ingestion status={progress.get('status')} error={progress.get('error')}")
        return 1
    print(f"  · ingested: {progress.get('chunk_count') or 'ok'}")

    started = time.time()
    status, answer = client.request("POST", "/api/chat/ask", json_body={
        "workspace_id": workspace_id,
        "question": args.question,
        "mode": args.mode,
    }, timeout=900.0)
    if status != 200:
        print(f"FAIL ask HTTP {status}: {json.dumps(answer)[:400]}")
        return 1
    text = answer.get("answer", "")
    print(f"  · answered in {time.time() - started:.1f}s via {answer.get('provider_label')}")
    print(f"  · grounding: {(answer.get('grounding') or {}).get('status')}")

    source_markers = markers(text)
    source_numbers = numbers(text)
    print(f"\n  source markers : {sorted(source_markers)}")
    print(f"  source numbers : {sorted(source_numbers)}")

    status, translated = client.request("POST", "/api/features/translate", json_body={
        "message_id": answer["message"]["id"],
        "language": args.language,
    }, timeout=900.0)
    if status != 200:
        print(f"\nFAIL translate HTTP {status}: {json.dumps(translated)[:400]}")
        return 1

    translated_text = translated.get("content", "")
    target_markers = markers(translated_text)
    target_numbers = numbers(translated_text)

    print(f"\n  translated markers: {sorted(target_markers)}")
    print(f"  translated numbers: {sorted(target_numbers)}")
    print(f"  provider          : {translated.get('provider')} / {translated.get('model')}")
    print(f"  cached            : {translated.get('cached')}")
    print(f"  validation        : {json.dumps(translated.get('validation'))}")
    print(f"  warning           : {translated.get('warning')!r}")
    print(f"  original_unchanged: {translated.get('original_unchanged')}")
    print("\n--- translated answer ---")
    print(translated_text[:1200])
    print("--- end ---\n")

    ok = True
    if target_markers != source_markers:
        print(f"FAIL citations differ: missing={sorted(source_markers - target_markers)} "
              f"extra={sorted(target_markers - source_markers)}")
        ok = False
    else:
        print("PASS translation preserves every citation marker")

    if translated.get("original_unchanged") is not True:
        print("FAIL canonical answer was modified")
        ok = False
    else:
        print("PASS canonical answer untouched")

    missing_numbers = source_numbers - target_numbers
    if missing_numbers:
        print(f"WARN numbers not found in translation: {sorted(missing_numbers)}")
    else:
        print("PASS translation preserves every numeric literal")

    warning = translated.get("warning") or ""
    if "stopped generating before it finished" in warning:
        print("FAIL provider reported the output was truncated (MAX_TOKENS)")
        ok = False
    elif warning:
        print(f"WARN translation carried a warning: {warning}")
    else:
        print("PASS no truncation and no validation warning")

    print("=" * 74)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

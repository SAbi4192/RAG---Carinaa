"""Focused reproduction for the document-delete 500.

Registers a throwaway user, creates a workspace, uploads a document, waits for
ingestion, then deletes it and prints the raw response body. Small enough to run
in a few seconds, unlike the full end-to-end suite.
"""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "cloud_computing_notes.md"

token = ""


def call(method: str, path: str, body: dict | None = None, raw: bytes | None = None,
         ctype: str = "application/json") -> tuple[int, object]:
    url = f"{BASE}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            text = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(text) if text else {})
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, text


def main() -> None:
    global token

    stamp = int(time.time())
    email = f"delete-probe-{stamp}@carinaa-e2e.dev"
    status, payload = call(
        "POST", "/api/auth/register",
        {"email": email, "password": "ProbePass!2026", "display_name": "Delete Probe"},
    )
    print(f"register            -> {status}")
    token = payload["access_token"]  # type: ignore[index]

    status, ws = call("POST", "/api/workspaces", {"name": f"Probe {stamp}", "description": "d"})
    print(f"create workspace    -> {status} id={ws.get('id')}")  # type: ignore[union-attr]
    ws_id = ws["id"]  # type: ignore[index]

    boundary = "----probe"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="probe.md"\r\n'
        f"Content-Type: text/markdown\r\n\r\n"
    ).encode() + SAMPLE.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    status, doc = call(
        "POST", f"/api/workspaces/{ws_id}/documents",
        raw=body, ctype=f"multipart/form-data; boundary={boundary}",
    )
    print(f"upload              -> {status} doc={doc.get('document', {}).get('id')}")  # type: ignore[union-attr]
    doc_id = doc["document"]["id"]  # type: ignore[index]

    for _ in range(60):
        status, prog = call("GET", f"/api/documents/{doc_id}/progress")
        if prog.get("status") in ("ready", "failed"):  # type: ignore[union-attr]
            break
        time.sleep(0.5)
    print(f"ingest              -> {prog.get('status')} stage={prog.get('stage')}")  # type: ignore[union-attr]

    # --- the interesting part ------------------------------------------------
    status, body_after = call("DELETE", f"/api/documents/{doc_id}")
    print(f"\nDELETE document     -> {status}")
    print(f"  response body     -> {body_after!r}")

    status2, after = call("GET", f"/api/documents/{doc_id}/chunks")
    print(f"GET chunks after    -> {status2}  {str(after)[:160]!r}")

    status3, listing = call("GET", f"/api/workspaces/{ws_id}/documents")
    print(f"list documents      -> {status3} total={listing.get('total')}")  # type: ignore[union-attr]


if __name__ == "__main__":
    main()

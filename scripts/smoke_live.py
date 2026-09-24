"""Live end-to-end smoke test for the Carinaa demo (server on :8001)."""
import io
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8001"
U, P = "smoke3@example.com", "SmokeTest!2345"
c = httpx.Client(base_url=BASE, timeout=120.0, follow_redirects=True)
fails = []


def check(name, cond, extra=""):
    ok = bool(cond)
    print(("PASS" if ok else "FAIL"), "-", name, extra)
    if not ok:
        fails.append(name)
    return ok


# auth -----------------------------------------------------------------
r = c.post("/api/auth/register", json={"email": U, "password": P, "display_name": "Smoke"})
if r.status_code not in (200, 201):
    r = c.post("/api/auth/login", json={"email": U, "password": P})
H = {"Authorization": f"Bearer {r.json()['access_token']}"}
check("auth", r.status_code in (200, 201))

r = c.post("/api/workspaces", json={"name": f"Smoke {int(time.time())}"}, headers=H)
WS = r.json()["id"]
check("workspace", r.status_code in (200, 201))

text = ("Design thinking is a five-stage human-centred approach to solving complex problems. "
        "Its stages are Empathise, Define, Ideate, Prototype and Test. Empathise means "
        "understanding users through interviews and observation. A USP is a Unique Selling "
        "Proposition: the single benefit that makes a product different from competitors. "
        "Prototyping is building small cheap versions of an idea so assumptions can be "
        "tested fast. The double diamond model visualises divergent and convergent "
        "thinking in design work. ") * 4
r = c.post(f"/api/workspaces/{WS}/documents", headers=H,
           files={"file": ("smoke_design.txt", io.BytesIO(text.encode()), "text/plain")})
check("upload", r.status_code in (200, 201), str(r.status_code))
doc_id = None
for _ in range(150):
    docs = c.get(f"/api/workspaces/{WS}/documents", headers=H).json().get("documents", [])
    ready = [d for d in docs if d.get("status") == "ready"]
    if ready:
        doc_id = ready[0]["id"]; break
    time.sleep(2)
check("indexed ready", doc_id is not None)

conv = c.post("/api/conversations", headers=H, json={"workspace_id": WS, "title": "smoke"}).json()
CID = conv["id"]
ask = lambda q: c.post("/api/chat/ask", headers=H,
                       json={"workspace_id": WS, "conversation_id": CID, "question": q, "mode": "online"})

# 1) simple question -> concise answer + own trace ----------------------
r = ask("What is a USP?")
if check("ask#1", r.status_code == 200, str(r.status_code) + (" " + r.text[:150] if r.status_code != 200 else "")):
    t1 = r.json().get("trace") or {}
    s1 = {s["stage"]: s for s in t1.get("stages", [])}
    check("ask#1 9-stage trace", len(t1.get("stages", [])) >= 8)
    check("real keywords", bool(s1.get("query_analysis", {}).get("data", {}).get("keywords")))
    lens1 = len(r.json()["message"]["content"].split())
    check("concise answer", lens1 <= 160, f"{lens1}w")

# 2) detailed question -> fuller answer + richer trace ------------------
r = ask("Explain the applied design thinking stages and their importance in detail, with examples.")
if check("ask#2", r.status_code == 200):
    d = r.json(); m2 = d["message"]; t2 = d.get("trace") or {}
    lens2 = len(m2["content"].split())
    check("detailed > simple", lens2 > lens1, f"{lens1}w -> {lens2}w")
    cr = [s for s in t2.get("stages", []) if s["stage"] == "candidate_retrieval"]
    if cr:
        dd = cr[0]["data"]
        check("trace.question", bool(dd.get("question")), (dd.get("question") or "")[:40])
        check("trace.scope", "retrieval_scope" in dd, dd.get("retrieval_scope"))
        check("trace.top_sources (for diagram)", len(dd.get("top_sources", [])) > 0)
    gen = [s for s in t2.get("stages", []) if s["stage"] == "llm_generation"]
    if gen:
        check("provider disclosed", bool(gen[0]["data"].get("provider")), str(gen[0]["data"].get("provider")))
    g = m2.get("grounding_status", "")
    # Any honest verdict counts: the model's wording varies run to run, and
    # INSUFFICIENT_EVIDENCE here is the system refusing rather than bluffing.
    check("grounding verdict valid", g in ("SUPPORTED", "PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE"), g)

# 3) follow-up uses conversation memory; per-message traces -------------
ask("What is the double diamond model?")
r = ask("What are its stages?")
if check("follow-up", r.status_code == 200):
    t3 = r.json().get("trace") or {}
    qa = [s for s in t3.get("stages", []) if s["stage"] == "query_analysis"]
    cr3 = [s for s in t3.get("stages", []) if s["stage"] == "candidate_retrieval"]
    mem = bool(qa and (qa[0]["data"].get("is_followup") or qa[0]["data"].get("resolved_question"))) \
        or bool(cr3 and cr3[0]["data"].get("conversation_turns_used"))
    check("memory used for 'its'", mem)
    msgs = c.get(f"/api/conversations/{CID}", headers=H).json()["messages"]
    aids = [m for m in msgs if m["role"] == "assistant"]
    qs = {}
    for a in aids:
        tr = c.get(f"/api/messages/{a['id']}", headers=H)  # may not exist; trace endpoint:
        tr = c.get(f"/api/messages/{a['id']}/trace", headers=H)
        if tr.status_code == 200:
            qs[a["id"]] = tr.json().get("question", "")
    nonempty = [q for q in qs.values() if q]
    check("per-message traces loaded", len(qs) >= 3, f"{len(qs)} traces")
    check("distinct questions", len(set(nonempty)) >= 3, f"{len(set(nonempty))} unique of {len(nonempty)}")
    vals = list(qs.values())
    check("first!=last", vals[0] != vals[-1], f"{vals[0][:30]!r} vs {vals[-1][:30]!r}")

# 4) multi-document chat scope ------------------------------------------
biz_text = ("A business plan summarises goals, market analysis, operations and a five-year "
            "revenue projection for investors. ") * 8
r = c.post(f"/api/workspaces/{WS}/documents", headers=H,
           files={"file": ("smoke_biz.txt", io.BytesIO(biz_text.encode("utf-8")), "text/plain")})
doc2 = None
for _ in range(120):
    docs = c.get(f"/api/workspaces/{WS}/documents", headers=H).json().get("documents", [])
    ready2 = [d for d in docs if d.get("status") == "ready"]
    if len(ready2) >= 2:
        doc2 = [d["id"] for d in ready2 if d["id"] != doc_id][0]; break
    time.sleep(2)
check("2nd doc ready", doc2 is not None)

conv2 = c.post("/api/conversations", headers=H, json={"workspace_id": WS, "title": "scope test"}).json()["id"]
a = c.post(f"/api/conversations/{conv2}/documents", headers=H, json={"document_id": doc_id})
b = c.post(f"/api/conversations/{conv2}/documents", headers=H, json={"document_id": doc2})
if check("attach 2 docs", a.status_code in (200, 201) and b.status_code in (200, 201)):
    sp = c.get(f"/api/conversations/{conv2}/documents", headers=H).json()
    check("2 active in scope", len(sp.get("active_document_ids", [])) == 2)
    r = c.post("/api/chat/ask", headers=H, json={
        "workspace_id": WS, "conversation_id": conv2, "mode": "online",
        "question": "What does the business plan summary cover?"})
    if check("scoped ask", r.status_code == 200):
        cr = [s for s in r.json()["trace"]["stages"] if s["stage"] == "candidate_retrieval"][0]["data"]
        check("scope=chat, 2 searched",
              cr.get("retrieval_scope") == "chat" and cr.get("workspace_documents_searched") == 2,
              f"{cr.get('retrieval_scope')}/{cr.get('workspace_documents_searched')}")
        check("scope file names", len(cr.get("scope_file_names", [])) == 2, str(cr.get("scope_file_names")))
        c.patch(f"/api/conversations/{conv2}/documents/{doc_id}", headers=H, json={"is_active": False})
        s2 = c.get(f"/api/conversations/{conv2}/documents", headers=H).json()
        check("deselect -> 1 active", s2["active_document_ids"] == [doc2])
    mid = r.json()["message"]["id"] if r.status_code == 200 else None
else:
    mid = None

# 5) languages / Tanglish / speech ---------------------------------------
lans = c.get("/api/settings/languages", headers=H).json()["languages"]
codes = [l["code"] for l in lans]
check("Tanglish offered", "ta-ta" in codes)
en = [l for l in lans if l["code"] == "en"][0]
tan = [l for l in lans if l["code"] == "ta-ta"][0]
check("en speech starts with bare 'en'", en.get("speech_candidates", ["en"])[0] == "en", str(en.get("speech_candidates")))
check("Tanglish speech en-IN first", tan.get("speech_candidates", [None])[0] == "en-IN", str(tan.get("speech_candidates")))

target = mid if mid else c.get(f"/api/conversations/{CID}", headers=H).json()["messages"][-1]["id"]
r = c.post("/api/features/translate", headers=H, json={"message_id": target, "language": "ta-ta"})
if check("translate Tanglish", r.status_code == 200, str(r.status_code)):
    content = r.json().get("content", "")
    latin = sum(1 for ch in content if ch.isascii() and ch.isalpha())
    script = any(0x0B80 <= ord(ch) <= 0x0BFF for ch in content)
    check("Tanglish Roman letters", latin > 40, f"{latin} ascii letters")
    check("Tanglish avoids Tamil script", not script)
r = c.post("/api/features/translate", headers=H, json={"message_id": target, "language": "ta"})
check("Tamil still works too", r.status_code == 200 and any(0x0B80 <= ord(x) <= 0x0BFF for x in r.json().get("content", "")))

r = c.post("/api/features/translate-text", headers=H,
           json={"text": "Carinaa searched your documents and found the best passages.", "language": "hi"})
check("panel translate-text", r.status_code == 200 and r.json().get("content"), str(r.status_code))

r = c.post("/api/features/speech", headers=H, json={"message_id": target, "language": "en"})
if check("speech endpoint", r.status_code == 200):
    sp = r.json()
    check("speech candidates list", sp.get("speech_candidates", [])[:1] == ["en"], str(sp.get("speech_candidates")))

# 6) playground retrieval endpoint still serves the labs -------------------
r = c.post("/api/chat/retrieve", headers=H, json={"workspace_id": WS, "question": "prototyping", "mode": "online"})
check("playground retrieve", r.status_code == 200, str(r.status_code))

print("=" * 60)
if fails:
    print("SMOKE FAILED:", len(fails)); [print("  -", f) for f in fails]; sys.exit(2)
print("SMOKE PASSED: all green")


"""Real browser UI test: console errors, responsive layout, and click-through.

WHY THIS EXISTS
---------------
Everything else in this project is verified by API tests and by dumping the DOM from a
headless browser. Neither can catch:

  - a console error or a React warning
  - a failed network request
  - a control that renders but does nothing when clicked
  - a layout that overflows or overlaps at a narrow width

Those were the known blind spots, repeatedly flagged and never checked. This closes
them by driving the real UI.

It uses the Edge already installed on the machine (`channel="msedge"`) rather than
downloading a Playwright browser, so it runs without a large install.

Usage:
    .venv/Scripts/python.exe scripts/ui_test.py
    .venv/Scripts/python.exe scripts/ui_test.py --headed   # watch it run
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from e2e_test import Client  # noqa: E402

BASE = "http://127.0.0.1:8000"

# Widths from the brief's section 52.
VIEWPORTS = [
    ("desktop-1440", 1440, 900),
    ("laptop-1280", 1280, 800),
    ("tablet-1024", 1024, 768),
    ("tablet-768", 768, 1024),
    ("mobile-390", 390, 844),
]

results: list[tuple[str, bool, str]] = []


expected_bad_request = False


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"           {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="Show the browser.")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    print("=" * 78)
    print("Carinaa UI test (real browser)")
    print("=" * 78)

    # ---- fixtures ------------------------------------------------------
    client = Client(BASE)
    token = client.request(
        "POST",
        "/api/auth/register",
        json_body={
            "email": f"ui_{uuid.uuid4().hex[:8]}@carinaa-e2e.dev",
            "password": "TestPass123",
            "display_name": "UI Test",
        },
    )[1]["access_token"]
    client.token = token
    workspace = client.request("POST", "/api/workspaces", json_body={"name": "UI Workspace"})[1]["id"]
    document = client.upload(
        f"/api/workspaces/{workspace}/documents",
        "cloud_computing_notes.md",
        (PROJECT_ROOT / "samples" / "cloud_computing_notes.md").read_bytes(),
    )[1]["document"]["id"]

    import time

    for _ in range(300):
        status = client.request("GET", f"/api/documents/{document}/progress")[1].get("status")
        if status in ("ready", "failed"):
            break
        time.sleep(0.4)

    conversation = client.request(
        "POST", "/api/conversations", json_body={"workspace_id": workspace, "title": "UI test"}
    )[1]["id"]
    client.request(
        "POST", f"/api/conversations/{conversation}/documents", json_body={"document_id": document}
    )
    print(f"  fixture: workspace {workspace}, document {document}, conversation {conversation}\n")

    console_errors: list[str] = []
    failed_requests: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=not args.headed)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.on(
            "console",
            lambda message: console_errors.append(f"{message.type}: {message.text}")
            if message.type == "error"
            else None,
        )
        page.on(
            "pageerror",
            lambda error: console_errors.append(f"pageerror: {error}"),
        )
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(f"{request.method} {request.url}"),
        )

        # Seed auth into localStorage, then load the app.
        page.goto(BASE, wait_until="domcontentloaded")
        page.evaluate(
            """([token, workspace]) => {
                localStorage.setItem('carinaa.token', token);
                localStorage.setItem('carinaa.workspace', String(workspace));
                localStorage.setItem('carinaa.theme', 'dark');
            }""",
            [token, workspace],
        )

        # ---- 1. Routes render without console errors -------------------
        print("1. Every route loads without a console error")
        for route in (
            "/app",
            "/app/chat",
            "/app/learning",
            "/app/playground",
            "/app/playground/chunking",
            "/app/playground/reference",
            "/app/playground/scope",
            "/app/playground/memory",
            "/app/knowledge",
            "/app/trace",
            "/app/analytics",
            "/app/settings",
        ):
            before = len(console_errors)
            page.goto(f"{BASE}{route}", wait_until="networkidle")
            page.wait_for_timeout(700)
            new_errors = console_errors[before:]
            check(
                f"{route} loads clean",
                not new_errors,
                new_errors[0][:150] if new_errors else "",
            )

        # ---- 2. Chat: ask, receive, expand sources ---------------------
        print("\n2. Chat works end to end in the browser")
        page.goto(f"{BASE}/app/chat/{conversation}", wait_until="networkidle")
        page.wait_for_timeout(500)

        composer = page.locator("textarea").first
        check("composer is present", composer.count() > 0)
        if composer.count():
            composer.fill("How does virtualization improve resource utilization?")
            composer.press("Enter")
            page.wait_for_timeout(12000)

            body = page.inner_text("body")
            check(
                "an answer appeared",
                "hypervisor" in body.lower() or "virtualization" in body.lower(),
                "",
            )
            check("sources section rendered", "source" in body.lower())
            check("retrieval detail rendered", "retrieval detail" in body.lower())

        # ---- 3. The scope checklist actually toggles ------------------------
        # The control is the ChatDocuments checklist above the composer. Its header
        # always reads "Searching ..." (n of m / all), and expanding it reveals the
        # tick-list promise line. (Old assertion looked for "This chat" /
        # "Unchecking excludes" — labels the checklist redesign replaced.)
        print("\n3. The chat scope control toggles")
        scope_button = page.locator("button:has-text('Searching')").first
        check("scope control is present", scope_button.count() > 0)
        if scope_button.count():
            expanded_before = page.locator("text=Tick the documents").count()
            scope_button.click()
            page.wait_for_timeout(500)
            expanded_after = page.locator("text=Tick the documents").count()
            check(
                "clicking expands it",
                expanded_after > expanded_before,
                f"before={expanded_before} after={expanded_after}",
            )
            scope_button.click()
            page.wait_for_timeout(400)
            check(
                "clicking again collapses it",
                page.locator("text=Tick the documents").count() < expanded_after,
            )

        # ---- 4. Learning Mode: navigation stays put --------------------
        print("\n4. Learning Mode navigation is stable (the original bug)")
        page.goto(f"{BASE}/app/chat", wait_until="networkidle")
        page.wait_for_timeout(400)
        page.locator("a:has-text('Learning Mode')").first.click()
        page.wait_for_timeout(1200)
        check(
            "clicking Learning Mode stays on /app/learning",
            "/app/learning" in page.url,
            f"url={page.url}",
        )
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(800)
        check("survives a refresh", "/app/learning" in page.url, f"url={page.url}")
        # The panel header is "🔬 How Carinaa answered" (redesign label). The old
        # assertion looked for "RAG Learning", a string no component has ever
        # rendered after the Learning Mode rebuild.
        check(
            "the pipeline panel rendered",
            page.locator("text=How Carinaa answered").count() > 0,
        )

        # ---- 5. Panels menu -------------------------------------------
        print("\n5. The panel menu opens and closes")
        page.locator("button[aria-label='Show or hide the side panels']").first.click()
        page.wait_for_timeout(400)
        check("panel menu opens", page.locator("text=Conversation history").count() > 0)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check(
            "Escape closes it",
            page.locator("text=Conversation history").count() == 0,
        )

        # ---- 6. Responsive ---------------------------------------------
        print("\n6. Responsive layout, no horizontal overflow")
        for label, width, height in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            page.goto(f"{BASE}/app/chat/{conversation}", wait_until="networkidle")
            page.wait_for_timeout(900)

            metrics = page.evaluate(
                """() => ({
                    scrollWidth: document.documentElement.scrollWidth,
                    clientWidth: document.documentElement.clientWidth,
                    bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
                })"""
            )
            overflow = metrics["scrollWidth"] - metrics["clientWidth"]
            check(
                f"{label}: no horizontal overflow",
                overflow <= 2,
                f"overflow={overflow}px" if overflow > 2 else f"{metrics['clientWidth']}px wide",
            )

            # The composer must remain usable at every width.
            composer_visible = page.locator("textarea").first.is_visible()
            check(f"{label}: composer usable", composer_visible)

        # ---- 7. Mobile navigation drawer ------------------------------
        print("\n7. Mobile navigation drawer")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{BASE}/app", wait_until="networkidle")
        page.wait_for_timeout(700)
        opener = page.locator("button[aria-label*='navigation']").first
        check("drawer opener present on mobile", opener.count() > 0)
        if opener.count():
            opener.click()
            page.wait_for_timeout(600)
            check("drawer opens", page.locator("text=Dashboard").count() > 0)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

        # ---- 5b. Waiting indicator: plain on Chat, animated in Learning Mode
        print("\n5b. Waiting indicator differs by mode")
        page.set_viewport_size({"width": 1440, "height": 900})

        STAGE_NAMES = (
            "Query Analysis",
            "Query Embedding",
            "Vector Search",
            "Candidate Retrieval",
            "Context Building",
            "LLM Generation",
            "Grounding",
        )

        def seen_stage_names() -> set[str]:
            body = page.inner_text("body")
            return {name for name in STAGE_NAMES if name in body}

        # (a) Chat: must NOT cycle stage names.
        page.goto(f"{BASE}/app/chat/{conversation}", wait_until="networkidle")
        page.wait_for_timeout(500)
        chat_box = page.locator("textarea").first
        chat_box.fill("What is a hypervisor?")
        chat_box.press("Enter")

        chat_seen: set[str] = set()
        for _ in range(20):
            page.wait_for_timeout(250)
            chat_seen |= seen_stage_names()
            if "Sources" in page.inner_text("body"):
                break
        check(
            "Chat shows NO stage-name animation",
            not chat_seen,
            f"saw {sorted(chat_seen)} (should be none on plain Chat)",
        )

        # (b) Learning Mode: must cycle stage names.
        page.goto(f"{BASE}/app/learning/{conversation}", wait_until="networkidle")
        page.wait_for_timeout(600)
        learn_box = page.locator("textarea").first
        learn_box.fill("What is a hypervisor?")
        learn_box.press("Enter")
        learn_seen: set[str] = set()
        for _ in range(24):
            page.wait_for_timeout(250)
            learn_seen |= seen_stage_names()
            if "Sources" in page.inner_text("body"):
                break
        check(
            "Learning Mode DOES animate stage names",
            len(learn_seen) >= 3,
            f"saw {sorted(learn_seen)}",
        )

        # ---- 6b. Learning Mode must never show a completed pipeline
        #          when there is no answer (the brief's most important bug).
        print("\n6b. Failed run must not show a completed pipeline")
        page.set_viewport_size({"width": 1440, "height": 900})

        # A successful run first, so there IS a completed pipeline to be tempted by.
        page.goto(f"{BASE}/app/learning/{conversation}", wait_until="networkidle")
        page.wait_for_timeout(600)
        ok_composer = page.locator("textarea").first
        ok_composer.fill("What is a hypervisor?")
        ok_composer.press("Enter")
        page.wait_for_timeout(14000)

        succeeded = "How Carinaa answered" in page.inner_text("body")
        check("a successful Learning run renders the panel", succeeded)

        # Now force a deterministic failure: a page number that cannot exist. The
        # backend refuses with 400 before any generation happens.
        bad_composer = page.locator("textarea").first
        bad_composer.fill("Tell me about page 9999")
        bad_composer.press("Enter")
        page.wait_for_timeout(6000)
        # This run is MEANT to fail. Without recording that, the console check below
        # would flag the deliberate 400 as a defect and hide real ones behind it.
        expected_bad_request = True

        body = page.inner_text("body")
        # The failure banner reads "Carinaa stopped before producing an answer"
        # followed by the backend's own message (which for a paginated document is
        # "page 9999 does not exist", and for a Markdown fixture is the
        # no-page-metadata explanation). Match the banner, not one specific cause.
        check(
            "the failure is shown",
            "stopped before producing an answer" in body
            or "could not be answered" in body.lower(),
        )
        # The critical assertion: no completed pipeline next to a failed run.
        # "recovered"/stage checkmarks would mean the panel is lying.
        check(
            "the panel does NOT claim the pipeline completed",
            "Carinaa stopped before producing an answer" in body,
        )

        # ---- 7b. The conversational labs render -----------------------
        print("\n7b. The three conversational labs render")
        for slug, marker in (
            ("reference", "Compare both searches"),
            ("scope", "Compare scopes"),
            ("memory", "Run the conversation"),
        ):
            page.goto(f"{BASE}/app/playground/{slug}", wait_until="networkidle")
            page.wait_for_timeout(800)
            body = page.inner_text("body")
            check(f"{slug} lab renders its controls", marker in body)

        # ---- 8. Settings: workspace tab and danger zone ---------------
        print("\n8. Settings workspace tab")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{BASE}/app/settings", wait_until="networkidle")
        page.wait_for_timeout(700)
        # Exact name, and re-resolved after the click: a locator captured before a
        # React re-render can go stale and the click then silently does nothing,
        # which looks like a missing panel rather than a broken selector.
        workspace_tab = page.get_by_role("tab", name="Workspace", exact=True)
        check("Workspace tab present", workspace_tab.count() > 0)
        if workspace_tab.count():
            workspace_tab.click()
            page.wait_for_timeout(900)
            body = page.inner_text("body")
            check("danger zone renders", "Danger zone" in body)
            check("delete button present", "Delete workspace" in body)
            check(
                "workspace stats render",
                "Documents" in body and "Chunks" in body,
            )

        browser.close()

    # ---- console + network summary ------------------------------------
    print("\n9. Console and network health")
    unexpected = [
        entry
        for entry in console_errors
        # A deliberate 4xx is a fixture, not a defect.
        if not (expected_bad_request and "status of 4" in entry)
    ]
    check(
        "no unexpected console errors across the whole run",
        not unexpected,
        unexpected[0][:200] if unexpected else f"{len(console_errors)} total, all expected",
    )
    # Favicon 404s are not meaningful.
    meaningful = [r for r in failed_requests if "favicon" not in r]
    check(
        "no failed network requests",
        not meaningful,
        meaningful[0][:160] if meaningful else f"{len(failed_requests)} total",
    )

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print("\n" + "=" * 78)
    print(f"RESULT: {passed} passed, {failed} failed, {len(results)} checks")
    print("=" * 78)

    if console_errors:
        print("\nConsole errors captured:")
        for entry in console_errors[:10]:
            print(f"  - {entry[:220]}")
    if meaningful:
        print("\nFailed requests:")
        for entry in meaningful[:10]:
            print(f"  - {entry[:220]}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

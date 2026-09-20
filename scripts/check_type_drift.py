"""Detect drift between the frontend's hand-written types and the backend's API.

WHY THIS EXISTS
---------------
`frontend/src/lib/types.ts` is a hand-written mirror of the FastAPI response models.
Nothing links the two, so a backend field can be renamed, added or removed and
TypeScript will keep compiling happily against a stale shape. The failure surfaces at
runtime, in the browser, as `undefined` - the worst kind of bug to debug, because the
type-checker said everything was fine.

This script compares the two and reports:

  * fields the backend sends that the frontend does not know about  (missing)
  * fields the frontend expects that the backend never sends        (extra)

`missing` is the dangerous direction: the data arrives and the UI ignores it.
`extra` is usually harmless but often means a rename happened.

Usage:
    .venv/Scripts/python.exe scripts/check_type_drift.py
    .venv/Scripts/python.exe scripts/check_type_drift.py --verbose

Exit code is 0 when the two agree, 1 when drift is found, so it can gate a build.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

TYPES_FILE = PROJECT_ROOT / "frontend" / "src" / "lib" / "types.ts"

# Frontend name -> OpenAPI schema name. They are not identical because the API uses
# `*Out` suffixes and the frontend prefers readable names. Every entry here is a
# deliberate, documented mapping rather than a guess.
NAME_MAP: dict[str, str] = {
    "User": "UserOut",
    "UserPreferences": "PreferencesUpdate",
    "TokenResponse": "TokenOut",
    "Workspace": "WorkspaceOut",
    "WorkspaceStats": "WorkspaceStats",
    "DocumentRecord": "DocumentOut",
    "ChunkPreview": "ChunkPreviewOut",
    "UploadResponse": "UploadResponse",
    "Conversation": "ConversationOut",
    "Citation": "CitationOut",
    "Grounding": "GroundingOut",
    "Message": "MessageOut",
    "AskResponse": "AskResponse",
    "AskRequest": "AskRequest",
    "TraceStage": "TraceStageOut",
    "Trace": "TraceOut",
    "Variant": "VariantOut",
    "SpeechPayload": "SpeechOut",
    "ExplainPayload": "ExplainOut",
    "RetrieveResponse": "RetrieveResponse",
}

# Frontend types that intentionally describe a *derived* shape the backend does not
# model directly. These are not drift, so they are skipped rather than reported.
#
# `ConversationDetail` is the clearest example: it is a client-side envelope
# `{conversation, messages}`, not a wire model - the backend returns the two pieces
# from separate endpoints.
INTENTIONALLY_LOCAL = {
    "WorkspaceList", "DocumentList", "ConversationList", "ConversationDetail",
    "SupportedTypes", "StageDefinition", "DocumentProgress",
    "Language", "ShortenLevelInfo", "Capabilities",
    "RetrievedChunk", "RetrievalOutcome", "ProviderStatus", "ModeStatus",
    "ProviderReport", "RagSettings", "SystemStats",
    "AnalyticsOverview", "RetrievalRecentRow", "RetrievalAnalytics",
    "ActivityPoint", "ActivityAnalytics", "SecurityCheckResult", "SecurityReport",
    "ApiErrorBody",
    # The laboratory endpoints return plain dicts rather than Pydantic models, so
    # there is no schema to compare them against. Their shape is pinned by the
    # route handlers in app/api/routes_labs.py and by the backend tests instead.
    "LabEmbedResponse", "LabEmbedQuery", "LabProjectionPoint",
    "LabStage", "LabStagesResponse",
    "PreviewChunk", "PreviewChunksResponse",
    "ContextBundleOut", "ContextExcerpt",
    # TraceSummary is assembled from TWO backend shapes: the inline `trace` object
    # on AskResponse (which is `trace.summary()`: trace_id, event_count, total_ms,
    # stages) and TraceOut from GET /messages/{id}/trace (which adds message_id and
    # note). There is no single schema to compare it against, so its shape is pinned
    # by `tests/test_labs.py` and the Live RAG Trace checks in verify_labs.py
    # instead. Deliberately recorded here rather than left as unexplained drift.
    "TraceSummary",
    # The chat-scope endpoints return plain dicts built by
    # routes_conversation_documents.scope_payload() rather than a Pydantic model,
    # so there is no schema to compare against. Their shape is pinned by the 10
    # tests in tests/test_conversation_scope.py, which assert the exact keys the
    # UI reads (scope, active_document_ids, documents[], workspace_document_count).
    "ConversationScope",
    "ConversationDocumentLink",
}

_INTERFACE_RE = re.compile(r"^export interface (\w+)\s*(?:extends\s+[\w<>, ]+)?\{", re.MULTILINE)
_FIELD_RE = re.compile(r"^\s{2}(\w+)\??\s*:", re.MULTILINE)


def frontend_fields() -> dict[str, set[str]]:
    """Field names per exported interface in types.ts."""
    source = TYPES_FILE.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}

    matches = list(_INTERFACE_RE.finditer(source))
    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        body = source[start:end]
        # Only capture fields at the interface's own indentation level, so nested
        # object literals and union members are not mistaken for fields.
        found[name] = set(_FIELD_RE.findall(body))
    return found


def backend_fields() -> dict[str, set[str]]:
    """Field names per OpenAPI component schema."""
    from app.main import app  # imported late so sys.path is already set up

    schemas = app.openapi().get("components", {}).get("schemas", {})
    result: dict[str, set[str]] = {}
    for name, schema in schemas.items():
        properties = schema.get("properties") or {}
        result[name] = set(properties)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="Show every compared type.")
    args = parser.parse_args()

    if not TYPES_FILE.is_file():
        print(f"FAIL cannot find {TYPES_FILE}")
        return 1

    fe = frontend_fields()
    be = backend_fields()

    print("=" * 74)
    print("Frontend / backend type drift check")
    print("=" * 74)
    print(f"  frontend interfaces : {len(fe)}")
    print(f"  backend schemas     : {len(be)}")
    print()

    drift = 0
    compared = 0
    skipped = 0

    for fe_name in sorted(fe):
        if fe_name in INTENTIONALLY_LOCAL:
            skipped += 1
            if args.verbose:
                print(f"  skip  {fe_name:26} (derived/local shape)")
            continue

        be_name = NAME_MAP.get(fe_name)
        if be_name is None:
            print(f"  ??    {fe_name:26} no schema mapping - add one to NAME_MAP")
            drift += 1
            continue
        if be_name not in be:
            print(f"  ??    {fe_name:26} maps to '{be_name}', which does not exist")
            drift += 1
            continue

        compared += 1
        fe_fields = fe[fe_name]
        be_fields = be[be_name]

        # `user` is an alias kept for readability; both names refer to the same shape.
        missing = be_fields - fe_fields
        extra = fe_fields - be_fields

        if not missing and not extra:
            if args.verbose:
                print(f"  ok    {fe_name:26} ({len(fe_fields)} fields)")
            continue

        print(f"  DRIFT {fe_name:26} -> {be_name}")
        if missing:
            print(f"        backend sends, frontend ignores : {sorted(missing)}")
        if extra:
            print(f"        frontend expects, backend lacks: {sorted(extra)}")
        drift += 1

    print()
    print("-" * 74)
    print(f"  compared {compared}, skipped {skipped}, drifted {drift}")
    print("-" * 74)

    # ---------------------------------------------------------------------
    # Pipeline stage order
    # ---------------------------------------------------------------------
    # The frontend hard-codes the stage list so it can attach an explanation and
    # an icon to each one. If the pipeline adds, removes or renames a stage and
    # this list is not updated, the laboratory would silently omit it - and a
    # laboratory that quietly hides a stage is worse than one that shows nothing,
    # because the learner cannot tell that something is missing.
    print()
    print("Pipeline stage order")
    print("-" * 74)

    stage_drift = 0
    explanations_file = (
        PROJECT_ROOT / "frontend" / "src" / "components" / "rag" / "stageExplanations.ts"
    )
    if not explanations_file.is_file():
        print(f"  ??    cannot find {explanations_file.name}")
        stage_drift += 1
    else:
        source = explanations_file.read_text(encoding="utf-8")
        frontend_stages = re.findall(r'^\s*stage:\s*"([a-z_]+)"', source, re.MULTILINE)

        # Conditional stages are declared separately in the same file, so they are
        # compared against the backend's conditional list rather than the ordered one.
        frontend_conditional = re.findall(
            r'stage:\s*"([a-z_]+)",\s*\n\s*label:\s*"[^"]+",\s*\n\s*icon:\s*\w+,\s*\n\s*conditional:\s*true',
            source,
        )
        frontend_ordered = [s for s in frontend_stages if s not in frontend_conditional]

        from app.rag.trace import (  # imported late; sys.path is set up
            conditional_stage_definitions,
            stage_definitions,
        )

        backend_stages = [item["stage"] for item in stage_definitions()]
        backend_conditional = [item["stage"] for item in conditional_stage_definitions()]

        if frontend_ordered == backend_stages:
            print(f"  ok    {len(frontend_ordered)} stages, identical and in the same order")
        else:
            stage_drift += 1
            only_backend = [s for s in backend_stages if s not in frontend_ordered]
            only_frontend = [s for s in frontend_ordered if s not in backend_stages]
            if only_backend:
                print(f"  DRIFT backend runs stages the UI does not explain: {only_backend}")
            if only_frontend:
                print(f"  DRIFT the UI explains stages the backend never runs: {only_frontend}")
            if not only_backend and not only_frontend:
                print("  DRIFT the same stages exist but in a different order:")
                print(f"        backend : {backend_stages}")
                print(f"        frontend: {frontend_ordered}")

        missing_conditional = [
            s for s in backend_conditional if s not in frontend_conditional
        ]
        if missing_conditional:
            stage_drift += 1
            print(
                "  DRIFT these stages can appear in a trace but the UI cannot label "
                f"them: {missing_conditional}"
            )
        else:
            print(f"  ok    {len(backend_conditional)} conditional stages are explained too")

    print("-" * 74)
    drift += stage_drift

    if drift:
        print("RESULT: DRIFT FOUND")
        print()
        print("Note: some drift is expected when a frontend type deliberately narrows a")
        print("backend model. Review each case, then either fix the frontend or add the type")
        print("to INTENTIONALLY_LOCAL with a comment explaining why.")
        return 1

    print("RESULT: no drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())

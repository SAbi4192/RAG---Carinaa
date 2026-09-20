"""Tests for the RAG Laboratory endpoints and the stage-order contract.

Two things are being protected here.

1. **The embedding endpoint tells the truth about what it returns.** Vectors are
   truncated for display, the response says by how much, and the 2-D scatter plot
   is labelled as a projection with its explained variance reported. A laboratory
   that quietly implies the plot IS the embedding space would teach a beginner
   something false, which is worse than teaching nothing.

2. **The stage list matches the order the pipeline actually runs.** `stage_definitions()`
   is documented as the contract that keeps the UI and the backend agreeing - and it
   was wrong. It listed `grounding` before `citation_resolution`, while `pipeline.py`
   runs citation resolution first and grounding second. A frontend built on the
   canonical list would have shown the pipeline in an order it never executes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.rag.trace import conditional_stage_definitions, stage_definitions

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The real execution order, read from the source rather than restated by hand.
PIPELINE_FILE = PROJECT_ROOT / "backend" / "app" / "rag" / "pipeline.py"
RETRIEVER_FILE = PROJECT_ROOT / "backend" / "app" / "rag" / "retriever.py"


# ---------------------------------------------------------------------------
# Embedding laboratory
# ---------------------------------------------------------------------------
def test_embed_requires_authentication(client) -> None:
    response = client.post("/api/labs/embed", json={"texts": ["hello"]})
    assert response.status_code == 401


def test_embed_returns_real_vectors(client, make_user) -> None:
    headers, _ = make_user()
    response = client.post(
        "/api/labs/embed",
        headers=headers,
        json={"texts": ["Virtualization abstracts physical hardware.", "A hypervisor runs virtual machines."]},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["count"] == 2
    assert body["dimensions"] > 0
    assert len(body["vectors"]) == 2
    # Truncated for display, and honest about it.
    assert len(body["vectors"][0]) == body["preview_dimensions"]
    assert body["preview_dimensions"] <= body["dimensions"]


def test_embed_vectors_are_normalised(client, make_user) -> None:
    """Cosine similarity is a dot product only because the vectors are unit length."""
    headers, _ = make_user()
    body = client.post(
        "/api/labs/embed", headers=headers, json={"texts": ["one sentence here", "another sentence"]}
    ).json()

    for norm in body["norms"]:
        assert norm == pytest.approx(1.0, abs=1e-3)


def test_similarity_matrix_is_symmetric_with_a_unit_diagonal(client, make_user) -> None:
    headers, _ = make_user()
    body = client.post(
        "/api/labs/embed",
        headers=headers,
        json={"texts": ["alpha beta", "gamma delta", "epsilon zeta"]},
    ).json()

    matrix = body["similarity"]
    assert len(matrix) == 3
    for i in range(3):
        assert matrix[i][i] == pytest.approx(1.0, abs=1e-3)
        for j in range(3):
            assert matrix[i][j] == pytest.approx(matrix[j][i], abs=1e-4)


def test_related_texts_score_higher_than_unrelated_ones(client, make_user) -> None:
    """The whole pedagogical point: meaning survives the conversion to numbers."""
    headers, _ = make_user()
    body = client.post(
        "/api/labs/embed",
        headers=headers,
        json={
            "texts": [
                "Virtualization lets one server host many virtual machines.",
                "A hypervisor creates and runs virtual machines.",
                "Photosynthesis converts light into chemical energy in plants.",
            ]
        },
    ).json()

    matrix = body["similarity"]
    assert matrix[0][1] > matrix[0][2], "the two virtualization texts should be closer"


def test_projection_is_labelled_as_a_projection(client, make_user) -> None:
    """A projection that hides its own distortion teaches something false."""
    headers, _ = make_user()
    body = client.post(
        "/api/labs/embed",
        headers=headers,
        json={"texts": ["one", "two", "three"]},
    ).json()

    note = body["projection_note"].lower()
    assert "not" in note and ("projection" in note or "pca" in note)
    assert len(body["projection"]) == 3
    assert len(body["projection_explained_variance"]) == 2
    assert sum(body["projection_explained_variance"]) <= 1.0001


def test_query_ranking_is_returned_and_ordered(client, make_user) -> None:
    headers, _ = make_user()
    body = client.post(
        "/api/labs/embed",
        headers=headers,
        json={
            "texts": [
                "Photosynthesis converts light energy into sugar.",
                "A hypervisor runs virtual machines on a host.",
            ],
            "query": "How do hypervisors work?",
        },
    ).json()

    query = body["query"]
    assert query["text"] == "How do hypervisors work?"
    ranking = query["ranking"]
    assert len(ranking) == 2
    # Ordered by descending score.
    assert ranking[0]["score"] >= ranking[1]["score"]
    # The hypervisor text must win.
    assert ranking[0]["index"] == 1


def test_empty_text_list_is_handled_without_error(client, make_user) -> None:
    headers, _ = make_user()
    response = client.post("/api/labs/embed", headers=headers, json={"texts": ["   "]})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0


def test_single_text_projection_does_not_crash(client, make_user) -> None:
    """One point cannot define two axes; the maths must not divide by zero."""
    headers, _ = make_user()
    body = client.post(
        "/api/labs/embed", headers=headers, json={"texts": ["only one text"]}
    ).json()
    assert len(body["projection"]) == 1


# ---------------------------------------------------------------------------
# Stage contract
# ---------------------------------------------------------------------------
def test_stages_endpoint_requires_authentication(client) -> None:
    assert client.get("/api/labs/stages").status_code == 401


def test_stages_endpoint_returns_the_canonical_list(client, make_user) -> None:
    headers, _ = make_user()
    body = client.get("/api/labs/stages", headers=headers).json()
    assert [item["stage"] for item in body["stages"]] == [
        item["stage"] for item in stage_definitions()
    ]


def test_grounding_runs_after_citation_resolution() -> None:
    """The regression.

    The canonical list had these two the wrong way round. Grounding needs to know
    which citations resolved before it can judge whether the evidence supports the
    answer, so citation resolution genuinely runs first.
    """
    order = [item["stage"] for item in stage_definitions()]
    assert order.index("citation_resolution") < order.index("grounding")


def test_canonical_order_matches_the_source() -> None:
    """Read the real call order out of the pipeline and retriever sources.

    A hand-maintained list will eventually drift from the code it describes. This
    compares the list against the order the `trace.stage(...)` calls actually
    appear in, so the contract is checked against the implementation rather than
    against another copy of itself.
    """
    def stage_mentions(path: Path) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.search(r'trace\.(?:stage|add)\(\s*"([a-z_]+)"', line)
            if match:
                found.append((number, match.group(1)))
        return found

    seen: list[str] = []
    for path in (RETRIEVER_FILE, PIPELINE_FILE):
        for _, stage in stage_mentions(path):
            if stage not in seen:
                seen.append(stage)

    canonical = [item["stage"] for item in stage_definitions()]
    conditional = {item["stage"] for item in conditional_stage_definitions()}

    # Every stage the code can emit must be declared somewhere - either in the
    # ordered flow or as a conditional stage. An undeclared stage would reach the
    # UI unlabelled, which is a gap the reader cannot see.
    for stage in seen:
        assert stage in canonical or stage in conditional, (
            f"{stage} can appear in a trace but is not declared by stage_definitions() "
            f"or conditional_stage_definitions()"
        )

    # The fixed-flow stages must appear in the same relative order.
    ordered = [stage for stage in seen if stage in canonical]
    positions = [canonical.index(stage) for stage in ordered]
    assert positions == sorted(positions), (
        f"execution order {ordered} contradicts the canonical order {canonical}"
    )


def test_conditional_stages_are_not_in_the_fixed_order() -> None:
    """A conditional stage drawn as an ordinary step would show a pipeline that
    never runs."""
    ordered = {item["stage"] for item in stage_definitions()}
    for item in conditional_stage_definitions():
        assert item["stage"] not in ordered


def test_every_conditional_stage_has_a_label() -> None:
    for item in conditional_stage_definitions():
        assert item["label"] and item["label"] != item["stage"]


def test_stages_endpoint_exposes_conditional_stages(client, make_user) -> None:
    headers, _ = make_user()
    body = client.get("/api/labs/stages", headers=headers).json()
    assert [item["stage"] for item in body["conditional_stages"]] == [
        item["stage"] for item in conditional_stage_definitions()
    ]


def test_frontend_explains_every_stage(client, make_user) -> None:
    """The laboratory must not silently omit a stage the pipeline runs.

    A missing explanation is not a crash - it is a gap a learner cannot see, which
    is the failure mode this whole project is about.
    """
    explanations = (
        PROJECT_ROOT
        / "frontend"
        / "src"
        / "components"
        / "rag"
        / "stageExplanations.ts"
    )
    if not explanations.is_file():
        pytest.skip("frontend sources are not present in this checkout")

    source = explanations.read_text(encoding="utf-8")
    frontend_stages = re.findall(r'^\s*stage:\s*"([a-z_]+)"', source, re.MULTILINE)

    backend_all = [item["stage"] for item in stage_definitions()] + [
        item["stage"] for item in conditional_stage_definitions()
    ]

    for stage in backend_all:
        assert stage in frontend_stages, f"the UI has no explanation for '{stage}'"

    # And nothing invented: the UI must not explain a stage that does not exist.
    for stage in frontend_stages:
        assert stage in backend_all, f"the UI explains '{stage}', which the backend never emits"


# ---------------------------------------------------------------------------
# Trace timestamps ("Live RAG Trace")
# ---------------------------------------------------------------------------
def test_recorded_events_carry_a_completion_timestamp() -> None:
    """The log-style trace needs wall-clock times, not just durations.

    Durations say how long each stage took but not when it happened, so they
    cannot produce a view like:

        16:32:01  Query processed
        16:32:02  Searching vector database
    """
    from app.rag.trace import TraceRecorder

    recorder = TraceRecorder()
    with recorder.stage("query_analysis"):
        pass

    event = recorder.events[0]
    assert event.created_at is not None
    assert event.as_dict()["created_at"]


def test_trace_timestamps_are_monotonic() -> None:
    """A later stage must not be stamped earlier than an earlier one."""
    from app.rag.trace import TraceRecorder

    recorder = TraceRecorder()
    for name in ("query_analysis", "query_embedding", "vector_search"):
        with recorder.stage(name):
            pass

    stamps = [event.created_at for event in recorder.events]
    assert stamps == sorted(stamps)


def test_persisting_a_trace_uses_the_event_timestamp_not_the_insert_time() -> None:
    """The regression that made every stage share one second.

    The column default fires at INSERT time, which is after the answer has
    finished. Passing the event's own timestamp is the only way the stored trace
    shows when each stage actually ran. Guarded here by checking the persistence
    call site explicitly, because the failure is invisible in the response and
    only shows up when the trace is read back later.
    """
    import inspect

    from app.api import routes_chat

    source = inspect.getsource(routes_chat)
    # The persist loop must set created_at from the event.
    assert "created_at=event.created_at" in source

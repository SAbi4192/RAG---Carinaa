"""Conversational memory, follow-up resolution and page-aware retrieval.

Three behaviours, all of which previously failed and all of which are easy to break
silently:

1. **Page references.** The page metadata already existed on every chunk; nothing
   looked for it. Dense retrieval compares MEANING, and "the second page" has no
   semantic relationship to whatever is written on page 2, so the constraint has to be
   a metadata filter rather than a hope about ranking.

2. **Follow-up detection.** The asymmetry matters and is tested explicitly: failing to
   rewrite a follow-up merely retrieves less well, while rewriting a standalone
   question sends retrieval after something the user never asked. The first version of
   this heuristic flagged "What is a hypervisor?" as a follow-up purely for being
   short, which is the harmful direction.

3. **Conversation memory.** "What is my name?" must be answerable from the chat, and
   the conversation must never be citable as though it were a document.
"""

from __future__ import annotations

import pytest

from app.rag.understanding import (
    clean_rewrite,
    detect_page_reference,
    needs_conversation_context,
)


# ---------------------------------------------------------------------------
# Page reference detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question,expected",
    [
        ("Tell me about the second page of the PDF?", 2),
        ("Tell me about page 2.", 2),
        ("What is explained on page 5?", 5),
        ("Summarize page 3.", 3),
        ("whats on p.12", 12),
        ("explain the 4th page", 4),
        ("page five please", 5),
        ("Tell me about page 12 of ADT_Notes.pdf", 12),
        ("the twentieth page", 20),
    ],
)
def test_page_references_are_detected(question: str, expected: int) -> None:
    number, _phrase, _relative, _direction = detect_page_reference(question)
    assert number == expected


@pytest.mark.parametrize(
    "question",
    [
        "How does virtualization work?",
        "What are the main differences between the concepts covered?",
        "Summarize this document.",
        "My name is Abishek.",
    ],
)
def test_questions_without_page_references_are_left_alone(question: str) -> None:
    """A false positive here would filter retrieval to a page nobody asked about."""
    number, _phrase, relative, _direction = detect_page_reference(question)
    assert number is None
    assert relative is False


def test_relative_page_references_are_detected_but_not_resolved() -> None:
    """Resolving "the next page" needs the page the conversation is on, which the
    detector does not know. Reporting the direction is honest; guessing a number
    would not be."""
    number, phrase, relative, direction = detect_page_reference("what about the next page?")
    assert number is None
    assert relative is True
    assert direction == "next"
    assert phrase


def test_page_zero_is_not_treated_as_a_page() -> None:
    number, _phrase, _relative, _direction = detect_page_reference("page 0")
    assert number is None


# ---------------------------------------------------------------------------
# Follow-up detection - both directions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        "What is my name?",
        "What about the second one?",
        "and the third?",
        "explain that",
        "tell me more",
        "Why is that?",
        "What is my favourite topic?",
        "but why?",
        "the second one",
    ],
)
def test_follow_ups_are_detected(question: str) -> None:
    assert needs_conversation_context(question, history_length=4) is True


@pytest.mark.parametrize(
    "question",
    [
        "What is a hypervisor?",
        "Explain photosynthesis.",
        "Summarize page 3.",
        "How does virtualization work?",
        "What are the main differences between the concepts covered?",
        "List the service models of cloud computing.",
    ],
)
def test_standalone_questions_are_not_rewritten(question: str) -> None:
    """The harmful direction of the error.

    Rewriting an independent question changes what retrieval searches for, so a
    false positive corrupts a question that would otherwise have worked. Length
    alone must never qualify a question as a follow-up.
    """
    assert needs_conversation_context(question, history_length=4) is False


def test_nothing_is_a_follow_up_without_history() -> None:
    """With no history there is nothing to resolve against, so the rewrite would be
    operating on nothing."""
    for question in ("What is my name?", "and the third?", "tell me more"):
        assert needs_conversation_context(question, history_length=0) is False


# ---------------------------------------------------------------------------
# Rewrite sanitising
# ---------------------------------------------------------------------------
def test_clean_rewrite_strips_model_wrappers() -> None:
    assert clean_rewrite('Rewritten question: What are the stages of photosynthesis?', "x") == (
        "What are the stages of photosynthesis?"
    )
    assert clean_rewrite('"What are the stages?"', "x") == "What are the stages?"


def test_clean_rewrite_takes_only_the_first_line() -> None:
    """A small model that answers instead of rewriting produces several lines."""
    raw = "What are the stages of photosynthesis?\nPhotosynthesis has two stages."
    assert clean_rewrite(raw, "What are its stages?") == "What are the stages of photosynthesis?"


def test_clean_rewrite_falls_back_to_the_original_when_unusable() -> None:
    original = "What about the second one?"
    # Empty.
    assert clean_rewrite("", original) == original
    # Whitespace only.
    assert clean_rewrite("   \n  ", original) == original
    # Absurdly long - clearly an answer rather than a rewrite.
    assert clean_rewrite("word " * 400, original) == original


# ---------------------------------------------------------------------------
# Page filtering in the vector store
# ---------------------------------------------------------------------------
def test_page_filter_builds_a_narrowing_clause_only() -> None:
    """The page filter must never replace the workspace condition.

    If it did, a page query could reach vectors outside the workspace - turning a
    convenience feature into a data-leak. The workspace clause is checked here
    explicitly because that failure would be silent.
    """
    from app.rag.vectorstore import VectorStore

    store = VectorStore.instance()
    clause = store._scope_filter(7, [3], page_number=2)
    text = str(clause)

    assert "workspace_id" in text, "the workspace condition must survive"
    assert "page_number" in text
    assert "$and" in text
    # Updated when the filter became a range test: a chunk spanning pages 1-4 must be
    # found by a question about page 2, which equality on either bound could not do.


def test_page_filter_alone_still_scopes_to_the_workspace() -> None:
    from app.rag.vectorstore import VectorStore

    store = VectorStore.instance()
    clause = store._scope_filter(7, None, page_number=4)
    assert "workspace_id" in str(clause)


def test_no_page_filter_means_no_page_condition() -> None:
    from app.rag.vectorstore import VectorStore

    store = VectorStore.instance()
    clause = store._scope_filter(7, None, page_number=None)
    assert "page_number" not in str(clause)


# ---------------------------------------------------------------------------
# Relative page references
# ---------------------------------------------------------------------------
def test_relative_page_resolves_from_the_last_page_asked_about() -> None:
    from app.rag.references import resolve_relative_page

    history = [
        {"role": "user", "content": "Tell me about page 5"},
        {"role": "assistant", "content": "Page 5 covers the introduction."},
    ]
    assert resolve_relative_page("next", history) == (6, "the last page you asked about")
    assert resolve_relative_page("previous", history) == (4, "the last page you asked about")
    assert resolve_relative_page("current", history) == (5, "the last page you asked about")


def test_relative_page_uses_the_last_answer_when_no_page_was_asked() -> None:
    """The user may never have named a page - the previous answer's sources still
    establish where the conversation is."""
    from app.rag.references import resolve_relative_page

    assert resolve_relative_page("next", [], last_cited_pages=[7, 9]) == (
        10,
        "the page the last answer came from",
    )


def test_relative_page_is_not_guessed_without_evidence() -> None:
    """No page was ever mentioned, so there is nothing to be relative TO. Returning
    a number here would send retrieval to an invented page."""
    from app.rag.references import resolve_relative_page

    assert resolve_relative_page("next", []) == (None, "")
    assert resolve_relative_page("previous", []) == (None, "")


def test_relative_page_prefers_the_users_own_reference() -> None:
    """A page the user asked about outranks pages inferred from citations."""
    from app.rag.references import resolve_relative_page

    history = [{"role": "user", "content": "what is on page 3?"}]
    assert resolve_relative_page("next", history, last_cited_pages=[40]) == (
        4,
        "the last page you asked about",
    )


# ---------------------------------------------------------------------------
# Document disambiguation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        "tell me about ADT_Notes.pdf",
        "summarize ADT_Notes",
        "what about adt notes page 2",
        "whats in the file ADT_Notes.pdf?",
    ],
)
def test_a_named_document_is_recognised(question: str) -> None:
    """People write the stem far more often than the full filename, and case and
    separators vary. All of these must resolve to the same document."""
    from app.rag.references import document_mentioned

    assert document_mentioned(question, ["ADT_Notes.pdf", "DBMS_Notes.pdf"]) == "ADT_Notes.pdf"


def test_an_unnamed_document_is_not_invented() -> None:
    from app.rag.references import document_mentioned

    assert document_mentioned("tell me about page 2", ["ADT_Notes.pdf"]) is None


def test_a_page_question_across_two_documents_asks_which() -> None:
    """The important case: a confident answer from the wrong document is worse than
    a clarifying question."""
    from app.rag.references import needs_document_clarification

    result = needs_document_clarification(
        "tell me about page 2", 2, ["ADT_Notes.pdf", "DBMS_Notes.pdf"]
    )
    assert result == ["ADT_Notes.pdf", "DBMS_Notes.pdf"]


def test_naming_a_document_removes_the_ambiguity() -> None:
    from app.rag.references import needs_document_clarification

    assert (
        needs_document_clarification(
            "tell me about page 2 of ADT_Notes", 2, ["ADT_Notes.pdf", "DBMS_Notes.pdf"]
        )
        is None
    )


def test_a_single_document_is_never_ambiguous() -> None:
    from app.rag.references import needs_document_clarification

    assert needs_document_clarification("tell me about page 2", 2, ["ADT_Notes.pdf"]) is None


def test_a_non_page_question_is_never_ambiguous() -> None:
    """Only a PAGE reference triggers clarification.

    "Summarize this document" with two documents in scope is a different situation -
    summarising both is reasonable, whereas "page 2" has no meaning across two
    documents. Treating them the same would nag the user for no reason.
    """
    from app.rag.references import needs_document_clarification

    assert (
        needs_document_clarification(
            "summarize this document", None, ["ADT_Notes.pdf", "DBMS_Notes.pdf"]
        )
        is None
    )


# ---------------------------------------------------------------------------
# Structural regression: methods must stay ON the class
# ---------------------------------------------------------------------------
def test_retriever_keeps_all_of_its_methods() -> None:
    """A regression guard for a specific accident.

    A patch once inserted a module-level helper into the middle of the Retriever
    class. The next method was still indented, so Python parsed it as a nested
    function inside that helper - after its `return`, therefore unreachable - and
    `Retriever` silently lost the method.

    It went unnoticed because the lost method is only called when retrieval returns
    ZERO candidates, which a page filter matching nothing produces but an ordinary
    query does not. So every existing test passed while a real request could 500.

    This asserts the methods exist on the class rather than merely existing somewhere
    in the file, which is exactly the distinction that was missed.
    """
    from app.rag.retriever import Retriever

    for name in (
        "retrieve",
        "_dedupe",
        "_join_chunk_rows",
        "_assert_workspace_has_documents",
    ):
        assert hasattr(Retriever, name), (
            f"Retriever lost {name!r} - it may have been nested inside a "
            f"module-level function by an edit"
        )


def test_no_public_method_is_nested_inside_a_module_level_function() -> None:
    """Catch the same class of accident more generally.

    Any `def` inside the retriever that is indented under a module-level function -
    rather than under the class - is almost certainly a mis-edit, because the file's
    only two top-level definitions are the class and one helper.
    """
    import ast
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[1] / "app" / "rag" / "retriever.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    nested: list[str] = []

    def walk(node: ast.AST, inside_module_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A def directly under a module-level function is the accident.
                if inside_module_function and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nested.append(child.name)
                walk(child, inside_module_function or not isinstance(node, ast.ClassDef))
            elif isinstance(child, ast.ClassDef):
                walk(child, False)
            else:
                walk(child, inside_module_function)

    walk(tree, False)
    assert not nested, f"these methods are nested inside module-level functions: {nested}"


# ---------------------------------------------------------------------------
# Section references
# ---------------------------------------------------------------------------
SECTIONS = [
    "Applying the Forge Innovation Rubric",
    "Interviewing Techniques",
    "Steps in Human-Centered Design",
    "1.3 Discovering areas of opportunity",
    "Principles of Human-Centered Design",
]


def test_a_named_section_is_matched() -> None:
    from app.rag.sections import match_section

    assert match_section("tell me about the Interviewing Techniques section", SECTIONS) == (
        "Interviewing Techniques"
    )
    assert (
        match_section("what does the section on Applying the Forge Innovation Rubric say", SECTIONS)
        == "Applying the Forge Innovation Rubric"
    )


def test_section_matching_ignores_numbering_and_filler() -> None:
    """Real section titles carry numbering and course codes that nobody types, so
    matching on the raw string would only work for tidy names."""
    from app.rag.sections import match_section

    assert match_section("explain Discovering areas of opportunity", SECTIONS) == (
        "1.3 Discovering areas of opportunity"
    )


def test_an_unrelated_question_matches_no_section() -> None:
    """The important direction: a false positive narrows retrieval to a section the
    user never asked about, which silently hides the right answer."""
    from app.rag.sections import match_section

    assert match_section("How does virtualization work?", SECTIONS) is None
    assert match_section("What are the main differences between the concepts?", SECTIONS) is None
    assert match_section("Tell me about page 2", SECTIONS) is None


def test_no_sections_available_matches_nothing() -> None:
    from app.rag.sections import match_section

    assert match_section("tell me about Interviewing Techniques", []) is None


def test_relative_sections_are_detected_not_resolved() -> None:
    """Resolving "the next section" needs the section the conversation is in, which
    this layer does not know. Reporting the direction is honest; inventing a section
    would not be."""
    from app.rag.sections import detect_relative_section

    assert detect_relative_section("what is in the next section") == "next"
    assert detect_relative_section("the previous chapter") == "previous"
    assert detect_relative_section("the current section") == "current"
    assert detect_relative_section("what is a hypervisor") == ""


def test_section_filter_narrows_without_replacing_the_workspace() -> None:
    """Like the page filter: it may only narrow, never widen."""
    from app.rag.vectorstore import VectorStore

    store = VectorStore.instance()
    clause = store._scope_filter(7, [3], section="Interviewing Techniques")
    text = str(clause)
    assert "workspace_id" in text, "the workspace condition must survive"
    assert "Interviewing Techniques" in text


# ---------------------------------------------------------------------------
# Page ranges must account for chunks that SPAN pages
# ---------------------------------------------------------------------------
def test_page_filter_covers_a_chunk_that_spans_the_page() -> None:
    """Found by running the page feature against a real PDF for the first time.

    A short document becomes ONE chunk covering all of its pages, recorded as
    `page_number: 1, page_end: 4`. The original filter matched
    `page_number == N OR page_end == N`, which that chunk satisfies for page 1 and
    page 4 but NOT for page 2 - so asking about page 2 of a four-page document found
    nothing, even though the chunk contains page 2.

    A chunk covers page N when it starts at or before N and ends at or after N.
    """
    from app.rag.vectorstore import VectorStore

    store = VectorStore.instance()
    clause = store._scope_filter(21, [30], page_number=2)
    text = str(clause)

    # Both bounds present, as a range test rather than an equality test.
    assert "$lte" in text and "$gte" in text, (
        "the page condition must be a range test, not equality on either bound"
    )
    assert "page_number" in text and "page_end" in text


def test_page_filter_still_scopes_to_the_workspace() -> None:
    """The range test must not have replaced the workspace condition."""
    from app.rag.vectorstore import VectorStore

    store = VectorStore.instance()
    clause = store._scope_filter(7, None, page_number=3)
    text = str(clause)
    assert "workspace_id" in text
    assert "$lte" in text and "$gte" in text


def test_page_range_uses_page_end_for_the_upper_bound() -> None:
    """A four-page document stored as one chunk reports page_number=1 and page_end=4.

    Reading `page_number` for both bounds gave "pages 1 to 1" and rejected every page
    question as out of range - a document that plainly has four pages claiming to have
    one.
    """
    import inspect

    from app.api import routes_chat

    source = inspect.getsource(routes_chat._page_range)
    assert 'doc_metadata["page_end"]' in source, (
        "_page_range must read page_end for the upper bound, not page_number"
    )


# ---------------------------------------------------------------------------
# Unit / module references
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question,expected_roman",
    [
        ("What does the third unit say?", "III"),
        ("What is the name of UNIT III?", "III"),
        ("Summarise Unit III", "III"),
        ("unit 4", "IV"),
        ("the second chapter", "II"),
        ("unit five", "V"),
        ("third module", "III"),
        ("5th unit", "V"),
        ("first part", "I"),
    ],
)
def test_unit_references_are_detected_in_every_form(question: str, expected_roman: str) -> None:
    """Roman, Arabic and word forms all name the same thing.

    The reported failure was exactly this: "the third unit" and "UNIT III" are the same
    concept in two alphabets, and nothing connected them. Embeddings cannot bridge an
    ordinal to a Roman numeral - that is a transformation, not a similarity.
    """
    from app.rag.units import detect_unit_reference

    number, roman, phrase = detect_unit_reference(question)
    assert number is not None, f"{question!r} should name a unit"
    assert roman == expected_roman
    assert phrase


@pytest.mark.parametrize(
    "question",
    [
        "How does virtualization work?",
        "Tell me about page 2",
        "What are the main differences between the concepts covered?",
        "Summarise this document.",
    ],
)
def test_questions_without_unit_references_are_untouched(question: str) -> None:
    """A false positive would append a unit number to an unrelated question."""
    from app.rag.units import detect_unit_reference

    number, roman, _phrase = detect_unit_reference(question)
    assert number is None
    assert roman == ""


def test_unit_reference_is_expanded_not_substituted() -> None:
    """The user's wording still drives the semantic search.

    Substituting would replace a natural question with a bare "UNIT III", discarding the
    meaning the user expressed. Appending adds the token the document actually uses
    without losing anything.
    """
    from app.rag.units import expand_unit_reference

    expanded, note = expand_unit_reference("What does the third unit say?")
    assert expanded.startswith("What does the third unit say?")
    assert "UNIT III" in expanded
    assert note


def test_an_already_canonical_reference_is_not_duplicated() -> None:
    """Appending "UNIT III" to a question that already says "UNIT III" would weight the
    same tokens twice, skewing the embedding for no benefit."""
    from app.rag.units import expand_unit_reference

    expanded, note = expand_unit_reference("What is the name of UNIT III?")
    assert expanded == "What is the name of UNIT III?"
    assert note == ""


def test_expansion_is_a_no_op_without_a_reference() -> None:
    from app.rag.units import expand_unit_reference

    question = "What is a hypervisor?"
    expanded, note = expand_unit_reference(question)
    assert expanded == question
    assert note == ""

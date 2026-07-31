"""RetrievalOutcome: the shape the trace and the prompt both want, db-free."""

from uuid import uuid4

from econometrica.tools.retrieval import RetrievalOutcome, Retrieved


def a_hit(name="notes.txt", ordinal=0, text="Beta exceeded one.") -> Retrieved:
    return Retrieved(
        document_id=uuid4(), document_name=name, ordinal=ordinal, text=text, score=0.9
    )


def test_the_step_record_names_the_model_and_query_under_the_planner():
    outcome = RetrievalOutcome(model="all-minilm", query="beta", hits=[a_hit()])

    step = outcome.to_step_record()

    assert step.agent == "planner"  # the retrieval feeds the planner
    assert step.kind == "tool"
    assert step.tool == "retrieval:all-minilm"
    assert step.status == "ok"
    assert "beta" in step.detail
    assert "1 passage" in step.detail


def test_a_failed_outcome_is_a_failed_step_with_its_reason():
    outcome = RetrievalOutcome(
        model="all-minilm", query="beta", failed=True, detail="ollama unreachable"
    )

    step = outcome.to_step_record()

    assert step.status == "failed"
    assert "ollama unreachable" in step.detail


def test_the_context_attributes_every_passage_and_marks_it_read_not_computed():
    outcome = RetrievalOutcome(
        model="all-minilm",
        query="beta",
        hits=[a_hit(name="a.txt", ordinal=2, text="Beta exceeded one.")],
    )

    context = outcome.as_context()

    assert "a.txt" in context and "#2" in context
    assert "Beta exceeded one." in context
    assert "not computed" in context.lower()


def test_an_empty_outcome_produces_no_context():
    assert RetrievalOutcome(model="m", query="q").as_context() == ""
    assert RetrievalOutcome(model="m", query="q", failed=True, detail="x").as_context() == ""

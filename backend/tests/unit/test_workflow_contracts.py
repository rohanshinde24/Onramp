from onramp.workflows.contracts import AGENT_TASK_QUEUE, CaseWorkflowInput, case_workflow_id


def test_case_workflow_id_is_stable() -> None:
    assert case_workflow_id("case_123") == "case-case_123"


def test_case_workflow_uses_the_agent_queue_by_default() -> None:
    assert CaseWorkflowInput("acme", "case_123").agent_task_queue == AGENT_TASK_QUEUE

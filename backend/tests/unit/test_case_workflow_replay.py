"""Workflows started before a change to ``CaseWorkflow`` must replay on the new code.

The fixture is the history of a real case workflow from the load benchmark, recorded before
``reuse-activity-snapshots``: intake, checks and a proposal waiting for an operator. Replaying
it fails with a nondeterminism error if the change is not behind its patch.
"""

import asyncio
from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from onramp.workflows.case import CaseWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "case_workflow_pre_patch_history.json"


def test_a_workflow_started_before_the_patch_replays_on_current_code() -> None:
    history = WorkflowHistory.from_json("case-pre-patch", FIXTURE.read_text())

    result = asyncio.run(Replayer(workflows=[CaseWorkflow]).replay_workflow(history))

    assert result.replay_failure is None

import asyncio
from datetime import UTC, datetime, timedelta

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from onramp.cases.state import CaseState
from onramp.workflows.case import CaseWorkflow
from onramp.workflows.contracts import (
    WORKFLOW_TASK_QUEUE,
    CaseSignal,
    CaseSnapshot,
    CaseWorkflowInput,
    case_workflow_id,
)


class _SignalPlan:
    def __init__(self) -> None:
        self.reads = 0
        self.first_read = asyncio.Event()

    @activity.defn(name="read_case")
    async def read_case(self, _: CaseWorkflowInput) -> CaseSnapshot:
        self.reads += 1
        if self.reads == 1:
            self.first_read.set()
            return CaseSnapshot(CaseState.AWAITING_APPROVAL.value)
        return CaseSnapshot(CaseState.RESOLVED.value)


def test_duplicate_signal_wakes_the_workflow_once() -> None:
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            plan = _SignalPlan()
            async with Worker(
                environment.client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[CaseWorkflow],
                activities=[plan.read_case],
            ):
                request = CaseWorkflowInput("acme", "case_1")
                handle = await environment.client.start_workflow(
                    CaseWorkflow.run,
                    request,
                    id=case_workflow_id(request.case_id),
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await plan.first_read.wait()
                signal = CaseSignal("evt_1")
                await handle.signal(CaseWorkflow.approval_decided, signal)
                await handle.signal(CaseWorkflow.approval_decided, signal)

                result = await handle.result()

                assert result.state == CaseState.RESOLVED
                assert result.applied_signal_ids == ("evt_1",)
                assert plan.reads == 2

    asyncio.run(scenario())


def test_signal_waits_in_history_across_worker_restart() -> None:
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            plan = _SignalPlan()
            request = CaseWorkflowInput("acme", "case_restart")
            async with Worker(
                environment.client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[CaseWorkflow],
                activities=[plan.read_case],
            ):
                handle = await environment.client.start_workflow(
                    CaseWorkflow.run,
                    request,
                    id=case_workflow_id(request.case_id),
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await plan.first_read.wait()

            await handle.signal(CaseWorkflow.case_changed, CaseSignal("evt_while_down"))

            async with Worker(
                environment.client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[CaseWorkflow],
                activities=[plan.read_case],
            ):
                result = await handle.result()

            assert result.state == CaseState.RESOLVED
            assert result.applied_signal_ids == ("evt_while_down",)

    asyncio.run(scenario())


class _TimerPlan:
    def __init__(self) -> None:
        self.resumed = False
        self.deadline = datetime.now(UTC) + timedelta(days=5)

    @activity.defn(name="read_case")
    async def read_case(self, _: CaseWorkflowInput) -> CaseSnapshot:
        if self.resumed:
            return CaseSnapshot(CaseState.RESOLVED.value)
        return CaseSnapshot(CaseState.WAITING_FOR_SETTLEMENT.value, wait_until=self.deadline)

    @activity.defn(name="resume_settlement_checks")
    async def resume_settlement_checks(self, _: CaseWorkflowInput) -> CaseSnapshot:
        self.resumed = True
        return CaseSnapshot(CaseState.INITIAL_CHECKS.value)


def test_settlement_deadline_uses_time_skipping() -> None:
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            plan = _TimerPlan()
            async with Worker(
                environment.client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[CaseWorkflow],
                activities=[plan.read_case, plan.resume_settlement_checks],
            ):
                request = CaseWorkflowInput("acme", "case_timer")
                result = await environment.client.execute_workflow(
                    CaseWorkflow.run,
                    request,
                    id=case_workflow_id(request.case_id),
                    task_queue=WORKFLOW_TASK_QUEUE,
                )

                assert result.state == CaseState.RESOLVED
                assert plan.resumed

    asyncio.run(scenario())


class _EvidenceTimerPlan:
    def __init__(self) -> None:
        self.expired = False
        self.deadline = datetime.now(UTC) + timedelta(days=7)

    @activity.defn(name="read_case")
    async def read_case(self, _: CaseWorkflowInput) -> CaseSnapshot:
        if self.expired:
            return CaseSnapshot(CaseState.ESCALATED.value)
        return CaseSnapshot(CaseState.WAITING_FOR_EVIDENCE.value, wait_until=self.deadline)

    @activity.defn(name="expire_evidence_wait")
    async def expire_evidence_wait(self, _: CaseWorkflowInput) -> CaseSnapshot:
        self.expired = True
        return CaseSnapshot(CaseState.ESCALATED.value)


def test_evidence_deadline_uses_time_skipping() -> None:
    async def scenario() -> None:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            plan = _EvidenceTimerPlan()
            async with Worker(
                environment.client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[CaseWorkflow],
                activities=[plan.read_case, plan.expire_evidence_wait],
            ):
                request = CaseWorkflowInput("acme", "case_evidence_timer")
                handle = await environment.client.start_workflow(
                    CaseWorkflow.run,
                    request,
                    id=case_workflow_id(request.case_id),
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await environment.sleep(timedelta(days=8))

                assert await handle.query(CaseWorkflow.applied_signal_ids) == []
                assert plan.expired

    asyncio.run(scenario())

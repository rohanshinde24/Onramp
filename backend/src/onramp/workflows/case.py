from contextlib import suppress
from datetime import timedelta
from typing import cast

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from onramp.cases.state import CaseState
    from onramp.workflows.contracts import (
        CaseSignal,
        CaseSnapshot,
        CaseWorkflowInput,
        WorkflowResult,
    )

_ACTIVITY_TIMEOUT = timedelta(minutes=2)
_RETRY = RetryPolicy(maximum_attempts=5)
_PASSIVE_STATES = frozenset(
    {
        CaseState.WAITING_FOR_EVIDENCE,
        CaseState.AWAITING_APPROVAL,
        CaseState.PROPOSED,
        CaseState.ESCALATED,
    }
)


@workflow.defn
class CaseWorkflow:
    def __init__(self) -> None:
        self._seen_signal_ids: set[str] = set()
        self._wake_version = 0
        self._waiting_version = 0

    @workflow.run
    async def run(self, request: CaseWorkflowInput) -> WorkflowResult:
        while True:
            snapshot = await self._activity("read_case", request)
            state = CaseState(snapshot.state)
            if state in {CaseState.RESOLVED, CaseState.CLOSED_NO_ACTION}:
                return WorkflowResult(state.value, tuple(sorted(self._seen_signal_ids)))
            if state in {CaseState.CREATED, CaseState.INITIAL_CHECKS}:
                await self._activity("run_case_checks", request)
                continue
            if state is CaseState.INVESTIGATING:
                await self._activity(
                    "run_investigation", request, task_queue=request.agent_task_queue
                )
                continue
            if state is CaseState.EXECUTING:
                await self._activity("execute_approved_action", request)
                continue
            if state is CaseState.WAITING_FOR_SETTLEMENT:
                await self._wait_for_signal_or_deadline(snapshot)
                await self._activity("resume_settlement_checks", request)
                continue
            if state is CaseState.VERIFYING:
                await self._activity("recover_action_verification", request)
                continue
            if state in _PASSIVE_STATES:
                self._waiting_version = self._wake_version
                await workflow.wait_condition(self._has_new_signal)
                continue
            raise RuntimeError(f"unsupported case state {state.value}")

    async def _wait_for_signal_or_deadline(self, snapshot: CaseSnapshot) -> None:
        if snapshot.wait_until is None:
            raise RuntimeError("settlement wait has no deadline")
        self._waiting_version = self._wake_version
        delay = snapshot.wait_until - workflow.now()
        if delay <= timedelta(0):
            return
        with suppress(TimeoutError):
            await workflow.wait_condition(
                self._has_new_signal,
                timeout=delay,
            )

    async def _activity(
        self,
        name: str,
        request: CaseWorkflowInput,
        *,
        task_queue: str | None = None,
    ) -> CaseSnapshot:
        return cast(
            CaseSnapshot,
            await workflow.execute_activity(
                name,
                request,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
                task_queue=task_queue,
                result_type=CaseSnapshot,
            ),
        )

    def _has_new_signal(self) -> bool:
        return self._wake_version != self._waiting_version

    @workflow.signal
    def evidence_arrived(self, signal: CaseSignal) -> None:
        self._apply_signal(signal)

    @workflow.signal
    def approval_decided(self, signal: CaseSignal) -> None:
        self._apply_signal(signal)

    @workflow.signal
    def operator_escalated(self, signal: CaseSignal) -> None:
        self._apply_signal(signal)

    @workflow.signal
    def case_changed(self, signal: CaseSignal) -> None:
        self._apply_signal(signal)

    @workflow.query
    def applied_signal_ids(self) -> list[str]:
        return sorted(self._seen_signal_ids)

    def _apply_signal(self, signal: CaseSignal) -> None:
        if signal.event_id in self._seen_signal_ids:
            return
        self._seen_signal_ids.add(signal.event_id)
        self._wake_version += 1

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
_READ_TIMEOUT = timedelta(seconds=30)
# Workflows started before this patch replay on the original loop; new ones take the loop with
# fewer server round trips. Remove the original loop once no workflow started before it is
# still open.
REUSE_SNAPSHOTS_PATCH = "reuse-activity-snapshots"
ACTIVITY_RETRY = RetryPolicy(maximum_attempts=5)
# A provider outage can outlast any fixed number of attempts. The case keeps waiting in
# INVESTIGATING, retried with capped backoff, until the provider answers or a person acts.
INVESTIGATION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=0,
)
_PASSIVE_STATES = frozenset(
    {
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
        if workflow.patched(REUSE_SNAPSHOTS_PATCH):
            return await self._run(request)
        return await self._run_before_patch(request)

    async def _run(self, request: CaseWorkflowInput) -> WorkflowResult:
        """Each step's activity returns the case as it left it, so the loop decides on that
        instead of reading again, and the reads that remain (at start and after a wake) are
        local activities, which complete inside the workflow task. A case that checks settle
        costs one activity and two workflow tasks on the server instead of three and four.

        ``observed_signals`` is taken before the call whose snapshot decides a wait. A signal
        that lands during that call moves the wake counter past it, so the wait returns at once
        and the case is read again: the lost-wake rule holds.
        """
        observed_signals = self._wake_version
        snapshot = await self._read_case(request)
        while True:
            state = CaseState(snapshot.state)
            if state in {CaseState.RESOLVED, CaseState.CLOSED_NO_ACTION}:
                return WorkflowResult(state.value, tuple(sorted(self._seen_signal_ids)))
            if state in _PASSIVE_STATES:
                self._waiting_version = observed_signals
                await workflow.wait_condition(self._has_new_signal)
                observed_signals = self._wake_version
                snapshot = await self._read_case(request)
                continue
            if state is CaseState.WAITING_FOR_SETTLEMENT:
                await self._wait_for_signal_or_deadline(snapshot, observed_signals)
                observed_signals = self._wake_version
                snapshot = await self._activity("resume_settlement_checks", request)
                continue
            if state is CaseState.WAITING_FOR_EVIDENCE:
                await self._wait_for_signal_or_deadline(snapshot, observed_signals)
                observed_signals = self._wake_version
                snapshot = await self._activity("expire_evidence_wait", request)
                continue
            observed_signals = self._wake_version
            if state in {CaseState.CREATED, CaseState.INITIAL_CHECKS}:
                snapshot = await self._activity("run_case_checks", request)
            elif state is CaseState.INVESTIGATING:
                snapshot = await self._activity(
                    "run_investigation",
                    request,
                    task_queue=request.agent_task_queue,
                    retry_policy=INVESTIGATION_RETRY,
                )
            elif state is CaseState.EXECUTING:
                snapshot = await self._activity("execute_approved_action", request)
            elif state is CaseState.VERIFYING:
                snapshot = await self._activity("recover_action_verification", request)
            else:
                raise RuntimeError(f"unsupported case state {state.value}")

    async def _read_case(self, request: CaseWorkflowInput) -> CaseSnapshot:
        return cast(
            CaseSnapshot,
            await workflow.execute_local_activity(
                "read_case",
                request,
                start_to_close_timeout=_READ_TIMEOUT,
                retry_policy=ACTIVITY_RETRY,
                result_type=CaseSnapshot,
            ),
        )

    async def _run_before_patch(self, request: CaseWorkflowInput) -> WorkflowResult:
        while True:
            observed_signals = self._wake_version
            snapshot = await self._activity("read_case", request)
            state = CaseState(snapshot.state)
            if state in {CaseState.RESOLVED, CaseState.CLOSED_NO_ACTION}:
                return WorkflowResult(state.value, tuple(sorted(self._seen_signal_ids)))
            if state in {CaseState.CREATED, CaseState.INITIAL_CHECKS}:
                await self._activity("run_case_checks", request)
                continue
            if state is CaseState.INVESTIGATING:
                await self._activity(
                    "run_investigation",
                    request,
                    task_queue=request.agent_task_queue,
                    retry_policy=INVESTIGATION_RETRY,
                )
                continue
            if state is CaseState.EXECUTING:
                await self._activity("execute_approved_action", request)
                continue
            if state is CaseState.WAITING_FOR_SETTLEMENT:
                await self._wait_for_signal_or_deadline(snapshot, observed_signals)
                await self._activity("resume_settlement_checks", request)
                continue
            if state is CaseState.WAITING_FOR_EVIDENCE:
                await self._wait_for_signal_or_deadline(snapshot, observed_signals)
                await self._activity("expire_evidence_wait", request)
                continue
            if state is CaseState.VERIFYING:
                await self._activity("recover_action_verification", request)
                continue
            if state in _PASSIVE_STATES:
                self._waiting_version = observed_signals
                await workflow.wait_condition(self._has_new_signal)
                continue
            raise RuntimeError(f"unsupported case state {state.value}")

    async def _wait_for_signal_or_deadline(
        self, snapshot: CaseSnapshot, observed_signals: int
    ) -> None:
        if snapshot.wait_until is None:
            raise RuntimeError("settlement wait has no deadline")
        self._waiting_version = observed_signals
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
        retry_policy: RetryPolicy = ACTIVITY_RETRY,
    ) -> CaseSnapshot:
        return cast(
            CaseSnapshot,
            await workflow.execute_activity(
                name,
                request,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=retry_policy,
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

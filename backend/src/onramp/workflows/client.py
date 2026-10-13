"""Synchronous application boundary around Temporal's asynchronous client."""

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any, Protocol, TypeVar

from opentelemetry import trace
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.contrib.opentelemetry import TracingInterceptor

from onramp.workflows.case import CaseWorkflow
from onramp.workflows.contracts import CaseSignal, CaseWorkflowInput, case_workflow_id

T = TypeVar("T")


class CaseCoordinator(Protocol):
    def start_case(self, tenant_id: str, case_id: str) -> None: ...

    def signal_case(self, tenant_id: str, case_id: str, signal: str, event_id: str) -> None: ...


class TemporalCaseCoordinator:
    """Own one client event loop for sync API and Kafka process roles."""

    def __init__(
        self,
        address: str,
        namespace: str,
        workflow_task_queue: str,
        agent_task_queue: str,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="temporal-client", daemon=True
        )
        self._thread.start()
        self._workflow_task_queue = workflow_task_queue
        self._agent_task_queue = agent_task_queue
        try:
            self._client = self._submit(
                Client.connect(
                    address,
                    namespace=namespace,
                    interceptors=[TracingInterceptor(trace.get_tracer("onramp.temporal"))],
                )
            )
        except BaseException:
            self.close()
            raise

    def start_case(self, tenant_id: str, case_id: str) -> None:
        self._submit(self._start_case(tenant_id, case_id))

    def signal_case(self, tenant_id: str, case_id: str, signal: str, event_id: str) -> None:
        self._submit(self._signal_case(tenant_id, case_id, signal, event_id))

    def close(self) -> None:
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        self._loop.close()

    async def _start_case(self, tenant_id: str, case_id: str) -> None:
        await self._client.start_workflow(
            CaseWorkflow.run,
            CaseWorkflowInput(tenant_id, case_id, self._agent_task_queue),
            id=case_workflow_id(case_id),
            task_queue=self._workflow_task_queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )

    async def _signal_case(self, tenant_id: str, case_id: str, signal: str, event_id: str) -> None:
        await self._start_case(tenant_id, case_id)
        await self._client.get_workflow_handle(case_workflow_id(case_id)).signal(
            signal, CaseSignal(event_id)
        )

    def _submit(self, coroutine: Coroutine[Any, Any, T]) -> T:
        future: Future[T] = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result()

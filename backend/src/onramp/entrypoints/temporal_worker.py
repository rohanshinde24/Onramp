"""Shared lifecycle for Temporal worker process roles."""

import asyncio
import signal
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import timedelta
from typing import Any

from opentelemetry import trace
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.runtime import OpenTelemetryConfig, Runtime, TelemetryConfig
from temporalio.worker import Worker


def _runtime(otlp_endpoint: str | None) -> Runtime | None:
    """Temporal's own worker metrics (poll and task latency, schedule-to-start, slots), sent to
    the same collector as Onramp's. Without an endpoint the SDK default runtime is used."""
    if not otlp_endpoint:
        return None
    return Runtime(
        telemetry=TelemetryConfig(
            metrics=OpenTelemetryConfig(
                url=f"{otlp_endpoint.rstrip('/')}/v1/metrics",
                http=True,
                metric_periodicity=timedelta(seconds=10),
                durations_as_seconds=True,
            ),
        )
    )


async def run_worker(
    *,
    address: str,
    namespace: str,
    task_queue: str,
    activities: Sequence[Callable[..., Any]],
    workflows: Sequence[type] = (),
    otlp_endpoint: str | None = None,
) -> None:
    """Run one worker until SIGINT/SIGTERM, then drain in-flight activities."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)

    client = await Client.connect(
        address,
        namespace=namespace,
        interceptors=[TracingInterceptor(trace.get_tracer("onramp.temporal"))],
        runtime=_runtime(otlp_endpoint),
    )
    with ThreadPoolExecutor(thread_name_prefix=f"{task_queue}-activity") as executor:
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=workflows,
            activities=activities,
            activity_executor=executor,
            graceful_shutdown_timeout=timedelta(seconds=20),
        )
        running = asyncio.create_task(worker.run())
        waiting = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({running, waiting}, return_when=asyncio.FIRST_COMPLETED)
        if waiting in done:
            await worker.shutdown()
            await running
        else:
            waiting.cancel()
            with suppress(asyncio.CancelledError):
                await waiting
            await running

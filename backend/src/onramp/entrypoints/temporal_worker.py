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
from temporalio.worker import Worker


async def run_worker(
    *,
    address: str,
    namespace: str,
    task_queue: str,
    activities: Sequence[Callable[..., Any]],
    workflows: Sequence[type] = (),
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

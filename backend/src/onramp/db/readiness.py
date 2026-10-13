import asyncio

from sqlalchemy import Engine

from onramp.api.health import ReadinessCheck
from onramp.db.engine import ping


def database_check(name: str, engine: Engine, timeout_s: float = 2.0) -> ReadinessCheck:
    async def probe() -> None:
        await asyncio.wait_for(asyncio.to_thread(ping, engine), timeout=timeout_s)

    return ReadinessCheck(name, probe)

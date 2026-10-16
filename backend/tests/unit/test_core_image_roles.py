"""Roles that run from the core image must not need the ml image's packages.

The core image leaves out the embedding model, fastembed and the model provider SDK. Each check
imports a core entrypoint in a clean interpreter with those packages blocked, as the core image
would have them missing.
"""

import subprocess
import sys

import pytest

ML_ONLY_PACKAGES = ["fastembed", "onnxruntime", "anthropic", "duckdb"]

CORE_ENTRYPOINTS = [
    "onramp.entrypoints.api",
    "onramp.entrypoints.migrate",
    "onramp.entrypoints.seed",
    "onramp.entrypoints.dev_token",
    "onramp.entrypoints.kafka_topics",
    "onramp.entrypoints.outbox_publisher",
    "onramp.entrypoints.event_consumer",
    "onramp.entrypoints.case_signal_consumer",
    "onramp.entrypoints.dlq_replay",
    "onramp.entrypoints.load_policies",
    "onramp.entrypoints.workflow_worker",
]


@pytest.mark.parametrize("module", CORE_ENTRYPOINTS)
def test_core_role_imports_without_ml_packages(module: str) -> None:
    code = (
        "import sys\n"
        f"for name in {ML_ONLY_PACKAGES!r}:\n"
        "    sys.modules[name] = None  # makes `import name` raise ImportError\n"
        f"import {module}\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and module list
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=60
    )

    assert result.returncode == 0, result.stderr[-2000:]

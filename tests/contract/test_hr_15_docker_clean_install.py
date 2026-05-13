"""HR-15 Docker contract test — real clean-install end-to-end in a sandbox.

HR-15 mandates that a fresh, never-before-installed machine can install the
reference implementation and reach a usable state. The sibling test
``test_hr_15_clean_install_works.py`` simulates that in a process-local
tmpdir for CI speed; this test builds the actual Docker image defined by
``./Dockerfile`` and runs ``docker/clean-install.sh`` inside the resulting
container.

Skip semantics (HR-15 + L10):
    If the ``docker`` binary is missing from PATH we call
    :func:`pytest.skip` at RUNTIME from inside the test body. This is
    intentionally NOT a ``@pytest.mark.skip`` decorator — L10
    (anti-loophole) forbids decorator-based skipping in the contract
    suite because that hides the contract from the runner statistics. A
    runtime ``pytest.skip()`` is allowed because it reflects environment
    capability (no docker installed), not a decision to bypass the
    contract; the contract is still attempted on every run.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_hr_15_docker_clean_install() -> None:
    """HR-15: clean-install works end-to-end in a fresh Docker container.

    Builds the image from ``./Dockerfile`` and runs the clean-install
    script. Asserts both stages return 0.

    Returns:
        None. Test passes when both ``docker build`` and ``docker run``
        exit 0.
    Refuses:
        Nothing. If the ``docker`` binary is unavailable the test calls
        :func:`pytest.skip` so the contract suite remains green on
        environments without a container runtime (HR-15 documents the
        Docker path as the canonical clean-install verifier; the
        in-process simulator in ``test_hr_15_clean_install_works.py``
        covers the same surface for CI environments without Docker).
    """
    if shutil.which("docker") is None:
        pytest.skip("docker binary not available in this environment")

    build = subprocess.run(
        ["docker", "build", "-t", "runaway-context-ci", "."],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build.returncode == 0, (
        "HR-15 docker build failed:\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )

    run = subprocess.run(
        ["docker", "run", "--rm", "runaway-context-ci"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, (
        "HR-15 clean-install script failed inside container:\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )

"""The `runaway adopt` orchestrator (v3.3.3).

One command that walks the full adoption flow on a host that already has
some form of knowledge management (canonical RC install, homemade KS,
auto-memory MDs, hand-edited CLAUDE.md / .cursor/rules, ...). The vision:

    "any system the user is using to manage context and knowledge —
     migrate all of that to the DB as a single source of truth, replace
     the files with detailed pointers that per-project give the DB row #
     and tags, so an AI can grep the local file and only fetch what it
     needs via MCP."

The orchestrator is composed of existing primitives. It owns no new write
logic; it sequences:

  1. ``doctor.scan_install_candidates()`` — discover knowledge.db files
  2. For each non-v3 candidate, recommend the right action:
       v1/v2 -> ``runaway db migrate``
       foreign/partial -> ``runaway import-legacy --from <path>``
  3. ``memory_ingest.ingest_all()`` — sweep Claude per-project memory MDs
  4. ``markdown_ingest.ingest_all()`` — sweep CLAUDE.md / AGENTS.md /
     ``.cursor/rules`` under each user-supplied project root
  5. Rewrite every ``MEMORY.md`` as a pointer index (tag-rich)
  6. Final report

Two execution modes:
  * **dry-run** (default): print what would happen, write nothing
  * **apply**: actually run each step. Each step is individually
    idempotent — re-running is safe.

This module is read-only on existing data with one exception: when
``apply=True``, it calls into the lower-level ingestors which DO write to
``knowledge.db`` (insert) and to MDs (rewrite as pointers).

Refuses:
    Running with ``apply=True`` AND no target install (must provide
    target_install_dir or have a pre-existing canonical install).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AdoptStep:
    """One step of the adoption flow."""

    name: str
    status: str  # 'ok', 'recommended', 'skipped', 'error'
    details: Dict[str, Any] = field(default_factory=dict)
    recommended_command: Optional[str] = None


@dataclass
class AdoptReport:
    """Aggregate of the orchestrator's pass."""

    target_install_dir: Path
    apply: bool
    steps: List[AdoptStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_install_dir": str(self.target_install_dir),
            "apply": self.apply,
            "steps": [
                {
                    "name": s.name, "status": s.status,
                    "details": s.details,
                    "recommended_command": s.recommended_command,
                }
                for s in self.steps
            ],
        }


def _shape_to_action(shape: str, path: str) -> Optional[str]:
    """Return the recommended next CLI command for a discovered DB shape."""
    if shape == "v3":
        return None  # already there
    if shape == "v2":
        return f"runaway db migrate --knowledge-db {path}"
    if shape == "v1":
        return f"runaway db migrate --knowledge-db {path}"
    if shape == "foreign":
        return f"runaway import-legacy --from {Path(path).parent}"
    if shape == "partial":
        return f"runaway import-legacy --from {Path(path).parent}"
    if shape == "foreign_v1":
        return f"runaway import-legacy --from {Path(path).parent}"
    return None


def run(
    *,
    target_install_dir: Path,
    project_roots: Optional[List[Path]] = None,
    project_slug: str = "general",
    apply: bool = False,
    claude_projects_root: Optional[Path] = None,
) -> AdoptReport:
    """Walk the adoption flow once.

    Args:
        target_install_dir: where the v3 install lives (or will live).
        project_roots: roots to scan for hand-edited MDs (CLAUDE.md,
            AGENTS.md, .cursor/rules/). Defaults to ``[~]``.
        project_slug: slug to attribute hand-edited MD content to.
            Memory-ingest already auto-detects per dir.
        apply: when ``False`` (default), reports without writing.
        claude_projects_root: override for memory-dir discovery (tests).

    Returns:
        :class:`AdoptReport` with one entry per pipeline step.
    """
    from runaway_context import doctor as _doctor
    from runaway_context import memory_ingest as _memi

    report = AdoptReport(target_install_dir=target_install_dir, apply=apply)
    project_roots = project_roots or [Path.home()]

    # Step 1: scan
    candidates = _doctor.scan_install_candidates()
    step1 = AdoptStep(
        name="scan_install_candidates", status="ok",
        details={"count": len(candidates), "candidates": candidates},
    )
    report.steps.append(step1)

    # Step 2: per-candidate recommendation
    for c in candidates:
        if str(Path(c["path"]).resolve()) == str(target_install_dir.resolve() / "knowledge.db"):
            # This IS the target install — no migrate/import recommended
            report.steps.append(AdoptStep(
                name=f"candidate:{c['path']}", status="ok",
                details={"shape": c["shape"], "is_target": True},
            ))
            continue
        cmd = _shape_to_action(c["shape"], c["path"])
        report.steps.append(AdoptStep(
            name=f"candidate:{c['path']}",
            status="recommended" if cmd else "skipped",
            details={"shape": c["shape"], "notes": c.get("notes", "")},
            recommended_command=cmd,
        ))

    # Step 3: memory ingest (per-project Claude memory dirs)
    step3 = AdoptStep(name="memory_ingest", status="ok")
    try:
        from runaway_context.client import Client
        client = Client(install_dir=target_install_dir)
        mem_report = _memi.ingest_all(
            client, claude_projects_root=claude_projects_root,
            dry_run=not apply,
        )
        step3.details = {
            "dry_run": mem_report.dry_run,
            "counts": mem_report.counts(),
            "files_seen": len(mem_report.records),
        }
    except Exception as exc:  # noqa: BLE001 — surface step error, keep going
        step3.status = "error"
        step3.details = {"error": str(exc)}
    report.steps.append(step3)

    # Step 4: markdown ingest (hand-edited CLAUDE.md / .cursor/rules /
    # AGENTS.md under each project root).
    step4 = AdoptStep(name="markdown_ingest", status="ok")
    try:
        from runaway_context import markdown_ingest as _mdi
        # Reuse the client from step3 if available
        if "client" not in locals():
            from runaway_context.client import Client as _C
            client = _C(install_dir=target_install_dir)
        md_report = _mdi.ingest_all(
            client, project=project_slug, roots=project_roots,
            dry_run=not apply,
        )
        step4.details = {
            "dry_run": md_report.dry_run,
            "counts": md_report.counts(),
            "files_seen": len(md_report.records),
        }
    except Exception as exc:  # noqa: BLE001
        step4.status = "error"
        step4.details = {"error": str(exc)}
    report.steps.append(step4)

    # Step 5: rewrite MEMORY.md as a pointer index (tag-rich, from v3.3.3).
    # We shell out to the CLI handler so the HR-5 guard + tag-format helper
    # run uniformly; running this inline would duplicate that surface.
    step5 = AdoptStep(name="brief_rewrite_pointers", status="ok")
    if apply:
        cmd_args = [
            sys.executable, "-m", "runaway_context.cli",
            "--install-dir", str(target_install_dir),
            "brief-rewrite-pointers",
        ]
        if claude_projects_root is not None:
            cmd_args.extend(["--claude-root", str(claude_projects_root)])
        try:
            import os
            env = {**os.environ}
            # Prefer the in-repo src/ in case the package isn't pip-installed.
            repo_src = Path(__file__).resolve().parent.parent
            env["PYTHONPATH"] = str(repo_src) + (
                ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
            proc = subprocess.run(
                cmd_args, capture_output=True, text=True, timeout=60, env=env,
            )
            try:
                step5.details = json.loads(proc.stdout)
            except json.JSONDecodeError:
                step5.details = {
                    "stdout": (proc.stdout or "")[-2000:],
                    "stderr": (proc.stderr or "")[-1000:],
                }
            if proc.returncode != 0:
                step5.status = "error"
        except (OSError, subprocess.TimeoutExpired) as exc:
            step5.status = "error"
            step5.details = {"error": str(exc)}
    else:
        step5.status = "recommended"
        step5.recommended_command = (
            f"runaway brief-rewrite-pointers"
            + (f" --claude-root {claude_projects_root}" if claude_projects_root else "")
        )
    report.steps.append(step5)

    return report

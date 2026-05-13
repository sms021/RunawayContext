"""Environment diagnostics for ``runaway doctor``.

The doctor inspects the local environment, the install, and the current
tier, then emits a structured list of findings. Each finding is
``{level: ok|warn|fail, code, message, remediation}`` so adopters' AIs can
walk the list and close gaps mechanically.

This module is read-only — it never mutates the install. Remediations are
suggested strings; the AI (or the user) executes them.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from runaway_context.config import Config


_LEVEL_OK = "ok"
_LEVEL_WARN = "warn"
_LEVEL_FAIL = "fail"


@dataclass
class Finding:
    """One diagnostic result.

    Attributes:
        level: ``"ok"`` / ``"warn"`` / ``"fail"``.
        code: short stable identifier (e.g. ``"PY_VERSION"``).
        message: human-readable summary.
        remediation: actionable suggestion when level != ``"ok"`` (empty otherwise).
        extra: optional structured data for AI consumption.
    """

    level: str
    code: str
    message: str
    remediation: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def _ok(code: str, message: str, **extra: Any) -> Finding:
    return Finding(level=_LEVEL_OK, code=code, message=message, extra=dict(extra))


def _warn(code: str, message: str, remediation: str, **extra: Any) -> Finding:
    return Finding(
        level=_LEVEL_WARN, code=code, message=message,
        remediation=remediation, extra=dict(extra),
    )


def _fail(code: str, message: str, remediation: str, **extra: Any) -> Finding:
    return Finding(
        level=_LEVEL_FAIL, code=code, message=message,
        remediation=remediation, extra=dict(extra),
    )


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_version() -> Finding:
    """Confirm Python is >= 3.8 (the floor declared in ``pyproject.toml``).

    Returns:
        A :class:`Finding` describing the running interpreter.
    """
    major, minor = sys.version_info[:2]
    msg = f"Python {sys.version.split()[0]}"
    if (major, minor) < (3, 8):
        return _fail(
            "PY_VERSION", f"{msg} — too old (need >=3.8)",
            remediation="Install Python 3.8 or newer and re-run.",
            major=major, minor=minor,
        )
    return _ok("PY_VERSION", msg, major=major, minor=minor)


def check_sqlite_version() -> Finding:
    """Confirm SQLite is recent enough for FTS5.

    Returns:
        A :class:`Finding`. FTS5 has been on by default since 3.9; v3 was
        written against 3.31 but functions on anything 3.9+.
    """
    ver = sqlite3.sqlite_version_info
    msg = f"SQLite {sqlite3.sqlite_version}"
    if ver < (3, 9, 0):
        return _fail(
            "SQLITE_VERSION",
            f"{msg} — too old; FTS5 needs >=3.9.",
            remediation="Upgrade your Python's bundled SQLite, "
            "or install a Python build linked against newer libsqlite3.",
            version=sqlite3.sqlite_version,
        )
    return _ok("SQLITE_VERSION", msg, version=sqlite3.sqlite_version)


def check_fts5_available() -> Finding:
    """Confirm SQLite has FTS5 compiled in.

    Returns:
        :class:`Finding` covering FTS5 capability.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
    except sqlite3.OperationalError as exc:
        conn.close()
        return _fail(
            "FTS5", f"FTS5 unavailable: {exc}",
            remediation="Reinstall Python with FTS5 support, "
            "or upgrade the system libsqlite3.",
        )
    conn.close()
    return _ok("FTS5", "FTS5 virtual tables work")


def check_sqlite_vec() -> Finding:
    """Probe for the optional ``sqlite-vec`` loadable extension.

    Returns:
        :class:`Finding`. Absence is ``warn`` (not ``fail``) — the reference
        falls back to FTS5-only retrieval if vec0 isn't available.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.NotSupportedError):
        conn.close()
        return _warn(
            "SQLITE_VEC",
            "Python's sqlite3 was built without loadable-extension support",
            remediation="Optional. To enable sqlite-vec accelerated semantic "
            "retrieval, rebuild Python with --enable-loadable-sqlite-extensions.",
        )
    try:
        conn.load_extension("vec0")
    except sqlite3.OperationalError as exc:
        conn.close()
        return _warn(
            "SQLITE_VEC",
            f"sqlite-vec not loaded: {exc}",
            remediation="Optional. Install sqlite-vec "
            "(https://github.com/asg017/sqlite-vec) to enable vec0-accelerated "
            "hybrid retrieval. The fallback FTS5 path stays functional without it.",
        )
    conn.close()
    return _ok("SQLITE_VEC", "sqlite-vec loadable")


def check_install_dir(cfg: Config) -> Finding:
    """Confirm the install dir exists and is writable.

    Returns:
        :class:`Finding` describing the directory state.
    """
    d = cfg.install_dir
    if not d.exists():
        return _fail(
            "INSTALL_DIR", f"install dir {d} does not exist",
            remediation=f"Run `runaway init` (or create {d} and re-run).",
        )
    test = d / ".write_probe"
    try:
        test.write_text("ok")
        test.unlink()
    except OSError as exc:
        return _fail(
            "INSTALL_DIR_WRITABLE", f"install dir {d} not writable: {exc}",
            remediation=f"chmod / chown {d} so the current user can write.",
        )
    return _ok("INSTALL_DIR", f"install dir {d} writable")


def check_schema_version(cfg: Config) -> Finding:
    """Confirm ``knowledge.db`` exists and is at v3.

    Distinguishes:
      - missing DB → ``fail SCHEMA_PRESENT``
      - v2 DB awaiting upgrade → ``fail V2_DB_UNUPGRADED`` with the v2-specific
        remediation string (so adopters' AIs route to `runaway db migrate`).
      - v3 DB → ``ok SCHEMA_VERSION``

    Returns:
        :class:`Finding` describing schema status.
    """
    kdb = cfg.knowledge_db
    if not kdb or not Path(kdb).exists():
        return _fail(
            "SCHEMA_PRESENT", f"knowledge.db missing at {kdb}",
            remediation="Run `runaway db migrate` (or `runaway init`).",
        )
    conn = sqlite3.connect(str(kdb))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        has_schema_version = "schema_version" in tables
        v2_core_present = {"knowledge_chunks", "lessons_learned"}.issubset(tables)
        row = None
        if has_schema_version:
            row = conn.execute(
                "SELECT major, minor, patch FROM schema_version WHERE id = 1"
            ).fetchone()
    except sqlite3.OperationalError as exc:
        conn.close()
        return _fail(
            "SCHEMA_VERSION_ROW", f"schema_version unreadable: {exc}",
            remediation="Re-run `runaway db migrate` to apply v3 schema.",
        )
    finally:
        try:
            conn.close()
        except Exception:  # emit-allowed: idempotent close
            pass

    if v2_core_present and not row:
        # Distinguish v1 (single-file: knowledge tables + sessions table
        # co-located) from v2 (split layout). The remediation is the same
        # command but the v1 path auto-splits — flag it so the AI can report
        # row counts honestly.
        try:
            from runaway_context.migrate import detect_v1_layout
            is_v1 = detect_v1_layout(Path(kdb))
        except ImportError:
            is_v1 = False
        if is_v1:
            return _fail(
                "V1_DB_UNUPGRADED",
                f"v1 single-file install detected at {kdb} — "
                "knowledge + transcripts in one DB",
                remediation="Run `runaway db migrate` to upgrade in place. "
                "The migrator auto-detects v1, copies transcripts into a new "
                "sessions.db (non-destructive — original file kept), then "
                "applies the v3 additive layer. HR-4 guarantees no rows lost.",
                knowledge_db=str(kdb),
            )
        return _fail(
            "V2_DB_UNUPGRADED",
            f"v2 install detected at {kdb} — schema_version row missing",
            remediation="Run `runaway db migrate` to upgrade in place. "
            "HR-4 guarantees this is non-destructive: every v2 row and column "
            "is preserved; only new v3 columns/tables are added.",
            knowledge_db=str(kdb),
        )
    if not row:
        return _fail(
            "SCHEMA_VERSION_ROW",
            "schema_version row absent and no v2 tables present",
            remediation="Run `runaway init` to set up a fresh install.",
        )
    major, minor, patch = int(row[0]), int(row[1]), int(row[2])
    if major < 3:
        return _fail(
            "SCHEMA_VERSION",
            f"knowledge.db is v{major}.{minor}.{patch}; need v3.x.y",
            remediation="Run `runaway db migrate` (HR-4 — non-destructive).",
        )
    return _ok("SCHEMA_VERSION", f"v{major}.{minor}.{patch}")


def check_audit_chain(cfg: Config) -> Finding:
    """Walk ``audit_log`` and confirm the hash chain verifies (HR-7).

    Returns:
        :class:`Finding` with the audit-chain verdict.
    """
    try:
        from runaway_context import audit as _audit
    except ImportError as exc:
        return _warn(
            "AUDIT_MODULE", f"audit module not importable: {exc}",
            remediation="Run `pip install -e .` from the repo root.",
        )
    ok, first_bad, reason = _audit.verify(Path(cfg.knowledge_db))
    if not ok:
        return _fail(
            "AUDIT_CHAIN", f"audit chain broken at id={first_bad}: {reason}",
            remediation="Investigate tampering; restore from `bin/backup_db.sh` snapshot.",
        )
    return _ok("AUDIT_CHAIN", "audit chain intact")


def check_slug_registry(cfg: Config) -> Finding:
    """Confirm at least one canonical slug is registered (HR-2 prerequisite).

    Returns:
        :class:`Finding`.
    """
    conn = sqlite3.connect(str(cfg.knowledge_db))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM slug_registry WHERE status IN ('active')"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        conn.close()
        return _fail(
            "SLUG_TABLE", f"slug_registry unreadable: {exc}",
            remediation="Re-run `runaway db migrate`.",
        )
    conn.close()
    count = int(row[0] or 0)
    if count == 0:
        return _warn(
            "SLUG_REGISTRY",
            "no active slugs registered — writes will fail HR-2",
            remediation="Run `runaway slug register <slug>` for each canonical project.",
        )
    return _ok("SLUG_REGISTRY", f"{count} active slug(s) registered")


def check_drift_hook(cfg: Config) -> Finding:
    """Confirm a drift detection hook is wired (Stop hook or cron).

    Returns:
        :class:`Finding`. Absence is ``warn``; the install works without it
        but drift detection is the early-warning system for HR-5 violations.
    """
    candidates = [
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".claude" / "hooks",
        Path("/etc/cron.d/runaway-drift"),
    ]
    found = [p for p in candidates if p.exists()]
    if found:
        return _ok("DRIFT_HOOK", f"drift hook present at {found[0]}")
    return _warn(
        "DRIFT_HOOK",
        "no drift hook detected (Claude settings.json / cron job)",
        remediation="Add `bin/check_md_drift.sh` as a Stop hook in your AI tool, "
        "or schedule `bin/md_drift_watcher.sh` via cron (`*/10 * * * *`).",
    )


def check_tier_gate(cfg: Config) -> Finding:
    """Report the current tier and what the next promotion gate needs.

    Returns:
        Informational :class:`Finding` — never ``fail``.
    """
    nexts = {
        "T0": "5+ project-specific notes accumulated manually",
        "T1": "30 days of use, 10 lessons across 2 projects, >=1 drift warning",
        "T2": "a second author_id has logged an approved lesson in the last 30 days",
        "T3": "5 resolved import conflicts, 1 admin designated, 30 days under T3",
        "T4": "SSO configured, federation source identified, 30 days of clean audit",
        "T5": "(top tier — no further gate)",
    }
    tier = cfg.tier
    return _ok(
        "TIER_GATE", f"current tier {tier}; next gate: {nexts.get(tier, '(unknown tier)')}",
        tier=tier,
        next_gate=nexts.get(tier),
    )


def check_optional_module(name: str, *, description: str) -> Finding:
    """Probe for an optional third-party module without importing it twice.

    Returns:
        :class:`Finding` describing the import probe result.
    """
    try:
        importlib.import_module(name)
        return _ok(f"OPTIONAL_{name.upper()}", f"{description} (importable)")
    except ImportError:
        return _warn(
            f"OPTIONAL_{name.upper()}",
            f"{description} — not installed (optional)",
            remediation=f"pip install {name}  # only if you want this provider",
        )


def check_runaway_cli() -> Finding:
    """Confirm the ``runaway`` entry point resolves on PATH.

    Returns:
        :class:`Finding` covering CLI installation.
    """
    found = shutil.which("runaway")
    if found:
        return _ok("RUNAWAY_CLI", f"runaway resolves to {found}")
    return _warn(
        "RUNAWAY_CLI",
        "runaway not on PATH (you may be running `python -m runaway_context.cli`)",
        remediation="Run `pip install -e .` from the repo root.",
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def run_diagnostics(install_dir: Optional[Path] = None) -> List[Finding]:
    """Run every diagnostic and return the collected findings.

    Returns:
        List of :class:`Finding` in stable order. The list is never empty.

    Refuses:
        Nothing — every check is non-destructive.
    """
    cfg = Config.load(install_dir)
    findings: List[Finding] = [
        check_python_version(),
        check_sqlite_version(),
        check_fts5_available(),
        check_sqlite_vec(),
        check_runaway_cli(),
        check_install_dir(cfg),
    ]
    # Schema-dependent checks only fire when the DB exists.
    if cfg.knowledge_db and Path(cfg.knowledge_db).exists():
        findings.extend([
            check_schema_version(cfg),
            check_audit_chain(cfg),
            check_slug_registry(cfg),
            check_drift_hook(cfg),
            check_tier_gate(cfg),
        ])
    # Optional ML providers — only flag if the user has opted into a network
    # provider (so we don't pester pure-local installs).
    if cfg.embeddings_enabled and cfg.embeddings_provider.startswith("ollama-"):
        findings.append(check_optional_module(
            "requests",
            description="optional helper for Ollama local server interaction",
        ))
    return findings


def render_report(findings: List[Finding]) -> str:
    """Render findings to a terminal-friendly multi-section report.

    Returns:
        Plain text suitable for ``print()``. Three sections: OK / WARN / FAIL.
    """
    by_level: Dict[str, List[Finding]] = {_LEVEL_OK: [], _LEVEL_WARN: [], _LEVEL_FAIL: []}
    for f in findings:
        by_level.setdefault(f.level, []).append(f)

    out: List[str] = []
    out.append("runaway doctor — environment diagnostics")
    out.append("=" * 42)
    if by_level[_LEVEL_FAIL]:
        out.append("")
        out.append("FAIL (must fix):")
        for f in by_level[_LEVEL_FAIL]:
            out.append(f"  [{f.code}] {f.message}")
            if f.remediation:
                out.append(f"      → {f.remediation}")
    if by_level[_LEVEL_WARN]:
        out.append("")
        out.append("WARN (recommended):")
        for f in by_level[_LEVEL_WARN]:
            out.append(f"  [{f.code}] {f.message}")
            if f.remediation:
                out.append(f"      → {f.remediation}")
    if by_level[_LEVEL_OK]:
        out.append("")
        out.append("OK:")
        for f in by_level[_LEVEL_OK]:
            out.append(f"  [{f.code}] {f.message}")
    out.append("")
    summary = (
        f"summary: {len(by_level[_LEVEL_FAIL])} fail, "
        f"{len(by_level[_LEVEL_WARN])} warn, {len(by_level[_LEVEL_OK])} ok"
    )
    out.append(summary)
    return "\n".join(out)


def render_json(findings: List[Finding]) -> str:
    """Render findings as JSON suitable for an AI to parse.

    Returns:
        JSON string with one top-level array of finding objects.
    """
    import json
    return json.dumps([asdict(f) for f in findings], indent=2)


def cli_main(install_dir: Optional[Path] = None, *, json_output: bool = False) -> int:
    """Entry point used by ``runaway doctor``.

    Returns:
        ``0`` when there are no FAIL findings (warnings still allowed).
        ``2`` when at least one FAIL exists.

    Refuses:
        Nothing — diagnostics are read-only.
    """
    findings = run_diagnostics(install_dir=install_dir)
    output = render_json(findings) if json_output else render_report(findings)
    print(output)
    has_fail = any(f.level == _LEVEL_FAIL for f in findings)
    return 2 if has_fail else 0


__all__ = [
    "Finding",
    "run_diagnostics",
    "render_report",
    "render_json",
    "cli_main",
    "check_python_version",
    "check_sqlite_version",
    "check_fts5_available",
    "check_sqlite_vec",
    "check_install_dir",
    "check_schema_version",
    "check_audit_chain",
    "check_slug_registry",
    "check_drift_hook",
    "check_tier_gate",
    "check_runaway_cli",
    "check_optional_module",
]

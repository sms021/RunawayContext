"""Interactive install wizard for ``runaway init`` (E8, HR-15).

The wizard is stdlib-only and must succeed on a clean machine. It:

1. Prints a banner.
2. Detects an existing install (offers ``upgrade`` vs ``reinit``).
3. Prompts for the install directory.
4. Prompts for the tier (T0..T5, default ``T1``).
5. Asks whether to enable MCP (default ``no``).
6. Asks about local telemetry (default ``yes``; never network).
7. Runs :func:`runaway_context.migrate.migrate` to create the DBs.
8. Walks ``templates/`` and offers to copy each into a per-project location.
9. Registers the user's first canonical slugs.
10. Prints a "next steps" message including the path to ``INSTALL_PROMPT.md``.

Refuses:
    Nothing — refusals come from the migrator or slug validator and are
    re-raised so the CLI can format them with a non-zero exit code.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional

from runaway_context.config import Config, default_install_dir
from runaway_context.identity import get_or_create_install_id


BANNER = r"""
  ____                                       _____            _            _
 |  _ \ _   _ _ __   __ ___      ____ _ _   |_   _|__ _ __ __| | ___ _ __ | |
 | |_) | | | | '_ \ / _` \ \ /\ / / _` | | | || |/ _ \ '_// _` |/ _ \ '_ \| |
 |  _ <| |_| | | | | (_| |\ V  V / (_| | |_| || |  __/ | | (_| |  __/ | | |_|
 |_| \_\\__,_|_| |_|\__,_| \_/\_/ \__,_|\__, ||_|\___|_|  \__,_|\___|_| |_(_)
                                        |___/
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ask(prompt: str, default: Optional[str] = None) -> str:
    """Prompt the user; return their answer, falling back to ``default``.

    Returns:
        The user's input stripped of trailing whitespace.
    """
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raw = ""
    if not raw and default is not None:
        return default
    return raw


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    """Yes/no prompt; returns the boolean answer.

    Returns:
        True for y/yes, False otherwise.
    """
    suffix = "Y/n" if default else "y/N"
    while True:
        ans = _ask(f"{prompt} ({suffix})", default="y" if default else "n").lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        # ambiguous → ask again


def detect_v2_install(install_dir: Path) -> dict:
    """Inspect *install_dir* and report whether a v2 RunawayContext lives there.

    A "v2 install" is one whose ``knowledge.db`` lacks a v3 ``schema_version``
    row but contains v2's core tables (``knowledge_chunks`` /
    ``lessons_learned``). The wizard uses this to take an upgrade path
    instead of a fresh init.

    Returns:
        ``{is_v2: bool, has_knowledge_db: bool, has_sessions_db: bool,
        row_counts: {table: int}, has_v3_config: bool}``.

    Refuses:
        Nothing — read-only probe; bad paths simply return ``is_v2=False``.
    """
    import sqlite3

    install_dir = Path(install_dir).expanduser()
    report = {
        "is_v2": False,
        "has_knowledge_db": False,
        "has_sessions_db": False,
        "has_v3_config": (install_dir / "config.json").exists(),
        "row_counts": {},
    }
    kdb = install_dir / "knowledge.db"
    if kdb.exists():
        report["has_knowledge_db"] = True
        conn = sqlite3.connect(str(kdb))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            v2_core = {"knowledge_chunks", "lessons_learned"}
            has_v2_core = v2_core.issubset(tables)
            has_v3_marker = False
            if "schema_version" in tables:
                row = conn.execute(
                    "SELECT major FROM schema_version WHERE id = 1"
                ).fetchone()
                if row and int(row[0]) >= 3:
                    has_v3_marker = True
            for t in sorted(v2_core | {"project_context_card"} & tables):
                try:
                    report["row_counts"][t] = conn.execute(
                        f"SELECT COUNT(*) FROM {t}"
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    report["row_counts"][t] = 0
            report["is_v2"] = has_v2_core and not has_v3_marker
        finally:
            conn.close()
    if (install_dir / "sessions.db").exists():
        report["has_sessions_db"] = True
    return report


def recommend_tier(
    *,
    headcount: int = 1,
    is_team_with_review_process: bool = False,
    has_sso: bool = False,
    multi_project: bool = False,
) -> str:
    """Recommend a starting tier from situational inputs.

    The rubric mirrors Part III of the plan: T0 for "I haven't logged anything
    yet", T1 for solo, T2 for active multi-project solo, T3 for a small pair
    or squad, T4 for a reviewed team, T5 for an SSO-backed org.

    Returns:
        One of ``"T0"`` … ``"T5"``.

    Refuses:
        Negative or zero headcount → coerced to 1.
    """
    n = max(int(headcount), 1)
    if has_sso and n >= 20:
        return "T5"
    if is_team_with_review_process and n >= 5:
        return "T4"
    if 2 <= n <= 5:
        return "T3"
    if n == 1 and multi_project:
        return "T2"
    return "T1"


def recommend_tier_interactive() -> str:
    """Walk the user through the tier decision tree and return the chosen tier.

    Asks four short situational questions, presents the recommended tier and
    its trade-offs, and allows the user to override. Always returns a valid
    canonical tier string.

    Returns:
        A tier from ``("T0", "T1", "T2", "T3", "T4", "T5")``.

    Refuses:
        Nothing — invalid overrides fall back to the recommendation.
    """
    print("")
    print("Choosing a tier")
    print("---------------")
    print("Tiers are progressive (T0..T5). Each unlocks more capability and")
    print("requires a small bit more setup. Don't over-pick — you can promote")
    print("with `runaway tier promote --to T<n>` once you outgrow your current rung.")
    print("")

    head_raw = _ask(
        "How many people will write to this install? (1 = solo)", default="1"
    )
    try:
        headcount = int(head_raw)
    except ValueError:
        headcount = 1

    multi_project = False
    if headcount == 1:
        multi_project = _ask_yes_no(
            "Do you work across >=2 distinct projects?", default=True
        )
    review_process = False
    if headcount >= 2:
        review_process = _ask_yes_no(
            "Do you already have a review process (PRs / approvals)?",
            default=False,
        )
    has_sso = False
    if headcount >= 5 and review_process:
        has_sso = _ask_yes_no(
            "Do you have an SSO provider you'd want identities bound to?",
            default=False,
        )

    rec = recommend_tier(
        headcount=headcount,
        is_team_with_review_process=review_process,
        has_sso=has_sso,
        multi_project=multi_project,
    )
    tier_descriptions = {
        "T0": "T0 hello-world — markdown only, no DB. Use this if you just want a paste-once template.",
        "T1": "T1 solo — full v2 surface; knowledge.db + sessions.db; no MCP, no telemetry.",
        "T2": "T2 solo-power — T1 + MCP + telemetry + semantic + specialists + cross-system map.",
        "T3": "T3 pair/squad — T2 + author attribution + JSON export/import + conflict reporter.",
        "T4": "T4 team — T3 + visibility ACLs + governance + audit log + multi-tenant rollout.",
        "T5": "T5 org/enterprise — T4 + federation + SSO + OTLP export.",
    }
    print("")
    print(f"Recommended: {tier_descriptions[rec]}")
    print("")
    override = _ask(
        "Use this tier? (press enter to accept, or type a different tier T0..T5)",
        default=rec,
    ).upper().strip()
    if override not in ("T0", "T1", "T2", "T3", "T4", "T5"):
        print(f"  unknown tier '{override}' — using recommendation {rec}")
        override = rec
    return override


def _templates_root() -> Path:
    """Return the canonical templates root inside the package source tree.

    Returns:
        Absolute :class:`Path` to ``templates/`` (may not exist on installs
        that were packaged without the template data).
    """
    # Package layout: src/runaway_context/init.py + templates/ at repo root.
    pkg_dir = Path(__file__).resolve().parent
    candidates = [
        pkg_dir.parent.parent / "templates",   # repo source layout
        pkg_dir / "templates",                  # bundled inside the package
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _list_template_dirs(root: Path) -> List[Path]:
    """List immediate subdirectories of the templates root.

    Returns:
        Sorted list of :class:`Path` directories (may be empty).
    """
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()])


def _register_slugs(install_dir: Path, slugs: Iterable[str]) -> List[str]:
    """Register the given slugs in the local ``project_slugs`` table.

    Returns:
        List of slugs that were inserted successfully.
    Refuses:
        Slugs that fail format validation are skipped with a warning.
    """
    from runaway_context._db import connect, transaction
    from runaway_context._slugs import is_valid_slug_format

    knowledge_db = install_dir / "knowledge.db"
    inserted: List[str] = []
    if not knowledge_db.exists():
        return inserted
    conn = connect(knowledge_db)
    try:
        for slug in slugs:
            if not is_valid_slug_format(slug):
                print(f"  skipping invalid slug '{slug}' (must be lowercase snake_case)")
                continue
            try:
                with transaction(conn):
                    conn.execute(
                        "INSERT OR IGNORE INTO slug_registry (slug) VALUES (?)",
                        (slug,),
                    )
                inserted.append(slug)
            except Exception as e:  # noqa: BLE001 — surfaced to caller per HR-10
                print(f"  failed to register '{slug}': {e}")
    finally:
        conn.close()
    return inserted


def _write_config(install_dir: Path, *, tier: str, mcp_enabled: bool,
                  telemetry_enabled: bool) -> Config:
    """Persist the resolved Config and return it.

    Returns:
        The new :class:`Config` instance, already saved to disk.
    """
    cfg = Config.load(install_dir)
    cfg.install_dir = install_dir
    cfg.tier = tier
    cfg.mcp_enabled = mcp_enabled
    cfg.telemetry_enabled = telemetry_enabled
    cfg.save()
    return cfg


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(install_dir: Optional[Path] = None,
        non_interactive: bool = False,
        defaults: Optional[dict] = None) -> Config:
    """Run the wizard end-to-end and return the resolved Config.

    When ``non_interactive`` is ``True``, the wizard writes the defaults
    (or values supplied via the ``defaults`` mapping) without prompting —
    this is the path tests use to exercise the install flow.

    Returns:
        The Config that was written to ``<install_dir>/config.json``.

    Refuses:
        Nothing of its own — refusals propagate from :func:`migrate.migrate`
        (HR-4) and from the slug registrar (HR-2).
    """
    defaults = defaults or {}

    if non_interactive:
        target_dir = Path(install_dir or defaults.get("install_dir")
                          or default_install_dir()).expanduser()
        tier = defaults.get("tier", "T1")
        mcp_enabled = bool(defaults.get("mcp_enabled", False))
        telemetry_enabled = bool(defaults.get("telemetry_enabled", True))
        target_dir.mkdir(parents=True, exist_ok=True)

        from runaway_context.migrate import migrate

        migrate(
            knowledge_db=target_dir / "knowledge.db",
            sessions_db=target_dir / "sessions.db",
            metrics_db=target_dir / "metrics.db",
        )
        get_or_create_install_id(target_dir)
        cfg = _write_config(
            target_dir,
            tier=tier,
            mcp_enabled=mcp_enabled,
            telemetry_enabled=telemetry_enabled,
        )
        try:
            from runaway_context.uninstall import capture_install_manifest
            capture_install_manifest(target_dir, tier=tier)
        except ImportError:
            pass  # emit-allowed: uninstall is optional
        return cfg

    # ---------------- interactive path ----------------------------------
    print(BANNER)
    print("RunawayContext install wizard")
    print("-----------------------------")
    print("This wizard creates the local knowledge / sessions / metrics DBs,")
    print("registers your first project slugs, and writes the config.")
    print("By default, zero network calls are made (HR-1).")
    print("")

    # 2. detect existing install (config.json OR a v2 DB)
    candidate_dir = install_dir or default_install_dir()
    existing_config = candidate_dir / "config.json"
    v2_report = detect_v2_install(candidate_dir)
    upgrade = False

    if v2_report["is_v2"]:
        counts = v2_report.get("row_counts") or {}
        chunk_n = counts.get("knowledge_chunks", 0)
        lesson_n = counts.get("lessons_learned", 0)
        print(f"v2 install detected at {candidate_dir}")
        print(f"  - knowledge_chunks: {chunk_n} row(s)")
        print(f"  - lessons_learned:  {lesson_n} row(s)")
        print("The v3 migrator is non-destructive (HR-4): existing rows and "
              "columns are preserved; new v3 tables/columns are added.")
        upgrade = _ask_yes_no(
            "Upgrade this v2 install in place to v3?", default=True
        )
    elif existing_config.exists():
        print(f"Existing v3 install detected at {existing_config.parent}")
        upgrade = _ask_yes_no(
            "Upgrade in place? (no = reinit alongside, keeping the existing DB)",
            default=True,
        )

    # 3. install dir
    install_dir_str = _ask(
        "Install directory",
        default=str(candidate_dir),
    )
    target_dir = Path(install_dir_str).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 4. tier — recommend based on situation, then offer override
    tier = recommend_tier_interactive()

    # 5. MCP
    mcp_enabled = _ask_yes_no("Enable MCP server?", default=False)

    # 6. telemetry
    telemetry_enabled = _ask_yes_no(
        "Enable local telemetry? (writes to metrics.db only — never network)",
        default=True,
    )

    # 7. migrate the DBs
    print("")
    print("Running migrator (additive — HR-4)…")
    from runaway_context.migrate import migrate

    report = migrate(
        knowledge_db=target_dir / "knowledge.db",
        sessions_db=target_dir / "sessions.db",
        metrics_db=target_dir / "metrics.db",
    )
    print(f"  applied {len(report.steps_applied)} schema step(s).")
    get_or_create_install_id(target_dir)

    # 8. templates
    print("")
    template_root = _templates_root()
    templates = _list_template_dirs(template_root)
    if templates:
        print(f"Found {len(templates)} work-type template(s) at {template_root}.")
        copy_them = _ask_yes_no(
            "Copy a template into your install dir?", default=False,
        )
        if copy_them:
            print("Available templates:")
            for idx, tpl in enumerate(templates, start=1):
                print(f"  {idx}. {tpl.name}")
            choice = _ask("Pick by number (blank to skip)", default="")
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(templates):
                    src = templates[idx]
                    dst = target_dir / "templates" / src.name
                    dst.mkdir(parents=True, exist_ok=True)
                    for entry in src.iterdir():
                        if entry.is_file():
                            shutil.copy2(entry, dst / entry.name)
                    print(f"  copied {src.name} -> {dst}")
                else:
                    print("  out of range; skipping")
    else:
        print(f"(no templates found at {template_root})")

    # 9. first slugs
    print("")
    print("Register your first canonical project slug(s) so writes pass HR-2.")
    print("(Comma-separated, lowercase snake_case. You can add more later via "
          "`runaway slug register`.)")
    slugs_str = _ask("Slugs", default="general")
    slugs = [s.strip() for s in slugs_str.split(",") if s.strip()]
    if slugs:
        inserted = _register_slugs(target_dir, slugs)
        print(f"  registered: {', '.join(inserted) if inserted else '(none)'}")

    # 10. config + next steps
    cfg = _write_config(
        target_dir,
        tier=tier,
        mcp_enabled=mcp_enabled,
        telemetry_enabled=telemetry_enabled,
    )

    # Record what the install touched so `runaway uninstall` can undo it.
    try:
        from runaway_context.uninstall import capture_install_manifest
        capture_install_manifest(target_dir, tier=cfg.tier)
    except ImportError:
        pass  # emit-allowed: uninstall module is optional during partial migrations

    print("")
    print("Install complete.")
    print("")
    print("Next steps:")
    print(f"  - read {Path(__file__).parent.parent.parent / 'INSTALL_PROMPT.md'}")
    print("  - try: runaway tier check")
    print("  - try: runaway brief <slug>     (after you have logged some lessons)")
    print("  - if this isn't for you: `runaway uninstall --export-markdown PATH`")
    print("  - try: runaway log-lesson --title 'first lesson' --projects <slug>")
    if upgrade:
        print("  - your existing DB was preserved; ran the additive migrator only.")
    print("")
    return cfg


if __name__ == "__main__":  # pragma: no cover
    run()

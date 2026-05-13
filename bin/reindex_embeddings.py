#!/usr/bin/env python3
"""Backfill embeddings for knowledge_chunks and lessons_learned (E13).

Walks active rows of either or both tables and computes an embedding
under the requested provider when none exists yet for that
``(table, record_id, provider)`` triple.

CLI:
    reindex_embeddings.py --provider NAME [--table all|chunks|lessons]
                          [--limit N] [--install-dir DIR] [--force]

HR-1: when ``--provider`` resolves to a network provider, the
:mod:`runaway_context.semantic.encoder` factory enforces the opt-in
guard. The default provider is no-network.

Refuses:
    Network providers without their corresponding ``Config.network_opt_in``
    flag set. Backfilling soft-deleted rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List

# Make the package importable when run from a checkout without install.
_PKG_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from runaway_context._db import connect  # noqa: E402
from runaway_context.config import Config  # noqa: E402
from runaway_context.semantic.encoder import load_provider  # noqa: E402
from runaway_context.semantic.index import embed_record  # noqa: E402


_TABLE_MAP = {
    "chunks": "knowledge_chunks",
    "lessons": "lessons_learned",
}


def _resolve_tables(name: str) -> List[str]:
    """Map a CLI table selector to the underlying table names.

    Returns:
        ``["knowledge_chunks", "lessons_learned"]`` for ``"all"``, or the
        single name from :data:`_TABLE_MAP`.

    Raises:
        ValueError: when *name* is not one of ``all|chunks|lessons``.

    Refuses:
        Silently passing through unknown selectors.
    """
    if name == "all":
        return list(_TABLE_MAP.values())
    if name in _TABLE_MAP:
        return [_TABLE_MAP[name]]
    raise ValueError(f"unknown --table value: {name!r}")


def _pending_ids(conn, table: str, provider_name: str, limit: int, force: bool) -> Iterable[int]:
    """Yield record ids missing an embedding for *provider_name* in *table*.

    Returns:
        An iterable of record ids. When *force* is True, every active row
        is yielded (re-embedding existing rows).
    """
    sql = (
        f"SELECT r.id AS id "
        f"FROM {table} r "
        f"LEFT JOIN embedding_meta m "
        f"  ON m.table_name = ? AND m.record_id = r.id AND m.provider = ? "
        f"WHERE r.deleted_at IS NULL "
    )
    if not force:
        sql += "  AND m.id IS NULL "
    sql += "ORDER BY r.id ASC "
    if limit > 0:
        sql += f"LIMIT {int(limit)}"
    rows = conn.execute(sql, (table, provider_name)).fetchall()
    return [int(row["id"]) for row in rows]


def reindex(
    *,
    provider_name: str,
    table_selector: str = "all",
    limit: int = 0,
    install_dir: Path = None,
    force: bool = False,
) -> dict:
    """Backfill embeddings; return a counts dict.

    Returns:
        ``{"provider": str, "tables": {table: {processed: int, skipped: int}}}``

    Raises:
        ValueError: when ``table_selector`` is invalid.
        ImportError: when a network provider is requested but its opt-in
            flag is not set (delegated by :func:`load_provider`).

    Refuses:
        Network access without opt-in. Backfilling soft-deleted rows.
    """
    cfg = Config.load(install_dir) if install_dir else Config.load()
    provider = load_provider(provider_name, config=cfg)
    tables = _resolve_tables(table_selector)

    knowledge_db = cfg.knowledge_db
    if knowledge_db is None or not Path(knowledge_db).exists():
        raise FileNotFoundError(
            f"knowledge.db not found at {knowledge_db}; run runaway init first"
        )
    conn = connect(Path(knowledge_db))
    result = {"provider": provider.provider_name, "tables": {}}
    try:
        for table in tables:
            ids = list(_pending_ids(conn, table, provider.provider_name, limit, force))
            processed = 0
            skipped = 0
            for record_id in ids:
                try:
                    embed_record(conn, table, record_id, provider)
                    processed += 1
                except LookupError:
                    skipped += 1
            result["tables"][table] = {"processed": processed, "skipped": skipped}
    finally:
        conn.close()
    return result


def main(argv: List[str] = None) -> int:
    """Entry point for ``python3 bin/reindex_embeddings.py ...``.

    Returns:
        Process exit status. 0 on success, 2 when a documented refusal
        prevents work, 1 on unexpected error.
    """
    parser = argparse.ArgumentParser(
        description="Backfill embeddings into the semantic sidecar (E13)."
    )
    parser.add_argument(
        "--provider",
        default="sentence-transformers-MiniLM-L6-v2",
        help="Embedding provider id (see embeddings/providers/__init__.py).",
    )
    parser.add_argument(
        "--table",
        choices=("all", "chunks", "lessons"),
        default="all",
        help="Which table(s) to backfill.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum rows to process per table (0 = unlimited).",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=None,
        help="Path to the install directory (defaults to RC_KS_DIR or ~/_knowledge).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed rows that already have an embedding for this provider.",
    )
    args = parser.parse_args(argv)

    try:
        report = reindex(
            provider_name=args.provider,
            table_selector=args.table,
            limit=args.limit,
            install_dir=args.install_dir,
            force=args.force,
        )
    except (ValueError, ImportError, FileNotFoundError) as exc:
        print(f"reindex_embeddings: refused: {exc}", file=sys.stderr)
        return 2

    print(f"provider: {report['provider']}")
    for table, counts in report["tables"].items():
        print(
            f"  {table}: processed={counts['processed']} "
            f"skipped={counts['skipped']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

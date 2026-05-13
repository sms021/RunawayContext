"""E11 — MCP server: serve_test handles initialize, tools/list, tools/call.

Touches every one of the 13 tools so HR-12 coverage is satisfied. Also
covers both stdio framings (Content-Length default, ndjson opt-in).
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from runaway_context.mcp_server import (
    FRAMING_CONTENT_LENGTH,
    FRAMING_NDJSON,
    _read_content_length_message,
    _resolve_framing,
    _serve_stdio_content_length,
    _serve_stdio_ndjson,
    _write_content_length_message,
    dispatch,
    serve_test,
)

pytestmark = pytest.mark.feature


def _make(msg_id, method, **params):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params if params else {},
    }


def test_e11_initialize(tmp_install):
    """E11: initialize returns serverInfo + capabilities."""
    out = serve_test([_make(1, "initialize")], install_dir=tmp_install)
    assert len(out) == 1
    result = out[0]["result"]
    assert "serverInfo" in result
    assert "capabilities" in result


def test_e11_tools_list_returns_13_tools(tmp_install):
    """E11: tools/list returns exactly the 13 documented tools."""
    out = serve_test([_make(2, "tools/list")], install_dir=tmp_install)
    assert len(out) == 1
    tools = out[0]["result"]["tools"]
    assert len(tools) == 13
    names = {t["name"] for t in tools}
    expected = {
        "get_brief", "search_chunks", "search_lessons", "propose_lesson_draft",
        "list_drafts", "log_lesson", "propose_knowledge", "mature_lesson",
        "regen_brief", "audit_verify", "stats", "tier_check",
        "list_specialists",
    }
    assert names == expected


def test_e11_tools_call_get_brief(seeded_client, tmp_install):
    """E11: tools/call get_brief returns content or a clear refusal."""
    # Wire a context card so get_brief has something to read.
    import sqlite3
    lessons = seeded_client.list_lessons(project="tooling")
    md_path = tmp_install / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling",
             json.dumps([lessons[0]["id"]]),
             "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()
    out = serve_test(
        [_make(3, "tools/call", name="get_brief", arguments={"project": "tooling"})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_search_chunks(seeded_client, tmp_install):
    """E11: tools/call search_chunks returns matches."""
    out = serve_test(
        [_make(4, "tools/call", name="search_chunks",
               arguments={"query": "CLI"})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_search_lessons(seeded_client, tmp_install):
    """E11: tools/call search_lessons returns matches."""
    out = serve_test(
        [_make(5, "tools/call", name="search_lessons",
               arguments={"query": "bulk"})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_propose_lesson_draft(seeded_client, tmp_install):
    """E11: tools/call propose_lesson_draft (HR-3) appends to drafts inbox."""
    out = serve_test(
        [_make(6, "tools/call", name="propose_lesson_draft",
               arguments={"title": "via mcp", "project_tags": ["tooling"]})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_list_drafts(seeded_client, tmp_install):
    """E11: tools/call list_drafts returns the inbox."""
    out = serve_test(
        [_make(7, "tools/call", name="list_drafts", arguments={})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_log_lesson(seeded_client, tmp_install):
    """E11: tools/call log_lesson dispatches to Client.log_lesson."""
    out = serve_test(
        [_make(8, "tools/call", name="log_lesson",
               arguments={"title": "mcp log", "project_tags": ["tooling"],
                          "severity": "info"})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False, out


def test_e11_tools_call_propose_knowledge(seeded_client, tmp_install):
    """E11: tools/call propose_knowledge inserts a knowledge_chunk."""
    out = serve_test(
        [_make(9, "tools/call", name="propose_knowledge",
               arguments={"project": "tooling", "topic": "via-mcp",
                          "title": "Via MCP", "body": "body text via mcp",
                          "tags": []})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_mature_lesson(seeded_client, tmp_install):
    """E11: tools/call mature_lesson applies a transition with an actor."""
    lessons = seeded_client.list_lessons(project="tooling")
    out = serve_test(
        [_make(10, "tools/call", name="mature_lesson",
               arguments={"lesson_id": lessons[0]["id"],
                          "to_state": "stable", "actor": "tester"})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_regen_brief_dry_run(seeded_client, tmp_install):
    """E11: tools/call regen_brief dry-run returns the preview payload."""
    # Wire a context card so the regenerator has a project to operate on.
    import sqlite3
    lessons = seeded_client.list_lessons(project="tooling")
    md_path = tmp_install / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling",
             json.dumps([lessons[0]["id"]]),
             "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()
    out = serve_test(
        [_make(11, "tools/call", name="regen_brief",
               arguments={"project": "tooling", "dry_run": True})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False, out


def test_e11_tools_call_audit_verify(seeded_client, tmp_install):
    """E11: tools/call audit_verify returns chain validity."""
    out = serve_test(
        [_make(12, "tools/call", name="audit_verify", arguments={})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_stats(seeded_client, tmp_install):
    """E11: tools/call stats returns the summary dict."""
    out = serve_test(
        [_make(13, "tools/call", name="stats", arguments={})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_tier_check(seeded_client, tmp_install):
    """E11: tools/call tier_check returns the tier dict."""
    out = serve_test(
        [_make(14, "tools/call", name="tier_check", arguments={})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_tools_call_list_specialists(seeded_client, tmp_install):
    """E11: tools/call list_specialists returns the active specialist list."""
    out = serve_test(
        [_make(15, "tools/call", name="list_specialists", arguments={})],
        install_dir=tmp_install,
    )
    assert out[0]["result"]["isError"] is False


def test_e11_unknown_method_returns_error(tmp_install):
    """E11: dispatch returns a JSON-RPC error for unknown methods."""
    response = dispatch(_make(99, "definitely_not_a_method"),
                        install_dir=tmp_install)
    assert response is not None
    assert "error" in response


# -------------------------------------------------------------- framing tests

def test_e11_content_length_round_trip(tmp_install):
    """E11: Content-Length framing round-trips an initialize request."""
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    body = json.dumps(init).encode("utf-8")
    framed = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
    stdin = io.BytesIO(framed)
    stdout = io.BytesIO()

    _serve_stdio_content_length(install_dir=tmp_install,
                                 stdin=stdin, stdout=stdout)

    raw = stdout.getvalue()
    assert raw.startswith(b"Content-Length: "), raw[:40]
    header_end = raw.index(b"\r\n\r\n")
    header = raw[:header_end].decode("ascii")
    length = int(header.split(":", 1)[1].strip())
    body_out = raw[header_end + 4 : header_end + 4 + length]
    resp = json.loads(body_out.decode("utf-8"))
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "serverInfo" in resp["result"]


def test_e11_content_length_reader_helper():
    """E11: _read_content_length_message parses a single framed message."""
    payload = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
    body = json.dumps(payload).encode("utf-8")
    framed = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
    msg = _read_content_length_message(io.BytesIO(framed))
    assert msg == payload


def test_e11_content_length_reader_eof_returns_none():
    """E11: empty stdin yields None (clean EOF)."""
    assert _read_content_length_message(io.BytesIO(b"")) is None


def test_e11_content_length_reader_rejects_missing_header():
    """E11: a header block without Content-Length raises ValueError (HR-10)."""
    framed = b"X-Foo: bar\r\n\r\n"
    with pytest.raises(ValueError):
        _read_content_length_message(io.BytesIO(framed))


def test_e11_content_length_writer_helper():
    """E11: _write_content_length_message emits ASCII header + UTF-8 body."""
    buf = io.BytesIO()
    _write_content_length_message(buf, {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}})
    raw = buf.getvalue()
    assert raw.startswith(b"Content-Length: ")
    header_end = raw.index(b"\r\n\r\n")
    length = int(raw[:header_end].split(b":", 1)[1].strip())
    body = raw[header_end + 4:]
    assert len(body) == length
    assert json.loads(body.decode("utf-8")) == {
        "jsonrpc": "2.0", "id": 3, "result": {"ok": True},
    }


def test_e11_ndjson_round_trip(tmp_install):
    """E11: ndjson framing still round-trips one message per line."""
    init = {"jsonrpc": "2.0", "id": 2, "method": "initialize"}
    stdin = io.StringIO(json.dumps(init) + "\n")
    stdout = io.StringIO()
    _serve_stdio_ndjson(install_dir=tmp_install, stdin=stdin, stdout=stdout)
    raw = stdout.getvalue().strip().splitlines()
    assert len(raw) == 1
    resp = json.loads(raw[0])
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 2
    assert "serverInfo" in resp["result"]


def test_e11_framing_env_var(monkeypatch):
    """E11: RC_MCP_FRAMING=ndjson is honored by the framing resolver."""
    monkeypatch.setenv("RC_MCP_FRAMING", "ndjson")
    assert _resolve_framing(None) == FRAMING_NDJSON
    # Explicit argument overrides the env var.
    assert _resolve_framing(FRAMING_CONTENT_LENGTH) == FRAMING_CONTENT_LENGTH


def test_e11_framing_default_is_content_length(monkeypatch):
    """E11: with no env var and no argument, framing defaults to Content-Length."""
    monkeypatch.delenv("RC_MCP_FRAMING", raising=False)
    assert _resolve_framing(None) == FRAMING_CONTENT_LENGTH


def test_e11_framing_unknown_falls_back(monkeypatch, caplog):
    """E11: unknown framing values log a warning and fall back (HR-10)."""
    monkeypatch.delenv("RC_MCP_FRAMING", raising=False)
    with caplog.at_level("WARNING"):
        resolved = _resolve_framing("xml-rpc")
    assert resolved == FRAMING_CONTENT_LENGTH
    assert any("unknown MCP framing" in rec.message for rec in caplog.records)


def test_e11_ndjson_parse_error_is_surfaced(tmp_install):
    """E11: malformed ndjson lines produce a JSON-RPC parse error (HR-10)."""
    stdin = io.StringIO("{not json\n")
    stdout = io.StringIO()
    _serve_stdio_ndjson(install_dir=tmp_install, stdin=stdin, stdout=stdout)
    raw = stdout.getvalue().strip().splitlines()
    assert len(raw) == 1
    err = json.loads(raw[0])
    assert err["error"]["code"] == -32700

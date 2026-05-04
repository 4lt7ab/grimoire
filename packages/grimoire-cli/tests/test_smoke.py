import json

import pytest
from grimoire_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


# ---------- help / no-args ----------


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("ingest", "search", "list", "get", "delete"):
        assert cmd in result.output


def test_no_args_shows_help():
    # Click convention: missing subcommand exits 2, but help is still shown.
    result = runner.invoke(app, [])
    assert "ingest" in result.output


# ---------- ingest ----------


def test_ingest_help_describes_options():
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "--db" in result.output
    assert "--model" in result.output


def test_ingest_missing_file_fails(tmp_path):
    db = tmp_path / "store.db"
    result = runner.invoke(
        app, ["ingest", str(tmp_path / "nope.jsonl"), "--db", str(db)]
    )
    assert result.exit_code != 0


def test_ingest_rejects_invalid_json(tmp_path):
    db = tmp_path / "store.db"
    data = tmp_path / "bad.jsonl"
    data.write_text("not valid json\n")
    result = runner.invoke(app, ["ingest", str(data), "--db", str(db)])
    assert result.exit_code == 1
    assert "invalid JSON" in result.output


def test_ingest_rejects_missing_required_field(tmp_path):
    db = tmp_path / "store.db"
    data = tmp_path / "missing.jsonl"
    data.write_text(json.dumps({"content": "no kind"}) + "\n")
    result = runner.invoke(app, ["ingest", str(data), "--db", str(db)])
    assert result.exit_code == 1
    assert "missing required fields" in result.output
    assert "'kind'" in result.output


def test_ingest_rejects_unknown_field(tmp_path):
    db = tmp_path / "store.db"
    data = tmp_path / "extra.jsonl"
    data.write_text(
        json.dumps({"kind": "note", "content": "x", "extra": "stuff"}) + "\n"
    )
    result = runner.invoke(app, ["ingest", str(data), "--db", str(db)])
    assert result.exit_code == 1
    assert "unknown fields" in result.output


def test_ingest_empty_file_succeeds(tmp_path):
    db = tmp_path / "store.db"
    data = tmp_path / "empty.jsonl"
    data.write_text("")
    result = runner.invoke(app, ["ingest", str(data), "--db", str(db)])
    assert result.exit_code == 0
    assert "No records" in result.output


# ---------- read-side commands: missing-file rejection (no ST needed) ----------


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["search", "query"],
        ["list"],
        ["get", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
        ["delete", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
    ],
)
def test_command_rejects_missing_db(tmp_path, cmd_args):
    result = runner.invoke(app, [*cmd_args, "--db", str(tmp_path / "nope.db")])
    assert result.exit_code != 0


@pytest.mark.parametrize("cmd", ["search", "list", "get", "delete"])
def test_command_help_describes_options(cmd):
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0
    assert "--db" in result.output


# ---------- end-to-end (gated on fastembed) ----------


@pytest.fixture
def populated_db(tmp_path):
    pytest.importorskip("fastembed")

    db = tmp_path / "store.db"
    data = tmp_path / "records.jsonl"
    data.write_text(
        json.dumps({"kind": "note", "content": "the moon is full"})
        + "\n"
        + json.dumps({"kind": "note", "content": "dragons fly at midnight"})
        + "\n"
    )
    result = runner.invoke(app, ["ingest", str(data), "--db", str(db)])
    assert result.exit_code == 0
    return db


def test_list_outputs_jsonl(populated_db):
    result = runner.invoke(app, ["list", "--db", str(populated_db)])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["content"] for p in parsed} == {
        "the moon is full",
        "dragons fly at midnight",
    }


def test_search_returns_relevant_entry_first(populated_db):
    result = runner.invoke(
        app, ["search", "the moon is full", "--db", str(populated_db), "--k", "2"]
    )
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["content"] == "the moon is full"
    assert "distance" in parsed[0]


def test_get_fetches_by_id(populated_db):
    list_result = runner.invoke(
        app, ["list", "--db", str(populated_db), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())

    get_result = runner.invoke(app, ["get", first["id"], "--db", str(populated_db)])
    assert get_result.exit_code == 0
    assert json.loads(get_result.output.strip())["id"] == first["id"]


def test_get_missing_id_fails(populated_db):
    result = runner.invoke(
        app, ["get", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--db", str(populated_db)]
    )
    assert result.exit_code == 1
    assert "No entry" in result.output


def test_delete_removes_entry(populated_db):
    list_result = runner.invoke(
        app, ["list", "--db", str(populated_db), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())

    del_result = runner.invoke(app, ["delete", first["id"], "--db", str(populated_db)])
    assert del_result.exit_code == 0
    assert f"Deleted {first['id']}" in del_result.output

    after = runner.invoke(app, ["list", "--db", str(populated_db)])
    remaining = [json.loads(line) for line in after.output.splitlines() if line.strip()]
    assert all(r["id"] != first["id"] for r in remaining)


def test_delete_missing_id_fails(populated_db):
    result = runner.invoke(
        app, ["delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--db", str(populated_db)]
    )
    assert result.exit_code == 1
    assert "No entry" in result.output


def test_list_filters_by_kind(populated_db):
    pytest.importorskip("fastembed")

    # Add a record of a different kind.
    data = populated_db.parent / "second.jsonl"
    data.write_text(json.dumps({"kind": "spell", "content": "lumos"}) + "\n")
    runner.invoke(app, ["ingest", str(data), "--db", str(populated_db)])

    result = runner.invoke(app, ["list", "--db", str(populated_db), "--kind", "spell"])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "spell"

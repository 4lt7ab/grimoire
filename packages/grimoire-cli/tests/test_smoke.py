import json

import pytest
from grimoire_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(scope="session")
def _grimoire_cache_dir(tmp_path_factory):
    """A session-shared cache so the embedder model downloads once across tests."""
    return tmp_path_factory.mktemp("grimoire-cache")


@pytest.fixture(autouse=True)
def _set_grimoire_cache(monkeypatch, _grimoire_cache_dir):
    monkeypatch.setenv("GRIMOIRE_CACHE", str(_grimoire_cache_dir))


# ---------- help / no-args ----------


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("ingest", "search", "list", "get", "delete", "add", "info"):
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
        ["info"],
    ],
)
def test_command_rejects_missing_db(tmp_path, cmd_args):
    result = runner.invoke(app, [*cmd_args, "--db", str(tmp_path / "nope.db")])
    assert result.exit_code != 0


@pytest.mark.parametrize("cmd", ["search", "list", "get", "delete", "add", "info"])
def test_command_help_describes_db_option(cmd):
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0
    assert "--db" in result.output
    assert "GRIMOIRE_DB" in result.output


@pytest.mark.parametrize("cmd", ["search", "list", "get", "delete", "add", "ingest"])
def test_command_help_describes_cache_folder(cmd):
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0
    assert "--cache-folder" in result.output
    assert "GRIMOIRE_CACHE" in result.output


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["info"],
        ["list"],
        ["search", "query"],
        ["get", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
        ["delete", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
    ],
)
def test_command_requires_db_when_envvar_unset(monkeypatch, cmd_args):
    monkeypatch.delenv("GRIMOIRE_DB", raising=False)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code != 0
    assert "GRIMOIRE_DB" in result.output


def test_command_requires_cache_folder_when_envvar_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("GRIMOIRE_CACHE", raising=False)
    monkeypatch.setenv("GRIMOIRE_DB", str(tmp_path / "store.db"))
    result = runner.invoke(app, ["add", "hello"])
    assert result.exit_code != 0
    assert "GRIMOIRE_CACHE" in result.output


def test_envvar_supplies_db_path(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_DB", str(tmp_path / "missing.db"))
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 1
    assert "No grimoire at" in result.output


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


# ---------- info / add / search --dynamic-threshold ----------


def test_info_reports_metadata_and_counts(populated_db):
    result = runner.invoke(app, ["info", "--db", str(populated_db)])
    assert result.exit_code == 0
    parsed = json.loads(result.output.strip())
    assert parsed["path"] == str(populated_db)
    assert parsed["model"]
    assert parsed["dimension"] > 0
    assert parsed["schema_version"] == 1
    assert parsed["entry_count"] == 2
    assert parsed["kinds"] == {"note": 2}


def test_info_rejects_non_grimoire_file(tmp_path):
    import sqlite3

    db = tmp_path / "stranger.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["info", "--db", str(db)])
    assert result.exit_code == 1
    assert "No grimoire at" in result.output


def test_info_reports_missing_path_with_friendly_error(tmp_path):
    result = runner.invoke(app, ["info", "--db", str(tmp_path / "nope.db")])
    assert result.exit_code == 1
    assert "No grimoire at" in result.output


def test_add_inserts_a_single_record(tmp_path):
    pytest.importorskip("fastembed")

    db = tmp_path / "store.db"
    add_result = runner.invoke(
        app, ["add", "the moon is full", "--kind", "note", "--db", str(db)]
    )
    assert add_result.exit_code == 0
    parsed = json.loads(add_result.output.strip())
    assert parsed["content"] == "the moon is full"
    assert parsed["kind"] == "note"
    assert "id" in parsed

    list_result = runner.invoke(app, ["list", "--db", str(db)])
    assert list_result.exit_code == 0
    rows = [
        json.loads(line) for line in list_result.output.splitlines() if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["id"] == parsed["id"]


def test_add_rejects_non_object_payload(tmp_path):
    pytest.importorskip("fastembed")

    db = tmp_path / "store.db"
    result = runner.invoke(
        app, ["add", "hello", "--db", str(db), "--payload", '"just a string"']
    )
    assert result.exit_code == 1
    assert "JSON object" in result.output


def test_add_rejects_invalid_payload_json(tmp_path):
    pytest.importorskip("fastembed")

    db = tmp_path / "store.db"
    result = runner.invoke(
        app, ["add", "hello", "--db", str(db), "--payload", "{not json"]
    )
    assert result.exit_code == 1
    assert "valid JSON" in result.output


def test_search_dynamic_threshold_filters_results(tmp_path):
    pytest.importorskip("fastembed")

    db = tmp_path / "store.db"
    # Two entries both gated on a very tight threshold (0.0); only an
    # exact-match query should make it through.
    data = tmp_path / "records.jsonl"
    data.write_text(
        json.dumps({"kind": "note", "content": "the moon is full", "threshold": 0.0})
        + "\n"
        + json.dumps(
            {"kind": "note", "content": "dragons fly at midnight", "threshold": 0.0}
        )
        + "\n"
    )
    assert runner.invoke(app, ["ingest", str(data), "--db", str(db)]).exit_code == 0

    ungated = runner.invoke(
        app, ["search", "the moon is full", "--db", str(db), "--k", "5"]
    )
    assert ungated.exit_code == 0
    assert len([line for line in ungated.output.splitlines() if line.strip()]) == 2

    gated = runner.invoke(
        app,
        [
            "search",
            "the moon is full",
            "--db",
            str(db),
            "--k",
            "5",
            "--dynamic-threshold",
        ],
    )
    assert gated.exit_code == 0
    gated_lines = [line for line in gated.output.splitlines() if line.strip()]
    assert len(gated_lines) == 1
    assert json.loads(gated_lines[0])["content"] == "the moon is full"

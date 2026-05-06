import json
from pathlib import Path

import pytest
from grimoire_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_mount_env(monkeypatch):
    """Ensure GRIMOIRE_MOUNT from the developer's shell never bleeds into tests."""
    monkeypatch.delenv("GRIMOIRE_MOUNT", raising=False)


def _new_mount(tmp_path: Path, shared: Path, name: str = "store") -> Path:
    """Create a per-test mount dir with `models/` symlinked to a shared cache."""
    mount = tmp_path / name
    mount.mkdir()
    (mount / "models").symlink_to(shared, target_is_directory=True)
    return mount


def _init(mount: Path) -> None:
    """Run `grimoire init` on a mount; assumes fastembed is available."""
    pytest.importorskip("fastembed")
    result = runner.invoke(app, ["init", "--mount", str(mount)])
    assert result.exit_code == 0, result.output


def _last_json_line(output: str) -> dict:
    """Return the last `{...}` JSON object in CLI output.

    fastembed's first-time model download prints tqdm progress bars whose final
    update lacks a trailing newline, so a subsequent `typer.echo(json...)` can
    end up glued to the tail of a `Fetching ...` line. Tolerate that by parsing
    from the rightmost `{` of any line that ends in `}`.
    """
    for line in reversed(output.replace("\r", "\n").splitlines()):
        line = line.strip()
        if not line.endswith("}"):
            continue
        i = 0
        while (start := line.find("{", i)) != -1:
            try:
                return json.loads(line[start:])
            except json.JSONDecodeError:
                i = start + 1
    raise ValueError(f"No JSON object found in output: {output!r}")


# ---------- help / no-args ----------


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "init",
        "ingest",
        "vector-search",
        "keyword-search",
        "list",
        "get",
        "delete",
        "delete-many",
        "add",
        "update",
        "update-many",
        "info",
    ):
        assert cmd in result.output


def test_no_args_shows_help():
    # Click convention: missing subcommand exits 2, but help is still shown.
    result = runner.invoke(app, [])
    assert "ingest" in result.output


# ---------- ingest ----------


def test_ingest_help_describes_options():
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "--mount" in result.output


def test_ingest_missing_file_fails(tmp_path):
    mount = tmp_path / "store"
    result = runner.invoke(
        app, ["ingest", str(tmp_path / "nope.jsonl"), "--mount", str(mount)]
    )
    assert result.exit_code != 0


def test_ingest_rejects_invalid_json(tmp_path):
    mount = tmp_path / "store"
    data = tmp_path / "bad.jsonl"
    data.write_text("not valid json\n")
    result = runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    assert result.exit_code == 1
    assert "invalid JSON" in result.output


def test_ingest_rejects_missing_required_field(tmp_path):
    mount = tmp_path / "store"
    data = tmp_path / "missing.jsonl"
    data.write_text(json.dumps({"content": "no kind"}) + "\n")
    result = runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    assert result.exit_code == 1
    assert "missing required fields" in result.output
    assert "'kind'" in result.output


def test_ingest_rejects_unknown_field(tmp_path):
    mount = tmp_path / "store"
    data = tmp_path / "extra.jsonl"
    data.write_text(
        json.dumps({"kind": "note", "content": "x", "extra": "stuff"}) + "\n"
    )
    result = runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    assert result.exit_code == 1
    assert "unknown fields" in result.output


def test_ingest_empty_file_succeeds(tmp_path):
    mount = tmp_path / "store"
    data = tmp_path / "empty.jsonl"
    data.write_text("")
    result = runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    assert result.exit_code == 0
    assert "No records" in result.output


# ---------- read-side commands: missing-mount rejection ----------


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["vector-search", "query"],
        ["keyword-search", "query"],
        ["list"],
        ["get", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
        ["delete", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
        ["info"],
    ],
)
def test_command_rejects_missing_mount(tmp_path, cmd_args):
    result = runner.invoke(app, [*cmd_args, "--mount", str(tmp_path / "nope")])
    assert result.exit_code != 0


def test_top_level_help_is_self_documenting():
    """`grimoire --help` should orient a new operator without a doc lookup."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.output
    assert "grimoire.db" in out
    assert "models/" in out
    assert "jq" in out
    assert "GRIMOIRE_MOUNT" in out
    assert "Environment variables" in out
    for cmd in (
        "init",
        "info",
        "add",
        "update",
        "ingest",
        "vector-search",
        "keyword-search",
        "list",
        "get",
        "delete",
    ):
        assert cmd in out


@pytest.mark.parametrize(
    "cmd",
    [
        "init",
        "ingest",
        "vector-search",
        "keyword-search",
        "list",
        "get",
        "delete",
        "delete-many",
        "add",
        "update",
        "update-many",
        "info",
    ],
)
def test_command_help_describes_mount_option(cmd):
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0
    assert "--mount" in result.output
    assert "GRIMOIRE_MOUNT" in result.output


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["init"],
        ["info"],
        ["list"],
        ["vector-search", "query"],
        ["keyword-search", "query"],
        ["get", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
        ["delete", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
    ],
)
def test_command_requires_mount_when_envvar_unset(cmd_args):
    result = runner.invoke(app, cmd_args)
    assert result.exit_code != 0
    assert "GRIMOIRE_MOUNT" in result.output


def test_envvar_supplies_mount_path(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_MOUNT", str(tmp_path / "missing"))
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 1
    assert "No grimoire at" in result.output


# ---------- init ----------


def test_init_creates_db_and_prints_info(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    result = runner.invoke(app, ["init", "--mount", str(mount)])
    assert result.exit_code == 0, result.output
    parsed = _last_json_line(result.output)
    assert parsed["path"] == str(mount / "grimoire.db")
    assert parsed["model"]
    assert parsed["dimension"] > 0
    assert parsed["schema_version"] == 2
    assert parsed["entry_count"] == 0
    assert parsed["kinds"] == {}
    assert (mount / "grimoire.db").exists()


def test_init_creates_mount_dir_if_missing(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    # Mount does not exist yet; init must create it. The models subdir, however,
    # needs to be the shared cache to avoid a second model download in the test
    # session — so we create the mount + symlink up front for this assertion.
    mount = _new_mount(tmp_path, _shared_models_cache, name="fresh")
    assert runner.invoke(app, ["init", "--mount", str(mount)]).exit_code == 0
    assert (mount / "grimoire.db").exists()
    assert (mount / "models").exists()


def test_init_is_idempotent(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    first = runner.invoke(app, ["init", "--mount", str(mount)])
    assert first.exit_code == 0
    second = runner.invoke(app, ["init", "--mount", str(mount)])
    assert second.exit_code == 0
    assert _last_json_line(first.output) == _last_json_line(second.output)


def test_init_strict_mismatch_on_explicit_model(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    assert runner.invoke(app, ["init", "--mount", str(mount)]).exit_code == 0

    result = runner.invoke(
        app, ["init", "--mount", str(mount), "--model", "some-other-model"]
    )
    assert result.exit_code == 1
    assert "locked to model" in result.output


# ---------- write commands against a missing grimoire ----------


def test_add_against_missing_grimoire_says_run_init_first(tmp_path):
    pytest.importorskip("fastembed")
    mount = tmp_path / "nope"
    result = runner.invoke(app, ["add", "hello", "--mount", str(mount)])
    assert result.exit_code == 1
    assert "no grimoire at" in result.output
    assert "grimoire init" in result.output


def test_ingest_against_missing_grimoire_says_run_init_first(tmp_path):
    pytest.importorskip("fastembed")
    mount = tmp_path / "nope"
    data = tmp_path / "records.jsonl"
    data.write_text(json.dumps({"kind": "note", "content": "hello"}) + "\n")
    result = runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    assert result.exit_code == 1
    assert "no grimoire at" in result.output
    assert "grimoire init" in result.output


# ---------- end-to-end (gated on fastembed) ----------


@pytest.fixture
def populated_mount(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")

    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    data = tmp_path / "records.jsonl"
    data.write_text(
        json.dumps({"kind": "note", "content": "the moon is full"})
        + "\n"
        + json.dumps({"kind": "note", "content": "dragons fly at midnight"})
        + "\n"
    )
    result = runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    assert result.exit_code == 0
    return mount


def test_list_outputs_jsonl(populated_mount):
    result = runner.invoke(app, ["list", "--mount", str(populated_mount)])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["content"] for p in parsed} == {
        "the moon is full",
        "dragons fly at midnight",
    }


def test_search_returns_relevant_entry_first(populated_mount):
    result = runner.invoke(
        app,
        [
            "vector-search",
            "the moon is full",
            "--mount",
            str(populated_mount),
            "--k",
            "2",
        ],
    )
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["content"] == "the moon is full"
    assert "distance" in parsed[0]


def test_keyword_search_finds_token(populated_mount):
    result = runner.invoke(
        app,
        ["keyword-search", "moon", "--mount", str(populated_mount), "--k", "5"],
    )
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == 1
    assert parsed[0]["content"] == "the moon is full"
    assert "rank" in parsed[0]
    assert "distance" not in parsed[0]


def test_keyword_search_returns_nothing_on_no_match(populated_mount):
    result = runner.invoke(
        app,
        ["keyword-search", "phoenix", "--mount", str(populated_mount)],
    )
    assert result.exit_code == 0
    assert [line for line in result.output.splitlines() if line.strip()] == []


def test_get_fetches_by_id(populated_mount):
    list_result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())

    get_result = runner.invoke(
        app, ["get", first["id"], "--mount", str(populated_mount)]
    )
    assert get_result.exit_code == 0
    assert json.loads(get_result.output.strip())["id"] == first["id"]


def test_get_missing_id_fails(populated_mount):
    result = runner.invoke(
        app,
        ["get", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--mount", str(populated_mount)],
    )
    assert result.exit_code == 1
    assert "No entry" in result.output


def test_delete_removes_entry(populated_mount):
    list_result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())

    del_result = runner.invoke(
        app, ["delete", first["id"], "--mount", str(populated_mount)]
    )
    assert del_result.exit_code == 0
    assert f"Deleted {first['id']}" in del_result.output

    after = runner.invoke(app, ["list", "--mount", str(populated_mount)])
    remaining = [json.loads(line) for line in after.output.splitlines() if line.strip()]
    assert all(r["id"] != first["id"] for r in remaining)


def test_update_changes_content(populated_mount):
    list_result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())

    upd = runner.invoke(
        app,
        [
            "update",
            first["id"],
            "--mount",
            str(populated_mount),
            "--content",
            "a brand new line",
        ],
    )
    assert upd.exit_code == 0, upd.output
    parsed = _last_json_line(upd.output)
    assert parsed["id"] == first["id"]
    assert parsed["content"] == "a brand new line"


def test_update_clear_keywords_drops_them(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    add_res = runner.invoke(
        app,
        [
            "add",
            "hello world",
            "--mount",
            str(mount),
            "--keyword",
            "alpha",
            "--keyword",
            "beta",
        ],
    )
    assert add_res.exit_code == 0
    added_id = _last_json_line(add_res.output)["id"]

    upd = runner.invoke(
        app,
        ["update", added_id, "--mount", str(mount), "--clear-keywords"],
    )
    assert upd.exit_code == 0, upd.output
    parsed = _last_json_line(upd.output)
    assert "keywords" not in parsed


def test_update_replaces_keywords(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    add_res = runner.invoke(
        app,
        ["add", "hello world", "--mount", str(mount), "--keyword", "old"],
    )
    added_id = _last_json_line(add_res.output)["id"]

    upd = runner.invoke(
        app,
        [
            "update",
            added_id,
            "--mount",
            str(mount),
            "--keyword",
            "new",
            "--keyword",
            "fresh",
        ],
    )
    assert upd.exit_code == 0, upd.output
    parsed = _last_json_line(upd.output)
    assert parsed["keywords"] == ["new", "fresh"]


def test_update_missing_id_fails(populated_mount):
    result = runner.invoke(
        app,
        [
            "update",
            "01HXXXXXXXXXXXXXXXXXXXXXXX",
            "--mount",
            str(populated_mount),
            "--content",
            "x",
        ],
    )
    assert result.exit_code == 1
    assert "No entry" in result.output


def test_update_rejects_keyword_and_clear_keywords_together(populated_mount):
    list_result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())
    result = runner.invoke(
        app,
        [
            "update",
            first["id"],
            "--mount",
            str(populated_mount),
            "--keyword",
            "x",
            "--clear-keywords",
        ],
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_update_rejects_invalid_payload_json(populated_mount):
    list_result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())
    result = runner.invoke(
        app,
        [
            "update",
            first["id"],
            "--mount",
            str(populated_mount),
            "--payload",
            "{not json",
        ],
    )
    assert result.exit_code == 1
    assert "valid JSON" in result.output


def test_update_many_patches_records_from_jsonl(populated_mount, tmp_path):
    list_result = runner.invoke(app, ["list", "--mount", str(populated_mount)])
    rows = [
        json.loads(line) for line in list_result.output.splitlines() if line.strip()
    ]
    data = tmp_path / "patches.jsonl"
    data.write_text(
        json.dumps({"id": rows[0]["id"], "content": "patched zero"})
        + "\n"
        + json.dumps({"id": rows[1]["id"], "kind": "spell"})
        + "\n"
    )
    result = runner.invoke(
        app, ["update-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 0, result.output
    assert "Updated 2 of 2" in result.output

    after = runner.invoke(app, ["list", "--mount", str(populated_mount)])
    parsed = [json.loads(line) for line in after.output.splitlines() if line.strip()]
    by_id = {p["id"]: p for p in parsed}
    assert by_id[rows[0]["id"]]["content"] == "patched zero"
    assert by_id[rows[1]["id"]]["kind"] == "spell"


def test_update_many_reports_unknown_ids(populated_mount, tmp_path):
    data = tmp_path / "patches.jsonl"
    data.write_text(
        json.dumps({"id": "01HXXXXXXXXXXXXXXXXXXXXXXX", "content": "ghost"}) + "\n"
    )
    result = runner.invoke(
        app, ["update-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 0, result.output
    assert "Updated 0 of 1" in result.output
    assert "1 unknown id" in result.output


def test_update_many_rejects_missing_id(populated_mount, tmp_path):
    data = tmp_path / "patches.jsonl"
    data.write_text(json.dumps({"content": "no id here"}) + "\n")
    result = runner.invoke(
        app, ["update-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 1
    assert "missing required fields" in result.output
    assert "'id'" in result.output


def test_update_many_rejects_unknown_field(populated_mount, tmp_path):
    data = tmp_path / "patches.jsonl"
    data.write_text(
        json.dumps({"id": "01HXXXXXXXXXXXXXXXXXXXXXXX", "extra": "stuff"}) + "\n"
    )
    result = runner.invoke(
        app, ["update-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 1
    assert "unknown fields" in result.output


def test_update_many_empty_file_succeeds(populated_mount, tmp_path):
    data = tmp_path / "empty.jsonl"
    data.write_text("")
    result = runner.invoke(
        app, ["update-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 0
    assert "No records" in result.output


def test_delete_many_removes_listed_ids(populated_mount, tmp_path):
    list_result = runner.invoke(app, ["list", "--mount", str(populated_mount)])
    rows = [
        json.loads(line) for line in list_result.output.splitlines() if line.strip()
    ]
    data = tmp_path / "ids.txt"
    data.write_text("\n".join(r["id"] for r in rows) + "\n")
    result = runner.invoke(
        app, ["delete-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 0, result.output
    assert "Deleted 2 of 2" in result.output

    after = runner.invoke(app, ["list", "--mount", str(populated_mount)])
    assert [line for line in after.output.splitlines() if line.strip()] == []


def test_delete_many_reports_unknown_ids(populated_mount, tmp_path):
    data = tmp_path / "ids.txt"
    data.write_text("01HXXXXXXXXXXXXXXXXXXXXXXX\n")
    result = runner.invoke(
        app, ["delete-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 0
    assert "Deleted 0 of 1" in result.output
    assert "1 unknown id" in result.output


def test_delete_many_skips_blank_and_comment_lines(populated_mount, tmp_path):
    list_result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())
    data = tmp_path / "ids.txt"
    data.write_text(f"# delete just one\n\n{first['id']}\n\n")
    result = runner.invoke(
        app, ["delete-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 0
    assert "Deleted 1 of 1" in result.output


def test_delete_many_empty_file_succeeds(populated_mount, tmp_path):
    data = tmp_path / "empty.txt"
    data.write_text("")
    result = runner.invoke(
        app, ["delete-many", str(data), "--mount", str(populated_mount)]
    )
    assert result.exit_code == 0
    assert "No ids" in result.output


def test_delete_many_reads_ids_from_stdin(populated_mount):
    list_result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--limit", "1"]
    )
    first = json.loads(list_result.output.strip())
    result = runner.invoke(
        app,
        ["delete-many", "-", "--mount", str(populated_mount)],
        input=f"{first['id']}\n",
    )
    assert result.exit_code == 0, result.output
    assert "Deleted 1 of 1" in result.output


def test_delete_missing_id_fails(populated_mount):
    result = runner.invoke(
        app,
        ["delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--mount", str(populated_mount)],
    )
    assert result.exit_code == 1
    assert "No entry" in result.output


def test_list_filters_by_kind(populated_mount):
    pytest.importorskip("fastembed")

    # Add a record of a different kind.
    data = populated_mount.parent / "second.jsonl"
    data.write_text(json.dumps({"kind": "spell", "content": "lumos"}) + "\n")
    runner.invoke(app, ["ingest", str(data), "--mount", str(populated_mount)])

    result = runner.invoke(
        app, ["list", "--mount", str(populated_mount), "--kind", "spell"]
    )
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "spell"


# ---------- info / add / search --dynamic-threshold ----------


def test_info_reports_metadata_and_counts(populated_mount):
    result = runner.invoke(app, ["info", "--mount", str(populated_mount)])
    assert result.exit_code == 0
    parsed = json.loads(result.output.strip())
    assert parsed["path"] == str(populated_mount / "grimoire.db")
    assert parsed["model"]
    assert parsed["dimension"] > 0
    assert parsed["schema_version"] == 2
    assert parsed["entry_count"] == 2
    assert parsed["kinds"] == {"note": 2}


def test_info_rejects_non_grimoire_file(tmp_path):
    import sqlite3

    mount = tmp_path / "store"
    mount.mkdir()
    db = mount / "grimoire.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["info", "--mount", str(mount)])
    assert result.exit_code == 1
    assert "No grimoire at" in result.output


def test_info_reports_missing_path_with_friendly_error(tmp_path):
    result = runner.invoke(app, ["info", "--mount", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "No grimoire at" in result.output


def test_add_inserts_a_single_record(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")

    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    add_result = runner.invoke(
        app,
        ["add", "the moon is full", "--kind", "note", "--mount", str(mount)],
    )
    assert add_result.exit_code == 0
    parsed = json.loads(add_result.output.strip())
    assert parsed["content"] == "the moon is full"
    assert parsed["kind"] == "note"
    assert "id" in parsed

    list_result = runner.invoke(app, ["list", "--mount", str(mount)])
    assert list_result.exit_code == 0
    rows = [
        json.loads(line) for line in list_result.output.splitlines() if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["id"] == parsed["id"]


def test_add_rejects_non_object_payload(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")

    mount = _new_mount(tmp_path, _shared_models_cache)
    result = runner.invoke(
        app,
        ["add", "hello", "--mount", str(mount), "--payload", '"just a string"'],
    )
    assert result.exit_code == 1
    assert "JSON object" in result.output


def test_add_rejects_invalid_payload_json(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")

    mount = _new_mount(tmp_path, _shared_models_cache)
    result = runner.invoke(
        app, ["add", "hello", "--mount", str(mount), "--payload", "{not json"]
    )
    assert result.exit_code == 1
    assert "valid JSON" in result.output


def test_search_dynamic_threshold_filters_results(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")

    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
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
    assert (
        runner.invoke(app, ["ingest", str(data), "--mount", str(mount)]).exit_code == 0
    )

    ungated = runner.invoke(
        app, ["vector-search", "the moon is full", "--mount", str(mount), "--k", "5"]
    )
    assert ungated.exit_code == 0
    assert len([line for line in ungated.output.splitlines() if line.strip()]) == 2

    gated = runner.invoke(
        app,
        [
            "vector-search",
            "the moon is full",
            "--mount",
            str(mount),
            "--k",
            "5",
            "--dynamic-threshold",
        ],
    )
    assert gated.exit_code == 0
    gated_lines = [line for line in gated.output.splitlines() if line.strip()]
    assert len(gated_lines) == 1
    assert json.loads(gated_lines[0])["content"] == "the moon is full"


# ---------- keywords ----------


def test_add_with_repeatable_keyword_flag(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    result = runner.invoke(
        app,
        [
            "add",
            "hello world",
            "--mount",
            str(mount),
            "--keyword",
            "alpha",
            "--keyword",
            "beta",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = _last_json_line(result.output)
    assert parsed["keywords"] == ["alpha", "beta"]


def test_add_without_keyword_flag_omits_keywords_in_output(
    tmp_path, _shared_models_cache
):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    result = runner.invoke(app, ["add", "hello world", "--mount", str(mount)])
    assert result.exit_code == 0, result.output
    parsed = _last_json_line(result.output)
    assert "keywords" not in parsed


def test_ingest_carries_keywords_through(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    data = tmp_path / "records.jsonl"
    data.write_text(
        json.dumps(
            {
                "kind": "spell",
                "content": "Coaxes a locked door to forget its keeper",
                "keywords": ["lockpick", "skeleton-key"],
            }
        )
        + "\n"
    )
    assert (
        runner.invoke(app, ["ingest", str(data), "--mount", str(mount)]).exit_code == 0
    )

    list_result = runner.invoke(app, ["list", "--mount", str(mount)])
    assert list_result.exit_code == 0
    parsed = json.loads(list_result.output.strip())
    assert parsed["keywords"] == ["lockpick", "skeleton-key"]


def test_ingest_handles_non_ascii_content(tmp_path, _shared_models_cache):
    """JSONL is utf-8 by spec; ingest must not depend on the locale encoding."""
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    data = tmp_path / "utf8.jsonl"
    payload = "über naïve café — 日本語 🜂"
    data.write_text(
        json.dumps({"kind": "note", "content": payload}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    assert result.exit_code == 0, result.output

    list_result = runner.invoke(app, ["list", "--mount", str(mount)])
    assert list_result.exit_code == 0
    parsed = json.loads(list_result.output.strip())
    assert parsed["content"] == payload


def test_keyword_search_finds_via_keywords_field(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _init(mount)
    data = tmp_path / "records.jsonl"
    data.write_text(
        json.dumps(
            {
                "kind": "spell",
                "content": "Coaxes a locked door to forget its keeper",
                "keywords": ["lockpick"],
            }
        )
        + "\n"
    )
    runner.invoke(app, ["ingest", str(data), "--mount", str(mount)])
    result = runner.invoke(app, ["keyword-search", "lockpick", "--mount", str(mount)])
    assert result.exit_code == 0
    parsed = json.loads(result.output.strip())
    assert parsed["content"] == "Coaxes a locked door to forget its keeper"
    assert parsed["keywords"] == ["lockpick"]

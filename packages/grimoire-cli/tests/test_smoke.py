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


def _mount(mount: Path) -> None:
    """Run `grimoire mount` on a mount; assumes fastembed is available."""
    pytest.importorskip("fastembed")
    result = runner.invoke(app, ["--mount", str(mount), "mount"])
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


def _json_lines(output: str) -> list[dict]:
    """Parse one JSON object per line from CLI output."""
    out: list[dict] = []
    for line in output.replace("\r", "\n").splitlines():
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@pytest.fixture
def populated_mount(tmp_path, _shared_models_cache):
    """A mount with a few entries already added — for read-side tests."""
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    for content, group_key, group_ref in [
        ("the moon is full", "note", None),
        ("dragons fly at midnight", "note", "dragon-001"),
        ("lumos lights the way", "spell", "lumos"),
    ]:
        cmd = ["--mount", str(mount), "entry", "add", content, "--group-key", group_key]
        if group_ref:
            cmd += ["--group-ref", group_ref]
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, result.output
    return mount


# ---------- output formatting (--raw, TTY auto-detect) ----------


def test_default_output_is_jsonl_when_piped(populated_mount):
    """CliRunner doesn't attach a TTY, so default output is the JSONL shape."""
    result = runner.invoke(app, ["--mount", str(populated_mount), "ls"])
    assert result.exit_code == 0, result.output
    rows = _json_lines(result.output)
    assert len(rows) == 1
    assert rows[0]["name"] is None  # default DB


def test_raw_flag_forces_jsonl_at_tty(populated_mount, monkeypatch):
    """`--raw` must emit JSONL even when stdout claims to be a TTY."""
    monkeypatch.setattr("grimoire_cli.output._is_tty", lambda: True)
    # Without --raw, pretty output would emit a Rich table (no parseable JSON).
    pretty = runner.invoke(app, ["--mount", str(populated_mount), "ls"])
    assert pretty.exit_code == 0
    assert _json_lines(pretty.output) == []  # no JSON objects in the table

    # With --raw, JSONL flows out the same as in pipe mode.
    raw = runner.invoke(app, ["--mount", str(populated_mount), "--raw", "ls"])
    assert raw.exit_code == 0
    rows = _json_lines(raw.output)
    assert len(rows) == 1
    assert rows[0]["name"] is None


def test_pretty_table_at_tty_for_ls(populated_mount, monkeypatch):
    """Pretty output renders a table with the column headers, not JSON."""
    monkeypatch.setattr("grimoire_cli.output._is_tty", lambda: True)
    result = runner.invoke(app, ["--mount", str(populated_mount), "ls"])
    assert result.exit_code == 0
    # Rich draws bold headers — substring check is enough.
    for header in ("NAME", "MODEL", "DIM", "ENTRIES", "DEFAULT"):
        assert header in result.output
    assert "(default)" in result.output  # default DB row label


def test_pretty_entries_at_tty_for_query(populated_mount, monkeypatch):
    """Query renders a CONTENT/GROUP/REF table at the terminal."""
    monkeypatch.setattr("grimoire_cli.output._is_tty", lambda: True)
    result = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    assert result.exit_code == 0
    for header in ("ID", "GROUP", "REF", "CONTENT"):
        assert header in result.output


# ---------- help / no-args ----------


def test_help_lists_new_command_set():
    """Top-level help shows the seven first-class commands.

    Hot-path entry reads (search, query) live at the top level; the rest
    of the entry CRUD lives under `entry`. See `test_entry_help_lists_*`.
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "mount",
        "create",
        "destroy",
        "ls",
        "query",
        "search",
        "entry",
    ):
        assert cmd in result.output


def test_entry_help_lists_subcommands():
    result = runner.invoke(app, ["entry", "--help"])
    assert result.exit_code == 0
    for cmd in ("add", "get", "update", "delete", "import", "export"):
        assert cmd in result.output


def test_help_does_not_list_removed_commands():
    """Removed top-level commands are gone — moved verbs that appear as
    substrings in the `entry` subapp's help description are not asserted
    here; they're not commands at the top level, but they ARE listed in
    the entry subapp's description text."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for removed in (
        "init",
        "ingest",
        "vector-search",
        "keyword-search",
        "list ",
        "info",
        "update-many",
        "delete-many",
    ):
        assert removed not in result.output


def test_no_args_with_no_grimoire_errors(tmp_path):
    """Bare `grimoire` against an empty mount fails clearly."""
    result = runner.invoke(app, ["--mount", str(tmp_path / "empty")])
    assert result.exit_code != 0
    assert "No grimoire" in result.output


def test_no_args_emits_info_for_existing_grimoire(populated_mount):
    """Bare `grimoire` (no subcommand) prints the info JSON."""
    result = runner.invoke(app, ["--mount", str(populated_mount)])
    assert result.exit_code == 0, result.output
    parsed = _last_json_line(result.output)
    assert parsed["entry_count"] == 3
    assert parsed["groups"] == {"note": 2, "spell": 1}
    assert "model" in parsed and "dimension" in parsed


# ---------- mount (creates mount + default DB) ----------


def test_mount_creates_db_and_prints_info(tmp_path, _shared_models_cache):
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    assert (mount / "grimoire.db").exists()
    result = runner.invoke(app, ["--mount", str(mount)])
    parsed = _last_json_line(result.output)
    assert parsed["entry_count"] == 0
    assert parsed["groups"] == {}


def test_mount_creates_mount_dir_if_missing(tmp_path, _shared_models_cache):
    pytest.importorskip("fastembed")
    fresh = tmp_path / "fresh"
    assert not fresh.exists()
    fresh.mkdir(parents=True)
    (fresh / "models").symlink_to(_shared_models_cache, target_is_directory=True)
    result = runner.invoke(app, ["--mount", str(fresh), "mount"])
    assert result.exit_code == 0, result.output
    assert (fresh / "grimoire.db").exists()


def test_mount_is_idempotent(tmp_path, _shared_models_cache):
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    _mount(mount)


def test_mount_emits_listing_like_ls(populated_mount, _shared_models_cache):
    """`mount` and `ls` produce the same JSONL shape; both report the default DB."""
    pytest.importorskip("fastembed")
    runner.invoke(app, ["--mount", str(populated_mount), "create", "alpha"])

    mount_result = runner.invoke(app, ["--mount", str(populated_mount), "mount"])
    ls_result = runner.invoke(app, ["--mount", str(populated_mount), "ls"])
    assert mount_result.exit_code == 0, mount_result.output
    assert ls_result.exit_code == 0, ls_result.output

    mount_rows = _json_lines(mount_result.output)
    ls_rows = _json_lines(ls_result.output)
    assert mount_rows == ls_rows
    # Default DB is reported by both.
    names = [r["name"] for r in mount_rows]
    assert names == [None, "alpha"]
    assert mount_rows[0]["is_default"] is True


def test_mount_skips_embedder_load_on_rerun(tmp_path, _shared_models_cache):
    """Re-running `mount` against an existing default DB needs no fastembed.

    Pin: the listing path must not import fastembed when the default DB
    already exists. Simulated by removing fastembed from sys.modules and
    rerunning — would explode if the codepath touched it.
    """
    pytest.importorskip("fastembed")
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)  # first run creates the DB; needs fastembed

    # Re-run should succeed without re-importing fastembed.
    import sys

    fastembed_mods = {k: v for k, v in sys.modules.items() if k.startswith("fastembed")}
    for k in fastembed_mods:
        del sys.modules[k]
    try:
        result = runner.invoke(app, ["--mount", str(mount), "mount"])
        assert result.exit_code == 0, result.output
        # Listing reports the default DB.
        rows = _json_lines(result.output)
        assert any(r["is_default"] for r in rows)
        # fastembed remained absent — re-mount didn't pull it back in.
        assert not any(k.startswith("fastembed") for k in sys.modules)
    finally:
        sys.modules.update(fastembed_mods)


# ---------- create (named DBs only) ----------


def test_create_named_db_lives_in_subdir(populated_mount, _shared_models_cache):
    pytest.importorskip("fastembed")
    result = runner.invoke(app, ["--mount", str(populated_mount), "create", "spells"])
    assert result.exit_code == 0, result.output
    assert (populated_mount / "spells" / "grimoire.db").exists()
    # Default DB unaffected.
    assert (populated_mount / "grimoire.db").exists()


def test_create_errors_on_duplicate(populated_mount, _shared_models_cache):
    pytest.importorskip("fastembed")
    runner.invoke(app, ["--mount", str(populated_mount), "create", "alpha"])
    result = runner.invoke(app, ["--mount", str(populated_mount), "create", "alpha"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_create_creates_mount_lazily(tmp_path, _shared_models_cache):
    """`grimoire create` works without a prior `grimoire mount`."""
    pytest.importorskip("fastembed")
    fresh = tmp_path / "fresh"
    assert not fresh.exists()
    fresh.mkdir(parents=True)
    (fresh / "models").symlink_to(_shared_models_cache, target_is_directory=True)
    result = runner.invoke(app, ["--mount", str(fresh), "create", "alpha"])
    assert result.exit_code == 0, result.output
    assert (fresh / "alpha" / "grimoire.db").exists()
    # No default DB was implicitly created.
    assert not (fresh / "grimoire.db").exists()


def test_create_records_description_in_manifest(populated_mount, _shared_models_cache):
    pytest.importorskip("fastembed")
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "create",
            "annotated",
            "--description",
            "the annotated one",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = (populated_mount / "grimoire.toml").read_text()
    assert "annotated" in manifest
    assert "the annotated one" in manifest


# ---------- add / get / delete ----------


def test_add_minimal_record(populated_mount):
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "entry", "add", "a brand new entry"]
    )
    assert result.exit_code == 0, result.output
    parsed = _last_json_line(result.output)
    assert parsed["content"] == "a brand new entry"
    assert "group_key" not in parsed
    assert "group_ref" not in parsed


def test_add_with_group_key_and_group_ref(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "add",
            "indexed entry",
            "--group-key",
            "indexed",
            "--group-ref",
            "ref-1",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = _last_json_line(result.output)
    assert parsed["group_key"] == "indexed"
    assert parsed["group_ref"] == "ref-1"


def test_add_collision_on_group_ref_fails(populated_mount):
    """The fixture has (note, dragon-001); re-adding should fail."""
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "add",
            "another dragon",
            "--group-key",
            "note",
            "--group-ref",
            "dragon-001",
        ],
    )
    assert result.exit_code != 0
    assert "collision" in result.output.lower()


def test_get_returns_entry(populated_mount):
    listing = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    first = _json_lines(listing.output)[0]
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "entry", "get", first["id"]]
    )
    assert result.exit_code == 0
    parsed = _last_json_line(result.output)
    assert parsed["id"] == first["id"]


def test_get_unknown_id_fails(populated_mount):
    result = runner.invoke(
        app,
        ["--mount", str(populated_mount), "entry", "get", "01HXXXXXXXXXXXXXXXXXXXXXXX"],
    )
    assert result.exit_code != 0


def test_delete_removes_entry(populated_mount):
    listing = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    target = _json_lines(listing.output)[0]
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "entry", "delete", target["id"]]
    )
    assert result.exit_code == 0
    after = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    assert target["id"] not in {e["id"] for e in _json_lines(after.output)}


def test_delete_unknown_id_fails(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "delete",
            "01HXXXXXXXXXXXXXXXXXXXXXXX",
        ],
    )
    assert result.exit_code != 0


# ---------- update ----------


def test_update_patches_content(populated_mount):
    listing = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    target = _json_lines(listing.output)[0]
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "update",
            target["id"],
            "--content",
            "rewritten",
        ],
    )
    assert result.exit_code == 0
    parsed = _last_json_line(result.output)
    assert parsed["content"] == "rewritten"


def test_update_clears_group_key(populated_mount):
    listing = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    target = _json_lines(listing.output)[0]
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "update",
            target["id"],
            "--clear-group-key",
        ],
    )
    assert result.exit_code == 0
    parsed = _last_json_line(result.output)
    assert "group_key" not in parsed


def test_update_set_and_clear_group_ref(populated_mount):
    listing = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    target = _json_lines(listing.output)[0]
    set_result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "update",
            target["id"],
            "--group-ref",
            "freshly-set",
        ],
    )
    assert set_result.exit_code == 0
    assert _last_json_line(set_result.output)["group_ref"] == "freshly-set"

    clear_result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "update",
            target["id"],
            "--clear-group-ref",
        ],
    )
    assert clear_result.exit_code == 0
    assert "group_ref" not in _last_json_line(clear_result.output)


def test_update_mutual_exclusion_errors(populated_mount):
    listing = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    target = _json_lines(listing.output)[0]
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "update",
            target["id"],
            "--group-key",
            "x",
            "--clear-group-key",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_update_collision_fails(populated_mount):
    """Updating an entry into an existing (group_key, group_ref) should fail."""
    listing = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    entries = _json_lines(listing.output)
    moon = next(e for e in entries if e["content"] == "the moon is full")
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "update",
            moon["id"],
            "--group-key",
            "note",
            "--group-ref",
            "dragon-001",
        ],
    )
    assert result.exit_code != 0
    assert "collision" in result.output.lower()


def test_update_unknown_id_fails(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "update",
            "01HXXXXXXXXXXXXXXXXXXXXXXX",
            "--content",
            "x",
        ],
    )
    assert result.exit_code != 0


# ---------- query ----------


def test_query_returns_all_entries_chronologically(populated_mount):
    result = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    assert result.exit_code == 0
    entries = _json_lines(result.output)
    assert len(entries) == 3
    ids = [e["id"] for e in entries]
    assert ids == sorted(ids)


def test_query_filter_by_group_key(populated_mount):
    result = runner.invoke(
        app,
        ["--mount", str(populated_mount), "query", "--group-key", "spell"],
    )
    entries = _json_lines(result.output)
    assert len(entries) == 1
    assert entries[0]["group_key"] == "spell"


def test_query_filter_by_group_ref(populated_mount):
    result = runner.invoke(
        app,
        ["--mount", str(populated_mount), "query", "--group-ref", "dragon-001"],
    )
    entries = _json_lines(result.output)
    assert len(entries) == 1
    assert entries[0]["group_ref"] == "dragon-001"


def test_query_combined_filters(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "query",
            "--group-key",
            "note",
            "--group-ref",
            "dragon-001",
        ],
    )
    entries = _json_lines(result.output)
    assert len(entries) == 1


def test_query_paginates_via_cursor(populated_mount):
    page1 = _json_lines(
        runner.invoke(
            app, ["--mount", str(populated_mount), "query", "--limit", "2"]
        ).output
    )
    assert len(page1) == 2
    page2 = _json_lines(
        runner.invoke(
            app,
            [
                "--mount",
                str(populated_mount),
                "query",
                "--limit",
                "2",
                "--cursor",
                page1[-1]["id"],
            ],
        ).output
    )
    assert len(page2) == 1
    assert page2[0]["id"] not in {e["id"] for e in page1}


def test_query_invalid_iso_fails(populated_mount):
    result = runner.invoke(
        app,
        ["--mount", str(populated_mount), "query", "--after", "not-a-date"],
    )
    assert result.exit_code != 0


# ---------- search ----------


def test_search_default_mode_is_vector(populated_mount):
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "search", "the moon is full"]
    )
    assert result.exit_code == 0, result.output
    entries = _json_lines(result.output)
    assert len(entries) > 0
    assert "distance" in entries[0]


def test_search_keyword_mode(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "search",
            "lumos",
            "--mode",
            "keyword",
        ],
    )
    assert result.exit_code == 0, result.output
    entries = _json_lines(result.output)
    assert any(e["content"] == "lumos lights the way" for e in entries)
    assert "rank" in entries[0]


def test_search_invalid_mode_fails(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "search",
            "x",
            "--mode",
            "fuzzy",
        ],
    )
    assert result.exit_code != 0
    assert "vector" in result.output and "keyword" in result.output


def test_search_dynamic_threshold_rejected_in_keyword_mode(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "search",
            "lumos",
            "--mode",
            "keyword",
            "--dynamic-threshold",
        ],
    )
    assert result.exit_code != 0
    assert "vector" in result.output


def test_search_filters_by_group_key(populated_mount):
    result = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "search",
            "lumos",
            "--mode",
            "keyword",
            "--group-key",
            "spell",
        ],
    )
    entries = _json_lines(result.output)
    assert all(e["group_key"] == "spell" for e in entries)


# ---------- import ----------


def test_import_records_into_grimoire(tmp_path, _shared_models_cache):
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    data = tmp_path / "data.jsonl"
    data.write_text(
        "\n".join(
            [
                json.dumps({"content": "first", "group_key": "note"}),
                json.dumps(
                    {
                        "content": "second",
                        "group_key": "note",
                        "group_ref": "ref-x",
                    }
                ),
            ]
        )
        + "\n"
    )
    result = runner.invoke(app, ["--mount", str(mount), "entry", "import", str(data)])
    assert result.exit_code == 0, result.output
    listing = runner.invoke(app, ["--mount", str(mount), "query"])
    assert len(_json_lines(listing.output)) == 2


def test_import_collision_aborts(tmp_path, _shared_models_cache):
    """Adding a record whose (group_key, group_ref) already exists fails loudly."""
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    runner.invoke(
        app,
        [
            "--mount",
            str(mount),
            "entry",
            "add",
            "existing",
            "--group-key",
            "doc",
            "--group-ref",
            "shared",
        ],
    )
    data = tmp_path / "data.jsonl"
    data.write_text(
        json.dumps({"content": "x", "group_key": "doc", "group_ref": "shared"}) + "\n"
    )
    result = runner.invoke(app, ["--mount", str(mount), "entry", "import", str(data)])
    assert result.exit_code != 0
    assert "collision" in result.output.lower()


def test_import_rejects_record_missing_content(tmp_path, _shared_models_cache):
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    data = tmp_path / "data.jsonl"
    data.write_text(json.dumps({"group_key": "note"}) + "\n")
    result = runner.invoke(app, ["--mount", str(mount), "entry", "import", str(data)])
    assert result.exit_code != 0
    assert "content" in result.output


def test_import_rejects_unknown_field(tmp_path, _shared_models_cache):
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    data = tmp_path / "data.jsonl"
    data.write_text(json.dumps({"content": "x", "extra": "boom"}) + "\n")
    result = runner.invoke(app, ["--mount", str(mount), "entry", "import", str(data)])
    assert result.exit_code != 0


def test_import_empty_file_is_noop(tmp_path, _shared_models_cache):
    mount = _new_mount(tmp_path, _shared_models_cache)
    _mount(mount)
    data = tmp_path / "data.jsonl"
    data.write_text("")
    result = runner.invoke(app, ["--mount", str(mount), "entry", "import", str(data)])
    assert result.exit_code == 0
    assert "No records" in result.output


# ---------- export ----------


def test_export_writes_default_path(populated_mount):
    result = runner.invoke(app, ["--mount", str(populated_mount), "entry", "export"])
    assert result.exit_code == 0, result.output
    out = populated_mount / "export.jsonl"
    assert out.exists()
    lines = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(lines) == 3
    assert all("id" not in r for r in lines)


def test_export_refuses_to_overwrite(populated_mount):
    out = populated_mount / "export.jsonl"
    out.write_text("preexisting\n")
    result = runner.invoke(app, ["--mount", str(populated_mount), "entry", "export"])
    assert result.exit_code != 0
    assert "exists" in result.output
    assert out.read_text() == "preexisting\n"


def test_export_force_overwrites(populated_mount):
    out = populated_mount / "export.jsonl"
    out.write_text("preexisting\n")
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "entry", "export", "--force"]
    )
    assert result.exit_code == 0, result.output
    assert "preexisting" not in out.read_text()


def test_export_custom_path(populated_mount, tmp_path):
    out = tmp_path / "custom.jsonl"
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "entry", "export", "-o", str(out)]
    )
    assert result.exit_code == 0
    assert out.exists()


def test_export_then_import_round_trips_content(
    tmp_path, _shared_models_cache, populated_mount
):
    """Export from one grimoire, import into a fresh one — content survives."""
    out = tmp_path / "round.jsonl"
    runner.invoke(
        app, ["--mount", str(populated_mount), "entry", "export", "-o", str(out)]
    )
    fresh = _new_mount(tmp_path, _shared_models_cache, name="fresh")
    _mount(fresh)
    result = runner.invoke(app, ["--mount", str(fresh), "entry", "import", str(out)])
    assert result.exit_code == 0, result.output
    listing = _json_lines(runner.invoke(app, ["--mount", str(fresh), "query"]).output)
    assert len(listing) == 3
    contents = {e["content"] for e in listing}
    assert contents == {
        "the moon is full",
        "dragons fly at midnight",
        "lumos lights the way",
    }


# ---------- destroy (per-database) ----------


def test_destroy_removes_default_db(populated_mount):
    """Bare `destroy` removes only the default database, leaving the mount intact."""
    result = runner.invoke(app, ["--mount", str(populated_mount), "destroy", "--yes"])
    assert result.exit_code == 0
    assert not (populated_mount / "grimoire.db").exists()
    # Mount root, models cache, and (if any) named DBs survive.
    assert populated_mount.exists()
    assert (populated_mount / "models").exists()


def test_destroy_default_requires_confirmation(populated_mount):
    """Without --yes, abort if the user doesn't type 'y'."""
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "destroy"], input="n\n"
    )
    assert result.exit_code != 0
    assert (populated_mount / "grimoire.db").exists()


def test_destroy_default_y_at_prompt_proceeds(populated_mount):
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "destroy"], input="y\n"
    )
    assert result.exit_code == 0
    assert not (populated_mount / "grimoire.db").exists()


def test_destroy_default_missing_is_noop(tmp_path, _shared_models_cache):
    """Destroying a default DB that doesn't exist exits cleanly with a message."""
    mount = _new_mount(tmp_path, _shared_models_cache, name="empty-mount")
    result = runner.invoke(app, ["--mount", str(mount), "destroy", "--yes"])
    assert result.exit_code == 0
    assert "Nothing to destroy" in result.output


def test_destroy_named_db_removes_subdir_and_manifest_entry(
    populated_mount, _shared_models_cache
):
    """`destroy NAME` removes the named DB and its manifest entry."""
    pytest.importorskip("fastembed")
    # Create a named DB alongside the default.
    init_named = runner.invoke(app, ["--mount", str(populated_mount), "create", "side"])
    assert init_named.exit_code == 0, init_named.output
    assert (populated_mount / "side" / "grimoire.db").exists()

    result = runner.invoke(
        app, ["--mount", str(populated_mount), "destroy", "side", "--yes"]
    )
    assert result.exit_code == 0
    assert not (populated_mount / "side").exists()
    # Default DB unaffected.
    assert (populated_mount / "grimoire.db").exists()


def test_destroy_unknown_name_is_noop(populated_mount):
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "destroy", "ghost", "--yes"]
    )
    assert result.exit_code == 0
    assert "Nothing to destroy" in result.output


# ---------- mount destroy (full wipe) ----------


def test_mount_destroy_wipes_entire_mount(populated_mount):
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "mount", "destroy", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert not populated_mount.exists()


def test_mount_destroy_requires_confirmation(populated_mount):
    result = runner.invoke(
        app,
        ["--mount", str(populated_mount), "mount", "destroy"],
        input="n\n",
    )
    assert result.exit_code != 0
    assert populated_mount.exists()


def test_mount_destroy_missing_mount_is_noop(tmp_path):
    nope = tmp_path / "nope"
    result = runner.invoke(app, ["--mount", str(nope), "mount", "destroy", "--yes"])
    assert result.exit_code == 0
    assert "Nothing to destroy" in result.output


# ---------- ls ----------


def test_ls_lists_default_when_only_default_present(populated_mount):
    result = runner.invoke(app, ["--mount", str(populated_mount), "ls"])
    assert result.exit_code == 0, result.output
    rows = _json_lines(result.output)
    assert len(rows) == 1
    assert rows[0]["name"] is None
    assert rows[0]["is_default"] is True
    assert rows[0]["entry_count"] == 3


def test_ls_lists_default_and_named(populated_mount, _shared_models_cache):
    pytest.importorskip("fastembed")
    runner.invoke(app, ["--mount", str(populated_mount), "create", "alpha"])
    runner.invoke(app, ["--mount", str(populated_mount), "create", "beta"])
    result = runner.invoke(app, ["--mount", str(populated_mount), "ls"])
    assert result.exit_code == 0, result.output
    rows = _json_lines(result.output)
    names = [r["name"] for r in rows]
    # Default first, then named in alphabetical order.
    assert names == [None, "alpha", "beta"]
    assert rows[0]["is_default"] is True
    assert all(r["is_default"] is False for r in rows[1:])


def test_ls_empty_mount_emits_nothing(tmp_path):
    result = runner.invoke(app, ["--mount", str(tmp_path / "fresh"), "ls"])
    assert result.exit_code == 0
    assert _json_lines(result.output) == []


# ---------- named-DB workflow (--db) ----------


def test_named_db_init_create_search_round_trip(populated_mount, _shared_models_cache):
    """A named DB lives alongside the default and is independently addressable."""
    pytest.importorskip("fastembed")
    init_named = runner.invoke(
        app, ["--mount", str(populated_mount), "create", "spells"]
    )
    assert init_named.exit_code == 0, init_named.output

    add_named = runner.invoke(
        app,
        [
            "--mount",
            str(populated_mount),
            "entry",
            "add",
            "alohomora opens locks",
            "--db",
            "spells",
        ],
    )
    assert add_named.exit_code == 0, add_named.output

    # Default DB is unchanged — still 3 entries from the populated_mount fixture.
    default_q = runner.invoke(app, ["--mount", str(populated_mount), "query"])
    assert len(_json_lines(default_q.output)) == 3

    # Named DB has the one entry we added.
    named_q = runner.invoke(
        app, ["--mount", str(populated_mount), "query", "--db", "spells"]
    )
    rows = _json_lines(named_q.output)
    assert len(rows) == 1
    assert rows[0]["content"] == "alohomora opens locks"


def test_open_unknown_named_db_errors_clearly(populated_mount):
    result = runner.invoke(
        app, ["--mount", str(populated_mount), "query", "--db", "nope"]
    )
    assert result.exit_code != 0
    assert "nope" in result.output

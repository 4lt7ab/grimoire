import json

import pytest
from grimoire_cli.cli import app
from grimoire_cli.mount import DB_FILENAME, ENV_VAR, MODELS_DIRNAME, REGISTRY_FILENAME
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def mounted(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    runner.invoke(app, ["mount", "create"])
    return tmp_path


def _add(*flags: str, db: str | None = None) -> str:
    """Add an entry via `entry add`, return its uniq_id."""
    cmd = ["entry", "add", *(["-d", db] if db else []), *flags]
    result = runner.invoke(app, cmd)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["uniq_id"]


# ----------------------------------------------------------------------
# mount admin
# ----------------------------------------------------------------------


def test_mount_create_initializes(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["mount", "create"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / MODELS_DIRNAME).is_dir()
    assert (tmp_path / REGISTRY_FILENAME).is_file()
    assert (tmp_path / DB_FILENAME).is_file()


def test_mount_flag_overrides_env(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "decoy"))
    target = tmp_path / "target"
    result = runner.invoke(app, ["--mount", str(target), "mount", "create"])
    assert result.exit_code == 0, result.output
    assert (target / DB_FILENAME).is_file()


def test_mount_create_is_idempotent(mounted):
    second = runner.invoke(app, ["mount", "create"])
    assert second.exit_code == 0, second.output


def test_mount_add_named(mounted):
    result = runner.invoke(app, ["mount", "add", "spellbook"])
    assert result.exit_code == 0, result.output
    assert (mounted / "spellbook" / DB_FILENAME).is_file()


def test_mount_add_lowercases_name(mounted):
    result = runner.invoke(app, ["mount", "add", "Spellbook"])
    assert json.loads(result.output)["db"] == "spellbook"


def test_mount_add_rejects_dunder(mounted):
    result = runner.invoke(app, ["mount", "add", "__models__"])
    assert result.exit_code != 0
    assert "reserved" in result.output


def test_mount_ls_lists_default_and_named(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    result = runner.invoke(app, ["mount", "ls"])
    assert result.exit_code == 0
    dbs = json.loads(result.output)
    assert [d["db"] for d in dbs] == [None, "spellbook"]


def test_mount_remove_requires_yes(mounted):
    runner.invoke(app, ["mount", "add", "scratch"])
    result = runner.invoke(app, ["mount", "remove", "scratch"])
    assert result.exit_code != 0


def test_mount_destroy_wipes(mounted):
    result = runner.invoke(app, ["mount", "destroy", "--yes"])
    assert result.exit_code == 0, result.output
    assert not mounted.exists()


# ----------------------------------------------------------------------
# entry add
# ----------------------------------------------------------------------


def test_entry_add_no_flags_is_data_null(mounted):
    result = runner.invoke(app, ["entry", "add"])
    assert result.exit_code == 0, result.output
    entry = json.loads(result.output)
    assert entry["uniq_id"] is not None
    assert entry["data"] is None


def test_entry_add_with_data(mounted):
    result = runner.invoke(app, ["entry", "add", "--data", '{"author": "merlin"}'])
    assert result.exit_code == 0
    entry = json.loads(result.output)
    assert entry["data"] == {"author": "merlin"}


def test_entry_add_rejects_invalid_data_json(mounted):
    result = runner.invoke(app, ["entry", "add", "--data", "not-json"])
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output


def test_entry_add_with_all_index_kwargs(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--data",
            '{"k": "v"}',
            "--ref",
            "book-1",
            "--ord-4",
            "novel",
            "--ord-5",
            "fantasy",
            "--ord-1",
            "1954",
            "--ord-2",
            "2.5",
            "--match",
            "fellowship ring",
            "--search",
            "an epic quest",
        ],
    )
    assert add.exit_code == 0, add.output
    uniq_id = json.loads(add.output)["uniq_id"]

    pairs = json.loads(runner.invoke(app, ["fetch", "book-1"]).output)
    assert len(pairs) == 1
    assert pairs[0]["entry"]["uniq_id"] == uniq_id
    assert pairs[0]["index"]["ordinal_4"] == "novel"
    assert pairs[0]["index"]["ordinal_5"] == "fantasy"
    assert pairs[0]["index"]["ordinal_1"] == 1954
    assert pairs[0]["index"]["ordinal_2"] == 2.5


def test_entry_add_with_named_db(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    uniq_id = _add("--data", '{"k":"v"}', db="spellbook")
    result = runner.invoke(app, ["entry", "get", uniq_id, "-d", "spellbook"])
    assert json.loads(result.output)[0]["uniq_id"] == uniq_id


def test_entry_add_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["entry", "add"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


# ----------------------------------------------------------------------
# entry get
# ----------------------------------------------------------------------


def test_entry_get_single(mounted):
    uniq_id = _add("--data", '{"a": 1}')
    result = runner.invoke(app, ["entry", "get", uniq_id])
    assert result.exit_code == 0
    entries = json.loads(result.output)
    assert len(entries) == 1
    assert entries[0]["uniq_id"] == uniq_id


def test_entry_get_multiple(mounted):
    a = _add()
    b = _add()
    result = runner.invoke(app, ["entry", "get", a, b])
    assert result.exit_code == 0
    entries = json.loads(result.output)
    assert {e["uniq_id"] for e in entries} == {a, b}


def test_entry_get_missing_id_returns_empty(mounted):
    result = runner.invoke(app, ["entry", "get", "01MISSINGMISSINGMISSINGMI"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


# ----------------------------------------------------------------------
# entry update
# ----------------------------------------------------------------------


def test_entry_update_data(mounted):
    uniq_id = _add("--data", '{"v": 1}')
    result = runner.invoke(app, ["entry", "update", uniq_id, "--data", '{"v": 2}'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"] == {"v": 2}


def test_entry_update_no_data_leaves_data_alone(mounted):
    uniq_id = _add("--data", '{"v": 1}')
    runner.invoke(app, ["entry", "update", uniq_id, "--ref", "newref"])
    result = runner.invoke(app, ["entry", "get", uniq_id])
    assert json.loads(result.output)[0]["data"] == {"v": 1}


def test_entry_update_index_kwargs_apply(mounted):
    uniq_id = _add("--data", '{"v": 1}')
    runner.invoke(app, ["entry", "update", uniq_id, "--ref", "newref", "--match", "kw"])
    pairs = json.loads(runner.invoke(app, ["fetch", "newref"]).output)
    assert pairs[0]["entry"]["uniq_id"] == uniq_id


def test_entry_update_unknown_id_errors(mounted):
    result = runner.invoke(
        app, ["entry", "update", "01MISSINGMISSINGMISSINGMI", "--data", "{}"]
    )
    assert result.exit_code != 0
    assert "No entry" in result.output


def test_entry_update_invalid_data_json(mounted):
    uniq_id = _add()
    result = runner.invoke(app, ["entry", "update", uniq_id, "--data", "not-json"])
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output


# ----------------------------------------------------------------------
# entry delete
# ----------------------------------------------------------------------


def test_entry_remove_removes(mounted):
    uniq_id = _add()
    result = runner.invoke(app, ["entry", "remove", uniq_id, "--yes"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"uniq_id": uniq_id, "removed": True}


def test_entry_remove_missing_id_is_soft(mounted):
    result = runner.invoke(
        app, ["entry", "remove", "01MISSINGMISSINGMISSINGMI", "--yes"]
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["removed"] is False


def test_entry_remove_requires_yes(mounted):
    uniq_id = _add()
    result = runner.invoke(app, ["entry", "remove", uniq_id])
    assert result.exit_code != 0
    assert "--yes" in result.output


# ----------------------------------------------------------------------
# info
# ----------------------------------------------------------------------


def test_info_empty_db(mounted):
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    info = json.loads(result.output)
    assert info["db"] is None
    assert info["entry_count"] == 0
    assert info["entry_idx_count"] == 0
    assert info["entry_fts_count"] == 0
    assert info["entry_vec_count"] == 0
    assert info["size_bytes"] > 0


def test_info_counts_per_table(mounted):
    _add("--ref", "r1", "--match", "text")
    _add("--search", "text")
    _add()  # entry only
    result = runner.invoke(app, ["info"])
    info = json.loads(result.output)
    assert info["entry_count"] == 3
    assert info["entry_idx_count"] == 1
    assert info["entry_fts_count"] == 1
    assert info["entry_vec_count"] == 1


def test_info_targets_named_db(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    _add(db="spellbook")
    info = json.loads(runner.invoke(app, ["info", "-d", "spellbook"]).output)
    assert info["db"] == "spellbook"
    assert info["entry_count"] == 1


# ----------------------------------------------------------------------
# query
# ----------------------------------------------------------------------


def test_query_returns_pairs(mounted):
    uniq_id = _add("--ref", "r1", "--ord-4", "x")
    result = runner.invoke(app, ["query"])
    pairs = json.loads(result.output)
    assert len(pairs) == 1
    assert pairs[0]["entry"]["uniq_id"] == uniq_id
    assert pairs[0]["index"]["ordinal_4"] == "x"


def test_query_filters_by_equals(mounted):
    _add("--ref", "a", "--ord-4", "x")
    _add("--ref", "b", "--ord-4", "y")
    result = runner.invoke(app, ["query", "--equals", "ordinal_4=x"])
    pairs = json.loads(result.output)
    assert [p["index"]["uniq_ref"] for p in pairs] == ["a"]


def test_query_filters_by_ordinal_range(mounted):
    _add("--ord-1", "1.0")
    in_window = _add("--ord-1", "5.0")
    _add("--ord-1", "10.0")
    result = runner.invoke(
        app, ["query", "--gte", "ordinal_1=2", "--lte", "ordinal_1=7"]
    )
    pairs = json.loads(result.output)
    assert [p["entry"]["uniq_id"] for p in pairs] == [in_window]


def test_query_cursor_paginates(mounted):
    ids = [_add("--ref", f"r{i}") for i in range(5)]
    page1 = json.loads(runner.invoke(app, ["query", "--limit", "2"]).output)
    page1_ids = [p["entry"]["uniq_id"] for p in page1]
    assert page1_ids == ids[:2]
    page2 = json.loads(
        runner.invoke(app, ["query", "--limit", "2", "--cursor", page1_ids[-1]]).output
    )
    assert [p["entry"]["uniq_id"] for p in page2] == ids[2:4]


def test_query_invalid_filter_column(mounted):
    result = runner.invoke(app, ["query", "--equals", "bogus=x"])
    assert result.exit_code != 0


def test_query_excludes_entries_without_idx(mounted):
    _add()  # no idx
    _add("--ref", "r")
    pairs = json.loads(runner.invoke(app, ["query"]).output)
    assert len(pairs) == 1
    assert pairs[0]["index"]["uniq_ref"] == "r"


# ----------------------------------------------------------------------
# fetch
# ----------------------------------------------------------------------


def test_fetch_by_uniq_ref(mounted):
    _add("--ref", "book-1", "--data", '{"k": "v"}')
    pairs = json.loads(runner.invoke(app, ["fetch", "book-1"]).output)
    assert len(pairs) == 1
    assert pairs[0]["entry"]["data"] == {"k": "v"}
    assert pairs[0]["index"]["uniq_ref"] == "book-1"


def test_fetch_multiple_refs(mounted):
    _add("--ref", "a")
    _add("--ref", "b")
    pairs = json.loads(runner.invoke(app, ["fetch", "a", "b"]).output)
    assert {p["index"]["uniq_ref"] for p in pairs} == {"a", "b"}


def test_fetch_unknown_ref_returns_empty(mounted):
    pairs = json.loads(runner.invoke(app, ["fetch", "ghost"]).output)
    assert pairs == []


# ----------------------------------------------------------------------
# match
# ----------------------------------------------------------------------


def test_match_returns_score(mounted):
    uniq_id = _add("--match", "phoenix arcane ember")
    pairs = json.loads(runner.invoke(app, ["match", "phoenix"]).output)
    assert len(pairs) == 1
    assert pairs[0]["entry"]["uniq_id"] == uniq_id
    assert pairs[0]["score"] >= 0


def test_match_tokenizer_handles_apostrophes(mounted):
    uniq_id = _add("--match", "happening mate")
    pairs = json.loads(runner.invoke(app, ["match", "what's going on mate?"]).output)
    assert any(p["entry"]["uniq_id"] == uniq_id for p in pairs)


def test_match_all_punctuation_returns_empty(mounted):
    _add("--match", "anything")
    result = runner.invoke(app, ["match", "?!?"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_match_filters_by_idx(mounted):
    a = _add("--match", "phoenix", "--ord-4", "alpha")
    _add("--match", "phoenix", "--ord-4", "beta")
    pairs = json.loads(
        runner.invoke(app, ["match", "phoenix", "--equals", "ordinal_4=alpha"]).output
    )
    assert [p["entry"]["uniq_id"] for p in pairs] == [a]


def test_match_default_limit(mounted):
    for _ in range(15):
        _add("--match", "phoenix")
    pairs = json.loads(runner.invoke(app, ["match", "phoenix"]).output)
    assert len(pairs) == 10


def test_match_respects_limit(mounted):
    for _ in range(5):
        _add("--match", "phoenix")
    pairs = json.loads(runner.invoke(app, ["match", "phoenix", "--limit", "2"]).output)
    assert len(pairs) == 2


def test_match_rejects_limit_zero(mounted):
    result = runner.invoke(app, ["match", "phoenix", "--limit", "0"])
    assert result.exit_code != 0


# ----------------------------------------------------------------------
# search
# ----------------------------------------------------------------------


def test_search_returns_distance(mounted):
    uniq_id = _add("--search", "phoenix")
    pairs = json.loads(runner.invoke(app, ["search", "phoenix"]).output)
    assert any(p["entry"]["uniq_id"] == uniq_id for p in pairs)
    assert all("distance" in p for p in pairs)


def test_search_orders_distance_asc(mounted):
    for _ in range(3):
        _add("--search", "text")
    pairs = json.loads(runner.invoke(app, ["search", "anything"]).output)
    distances = [p["distance"] for p in pairs]
    assert distances == sorted(distances)

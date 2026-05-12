import json

import pytest
from typer.testing import CliRunner

from grimoire_cli.cli import app
from grimoire_cli.mount import DB_FILENAME, ENV_VAR, MODELS_DIRNAME, REGISTRY_FILENAME

runner = CliRunner()


@pytest.fixture
def mounted(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    runner.invoke(app, ["mount", "create"])
    return tmp_path


def _add(
    *flags: str,
    embed: str | None = None,
    keyword: str | None = None,
    partition: str | None = None,
    name: str | None = None,
) -> str:
    """Add an entry; optionally embed it and/or index its keyword text.

    `name` selects the DB for every sub-call (entry add, embed, keyword).
    """
    name_flag = ["-n", name] if name else []
    add_result = runner.invoke(app, ["entry", "add", *name_flag, *flags])
    entry_id = json.loads(add_result.output)["id"]
    if embed is not None:
        cmd = ["index", "semantic", entry_id, *name_flag, "--text", embed]
        if partition is not None:
            cmd += ["--partition", partition]
        runner.invoke(app, cmd)
    if keyword is not None:
        runner.invoke(
            app, ["index", "keyword", entry_id, *name_flag, "--text", keyword]
        )
    return entry_id


def test_mount_create_initializes_layout(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["mount", "create"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / MODELS_DIRNAME).is_dir()
    assert (tmp_path / REGISTRY_FILENAME).is_file()
    assert (tmp_path / DB_FILENAME).is_file()


def test_mount_create_prints_resolved_path(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["mount", "create"])
    assert result.exit_code == 0
    assert str(tmp_path.resolve()) in result.output


def test_mount_create_is_idempotent(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    first = runner.invoke(app, ["mount", "create"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["mount", "create"])
    assert second.exit_code == 0, second.output


def test_mount_create_creates_parents(tmp_path, monkeypatch, patched_embedder):
    target = tmp_path / "nested" / "path" / "mount"
    monkeypatch.setenv(ENV_VAR, str(target))
    result = runner.invoke(app, ["mount", "create"])
    assert result.exit_code == 0, result.output
    assert (target / DB_FILENAME).is_file()


def test_entry_add_populates_every_field(mounted):
    payload = {"author": "merlin", "tags": ["arcane", "ambient"]}
    result = runner.invoke(
        app,
        [
            "entry", "add",
            "--group-key", "notes",
            "--group-ref", "note-001",
            "--context", "from chapter 3",
            "--payload", json.dumps(payload),
        ],
    )
    assert result.exit_code == 0, result.output

    entry = json.loads(result.output)
    assert entry["id"] is not None
    assert entry["group_key"] == "notes"
    assert entry["group_ref"] == "note-001"
    assert entry["context"] == "from chapter 3"
    assert entry["payload"] == payload


def test_entry_add_with_no_flags_is_a_metadata_only_entry(mounted):
    result = runner.invoke(app, ["entry", "add"])
    assert result.exit_code == 0, result.output

    entry = json.loads(result.output)
    assert entry["id"] is not None
    for field in (
        "group_key",
        "group_ref",
        "context",
        "payload",
    ):
        assert entry[field] is None, field


def test_entry_add_fails_when_mount_does_not_exist(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["entry", "add"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_entry_add_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    result = runner.invoke(app, ["entry", "add", "--group-ref", "ref-1", "-n", "spellbook"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["group_ref"] == "ref-1"


def test_entry_add_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["entry", "add", "-n", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_entry_add_rejects_invalid_payload_json(mounted):
    result = runner.invoke(app, ["entry", "add", "--payload", "not-json"])
    assert result.exit_code != 0
    assert "Invalid JSON payload" in result.output


def test_mount_add_lowercases_name(mounted):
    result = runner.invoke(app, ["mount", "add", "Spellbook"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["name"] == "spellbook"
    assert (mounted / "spellbook" / DB_FILENAME).is_file()


def test_mount_add_rejects_invalid_name(mounted):
    result = runner.invoke(app, ["mount", "add", "spell book"])
    assert result.exit_code != 0
    assert "Invalid database name" in result.output


def test_mount_add_rejects_slash_in_name(mounted):
    result = runner.invoke(app, ["mount", "add", "subdir/db"])
    assert result.exit_code != 0
    assert "Invalid database name" in result.output


def test_named_db_lookup_is_case_insensitive(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    result = runner.invoke(app, ["entry", "add", "-n", "SPELLBOOK"])
    assert result.exit_code == 0, result.output


def test_mount_remove_canonical_name_in_output(mounted):
    runner.invoke(app, ["mount", "add", "Spellbook"])
    result = runner.invoke(app, ["mount", "remove", "SPELLBOOK", "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"name": "spellbook", "removed": True}


def test_index_keyword_unknown_id_is_clean_error(mounted):
    result = runner.invoke(
        app,
        ["index", "keyword", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--text", "ghost"],
    )
    assert result.exit_code != 0
    assert "No entry with id" in result.output
    assert "Traceback" not in result.output


def test_index_semantic_unknown_id_is_clean_error(mounted):
    result = runner.invoke(
        app,
        ["index", "semantic", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--text", "ghost"],
    )
    assert result.exit_code != 0
    assert "No entry with id" in result.output
    assert "Traceback" not in result.output


def test_entry_update_rejects_invalid_payload_json(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app, ["entry", "update", entry_id, "--payload", "not-json"]
    )
    assert result.exit_code != 0
    assert "Invalid JSON payload" in result.output


def test_entry_update_changes_only_specified_fields(mounted):
    add = runner.invoke(
        app,
        [
            "entry", "add",
            "--group-key", "tale",
            "--group-ref", "ref-1",
            "--context", "ch.3",
            "--payload", json.dumps({"author": "merlin"}),
        ],
    )
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "--payload", json.dumps({"author": "morgana"})],
    )
    assert result.exit_code == 0, result.output

    out = json.loads(result.output)
    assert out["payload"] == {"author": "morgana"}
    # unchanged
    assert out["group_key"] == "tale"
    assert out["group_ref"] == "ref-1"
    assert out["context"] == "ch.3"


def test_entry_update_sets_group_key(mounted):
    add = runner.invoke(app, ["entry", "add", "--group-key", "tale"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "--group-key", "note"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["group_key"] == "note"


def test_entry_update_sets_group_ref(mounted):
    add = runner.invoke(app, ["entry", "add", "--group-ref", "ref-old"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "--group-ref", "ref-new"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["group_ref"] == "ref-new"


def test_entry_update_sets_context(mounted):
    add = runner.invoke(app, ["entry", "add", "--context", "first"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "--context", "second"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["context"] == "second"


def test_entry_update_fails_for_unknown_id(mounted):
    result = runner.invoke(
        app,
        ["entry", "update", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--payload", "{}"],
    )
    assert result.exit_code != 0
    assert "No entry" in result.output


def test_entry_update_targets_named_db(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    add = runner.invoke(app, ["entry", "add", "-n", "spellbook"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "-n", "spellbook", "--payload", json.dumps({"x": 1})],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["payload"] == {"x": 1}


def test_entry_update_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(
        app,
        ["entry", "update", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--payload", "{}"],
    )
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_entry_update_fails_for_unknown_named_db(mounted):
    result = runner.invoke(
        app,
        ["entry", "update", "01HXXXXXXXXXXXXXXXXXXXXXXX", "-n", "nonesuch", "--payload", "{}"],
    )
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_fetch_no_filters_returns_all(mounted):
    runner.invoke(app, ["entry", "add", "--group-ref", "first"])
    runner.invoke(app, ["entry", "add", "--group-ref", "second"])

    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert {e["group_ref"] for e in entries} == {"first", "second"}


def test_fetch_filters_by_id(mounted):
    a = json.loads(runner.invoke(app, ["entry", "add"]).output)
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--id", a["id"]])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert [e["id"] for e in entries] == [a["id"]]


def test_fetch_filters_by_group_key(mounted):
    runner.invoke(app, ["entry", "add", "--group-key", "tale", "--group-ref", "tale-one"])
    runner.invoke(app, ["entry", "add", "--group-key", "note", "--group-ref", "note-one"])

    result = runner.invoke(app, ["fetch", "--group-key", "tale"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert [e["group_ref"] for e in entries] == ["tale-one"]


def test_fetch_repeatable_filter(mounted):
    a = json.loads(runner.invoke(app, ["entry", "add"]).output)
    b = json.loads(runner.invoke(app, ["entry", "add"]).output)
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--id", a["id"], "--id", b["id"]])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert {e["id"] for e in entries} == {a["id"], b["id"]}


def test_fetch_respects_explicit_limit(mounted):
    for _ in range(5):
        runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 2


def test_fetch_default_limit_caps_at_100(mounted):
    for _ in range(101):
        runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 100


def test_fetch_empty_when_no_match(mounted):
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--id", "01HXXXXXXXXXXXXXXXXXXXXXXX"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_fetch_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    runner.invoke(app, ["entry", "add", "--group-ref", "named-hello", "-n", "spellbook"])

    result = runner.invoke(app, ["fetch", "-n", "spellbook"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert [e["group_ref"] for e in entries] == ["named-hello"]


def test_fetch_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_fetch_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["fetch", "-n", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_entry_delete_removes_existing(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "delete", entry_id, "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": entry_id, "deleted": True}

    fetched = runner.invoke(app, ["fetch", "--id", entry_id])
    assert json.loads(fetched.output) == []


def test_entry_delete_missing_id_is_soft(mounted):
    result = runner.invoke(app, ["entry", "delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": "01HXXXXXXXXXXXXXXXXXXXXXXX", "deleted": False}


def test_entry_delete_requires_yes(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "delete", entry_id])
    assert result.exit_code != 0
    assert "--yes" in result.output

    fetched = runner.invoke(app, ["fetch", "--id", entry_id])
    assert len(json.loads(fetched.output)) == 1


def test_entry_delete_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    add = runner.invoke(app, ["entry", "add", "-n", "spellbook"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "delete", entry_id, "-n", "spellbook", "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": entry_id, "deleted": True}


def test_entry_delete_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["entry", "delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--yes"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_entry_delete_fails_for_unknown_named_db(mounted):
    result = runner.invoke(
        app,
        ["entry", "delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "-n", "nonesuch", "--yes"],
    )
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_mount_ls_lists_default_only(mounted):
    result = runner.invoke(app, ["mount", "ls"])
    assert result.exit_code == 0, result.output
    dbs = json.loads(result.output)
    assert dbs == [{"name": None, "path": str(mounted / DB_FILENAME)}]


def test_mount_ls_includes_named_dbs(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    runner.invoke(app, ["mount", "add", "atlas"])

    result = runner.invoke(app, ["mount", "ls"])
    assert result.exit_code == 0, result.output
    names = [d["name"] for d in json.loads(result.output)]
    assert names == [None, "atlas", "spellbook"]


def test_mount_ls_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["mount", "ls"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_mount_remove_deletes_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "scratch"])
    assert create.exit_code == 0, create.output

    remove = runner.invoke(app, ["mount", "remove", "scratch", "--yes"])
    assert remove.exit_code == 0, remove.output
    assert not (mounted / "scratch").exists()


def test_mount_remove_requires_yes(mounted):
    runner.invoke(app, ["mount", "add", "scratch"])
    result = runner.invoke(app, ["mount", "remove", "scratch"])
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_mount_destroy_wipes_mount(mounted):
    result = runner.invoke(app, ["mount", "destroy", "--yes"])
    assert result.exit_code == 0, result.output
    assert not mounted.exists()


def test_info_reports_empty_default_db(mounted):
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0, result.output

    info = json.loads(result.output)
    assert info["name"] is None
    assert info["path"] == str(mounted / DB_FILENAME)
    assert info["size_bytes"] > 0
    assert info["size"].endswith(("B", "KB", "MB", "GB", "TB"))
    assert info["model"] == "noop"
    assert info["dimension"] == 1
    assert info["schema_version"] == 1
    assert info["entry_count"] == 0
    assert info["group_counts"] == {}
    assert info["partition_counts"] == {}


def test_info_counts_entries_and_groups(mounted):
    runner.invoke(app, ["entry", "add", "--group-key", "tale"])
    runner.invoke(app, ["entry", "add", "--group-key", "tale"])
    runner.invoke(app, ["entry", "add", "--group-key", "note"])
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0, result.output

    info = json.loads(result.output)
    assert info["entry_count"] == 4
    assert info["group_counts"] == {"tale": 2, "note": 1, "null": 1}


def test_info_counts_partitions(mounted):
    _add(embed="a", partition="alpha")
    _add(embed="b", partition="alpha")
    _add(embed="c", partition="beta")
    _add(embed="d")  # NULL partition
    _add()  # unembedded — no vec row, not in any partition

    result = runner.invoke(app, ["info"])
    info = json.loads(result.output)
    assert info["partition_counts"] == {"alpha": 2, "beta": 1, "null": 1}


def test_info_targets_named_db(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    runner.invoke(app, ["entry", "add", "-n", "spellbook"])

    result = runner.invoke(app, ["info", "-n", "spellbook"])
    assert result.exit_code == 0, result.output

    info = json.loads(result.output)
    assert info["name"] == "spellbook"
    assert info["path"] == str(mounted / "spellbook" / DB_FILENAME)
    assert info["entry_count"] == 1


def test_info_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["info"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_info_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["info", "-n", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_embed_writes_vec_row(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["index", "semantic", entry_id, "--text", "hello"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["id"] == entry_id

    sem = runner.invoke(app, ["search", "hello"])
    sem_hits = json.loads(sem.output)["semantic"]
    assert any(h["entry"]["id"] == entry_id for h in sem_hits)
    assert any(h["semantic_text"] == "hello" for h in sem_hits)


def test_embed_into_named_partition(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["index", "semantic", entry_id, "--text", "hello", "--partition", "alpha"],
    )
    assert result.exit_code == 0, result.output

    in_partition = runner.invoke(app, ["search", "hello", "--partition", "alpha"])
    out = json.loads(in_partition.output)
    assert [h["entry"]["id"] for h in out["semantic"]] == [entry_id]

    in_null = runner.invoke(app, ["search", "hello"])
    out_null = json.loads(in_null.output)
    assert out_null["semantic"] == []


def test_embed_replaces_existing_vec_row(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    runner.invoke(app, ["index", "semantic", entry_id, "--text", "first"])
    runner.invoke(app, ["index", "semantic", entry_id, "--text", "second"])

    sem = runner.invoke(app, ["search", "anything"])
    hits = json.loads(sem.output)["semantic"]
    texts = [h["semantic_text"] for h in hits if h["entry"]["id"] == entry_id]
    assert texts == ["second"]


def test_embed_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(
        app,
        ["index", "semantic", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--text", "anything"],
    )
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_embed_fails_for_unknown_id(mounted):
    result = runner.invoke(
        app,
        ["index", "semantic", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--text", "anything"],
    )
    assert result.exit_code != 0


def test_keyword_indexes_for_match(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app, ["index", "keyword", entry_id, "--text", "the moon glows"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == entry_id

    kw = runner.invoke(app, ["search", "moon"])
    hits = json.loads(kw.output)["keyword"]
    assert any(h["entry"]["id"] == entry_id and h["keyword_text"] == "the moon glows" for h in hits)


def test_keyword_replaces_text_on_reindex(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    runner.invoke(app, ["index", "keyword", entry_id, "--text", "moon"])
    runner.invoke(app, ["index", "keyword", entry_id, "--text", "stars"])

    moon = runner.invoke(app, ["search", "moon"])
    stars = runner.invoke(app, ["search", "stars"])
    assert json.loads(moon.output)["keyword"] == []
    assert any(h["entry"]["id"] == entry_id for h in json.loads(stars.output)["keyword"])


def test_keyword_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(
        app,
        ["index", "keyword", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--text", "anything"],
    )
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_keyword_fails_for_unknown_id(mounted):
    result = runner.invoke(
        app,
        ["index", "keyword", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--text", "anything"],
    )
    assert result.exit_code != 0


def test_keyword_stores_threshold_rank(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    runner.invoke(
        app,
        ["index", "keyword", entry_id, "--text", "hello", "--threshold-rank", "0.25"],
    )

    out = json.loads(runner.invoke(app, ["search", "hello"]).output)
    assert [h["threshold_rank"] for h in out["keyword"]] == [0.25]


def test_keyword_delete_removes_fts_row(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]
    runner.invoke(app, ["index", "keyword", entry_id, "--text", "hello"])

    result = runner.invoke(app, ["index", "keyword", entry_id, "--delete"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": entry_id, "deleted": True}

    out = json.loads(runner.invoke(app, ["search", "hello"]).output)
    assert out["keyword"] == []


def test_keyword_delete_missing_id_is_soft(mounted):
    result = runner.invoke(
        app, ["index", "keyword", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--delete"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "id": "01HXXXXXXXXXXXXXXXXXXXXXXX",
        "deleted": False,
    }


def test_keyword_requires_text_or_delete(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["index", "keyword", entry_id])
    assert result.exit_code != 0
    assert "--text" in result.output and "--delete" in result.output


def test_keyword_rejects_text_with_delete(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app, ["index", "keyword", entry_id, "--text", "hello", "--delete"]
    )
    assert result.exit_code != 0
    assert "--delete" in result.output


def test_keyword_delete_leaves_entry_intact(mounted):
    add = runner.invoke(app, ["entry", "add", "--group-key", "tale"])
    entry_id = json.loads(add.output)["id"]
    runner.invoke(app, ["index", "keyword", entry_id, "--text", "hello"])

    runner.invoke(app, ["index", "keyword", entry_id, "--delete"])

    fetched = json.loads(runner.invoke(app, ["fetch", "--id", entry_id]).output)
    assert len(fetched) == 1
    assert fetched[0]["group_key"] == "tale"


def test_embed_delete_removes_vec_row(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]
    runner.invoke(app, ["index", "semantic", entry_id, "--text", "hello"])

    result = runner.invoke(app, ["index", "semantic", entry_id, "--delete"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": entry_id, "deleted": True}

    out = json.loads(runner.invoke(app, ["search", "hello"]).output)
    assert out["semantic"] == []


def test_embed_delete_missing_id_is_soft(mounted):
    result = runner.invoke(
        app, ["index", "semantic", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--delete"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "id": "01HXXXXXXXXXXXXXXXXXXXXXXX",
        "deleted": False,
    }


def test_semantic_requires_text_or_delete(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["index", "semantic", entry_id])
    assert result.exit_code != 0
    assert "--text" in result.output and "--delete" in result.output


def test_semantic_rejects_partition_with_delete(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["index", "semantic", entry_id, "--partition", "alpha", "--delete"],
    )
    assert result.exit_code != 0
    assert "--delete" in result.output


def test_embed_stores_threshold_distance(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    runner.invoke(
        app,
        [
            "index", "semantic", entry_id,
            "--text", "hello", "--threshold-distance", "0.75",
        ],
    )

    out = json.loads(runner.invoke(app, ["search", "hello"]).output)
    assert [h["threshold_distance"] for h in out["semantic"]] == [0.75]


def test_search_runs_both_modes(mounted):
    _add(embed="the moon glows", keyword="moon glow")

    result = runner.invoke(app, ["search", "moon"])
    assert result.exit_code == 0, result.output

    out = json.loads(result.output)
    assert set(out.keys()) == {"keyword", "semantic"}
    assert any(h["keyword_text"] == "moon glow" for h in out["keyword"])
    assert any(h["semantic_text"] == "the moon glows" for h in out["semantic"])


def test_search_keyword_rank_is_low_is_better(mounted):
    _add(keyword="match match match")
    _add(keyword="match")

    result = runner.invoke(app, ["search", "match"])
    out = json.loads(result.output)
    ranks = [h["rank"] for h in out["keyword"]]
    assert ranks == sorted(ranks)
    assert all(r <= 0 for r in ranks)


def test_search_semantic_distance_is_low_is_better(mounted):
    _add(embed="alpha")
    _add(embed="beta")

    result = runner.invoke(app, ["search", "anything"])
    out = json.loads(result.output)
    distances = [h["distance"] for h in out["semantic"]]
    assert len(distances) == 2
    assert distances == sorted(distances)


def test_search_default_limit_is_10(mounted):
    for i in range(15):
        _add(embed=f"entry {i}", keyword=f"target {i}")

    result = runner.invoke(app, ["search", "target"])
    out = json.loads(result.output)
    assert len(out["keyword"]) == 10
    assert len(out["semantic"]) == 10


def test_search_respects_explicit_limit(mounted):
    for i in range(5):
        _add(embed=f"entry {i}", keyword=f"target {i}")

    result = runner.invoke(app, ["search", "target", "--limit", "2"])
    out = json.loads(result.output)
    assert len(out["keyword"]) == 2
    assert len(out["semantic"]) == 2


def test_search_group_key_filters_keyword_only(mounted):
    _add("--group-key", "tale", keyword="target")
    _add("--group-key", "note", keyword="target")

    result = runner.invoke(app, ["search", "target", "--group-key", "tale"])
    out = json.loads(result.output)
    assert all(h["entry"]["group_key"] == "tale" for h in out["keyword"])
    assert len(out["keyword"]) >= 1


def test_search_partition_filters_semantic_only(mounted):
    _add(keyword="target", embed="tale-a", partition="tale")
    _add(keyword="target", embed="note-b", partition="note")

    result = runner.invoke(app, ["search", "target", "--partition", "tale"])
    out = json.loads(result.output)
    assert all(h["semantic_text"] == "tale-a" for h in out["semantic"])
    assert len(out["semantic"]) == 1


def test_search_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    _add(name="spellbook", keyword="named")

    result = runner.invoke(app, ["search", "named", "-n", "spellbook"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["keyword_text"] == "named" for h in out["keyword"])


def test_search_accepts_apostrophes_and_punctuation(mounted):
    _add(keyword="happening mate")

    result = runner.invoke(app, ["search", "what's going on mate?"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["keyword_text"] == "happening mate" for h in out["keyword"])


def test_search_treats_operator_words_as_literals(mounted):
    _add(keyword="AND OR NOT")

    result = runner.invoke(app, ["search", "AND OR NOT"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["keyword_text"] == "AND OR NOT" for h in out["keyword"])


def test_search_all_punctuation_query_skips_keyword(mounted):
    _add(keyword="anything")

    result = runner.invoke(app, ["search", "?!?"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["keyword"] == []
    # Semantic still runs against the embedded literal query.
    assert "semantic" in out


def test_search_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["search", "anything"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_search_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["search", "anything", "-n", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output

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


def _add(
    *flags: str,
    embed: str | None = None,
    keyword: str | None = None,
    partition: str | None = None,
    db: str | None = None,
) -> str:
    """Add an entry; optionally embed it and/or index its keyword text in one call."""
    db_flag = ["-d", db] if db else []
    cmd = ["entry", "add", *db_flag, *flags]
    if keyword is not None:
        cmd += ["--keyword-text", keyword]
    if embed is not None:
        cmd += ["--semantic-text", embed]
        if partition is not None:
            cmd += ["--partition", partition]
    add_result = runner.invoke(app, cmd)
    return json.loads(add_result.output)["id"]


def test_mount_create_initializes_layout(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["mount", "create"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / MODELS_DIRNAME).is_dir()
    assert (tmp_path / REGISTRY_FILENAME).is_file()
    assert (tmp_path / DB_FILENAME).is_file()


def test_mount_flag_overrides_env(tmp_path, monkeypatch, patched_embedder):
    # Set the env to one path, point --mount at another; --mount wins.
    decoy = tmp_path / "decoy"
    target = tmp_path / "target"
    monkeypatch.setenv(ENV_VAR, str(decoy))
    result = runner.invoke(app, ["--mount", str(target), "mount", "create"])
    assert result.exit_code == 0, result.output
    assert (target / MODELS_DIRNAME).is_dir()
    assert (target / DB_FILENAME).is_file()
    assert not decoy.exists()


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
            "entry",
            "add",
            "--group-key",
            "notes",
            "--group-ref",
            "note-001",
            "--context",
            "from chapter 3",
            "--payload",
            json.dumps(payload),
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


def test_entry_add_fails_when_mount_does_not_exist(
    tmp_path, monkeypatch, patched_embedder
):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["entry", "add"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_entry_add_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    result = runner.invoke(
        app, ["entry", "add", "--group-ref", "ref-1", "-d", "spellbook"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["group_ref"] == "ref-1"


def test_entry_add_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["entry", "add", "-d", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_entry_add_rejects_invalid_payload_json(mounted):
    result = runner.invoke(app, ["entry", "add", "--payload", "not-json"])
    assert result.exit_code != 0
    assert "Invalid JSON payload" in result.output


def test_entry_add_rejects_duplicate_group_key_and_ref(mounted):
    first = runner.invoke(
        app, ["entry", "add", "--group-key", "wizard", "--group-ref", "gandalf"]
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app, ["entry", "add", "--group-key", "wizard", "--group-ref", "gandalf"]
    )
    assert second.exit_code != 0
    assert "group_key, group_ref" in second.output

    listed = runner.invoke(app, ["fetch", "--group-ref", "gandalf"])
    assert len(json.loads(listed.output)) == 1


def test_mount_add_lowercases_name(mounted):
    result = runner.invoke(app, ["mount", "add", "Spellbook"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["db"] == "spellbook"
    assert (mounted / "spellbook" / DB_FILENAME).is_file()


def test_mount_add_rejects_invalid_name(mounted):
    result = runner.invoke(app, ["mount", "add", "spell book"])
    assert result.exit_code != 0
    assert "Invalid database name" in result.output


def test_mount_add_rejects_slash_in_name(mounted):
    result = runner.invoke(app, ["mount", "add", "subdir/db"])
    assert result.exit_code != 0
    assert "Invalid database name" in result.output


def test_mount_add_rejects_dunder_prefixed_name(mounted):
    result = runner.invoke(app, ["mount", "add", "__models__"])
    assert result.exit_code != 0
    assert "reserved" in result.output


def test_named_db_lookup_is_case_insensitive(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    result = runner.invoke(app, ["entry", "add", "-d", "SPELLBOOK"])
    assert result.exit_code == 0, result.output


def test_mount_remove_canonical_name_in_output(mounted):
    runner.invoke(app, ["mount", "add", "Spellbook"])
    result = runner.invoke(app, ["mount", "remove", "SPELLBOOK", "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"db": "spellbook", "removed": True}


@pytest.mark.parametrize("text", ["", "   "])
def test_entry_add_rejects_empty_keyword_text(mounted, text):
    result = runner.invoke(app, ["entry", "add", "--keyword-text", text])
    assert result.exit_code != 0
    assert "keyword_text must be non-empty" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("text", ["", "   "])
def test_entry_add_rejects_empty_semantic_text(mounted, text):
    result = runner.invoke(app, ["entry", "add", "--semantic-text", text])
    assert result.exit_code != 0
    assert "semantic_text must be non-empty" in result.output
    assert "Traceback" not in result.output


def test_entry_update_unknown_id_with_indexing_is_clean_error(mounted):
    result = runner.invoke(
        app,
        [
            "entry",
            "update",
            "01HXXXXXXXXXXXXXXXXXXXXXXX",
            "--keyword-text",
            "ghost",
        ],
    )
    assert result.exit_code != 0
    assert "No entry" in result.output
    assert "Traceback" not in result.output


def test_entry_update_rejects_invalid_payload_json(mounted):
    add = runner.invoke(app, ["entry", "add"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "update", entry_id, "--payload", "not-json"])
    assert result.exit_code != 0
    assert "Invalid JSON payload" in result.output


def test_entry_update_changes_only_specified_fields(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-key",
            "tale",
            "--group-ref",
            "ref-1",
            "--context",
            "ch.3",
            "--payload",
            json.dumps({"author": "merlin"}),
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


def test_entry_update_rejects_collision_with_existing_pair(mounted):
    runner.invoke(
        app, ["entry", "add", "--group-key", "wizard", "--group-ref", "gandalf"]
    )
    other = runner.invoke(
        app, ["entry", "add", "--group-key", "wizard", "--group-ref", "saruman"]
    )
    other_id = json.loads(other.output)["id"]

    result = runner.invoke(
        app,
        ["entry", "update", other_id, "--group-ref", "gandalf"],
    )
    assert result.exit_code != 0
    assert "group_key, group_ref" in result.output


def test_entry_update_put_clears_unspecified_fields(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-key",
            "wizard",
            "--group-ref",
            "gandalf",
            "--payload",
            '{"order":"Istari"}',
            "--context",
            "ancient",
        ],
    )
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "--put", "--group-key", "wizard"],
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["group_key"] == "wizard"
    assert out["group_ref"] is None
    assert out["payload"] is None
    assert out["context"] is None


def test_entry_update_put_with_no_fields_clears_everything(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-key",
            "wizard",
            "--group-ref",
            "gandalf",
            "--payload",
            '{"a":1}',
            "--context",
            "ctx",
        ],
    )
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "update", entry_id, "--put"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["group_key"] is None
    assert out["group_ref"] is None
    assert out["payload"] is None
    assert out["context"] is None


def test_entry_update_put_does_not_disturb_index_rows(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-key",
            "wizard",
            "--group-ref",
            "gandalf",
            "--keyword-text",
            "white wizard",
            "--threshold-rank",
            "0.5",
            "--semantic-text",
            "a wandering wizard in grey",
            "--partition",
            "fellowship",
            "--threshold-distance",
            "0.75",
        ],
    )
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "update", entry_id, "--put"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["group_key"] is None
    assert out["payload"] is None
    assert out["keyword_text"] == "white wizard"
    assert out["threshold_rank"] == 0.5
    assert out["semantic_text"] == "a wandering wizard in grey"
    assert out["partition"] == "fellowship"
    assert out["threshold_distance"] == 0.75


def test_entry_update_put_keeps_fields_that_are_restated(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-key",
            "wizard",
            "--group-ref",
            "gandalf",
            "--payload",
            '{"order":"Istari"}',
            "--context",
            "ancient",
        ],
    )
    entry_id = json.loads(add.output)["id"]

    # Restate every field except context; context should clear.
    result = runner.invoke(
        app,
        [
            "entry",
            "update",
            entry_id,
            "--put",
            "--group-key",
            "wizard",
            "--group-ref",
            "gandalf",
            "--payload",
            '{"order":"Istari"}',
        ],
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["group_key"] == "wizard"
    assert out["group_ref"] == "gandalf"
    assert out["payload"] == {"order": "Istari"}
    assert out["context"] is None


def test_entry_update_default_mode_still_preserves_unspecified(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-key",
            "wizard",
            "--group-ref",
            "gandalf",
            "--payload",
            '{"a":1}',
            "--context",
            "ctx",
        ],
    )
    entry_id = json.loads(add.output)["id"]

    # No --put: only payload changes; group_key, group_ref, context preserved.
    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "--payload", '{"a":2}'],
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["group_key"] == "wizard"
    assert out["group_ref"] == "gandalf"
    assert out["payload"] == {"a": 2}
    assert out["context"] == "ctx"


def test_entry_get_returns_entry(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-key",
            "wizard",
            "--group-ref",
            "gandalf",
            "--payload",
            '{"order":"Istari"}',
        ],
    )
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "get", entry_id])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["id"] == entry_id
    assert out["group_ref"] == "gandalf"
    assert out["payload"] == {"order": "Istari"}


def test_entry_get_unknown_id_errors(mounted):
    result = runner.invoke(app, ["entry", "get", "01HZZZZZZZZZZZZZZZZZZZZZZZ"])
    assert result.exit_code != 0
    assert "No entry" in result.output


def test_entry_update_targets_named_db(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    add = runner.invoke(app, ["entry", "add", "-d", "spellbook"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app,
        [
            "entry",
            "update",
            entry_id,
            "-d",
            "spellbook",
            "--payload",
            json.dumps({"x": 1}),
        ],
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
        [
            "entry",
            "update",
            "01HXXXXXXXXXXXXXXXXXXXXXXX",
            "-d",
            "nonesuch",
            "--payload",
            "{}",
        ],
    )
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_fetch_no_filters_returns_all(mounted):
    runner.invoke(app, ["entry", "add", "--group-ref", "first"])
    runner.invoke(app, ["entry", "add", "--group-ref", "second"])

    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert {r["group_ref"] for r in rows} == {"first", "second"}


def test_fetch_filters_by_id(mounted):
    a = json.loads(runner.invoke(app, ["entry", "add"]).output)
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--id", a["id"]])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["id"] for r in rows] == [a["id"]]


def test_fetch_filters_by_group_key(mounted):
    runner.invoke(
        app, ["entry", "add", "--group-key", "tale", "--group-ref", "tale-one"]
    )
    runner.invoke(
        app, ["entry", "add", "--group-key", "note", "--group-ref", "note-one"]
    )

    result = runner.invoke(app, ["fetch", "--group-key", "tale"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["group_ref"] for r in rows] == ["tale-one"]


def test_fetch_repeatable_filter(mounted):
    a = json.loads(runner.invoke(app, ["entry", "add"]).output)
    b = json.loads(runner.invoke(app, ["entry", "add"]).output)
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--id", a["id"], "--id", b["id"]])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert {r["id"] for r in rows} == {a["id"], b["id"]}


def test_fetch_surfaces_keyword_and_semantic_index_fields(mounted):
    add = runner.invoke(
        app,
        [
            "entry",
            "add",
            "--group-ref",
            "indexed",
            "--keyword-text",
            "moon glow",
            "--threshold-rank",
            "0.5",
            "--semantic-text",
            "the moon glows",
            "--partition",
            "night",
            "--threshold-distance",
            "0.75",
        ],
    )
    assert add.exit_code == 0, add.output
    runner.invoke(app, ["entry", "add", "--group-ref", "bare"])

    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 0, result.output
    rows = {r["group_ref"]: r for r in json.loads(result.output)}

    indexed = rows["indexed"]
    assert indexed["keyword_text"] == "moon glow"
    assert indexed["threshold_rank"] == 0.5
    assert indexed["semantic_text"] == "the moon glows"
    assert indexed["partition"] == "night"
    assert indexed["threshold_distance"] == 0.75

    bare = rows["bare"]
    assert bare["keyword_text"] is None
    assert bare["threshold_rank"] is None
    assert bare["semantic_text"] is None
    assert bare["partition"] is None
    assert bare["threshold_distance"] is None


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


def test_fetch_rejects_negative_limit(mounted):
    result = runner.invoke(app, ["fetch", "--limit", "-1"])
    assert result.exit_code != 0
    assert "--limit" in result.output


def test_fetch_cursor_paginates_chronologically(mounted):
    ids = [
        json.loads(runner.invoke(app, ["entry", "add"]).output)["id"] for _ in range(5)
    ]

    page1 = runner.invoke(app, ["fetch", "--limit", "2"])
    page1_ids = [r["id"] for r in json.loads(page1.output)]
    assert page1_ids == ids[:2]

    page2 = runner.invoke(app, ["fetch", "--limit", "2", "--cursor", page1_ids[-1]])
    page2_ids = [r["id"] for r in json.loads(page2.output)]
    assert page2_ids == ids[2:4]

    page3 = runner.invoke(app, ["fetch", "--limit", "2", "--cursor", page2_ids[-1]])
    assert [r["id"] for r in json.loads(page3.output)] == [ids[4]]


def test_fetch_cursor_past_end_returns_empty(mounted):
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--cursor", "01ZZZZZZZZZZZZZZZZZZZZZZZZ"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_fetch_empty_when_no_match(mounted):
    runner.invoke(app, ["entry", "add"])

    result = runner.invoke(app, ["fetch", "--id", "01HXXXXXXXXXXXXXXXXXXXXXXX"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_fetch_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    runner.invoke(
        app, ["entry", "add", "--group-ref", "named-hello", "-d", "spellbook"]
    )

    result = runner.invoke(app, ["fetch", "-d", "spellbook"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["group_ref"] for r in rows] == ["named-hello"]


def test_fetch_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_fetch_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["fetch", "-d", "nonesuch"])
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
    result = runner.invoke(
        app, ["entry", "delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "id": "01HXXXXXXXXXXXXXXXXXXXXXXX",
        "deleted": False,
    }


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

    add = runner.invoke(app, ["entry", "add", "-d", "spellbook"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(
        app, ["entry", "delete", entry_id, "-d", "spellbook", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": entry_id, "deleted": True}


def test_entry_delete_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(
        app, ["entry", "delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--yes"]
    )
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_entry_delete_fails_for_unknown_named_db(mounted):
    result = runner.invoke(
        app,
        ["entry", "delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "-d", "nonesuch", "--yes"],
    )
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_mount_ls_lists_default_only(mounted):
    result = runner.invoke(app, ["mount", "ls"])
    assert result.exit_code == 0, result.output
    dbs = json.loads(result.output)
    assert dbs == [{"db": None, "path": str(mounted / DB_FILENAME)}]


def test_mount_ls_includes_named_dbs(mounted):
    runner.invoke(app, ["mount", "add", "spellbook"])
    runner.invoke(app, ["mount", "add", "atlas"])

    result = runner.invoke(app, ["mount", "ls"])
    assert result.exit_code == 0, result.output
    names = [d["db"] for d in json.loads(result.output)]
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
    assert info["db"] is None
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
    runner.invoke(app, ["entry", "add", "-d", "spellbook"])

    result = runner.invoke(app, ["info", "-d", "spellbook"])
    assert result.exit_code == 0, result.output

    info = json.loads(result.output)
    assert info["db"] == "spellbook"
    assert info["path"] == str(mounted / "spellbook" / DB_FILENAME)
    assert info["entry_count"] == 1


def test_info_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["info"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_info_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["info", "-d", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_entry_add_with_semantic_text_writes_vec_row(mounted):
    add = runner.invoke(app, ["entry", "add", "--semantic-text", "hello"])
    assert add.exit_code == 0, add.output
    entry_id = json.loads(add.output)["id"]

    sem = runner.invoke(app, ["search", "semantic", "hello"])
    hits = json.loads(sem.output)
    assert any(h["entry"]["id"] == entry_id for h in hits)
    assert any(h["entry"]["semantic_text"] == "hello" for h in hits)


def test_entry_add_with_semantic_text_into_named_partition(mounted):
    add = runner.invoke(
        app,
        ["entry", "add", "--semantic-text", "hello", "--partition", "alpha"],
    )
    assert add.exit_code == 0, add.output
    entry_id = json.loads(add.output)["id"]

    in_partition = runner.invoke(
        app, ["search", "semantic", "hello", "--partition", "alpha"]
    )
    assert [h["entry"]["id"] for h in json.loads(in_partition.output)] == [entry_id]

    other_partition = runner.invoke(
        app, ["search", "semantic", "hello", "--partition", "beta"]
    )
    assert json.loads(other_partition.output) == []


def test_entry_add_stores_threshold_rank(mounted):
    add = runner.invoke(
        app,
        ["entry", "add", "--keyword-text", "moon", "--threshold-rank", "0.25"],
    )
    assert add.exit_code == 0, add.output

    out = json.loads(runner.invoke(app, ["search", "keyword", "moon"]).output)
    assert [h["entry"]["threshold_rank"] for h in out] == [0.25]


def test_entry_add_stores_threshold_distance(mounted):
    add = runner.invoke(
        app,
        ["entry", "add", "--semantic-text", "moon", "--threshold-distance", "0.75"],
    )
    assert add.exit_code == 0, add.output

    out = json.loads(runner.invoke(app, ["search", "semantic", "moon"]).output)
    assert [h["entry"]["threshold_distance"] for h in out] == [0.75]


def test_entry_update_replaces_keyword_text(mounted):
    entry_id = _add(keyword="moon")
    runner.invoke(app, ["entry", "update", entry_id, "--keyword-text", "stars"])

    moon = runner.invoke(app, ["search", "keyword", "moon"])
    stars = runner.invoke(app, ["search", "keyword", "stars"])
    assert json.loads(moon.output) == []
    assert any(h["entry"]["id"] == entry_id for h in json.loads(stars.output))


def test_entry_update_replaces_semantic_text(mounted):
    entry_id = _add(embed="first")
    runner.invoke(app, ["entry", "update", entry_id, "--semantic-text", "second"])

    sem = runner.invoke(app, ["search", "semantic", "anything"])
    hits = json.loads(sem.output)
    texts = [h["entry"]["semantic_text"] for h in hits if h["entry"]["id"] == entry_id]
    assert texts == ["second"]


def test_entry_update_without_text_leaves_index_alone(mounted):
    entry_id = _add(keyword="moon glow", embed="lunar light")
    result = runner.invoke(
        app,
        ["entry", "update", entry_id, "--payload", json.dumps({"x": 1})],
    )
    assert result.exit_code == 0, result.output

    kw = json.loads(runner.invoke(app, ["search", "keyword", "moon"]).output)
    assert any(
        h["entry"]["id"] == entry_id and h["entry"]["keyword_text"] == "moon glow" for h in kw
    )
    sem = json.loads(runner.invoke(app, ["search", "semantic", "lunar"]).output)
    assert any(
        h["entry"]["id"] == entry_id and h["entry"]["semantic_text"] == "lunar light"
        for h in sem
    )


def test_entry_add_threshold_rank_requires_keyword_text(mounted):
    result = runner.invoke(app, ["entry", "add", "--threshold-rank", "0.5"])
    assert result.exit_code != 0
    assert "--threshold-rank" in result.output and "--keyword-text" in result.output


def test_entry_add_partition_requires_semantic_text(mounted):
    result = runner.invoke(app, ["entry", "add", "--partition", "alpha"])
    assert result.exit_code != 0
    assert "--partition" in result.output and "--semantic-text" in result.output


def test_entry_add_rejects_negative_threshold_rank(mounted):
    result = runner.invoke(
        app,
        ["entry", "add", "--keyword-text", "x", "--threshold-rank", "-1"],
    )
    assert result.exit_code != 0
    assert "--threshold-rank" in result.output


def test_entry_add_rejects_negative_threshold_distance(mounted):
    result = runner.invoke(
        app,
        ["entry", "add", "--semantic-text", "x", "--threshold-distance", "-1"],
    )
    assert result.exit_code != 0
    assert "--threshold-distance" in result.output


def test_search_bare_verb_requires_subcommand(mounted):
    result = runner.invoke(app, ["search"])
    assert result.exit_code != 0
    # Help / usage message should mention the subcommands.
    assert "keyword" in result.output
    assert "semantic" in result.output


def test_search_keyword_returns_flat_list(mounted):
    _add(keyword="moon glow")

    result = runner.invoke(app, ["search", "keyword", "moon"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert isinstance(out, list)
    assert any(h["entry"]["keyword_text"] == "moon glow" for h in out)


def test_search_semantic_returns_flat_list(mounted):
    _add(embed="the moon glows")

    result = runner.invoke(app, ["search", "semantic", "moon"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert isinstance(out, list)
    assert any(h["entry"]["semantic_text"] == "the moon glows" for h in out)


def test_search_keyword_rank_is_high_is_better(mounted):
    _add(keyword="match match match")
    _add(keyword="match")

    result = runner.invoke(app, ["search", "keyword", "match"])
    out = json.loads(result.output)
    ranks = [h["rank"] for h in out]
    # Best-first ordering for canonical BM25: descending, non-negative.
    assert ranks == sorted(ranks, reverse=True)
    assert all(r >= 0 for r in ranks)


def test_search_semantic_distance_is_low_is_better(mounted):
    _add(embed="alpha")
    _add(embed="beta")

    result = runner.invoke(app, ["search", "semantic", "anything"])
    out = json.loads(result.output)
    distances = [h["distance"] for h in out]
    assert len(distances) == 2
    assert distances == sorted(distances)


def test_search_keyword_default_limit_is_10(mounted):
    for i in range(15):
        _add(keyword=f"target {i}")

    result = runner.invoke(app, ["search", "keyword", "target"])
    out = json.loads(result.output)
    assert len(out) == 10


def test_search_semantic_default_limit_is_10(mounted):
    for i in range(15):
        _add(embed=f"entry {i}")

    result = runner.invoke(app, ["search", "semantic", "anything"])
    out = json.loads(result.output)
    assert len(out) == 10


def test_search_keyword_respects_explicit_limit(mounted):
    for i in range(5):
        _add(keyword=f"target {i}")

    result = runner.invoke(app, ["search", "keyword", "target", "--limit", "2"])
    out = json.loads(result.output)
    assert len(out) == 2


def test_search_keyword_rejects_negative_limit(mounted):
    result = runner.invoke(app, ["search", "keyword", "wizard", "--limit", "-1"])
    assert result.exit_code != 0
    assert "--limit" in result.output


def test_search_semantic_rejects_negative_limit(mounted):
    result = runner.invoke(app, ["search", "semantic", "wizard", "--limit", "-1"])
    assert result.exit_code != 0
    assert "--limit" in result.output


def test_search_keyword_filters_by_group_key(mounted):
    _add("--group-key", "tale", keyword="target")
    _add("--group-key", "note", keyword="target")

    result = runner.invoke(app, ["search", "keyword", "target", "--group-key", "tale"])
    out = json.loads(result.output)
    assert [h["entry"]["group_key"] for h in out] == ["tale"]


def test_search_keyword_filters_by_group_ref(mounted):
    _add("--group-ref", "a", keyword="target")
    _add("--group-ref", "b", keyword="target")

    result = runner.invoke(app, ["search", "keyword", "target", "--group-ref", "a"])
    out = json.loads(result.output)
    assert [h["entry"]["group_ref"] for h in out] == ["a"]


def test_search_keyword_filters_by_id(mounted):
    a_id = _add(keyword="target")
    _add(keyword="target")

    result = runner.invoke(app, ["search", "keyword", "target", "--id", a_id])
    out = json.loads(result.output)
    assert [h["entry"]["id"] for h in out] == [a_id]


def test_search_keyword_repeatable_filters_or_within_field(mounted):
    _add("--group-key", "tale", keyword="target")
    _add("--group-key", "note", keyword="target")
    _add("--group-key", "spell", keyword="target")

    result = runner.invoke(
        app,
        ["search", "keyword", "target", "--group-key", "tale", "--group-key", "note"],
    )
    out = json.loads(result.output)
    assert sorted(h["entry"]["group_key"] for h in out) == ["note", "tale"]


def test_search_semantic_filters_by_partition(mounted):
    _add(embed="tale-a", partition="tale")
    _add(embed="note-b", partition="note")

    result = runner.invoke(app, ["search", "semantic", "target", "--partition", "tale"])
    out = json.loads(result.output)
    assert all(h["entry"]["semantic_text"] == "tale-a" for h in out)
    assert len(out) == 1


def test_search_keyword_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    _add(db="spellbook", keyword="named")

    result = runner.invoke(app, ["search", "keyword", "named", "-d", "spellbook"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["entry"]["keyword_text"] == "named" for h in out)


def test_search_keyword_accepts_apostrophes_and_punctuation(mounted):
    _add(keyword="happening mate")

    result = runner.invoke(app, ["search", "keyword", "what's going on mate?"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["entry"]["keyword_text"] == "happening mate" for h in out)


def test_search_keyword_treats_operator_words_as_literals(mounted):
    _add(keyword="AND OR NOT")

    result = runner.invoke(app, ["search", "keyword", "AND OR NOT"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["entry"]["keyword_text"] == "AND OR NOT" for h in out)


def test_search_keyword_all_punctuation_query_returns_empty(mounted):
    _add(keyword="anything")

    result = runner.invoke(app, ["search", "keyword", "?!?"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_search_keyword_fails_when_mount_missing(
    tmp_path, monkeypatch, patched_embedder
):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["search", "keyword", "anything"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_search_keyword_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["search", "keyword", "anything", "-d", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_entry_add_persists_ordinal(mounted):
    entry_id = _add("--ordinal", "3.5")
    result = runner.invoke(app, ["entry", "get", entry_id])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ordinal"] == 3.5


def test_entry_update_partial_preserves_ordinal(mounted):
    entry_id = _add("--ordinal", "1.0")
    result = runner.invoke(
        app, ["entry", "update", entry_id, "--payload", '{"a":2}']
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ordinal"] == 1.0


def test_entry_update_put_clears_unspecified_ordinal(mounted):
    entry_id = _add("--ordinal", "1.0")
    result = runner.invoke(app, ["entry", "update", entry_id, "--put"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ordinal"] is None


def test_fetch_filters_ordinal_range_and_orders_by_ordinal(mounted):
    a = _add("--ordinal", "1.0")
    b = _add("--ordinal", "5.0")
    _ = _add("--ordinal", "10.0")
    result = runner.invoke(
        app,
        [
            "fetch",
            "--ordinal-gte",
            "1.0",
            "--ordinal-lte",
            "5.0",
            "--order-by",
            "ordinal",
            "--desc",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["id"] for r in rows] == [b, a]


def test_fetch_rejects_unknown_order_by(mounted):
    result = runner.invoke(app, ["fetch", "--order-by", "bogus"])
    assert result.exit_code != 0
    assert "order-by" in result.output.lower() or "ordinal" in result.output.lower()

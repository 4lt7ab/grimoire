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
            "the wizard's spellbook hums in the dark",
            "--keyword-text", "spellbook wizard humming",
            "--group-key", "notes",
            "--group-ref", "note-001",
            "--context", "from chapter 3",
            "--payload", json.dumps(payload),
            "--threshold-rank", "0.5",
            "--threshold-distance", "0.8",
        ],
    )
    assert result.exit_code == 0, result.output

    entry = json.loads(result.output)
    assert entry["id"] is not None
    assert entry["semantic_text"] == "the wizard's spellbook hums in the dark"
    assert entry["keyword_text"] == "spellbook wizard humming"
    assert entry["group_key"] == "notes"
    assert entry["group_ref"] == "note-001"
    assert entry["context"] == "from chapter 3"
    assert entry["payload"] == payload
    assert entry["threshold_rank"] == 0.5
    assert entry["threshold_distance"] == 0.8


def test_entry_add_with_no_flags_is_a_payload_less_entry(mounted):
    result = runner.invoke(app, ["entry", "add"])
    assert result.exit_code == 0, result.output

    entry = json.loads(result.output)
    assert entry["id"] is not None
    for field in (
        "semantic_text",
        "keyword_text",
        "group_key",
        "group_ref",
        "context",
        "payload",
        "threshold_rank",
        "threshold_distance",
    ):
        assert entry[field] is None, field


def test_entry_add_short_flag_for_keyword_text(mounted):
    result = runner.invoke(app, ["entry", "add", "-k", "shorthand"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["keyword_text"] == "shorthand"


def test_entry_add_fails_when_mount_does_not_exist(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["entry", "add", "anything"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_entry_add_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    result = runner.invoke(app, ["entry", "add", "hello", "-n", "spellbook"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["semantic_text"] == "hello"


def test_entry_add_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["entry", "add", "hello", "-n", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_entry_fetch_no_filters_returns_all(mounted):
    runner.invoke(app, ["entry", "add", "first"])
    runner.invoke(app, ["entry", "add", "second"])

    result = runner.invoke(app, ["entry", "fetch"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert {e["semantic_text"] for e in entries} == {"first", "second"}


def test_entry_fetch_filters_by_id(mounted):
    a = json.loads(runner.invoke(app, ["entry", "add", "alpha"]).output)
    runner.invoke(app, ["entry", "add", "beta"])

    result = runner.invoke(app, ["entry", "fetch", "--id", a["id"]])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert [e["id"] for e in entries] == [a["id"]]


def test_entry_fetch_filters_by_group_key(mounted):
    runner.invoke(app, ["entry", "add", "tale-one", "--group-key", "tale"])
    runner.invoke(app, ["entry", "add", "note-one", "--group-key", "note"])

    result = runner.invoke(app, ["entry", "fetch", "--group-key", "tale"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert [e["semantic_text"] for e in entries] == ["tale-one"]


def test_entry_fetch_repeatable_filter(mounted):
    a = json.loads(runner.invoke(app, ["entry", "add", "alpha"]).output)
    b = json.loads(runner.invoke(app, ["entry", "add", "beta"]).output)
    runner.invoke(app, ["entry", "add", "gamma"])

    result = runner.invoke(app, ["entry", "fetch", "--id", a["id"], "--id", b["id"]])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert {e["id"] for e in entries} == {a["id"], b["id"]}


def test_entry_fetch_respects_explicit_limit(mounted):
    for word in ["one", "two", "three", "four", "five"]:
        runner.invoke(app, ["entry", "add", word])

    result = runner.invoke(app, ["entry", "fetch", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 2


def test_entry_fetch_default_limit_caps_at_100(mounted):
    for i in range(101):
        runner.invoke(app, ["entry", "add", f"entry-{i}"])

    result = runner.invoke(app, ["entry", "fetch"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 100


def test_entry_fetch_empty_when_no_match(mounted):
    runner.invoke(app, ["entry", "add", "hello"])

    result = runner.invoke(app, ["entry", "fetch", "--id", "01HXXXXXXXXXXXXXXXXXXXXXXX"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_entry_fetch_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    runner.invoke(app, ["entry", "add", "named-hello", "-n", "spellbook"])

    result = runner.invoke(app, ["entry", "fetch", "-n", "spellbook"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.output)
    assert [e["semantic_text"] for e in entries] == ["named-hello"]


def test_entry_fetch_fails_when_mount_missing(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(app, ["entry", "fetch"])
    assert result.exit_code != 0
    assert "Mount does not exist" in result.output


def test_entry_fetch_fails_for_unknown_named_db(mounted):
    result = runner.invoke(app, ["entry", "fetch", "-n", "nonesuch"])
    assert result.exit_code != 0
    assert "nonesuch" in result.output


def test_entry_delete_removes_existing(mounted):
    add = runner.invoke(app, ["entry", "add", "ephemeral"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "delete", entry_id, "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": entry_id, "deleted": True}

    fetched = runner.invoke(app, ["entry", "fetch", "--id", entry_id])
    assert json.loads(fetched.output) == []


def test_entry_delete_missing_id_is_soft(mounted):
    result = runner.invoke(app, ["entry", "delete", "01HXXXXXXXXXXXXXXXXXXXXXXX", "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"id": "01HXXXXXXXXXXXXXXXXXXXXXXX", "deleted": False}


def test_entry_delete_requires_yes(mounted):
    add = runner.invoke(app, ["entry", "add", "ephemeral"])
    entry_id = json.loads(add.output)["id"]

    result = runner.invoke(app, ["entry", "delete", entry_id])
    assert result.exit_code != 0
    assert "--yes" in result.output

    fetched = runner.invoke(app, ["entry", "fetch", "--id", entry_id])
    assert len(json.loads(fetched.output)) == 1


def test_entry_delete_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    add = runner.invoke(app, ["entry", "add", "named", "-n", "spellbook"])
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


def test_search_runs_both_modes(mounted):
    runner.invoke(app, ["entry", "add", "the moon glows", "--keyword-text", "moon glow"])

    result = runner.invoke(app, ["search", "moon"])
    assert result.exit_code == 0, result.output

    out = json.loads(result.output)
    assert set(out.keys()) == {"keyword", "semantic"}
    assert any(h["entry"]["keyword_text"] == "moon glow" for h in out["keyword"])
    assert any(h["entry"]["semantic_text"] == "the moon glows" for h in out["semantic"])


def test_search_keyword_rank_is_low_is_better(mounted):
    runner.invoke(app, ["entry", "add", "first", "--keyword-text", "match match match"])
    runner.invoke(app, ["entry", "add", "second", "--keyword-text", "match"])

    result = runner.invoke(app, ["search", "match"])
    out = json.loads(result.output)
    ranks = [h["rank"] for h in out["keyword"]]
    assert ranks == sorted(ranks)
    assert all(r <= 0 for r in ranks)


def test_search_semantic_distance_is_low_is_better(mounted):
    runner.invoke(app, ["entry", "add", "alpha"])
    runner.invoke(app, ["entry", "add", "beta"])

    result = runner.invoke(app, ["search", "anything"])
    out = json.loads(result.output)
    distances = [h["distance"] for h in out["semantic"]]
    assert distances == sorted(distances)


def test_search_default_limit_is_10(mounted):
    for i in range(15):
        runner.invoke(app, ["entry", "add", f"entry {i}", "--keyword-text", f"target {i}"])

    result = runner.invoke(app, ["search", "target"])
    out = json.loads(result.output)
    assert len(out["keyword"]) == 10
    assert len(out["semantic"]) == 10


def test_search_respects_explicit_limit(mounted):
    for i in range(5):
        runner.invoke(app, ["entry", "add", f"entry {i}", "--keyword-text", f"target {i}"])

    result = runner.invoke(app, ["search", "target", "--limit", "2"])
    out = json.loads(result.output)
    assert len(out["keyword"]) == 2
    assert len(out["semantic"]) == 2


def test_search_filters_by_group_key(mounted):
    runner.invoke(app, ["entry", "add", "a", "--keyword-text", "target", "--group-key", "tale"])
    runner.invoke(app, ["entry", "add", "b", "--keyword-text", "target", "--group-key", "note"])

    result = runner.invoke(app, ["search", "target", "--group-key", "tale"])
    out = json.loads(result.output)
    assert all(h["entry"]["group_key"] == "tale" for h in out["keyword"])
    assert all(h["entry"]["group_key"] == "tale" for h in out["semantic"])


def test_search_targets_named_db(mounted):
    create = runner.invoke(app, ["mount", "add", "spellbook"])
    assert create.exit_code == 0, create.output

    runner.invoke(app, ["entry", "add", "named-thing", "--keyword-text", "named", "-n", "spellbook"])

    result = runner.invoke(app, ["search", "named", "-n", "spellbook"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["entry"]["semantic_text"] == "named-thing" for h in out["keyword"])


def test_search_accepts_apostrophes_and_punctuation(mounted):
    runner.invoke(app, ["entry", "add", "what is happening", "--keyword-text", "happening mate"])

    result = runner.invoke(app, ["search", "what's going on mate?"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["entry"]["keyword_text"] == "happening mate" for h in out["keyword"])


def test_search_treats_operator_words_as_literals(mounted):
    runner.invoke(app, ["entry", "add", "boom", "--keyword-text", "AND OR NOT"])

    result = runner.invoke(app, ["search", "AND OR NOT"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert any(h["entry"]["keyword_text"] == "AND OR NOT" for h in out["keyword"])


def test_search_all_punctuation_query_skips_keyword(mounted):
    runner.invoke(app, ["entry", "add", "anything", "--keyword-text", "anything"])

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

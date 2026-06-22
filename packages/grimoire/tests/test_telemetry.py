import contextlib
import logging
from contextlib import AbstractContextManager
from typing import Any

import pytest
from grimoire.data.entry import Entry, Filters
from grimoire.grimoire import Grimoire
from grimoire.telemetry import LoggingTelemetry, NoOpTelemetry


class RecordingTelemetry:
    """Test stand-in: records every span/event call for assertion."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, Any]]] = []
        self.span_errors: list[tuple[str, type[BaseException]]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any):
        self.spans.append((name, attrs))
        try:
            yield
        except BaseException as exc:
            self.span_errors.append((name, type(exc)))
            raise

    def event(self, name: str, **attrs: Any) -> None:
        self.events.append((name, attrs))


# ----------------------------------------------------------------------
# Bundled implementations
# ----------------------------------------------------------------------


def test_noop_span_is_a_context_manager():
    t = NoOpTelemetry()
    cm = t.span("anything", foo="bar")
    assert isinstance(cm, AbstractContextManager)
    with cm:
        pass


def test_noop_event_returns_none():
    assert NoOpTelemetry().event("anything", foo="bar") is None


def test_logging_telemetry_emits_record_on_span_exit(caplog):
    logger = logging.getLogger("grimoire.test")
    tel = LoggingTelemetry(logger=logger)
    with (
        caplog.at_level(logging.INFO, logger="grimoire.test"),
        tel.span("op.foo", k="v"),
    ):
        pass
    [rec] = caplog.records
    assert rec.levelno == logging.INFO
    assert "op.foo" in rec.getMessage()
    assert rec.grimoire["name"] == "op.foo"
    assert rec.grimoire["k"] == "v"
    assert rec.grimoire["elapsed_ms"] >= 0


def test_logging_telemetry_emits_error_on_span_exception(caplog):
    logger = logging.getLogger("grimoire.test")
    tel = LoggingTelemetry(logger=logger)
    with (
        caplog.at_level(logging.INFO, logger="grimoire.test"),
        pytest.raises(RuntimeError, match="boom"),
        tel.span("op.bar"),
    ):
        raise RuntimeError("boom")
    [rec] = caplog.records
    assert rec.levelno == logging.ERROR
    assert rec.grimoire["name"] == "op.bar"
    assert rec.grimoire["error"] == "RuntimeError"


def test_logging_telemetry_event_emits_info_record(caplog):
    logger = logging.getLogger("grimoire.test")
    tel = LoggingTelemetry(logger=logger)
    with caplog.at_level(logging.INFO, logger="grimoire.test"):
        tel.event("something.happened", count=3)
    [rec] = caplog.records
    assert rec.levelno == logging.INFO
    assert rec.grimoire["name"] == "something.happened"
    assert rec.grimoire["count"] == 3


# ----------------------------------------------------------------------
# Library wiring — every public op produces a span
# ----------------------------------------------------------------------


def _g(tmp_path, fake_embedder, tel):
    return Grimoire.open(tmp_path / "g.db", embedder=fake_embedder, telemetry=tel)


def test_open_emits_span_and_schema_installed_event(tmp_path, fake_embedder):
    tel = RecordingTelemetry()
    with _g(tmp_path, fake_embedder, tel):
        pass
    [span_name] = [name for name, _ in tel.spans]
    assert span_name == "grimoire.open"
    [event_name] = [name for name, _ in tel.events]
    assert event_name == "grimoire.schema_installed"


def test_open_emits_lock_validated_event_on_reopen(tmp_path, fake_embedder):
    Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)  # initial create
    tel = RecordingTelemetry()
    with _g(tmp_path, fake_embedder, tel):
        pass
    event_names = [name for name, _ in tel.events]
    assert "grimoire.lock_validated" in event_names
    assert "grimoire.schema_installed" not in event_names


def test_add_update_remove_get_emit_spans(tmp_path, fake_embedder):
    tel = RecordingTelemetry()
    with _g(tmp_path, fake_embedder, tel) as g:
        [e] = g.add([Entry(None, {"k": "v"})])
        g.update([Entry(e.uniq_id, {"k": "v2"})])
        g.get([e.uniq_id])
        g.remove([e.uniq_id])

    names = [name for name, _ in tel.spans]
    assert "grimoire.add" in names
    assert "grimoire.update" in names
    assert "grimoire.get" in names
    assert "grimoire.remove" in names

    # attrs carry the input count
    add_attrs = next(a for n, a in tel.spans if n == "grimoire.add")
    assert add_attrs == {"count": 1}


def test_index_span_records_which_sidecars_touched(tmp_path, fake_embedder):
    tel = RecordingTelemetry()
    with _g(tmp_path, fake_embedder, tel) as g:
        [e] = g.add([Entry(None, None)])
        g.index(e.uniq_id, ref="r", match="text", search="text")

    [(_, attrs)] = [(n, a) for n, a in tel.spans if n == "grimoire.index"]
    assert attrs == {
        "has_ref": True,
        "has_group": False,
        "has_ord": False,
        "has_match": True,
        "has_search": True,
    }
    embed_spans = [a for n, a in tel.spans if n == "grimoire.embed"]
    assert len(embed_spans) == 1
    assert embed_spans[0]["model"] == fake_embedder.model
    assert embed_spans[0]["text_length"] == len("text")


def test_query_match_search_fetch_emit_spans(tmp_path, fake_embedder):
    tel = RecordingTelemetry()
    with _g(tmp_path, fake_embedder, tel) as g:
        [e] = g.add([Entry(None, {"k": "v"})])
        g.index(e.uniq_id, ref="r", match="hello world", search="hello world")
        g.query(Filters(equals={"uniq_ref": ["r"]}))
        g.match("hello")
        g.search("hello")
        g.fetch(["r"])

    names = [name for name, _ in tel.spans]
    for expected in (
        "grimoire.query",
        "grimoire.match",
        "grimoire.search",
        "grimoire.fetch",
    ):
        assert expected in names

    query_attrs = next(a for n, a in tel.spans if n == "grimoire.query")
    assert query_attrs == {"limit": 100, "has_filters": True, "has_cursor": False}

    search_attrs = next(a for n, a in tel.spans if n == "grimoire.search")
    assert search_attrs == {"query_length": len("hello"), "limit": 10}


def test_analyze_emits_span(tmp_path, fake_embedder):
    tel = RecordingTelemetry()
    with _g(tmp_path, fake_embedder, tel) as g:
        g.analyze()
    assert "grimoire.analyze" in [name for name, _ in tel.spans]


def test_span_exception_path_records_error(tmp_path, fake_embedder):
    tel = RecordingTelemetry()
    with _g(tmp_path, fake_embedder, tel) as g, pytest.raises(ValueError):
        g.index(
            "01MISSINGMISSINGMISSINGMI",
            ref="r",  # nonexistent uniq_id → entry.entry_idx_set raises
        )
    [(name, exc_type)] = tel.span_errors
    assert name == "grimoire.index"
    assert exc_type is ValueError


def test_default_telemetry_is_noop(tmp_path, fake_embedder):
    # Open without telemetry — public ops must still work; this is the
    # backward-compatibility guarantee for callers that haven't opted in.
    with Grimoire.open(tmp_path / "g.db", embedder=fake_embedder) as g:
        [e] = g.add([Entry(None, {"k": "v"})])
        g.search("anything")
        g.remove([e.uniq_id])

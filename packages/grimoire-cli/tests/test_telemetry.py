import pytest
import typer
from grimoire.telemetry import LoggingTelemetry, NoOpTelemetry
from grimoire_cli.telemetry import ENV_VAR, build_telemetry


def test_unset_env_var_defaults_to_noop(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert isinstance(build_telemetry(), NoOpTelemetry)


@pytest.mark.parametrize("value", ["off", "OFF", "  off  ", ""])
def test_off_value_returns_noop(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)
    assert isinstance(build_telemetry(), NoOpTelemetry)


@pytest.mark.parametrize("value", ["logging", "LOGGING", "  logging "])
def test_logging_value_returns_logging_telemetry(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)
    assert isinstance(build_telemetry(), LoggingTelemetry)


def test_unknown_value_raises(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "otel")
    with pytest.raises(typer.BadParameter, match="otel"):
        build_telemetry()

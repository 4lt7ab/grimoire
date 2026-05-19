"""Resolve the telemetry sink from the user's environment.

`$GRIMOIRE_TELEMETRY` accepts `off` (default) or `logging`. When `logging`
is selected we also ensure stdlib logging has at least a stderr handler so
the records actually surface to the user; the CLI's JSON output goes to
stdout, so this doesn't interfere with `jq` piping.
"""

import logging
import os

import typer
from grimoire.telemetry import LoggingTelemetry, NoOpTelemetry, Telemetry

ENV_VAR = "GRIMOIRE_TELEMETRY"


def build_telemetry() -> Telemetry:
    """Return the Telemetry impl indicated by `$GRIMOIRE_TELEMETRY`."""
    raw = os.environ.get(ENV_VAR, "off").strip().lower()
    if raw in ("", "off"):
        return NoOpTelemetry()
    if raw == "logging":
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s %(message)s",
        )
        return LoggingTelemetry()
    raise typer.BadParameter(f"${ENV_VAR}={raw!r}; expected one of 'off', 'logging'.")

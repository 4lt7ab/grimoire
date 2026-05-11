from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mount(tmp_path: Path) -> Path:
    """A unique mount directory per test; the CLI creates it via `mount`."""
    return tmp_path / "mount"

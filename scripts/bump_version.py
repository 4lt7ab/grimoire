"""Bump the version of every workspace package in lockstep."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

Version = tuple[int, int, int]

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECTS: tuple[Path, ...] = (
    REPO_ROOT / "packages" / "grimoire" / "pyproject.toml",
    REPO_ROOT / "packages" / "grimoire-cli" / "pyproject.toml",
)
VERSION_RE = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"', re.MULTILINE)


def read_version(path: Path) -> Version:
    match = VERSION_RE.search(path.read_text())
    if match is None:
        sys.exit(f"could not find version line in {path}")
    groups = match.groups()
    return int(groups[0]), int(groups[1]), int(groups[2])


def write_version(path: Path, version: Version) -> None:
    new_line = f'version = "{version[0]}.{version[1]}.{version[2]}"'
    path.write_text(VERSION_RE.sub(new_line, path.read_text(), count=1))


def bump(version: Version, level: str) -> Version:
    major, minor, patch = version
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def format_version(version: Version) -> str:
    return f"{version[0]}.{version[1]}.{version[2]}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "level",
        nargs="?",
        default="patch",
        choices=("major", "minor", "patch"),
    )
    args = parser.parse_args()

    versions = {path: read_version(path) for path in PYPROJECTS}
    if len(set(versions.values())) != 1:
        sys.exit("packages have drifted versions; reconcile before bumping")

    current = next(iter(versions.values()))
    new = bump(current, args.level)

    for path in PYPROJECTS:
        write_version(path, new)

    print(f"{format_version(current)} -> {format_version(new)}")


if __name__ == "__main__":
    main()

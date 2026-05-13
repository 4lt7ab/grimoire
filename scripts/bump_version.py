"""Bump the version of every workspace package in lockstep."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

Version = tuple[int, int, int]

REPO_ROOT = Path(__file__).resolve().parent.parent

PYPROJECT_PATTERN = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"', re.MULTILINE)
PYPROJECT_TEMPLATE = 'version = "{major}.{minor}.{patch}"'


@dataclass(frozen=True)
class Target:
    path: Path
    pattern: re.Pattern[str]
    template: str


TARGETS: tuple[Target, ...] = (
    Target(
        REPO_ROOT / "packages" / "grimoire" / "pyproject.toml",
        PYPROJECT_PATTERN,
        PYPROJECT_TEMPLATE,
    ),
    Target(
        REPO_ROOT / "packages" / "grimoire-cli" / "pyproject.toml",
        PYPROJECT_PATTERN,
        PYPROJECT_TEMPLATE,
    ),
)


def read_version(target: Target) -> Version:
    match = target.pattern.search(target.path.read_text())
    if match is None:
        sys.exit(f"could not find version line in {target.path}")
    groups = match.groups()
    return int(groups[0]), int(groups[1]), int(groups[2])


def write_version(target: Target, version: Version) -> None:
    new_line = target.template.format(
        major=version[0], minor=version[1], patch=version[2]
    )
    target.path.write_text(
        target.pattern.sub(new_line, target.path.read_text(), count=1)
    )


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

    versions = {target.path: read_version(target) for target in TARGETS}
    if len(set(versions.values())) != 1:
        sys.exit("packages have drifted versions; reconcile before bumping")

    current = next(iter(versions.values()))
    new = bump(current, args.level)

    for target in TARGETS:
        write_version(target, new)

    print(f"{format_version(current)} -> {format_version(new)}")


if __name__ == "__main__":
    main()

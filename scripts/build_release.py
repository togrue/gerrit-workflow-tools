#!/usr/bin/env python3
"""Build what a release publishes, into ``dist/release/``.

    ger.tar.gz        ger with the VS Code extension inside; `uv tool install <url>` takes it
    ger-vscode.vsix   the extension alone, for 'Extensions: Install from VSIX…'
    *.whl             the same ger as a wheel

The extension is packaged first and put inside the Python package, so every ger release carries
the extension that matches it (`ger vscode install`). Needs ``uv`` and ``npm`` on ``PATH``.

Usage: python scripts/build_release.py [--tag vX.Y.Z]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "gerrit_workflow_tools"
VSCODE = ROOT / "vscode"
BUNDLED = PACKAGE / "vscode_extension" / "ger-vscode.vsix"
DIST = ROOT / "dist"
RELEASE = DIST / "release"


def versions() -> dict[str, str]:
    """The version each of the three places states; a release needs them equal."""

    def first(path: Path, pattern: str) -> str:
        match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
        if not match:
            raise SystemExit(f"error: no version in {path.relative_to(ROOT)}")
        return match.group(1)

    return {
        "pyproject.toml": first(ROOT / "pyproject.toml", r'^version = "([^"]+)"'),
        "src/gerrit_workflow_tools/__init__.py": first(PACKAGE / "__init__.py", r'^__version__ = "([^"]+)"'),
        "vscode/package.json": json.loads((VSCODE / "package.json").read_text(encoding="utf-8"))["version"],
    }


def release_version(tag: str | None) -> str:
    stated = versions()
    if len(set(stated.values())) != 1:
        lines = "\n".join(f"  {where}: {version}" for where, version in stated.items())
        raise SystemExit(f"error: the version differs between\n{lines}")
    version = next(iter(stated.values()))
    if tag is not None and tag != f"v{version}":
        raise SystemExit(f"error: tag {tag} does not match version {version} (expected v{version})")
    return version


def run(program: str, *args: str, cwd: Path) -> None:
    found = shutil.which(program)  # npm is npm.cmd on Windows
    if found is None:
        raise SystemExit(f"error: {program} not found on PATH")
    print(f"+ {program} {' '.join(args)}", flush=True)
    subprocess.run([found, *args], cwd=cwd, check=True)


def build_extension() -> None:
    if not (VSCODE / "node_modules").is_dir():
        run("npm", "ci", "--no-audit", "--no-fund", cwd=VSCODE)
    BUNDLED.parent.mkdir(parents=True, exist_ok=True)
    licence = VSCODE / "LICENSE"  # the package must carry it; the repository keeps one copy, at the root
    shutil.copyfile(ROOT / "LICENSE", licence)
    try:
        run("npm", "run", "compile", cwd=VSCODE)
        run("npm", "run", "package", "--", "--out", str(BUNDLED), cwd=VSCODE)
    finally:
        licence.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--tag", help="The release tag; the build fails unless it is v<version>.")
    version = release_version(parser.parse_args().tag)

    if RELEASE.exists():
        shutil.rmtree(RELEASE)
    RELEASE.mkdir(parents=True)
    try:
        build_extension()
        run("uv", "build", "--out-dir", str(RELEASE), cwd=ROOT)
        shutil.copyfile(BUNDLED, RELEASE / BUNDLED.name)
    finally:
        # Left in place, a source checkout would keep offering this build as its extension.
        shutil.rmtree(BUNDLED.parent, ignore_errors=True)
    (RELEASE / ".gitignore").unlink(missing_ok=True)  # uv's, for a build directory inside a repository
    sdist = RELEASE / f"gerrit_workflow_tools-{version}.tar.gz"
    sdist.rename(RELEASE / "ger.tar.gz")  # one name for every version: releases/latest/download/ger.tar.gz

    print(f"\nRelease {version}:")
    for path in sorted(RELEASE.iterdir()):
        print(f"  {path.relative_to(ROOT).as_posix()}  ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

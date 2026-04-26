"""``ger bash-completion`` — print, install, or uninstall bash tab-completion for ``ger``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gerrit_workflow_tools

MARKER_START = "# >>> gerrit-workflow-tools bash completion (ger) >>>"
MARKER_END = "# <<< gerrit-workflow-tools bash completion (ger) <<<"


def _sh_quote_posix_path(path_posix: str) -> str:
    """Quote a POSIX path for use in a printed ``source …`` hint."""
    if not path_posix:
        return "''"
    return "'" + path_posix.replace("'", "'\"'\"'") + "'"


def completion_script_path() -> Path:
    """Return the path to ``ger.bash``, whether from the installed wheel or a dev tree."""
    pkg_root = Path(gerrit_workflow_tools.__file__).resolve().parent
    bundled = pkg_root / "completion" / "ger.bash"
    if bundled.is_file():
        return bundled
    # Editable / src checkout: Hatch copies completion only into the wheel; contrib/ may be the only copy.
    dev = Path(__file__).resolve().parent.parent.parent / "contrib" / "completion" / "ger.bash"
    if dev.is_file():
        return dev
    raise FileNotFoundError("Could not find ger.bash (expected next to the package or under contrib/completion/).")


def source_command_line() -> str:
    """Single bash line to load completion: ``source \"…/ger.bash\"``."""
    p = completion_script_path().resolve()
    return f'source "{p.as_posix()}"'


def _block_lines() -> list[str]:
    return [MARKER_START, source_command_line(), MARKER_END, ""]


def _strip_marked_blocks(text: str) -> str:
    """Remove all marked completion blocks (including markers)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.rstrip("\r\n") == MARKER_START:
            i += 1
            while i < len(lines) and lines[i].rstrip("\r\n") != MARKER_END:
                i += 1
            if i < len(lines):
                i += 1  # skip MARKER_END
            continue
        out.append(line)
        i += 1
    return "".join(out)


def _has_marked_block(text: str) -> bool:
    return MARKER_START in text and MARKER_END in text


def _install_completion_block(rc_path: Path, log) -> int:
    try:
        script_path = completion_script_path()
    except FileNotFoundError as e:
        print(f"ger bash-completion: {e}", file=sys.stderr)
        return 1
    log(f"Using completion script: {script_path.resolve().as_posix()}")
    log(f"Target rc file: {rc_path.resolve().as_posix()}")
    block = "".join(line + "\n" for line in _block_lines())
    existing = rc_path.read_text(encoding="utf-8") if rc_path.is_file() else ""
    if _has_marked_block(existing):
        log("Marked gerrit-workflow-tools completion block already present; leaving file unchanged.")
        return 0
    if not rc_path.exists():
        log(f"Creating {rc_path} (did not exist).")
    else:
        log(f"Appending completion block to existing file ({rc_path.stat().st_size} bytes).")
    with rc_path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
            log("Added missing newline before appended block.")
        f.write(block)
    log("Wrote marked block:")
    for ln in _block_lines():
        if ln:
            log(f"  {ln}")
    log("Done. Restart the shell or run: source " + _sh_quote_posix_path(rc_path.as_posix()))
    return 0


def _uninstall_completion_block(rc_path: Path, log) -> int:
    log(f"Reading rc file: {rc_path.resolve().as_posix()}")
    if not rc_path.is_file():
        print(f"ger bash-completion: rc file does not exist: {rc_path}", file=sys.stderr)
        return 1
    before = rc_path.read_text(encoding="utf-8")
    if not _has_marked_block(before):
        print(
            f"ger bash-completion: no gerrit-workflow-tools completion block found in {rc_path}",
            file=sys.stderr,
        )
        return 1
    after = _strip_marked_blocks(before)
    log("Removing marked completion block from file.")
    rc_path.write_text(after, encoding="utf-8")
    log(f"Updated file ({len(before)} → {len(after)} bytes).")
    log("Done. Restart the shell for the change to take effect.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Print, install, or uninstall shell completion bootstrap lines for ``ger``."""

    parser = argparse.ArgumentParser(
        prog="ger bash-completion",
        description="Show the bash line to source tab-completion for ger, or install/uninstall it in a shell rc file.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Append a marked block that sources the completion script to the rc file (default: ~/.bashrc).",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the marked completion block from the rc file.",
    )
    parser.add_argument(
        "--rc-file",
        type=Path,
        metavar="PATH",
        help="Rc file to modify for --install / --uninstall (default: ~/.bashrc).",
    )
    ns = parser.parse_args(argv)

    if ns.install and ns.uninstall:
        parser.error("cannot combine --install and --uninstall")

    rc_path = (ns.rc_file or Path.home() / ".bashrc").expanduser()

    if not ns.install and not ns.uninstall:
        try:
            install_hint = ' && echo "Note: You can also install the completion with `ger bash-completion --install`'
            print(source_command_line() + install_hint)
        except FileNotFoundError as e:
            print(f"ger bash-completion: {e}", file=sys.stderr)
            return 1
        return 0

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)

    if ns.install:
        return _install_completion_block(rc_path, log)

    if ns.uninstall:
        return _uninstall_completion_block(rc_path, log)

    return 0

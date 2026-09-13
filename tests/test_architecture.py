"""Architecture guardrails: layer direction, one owner per concept, size ratchets.

Every rule carries a table of known exceptions. Each entry is today's debt and names the
item in ``docu/architecture.md`` § Consolidation backlog that retires it. The tables only
shrink: a rule goes red on a new violation *and* on an entry that no longer occurs, so
fixing the debt means deleting its entry here.

Adding an entry is a design decision for a human, not a way to get a change through.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from gerrit_workflow_tools.cli_ger import _COMMANDS


PKG = "gerrit_workflow_tools"
PKG_DIR = Path(__file__).resolve().parents[1] / "src" / PKG


def _modules() -> dict[str, Path]:
    """Package-relative path (``core/stack.py``) → file, for every module in the package."""
    return {p.relative_to(PKG_DIR).as_posix(): p for p in sorted(PKG_DIR.rglob("*.py"))}


MODULES = _modules()
SOURCES = {rel: p.read_text(encoding="utf-8") for rel, p in MODULES.items()}


def _rel_for_dotted(dotted: str) -> str | None:
    if dotted != PKG and not dotted.startswith(PKG + "."):
        return None
    parts = dotted.split(".")[1:]
    for candidate in ("/".join(parts) + ".py", "/".join([*parts, "__init__.py"])):
        if candidate in MODULES:
            return candidate
    return None


@dataclass(frozen=True)
class Import:
    importer: str
    target: str
    name: str | None = None


def _imports() -> list[Import]:
    """Every first-party import, including lazy ones inside functions and TYPE_CHECKING blocks."""
    found: list[Import] = []
    for rel, src in SOURCES.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = _rel_for_dotted(alias.name)
                    if target:
                        found.append(Import(rel, target))
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                base = _rel_for_dotted(node.module)
                if base is None:
                    continue
                for alias in node.names:
                    submodule = _rel_for_dotted(f"{node.module}.{alias.name}")
                    if submodule:
                        found.append(Import(rel, submodule))
                    else:
                        found.append(Import(rel, base, alias.name))
    return found


IMPORTS = _imports()

COMMAND_MODULES = {_rel_for_dotted(path.partition(":")[0]) for _desc, path in _COMMANDS.values()}


def _check(rule: str, found: set[str], debt: dict[str, str], fix: str) -> None:
    """Fail on anything in *found* that is not debt, and on debt that is no longer found."""
    new = sorted(found - debt.keys())
    stale = sorted(debt.keys() - found)
    lines: list[str] = []
    if new:
        lines.append(f"{rule} — new violation(s):")
        lines += [f"  {v}" for v in new]
        lines.append(f"  Fix: {fix}")
    if stale:
        lines.append(f"{rule} — exception(s) no longer needed; delete them from {Path(__file__).name}:")
        lines += [f"  {v}" for v in stale]
    assert not lines, "\n".join(lines)


# --- Layer direction ---------------------------------------------------------------


def test_core_imports_only_core() -> None:
    found = {
        f"{i.importer} -> {i.target}"
        for i in IMPORTS
        if i.importer.startswith("core/") and not i.target.startswith("core/") and i.target != "__init__.py"
    }
    _check(
        "core/ imports outside core/",
        found,
        {},
        "core never depends on commands, rendering or prompts. Move the needed logic into core/, "
        "or return data and let the caller render it.",
    )


# The bottom of core: every other module may use these, they use nothing above them.
SUBSTRATE = {
    "core/call_trace.py",
    "core/change_id.py",
    "core/changeish.py",
    "core/config.py",
    "core/git_run.py",
}


def test_substrate_stays_at_the_bottom() -> None:
    found = {
        f"{i.importer} -> {i.target}"
        for i in IMPORTS
        if i.importer in SUBSTRATE and i.target not in SUBSTRATE and i.target != "__init__.py"
    }
    _check(
        "core substrate imports upward",
        found,
        {
            # Backlog: "Cache-clear hub" — clear_git_cache reaches up to clear caches it does not own.
            "core/git_run.py -> core/git_state.py": "cache-clear hub",
            "core/git_run.py -> core/gerrit/change_resolution.py": "cache-clear hub",
        },
        f"{', '.join(sorted(SUBSTRATE))} must not import anything above them.",
    )


def test_render_does_not_import_cli() -> None:
    found = {
        f"{i.importer} -> {i.target}"
        for i in IMPORTS
        if i.importer.startswith("render/") and i.target.startswith("cli_")
    }
    _check(
        "render/ imports the CLI layer",
        found,
        {
            # Backlog: "Styling below the CLI" — ANSI helpers live in cli_style.py.
            "render/comments.py -> cli_style.py": "styling below the CLI",
            "render/commit_row.py -> cli_style.py": "styling below the CLI",
        },
        "render/ formats domain objects; it must not reach into cli_*.",
    )


def test_command_modules_are_leaves() -> None:
    """Nothing imports a command module except the dispatcher and the completion generator."""
    importers_by_design = {"cli_ger.py", "bash_completion_generator.py"}
    found = {
        f"{i.importer} -> {i.target}"
        for i in IMPORTS
        if i.target in COMMAND_MODULES and i.importer not in importers_by_design and i.importer != i.target
    }
    _check(
        "command module imported by another module",
        found,
        {},
        "Behaviour two commands share belongs in core/ (logic), render/ (output) or cli_common.py (runtime).",
    )


def test_no_private_names_imported_across_modules() -> None:
    found = {
        f"{i.importer} -> {i.target}:{i.name}"
        for i in IMPORTS
        if i.name and i.name.startswith("_") and not i.name.startswith("__")
    }
    _check(
        "private name imported from another module",
        found,
        {
            # By design until cli_ger exposes a public registry accessor.
            "bash_completion_generator.py -> cli_ger.py:_ALIASES": "command registry",
            "bash_completion_generator.py -> cli_ger.py:_COMMANDS": "command registry",
            # Backlog: "One commit reader".
            "cli_sha.py -> core/stack.py:_parse_rs_metadata_records": "one commit reader",
        },
        "Promote the helper to a public name in its owning module (move it down a layer if needed). Never copy it.",
    )


# --- One owner per concept ---------------------------------------------------------


@dataclass(frozen=True)
class Concept:
    name: str
    pattern: str
    owners: dict[str, str]
    debt: dict[str, str] = field(default_factory=dict)
    scope: str = ""
    fix: str = ""


CONCEPTS = [
    Concept(
        name="git log record separator (%x1e)",
        pattern=r"x1e",
        owners={"core/stack.py": "reads commit rows"},
        debt={
            "cli_changeid.py": "one commit reader",
            "cli_sha.py": "one commit reader",
            "rebase_enricher.py": "one commit reader",
        },
        fix="Read commit rows through core/stack.py instead of formatting and splitting `git log` yourself.",
    ),
    Concept(
        name="subprocess",
        pattern=r"\bsubprocess\.(run|Popen|call|check_call|check_output)\(",
        owners={
            "core/git_run.py": "runs git",
            "rebase_enricher.py": "opens the user's editor (not git)",
        },
        debt={
            "cli_edit.py": "one git runner: interactive `git rebase -i`",
            "cli_rebase.py": "one git runner: interactive `git rebase -i`",
            "cli_push.py": "one git runner: `git push` bytes output",
            "core/gerrit_change_status.py": "one git runner: `git patch-id` needs stdin",
        },
        fix="Run git through core/git_run.py; extend git_run if it lacks what you need.",
    ),
    Concept(
        name="HttpGerritRest construction",
        pattern=r"HttpGerritRest(\.from_settings)?\(",
        owners={
            "core/gerrit/rest.py": "defines it",
            "core/gerrit/service.py": "GerritService.from_cwd is the construction path",
            "core/gerrit/change_resolution.py": "built lazily so local-only resolution never needs gerrit.webUrl",
            "cli_fetch_api.py": "GETs raw paths from the real server by purpose",
            "reviewer_catalog.py": "background worker in the push prompt (ADR-0006)",
        },
        fix="Reach Gerrit through GerritService.from_cwd(...).",
    ),
    Concept(
        name="y/n confirmation parsing",
        pattern=r"""in \(\s*["']y["']|== ["']y["']|\[[yY]/[nN]\]""",
        owners={},
        debt={
            "cli_fix.py": "one confirmation prompt",
            "cli_push.py": "one confirmation prompt",
        },
        fix="Retire the existing copies into one shared prompt first; see the backlog item.",
    ),
]


def test_each_concept_has_one_owner() -> None:
    failures: list[str] = []
    for concept in CONCEPTS:
        rx = re.compile(concept.pattern)
        found = {
            rel
            for rel, src in SOURCES.items()
            if rel.startswith(concept.scope) and rel not in concept.owners and rx.search(src)
        }
        try:
            _check(
                concept.name, found, concept.debt, f"{concept.fix} Owners: {', '.join(concept.owners) or 'none yet'}."
            )
        except AssertionError as e:
            failures.append(str(e))
    assert not failures, "\n\n".join(failures)


def _is_terminal_io(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in ("print", "input")
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "write"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr in ("stdout", "stderr")
    )


def test_core_has_no_terminal_io() -> None:
    found = {
        rel
        for rel, src in SOURCES.items()
        if rel.startswith("core/")
        and any(isinstance(n, ast.Call) and _is_terminal_io(n) for n in ast.walk(ast.parse(src)))
    }
    _check(
        "core/ prints or prompts",
        found,
        {
            # Backlog: "Core that talks to the terminal".
            "core/call_trace.py": "core that talks to the terminal",
            "core/upstream_interactive.py": "core that talks to the terminal",
        },
        "core never prints or prompts. Return data or raise; the command decides what to show or ask.",
    )


# --- Every command uses the shared runtime -----------------------------------------


def test_commands_use_shared_runtime() -> None:
    """AGENTS.md: build the runtime with init_cli_runtime, wrap the body in run_cli_command."""
    found = set()
    for rel in sorted(m for m in COMMAND_MODULES if m):
        for helper in ("init_cli_runtime(", "run_cli_command("):
            if helper not in SOURCES[rel]:
                found.add(f"{rel} lacks {helper[:-1]}")
    # Backlog: "One command entry point".
    debt = dict.fromkeys(
        [
            "cli_bash_completion.py lacks init_cli_runtime",
            "cli_bash_completion.py lacks run_cli_command",
            "cli_cache.py lacks run_cli_command",
            "cli_changeid.py lacks init_cli_runtime",
            "cli_changeid.py lacks run_cli_command",
            "cli_edit.py lacks init_cli_runtime",
            "cli_fetch_api.py lacks init_cli_runtime",
            "cli_fetch_api.py lacks run_cli_command",
            "cli_fix.py lacks init_cli_runtime",
            "cli_push.py lacks run_cli_command",
            "cli_rebase.py lacks init_cli_runtime",
            "cli_setup.py lacks init_cli_runtime",
            "cli_setup.py lacks run_cli_command",
            "cli_sha.py lacks init_cli_runtime",
            "cli_sha.py lacks run_cli_command",
        ],
        "one command entry point",
    )
    _check(
        "command bypasses the shared runtime",
        found,
        debt,
        "Call cli_common.init_cli_runtime and wrap the body in cli_common.run_cli_command.",
    )


# --- Ratchets ----------------------------------------------------------------------

MAX_LINES = 400

# Files already over MAX_LINES, pinned at their size. They may only shrink; lower the
# number when they do. A file here that must grow needs an extraction commit first.
SIZE_BASELINE = {
    "cli_changeid.py": 408,
    "cli_push.py": 1364,
    "cli_show.py": 495,
    "core/gerrit/cache.py": 819,
    "core/gerrit/change_resolution.py": 648,
    "core/gerrit/rest.py": 968,
    "core/gerrit/service.py": 692,
    "core/gerrit_change_status.py": 503,
    "core/git_state.py": 454,
    "core/review_chain.py": 573,
    "push_input_line.py": 442,
    "reviewer_catalog.py": 478,
}


def test_module_size_ratchet() -> None:
    problems: list[str] = []
    for rel, src in SOURCES.items():
        n = len(src.splitlines())
        pinned = SIZE_BASELINE.get(rel)
        if pinned is None and n > MAX_LINES:
            problems.append(f"{rel}: {n} lines > {MAX_LINES}. Split it along a concept boundary before adding more.")
        elif pinned is not None and n > pinned:
            problems.append(
                f"{rel}: grew to {n} lines (pinned at {pinned}). Make room first: extract a concept "
                f"into its owner in a separate refactor commit, then add your change."
            )
        elif pinned is not None and n < pinned:
            target = "delete its entry" if n <= MAX_LINES else f"lower its pin to {n}"
            problems.append(f"{rel}: shrank to {n} lines — {target} in SIZE_BASELINE.")
    problems += [f"{rel}: in SIZE_BASELINE but no longer exists." for rel in SIZE_BASELINE if rel not in SOURCES]
    assert not problems, "\n".join(problems)


# ``# pylint: disable=too-many-*`` silences the signal that a function should be split.
# Counts per file may only go down.
TOO_MANY_BASELINE = {
    "cli_edit.py": 1,
    "cli_log.py": 4,
    "cli_push.py": 5,
    "cli_sha.py": 2,
    "cli_show.py": 3,
    "core/gerrit/service.py": 1,
    "core/gerrit_change_status.py": 2,
    "core/git_state.py": 1,
    "core/ready_calc.py": 1,
    "core/review_chain.py": 7,
    "push_input_line.py": 3,
    "rebase_enricher.py": 2,
}

_TOO_MANY = re.compile(r"pylint:\s*disable=([^\n]*)")


def test_too_many_disables_ratchet() -> None:
    problems: list[str] = []
    for rel, src in SOURCES.items():
        n = sum(len(re.findall(r"too-many-", m)) for m in _TOO_MANY.findall(src))
        pinned = TOO_MANY_BASELINE.get(rel, 0)
        if n > pinned:
            problems.append(
                f"{rel}: {n} `too-many-*` disables (pinned at {pinned}). "
                "Split the function instead of silencing pylint."
            )
        elif n < pinned:
            target = "delete its entry" if n == 0 else f"lower its pin to {n}"
            problems.append(f"{rel}: down to {n} `too-many-*` disables — {target} in TOO_MANY_BASELINE.")
    assert not problems, "\n".join(problems)

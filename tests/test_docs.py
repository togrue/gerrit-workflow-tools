"""Doc guardrails: the docs agents read must describe the code that exists.

- Every relative link and ``#anchor`` resolves.
- Every code identifier in backticks names something that exists in ``src/`` or ``tests/``.
- The module catalog in ``docu/architecture.md`` lists exactly the modules in the package.
- The command registry in ``docu/SPEC.md`` lists exactly the registered commands.

ADRs (``docu/adr/``) are exempt from the identifier check: they record decisions and name
rejected or since-renamed things on purpose. ``docu/gerrit/`` is upstream Gerrit reference.
"""

from __future__ import annotations

import re
from pathlib import Path

from gerrit_workflow_tools.cli_ger import _COMMANDS


REPO = Path(__file__).resolve().parents[1]
PKG_DIR = REPO / "src" / "gerrit_workflow_tools"


def _living_docs() -> list[Path]:
    docs = [REPO / name for name in ("AGENTS.md", "CLAUDE.md", "CONTEXT.md", "README.md")]
    for root in ("docu", "tests", "contrib"):
        docs += sorted((REPO / root).rglob("*.md"))
    return [p for p in docs if p.is_file() and "docu/gerrit/" not in p.relative_to(REPO).as_posix()]


DOCS = _living_docs()

_FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _prose(text: str) -> str:
    """Text outside fenced code blocks."""
    return _FENCE.sub("", text)


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


# --- Links -------------------------------------------------------------------------


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(doc: Path) -> set[str]:
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for line in _prose(doc.read_text(encoding="utf-8")).splitlines():
        m = re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        slug = _slug(m.group(1))
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        anchors.add(slug if n == 0 else f"{slug}-{n}")
    return anchors


def test_relative_links_resolve() -> None:
    broken: list[str] = []
    for doc in DOCS:
        prose = _CODE_SPAN.sub("", _prose(doc.read_text(encoding="utf-8")))
        for target in _LINK.findall(prose):
            if re.match(r"[a-z]+:", target):
                continue
            path_part, _, anchor = target.partition("#")
            dest = (doc.parent / path_part).resolve() if path_part else doc
            if not dest.exists():
                broken.append(f"{_rel(doc)}: link to missing {target}")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
                broken.append(f"{_rel(doc)}: link to missing anchor {target}")
    assert not broken, "Broken doc links — fix the link or remove it:\n" + "\n".join(broken)


# --- Identifiers -------------------------------------------------------------------

_CODE_TEXT = "\n".join(
    p.read_text(encoding="utf-8", errors="ignore")
    for root in ("src", "tests", "contrib", "scripts")
    for p in (REPO / root).rglob("*")
    if p.is_file() and p.suffix in {".py", ".sh", ".bash", ""} and "__pycache__" not in p.parts
)
_REPO_FILES = {
    _rel(p)
    for root in ("src", "tests", "docu", "scripts", "contrib", ".github")
    for p in (REPO / root).rglob("*")
    if "__pycache__" not in p.parts
} | {p.name for p in REPO.iterdir()}

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
# Dotted tokens with these roots are git config keys or git refs, not code.
_NOT_CODE_ROOTS = {"gerrit", "branch", "user", "remote", "core", "ger", "init", "http", "url", "origin"}
# Words that look like identifiers in prose but are not meant to name code.
_ALLOWED = {"_private", "__init__"}
_FILE_SUFFIXES = (".py", ".md", ".sh", ".toml", ".yml", ".yaml", ".json", ".mdc")


def _looks_like_code(tok: str) -> bool:
    return "_" in tok or bool(re.search(r"[a-z][A-Z]", tok))


def _path_exists(tok: str, doc: Path) -> bool:
    tok = tok.rstrip("/")
    for base in (REPO, doc.parent, PKG_DIR):
        if (base / tok).exists():
            return True
    return any(f == tok or f.endswith("/" + tok) for f in _REPO_FILES)


def _stale_tokens(doc: Path) -> list[str]:
    stale: list[str] = []
    for tok in _CODE_SPAN.findall(_prose(doc.read_text(encoding="utf-8"))):
        tok = tok.strip()
        if tok.endswith(_FILE_SUFFIXES) and re.fullmatch(r"[\w./-]+", tok) and not tok.startswith((".ger", "~")):
            if not _path_exists(tok, doc):
                stale.append(f"{_rel(doc)}: `{tok}` — no such file")
            continue
        bare = tok.removesuffix("()")
        if not _IDENT.fullmatch(bare) or bare in _ALLOWED or not _looks_like_code(bare):
            continue
        parts = bare.split(".")
        if len(parts) > 1 and parts[0].lower() in _NOT_CODE_ROOTS:
            continue
        if not re.search(rf"\b{re.escape(parts[-1])}\b", _CODE_TEXT):
            stale.append(f"{_rel(doc)}: `{tok}` — not found in src/ or tests/")
    return stale


def test_code_identifiers_in_docs_exist() -> None:
    stale = [s for doc in DOCS if "docu/adr/" not in _rel(doc) for s in _stale_tokens(doc)]
    assert not stale, (
        "Docs name code that does not exist. Update the doc to the current name, "
        "or delete the sentence if the thing is gone:\n" + "\n".join(stale)
    )


# --- Catalogs ----------------------------------------------------------------------


def _section(doc: Path, heading: str) -> str:
    text = doc.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, f"{_rel(doc)} has no '## {heading}' section"
    return m.group(1)


def test_module_catalog_lists_every_module() -> None:
    catalog = _section(REPO / "docu" / "architecture.md", "Module catalog")
    listed = set(re.findall(r"^\|\s*`([^`]+\.py)`", catalog, re.M))
    actual = {p.relative_to(PKG_DIR).as_posix() for p in PKG_DIR.rglob("*.py") if p.name != "__init__.py"}
    missing = sorted(actual - listed)
    unknown = sorted(listed - actual)
    assert not (missing or unknown), (
        "docu/architecture.md § Module catalog must list every module by package-relative path "
        "(e.g. `core/stack.py`) with the concept it owns.\n"
        + "".join(f"  missing: {m}\n" for m in missing)
        + "".join(f"  no such module: {u}\n" for u in unknown)
    )


def test_spec_registry_matches_registered_commands() -> None:
    registry = _section(REPO / "docu" / "SPEC.md", "Command registry")
    listed = set(re.findall(r"^\|\s*`ger ([a-z-]+)`", registry, re.M))
    registered = set(_COMMANDS)
    assert listed == registered, (
        "docu/SPEC.md § Command registry must list exactly the commands in cli_ger._COMMANDS.\n"
        f"  registered but not listed: {sorted(registered - listed)}\n"
        f"  listed but not registered: {sorted(listed - registered)}"
    )

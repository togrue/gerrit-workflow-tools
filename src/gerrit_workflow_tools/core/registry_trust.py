"""Whether a project-local extension registry may run: the trust gate for ``<scriptsDir>/<domain>/``.

A registry in the work tree is code that arrives with a checkout or a clone, so ger does not run
it just because it is there (git does not clone hooks for the same reason). It runs when either:

1. **The scripts dir is configured where a checkout cannot write.** ``gerrit.scriptsDir`` is set in
   the repository's own git config (local or worktree scope, or ``git -c``), which is never cloned;
   or it is set in global or system config and names a directory outside the work tree.
2. **The user trusted these exact contents.** The :func:`source_digest` of the registry directory's
   Python files is recorded in :func:`trust_store_path`. Any change to them changes the digest, so a
   changed registry needs trust again. Equal contents are trusted wherever they are (every worktree).

Core only decides; it never asks. The CLI asks before a command runs (``registry_trust_prompt``)
and records the answer with :func:`record_trust`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from gerrit_workflow_tools.core.config import ConfigScope, Settings
from gerrit_workflow_tools.core.gerrit.paths import xdg_config_home


logger = logging.getLogger(__name__)

EXTENSION_DOMAINS = ("ci", "ready", "attention", "reviewers")
"""Every ``<scriptsDir>/<domain>/`` a command may load a registry from."""

SCRIPTS_DIR_KEY = "gerrit.scriptsDir"

_UNCLONED_SCOPES = (ConfigScope.LOCAL, ConfigScope.WORKTREE, ConfigScope.COMMAND)
_STORE_VERSION = 1


def source_digest(paths: Iterable[Path]) -> str:
    """One digest over the names and contents of *paths*, in sorted order; unreadable files count by name."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            content = path.read_bytes()
        except OSError:
            content = b""
        digest.update(path.name.encode("utf-8") + b"\0" + content + b"\0")
    return digest.hexdigest()


def registry_source_digest(domain_dir: Path) -> str:
    """The :func:`source_digest` of every Python file under *domain_dir*: the code a load runs."""
    return source_digest(p for p in domain_dir.rglob("*.py") if "__pycache__" not in p.parts)


def resolve_scripts_root(root: Path | None, scripts_dir: str) -> Path | None:
    """Resolve ``gerrit.scriptsDir`` to an absolute path (may not exist yet).

    A relative directory is relative to *root*, the repository's top level; there is none
    outside a work tree.
    """
    raw = scripts_dir.strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    if root is None:
        return None
    return (root / path).resolve()


def local_domain_dir(root: Path | None, scripts_dir: str, domain: str) -> Path | None:
    """Return ``<scriptsRoot>/<domain>`` when ``registry.py`` exists there."""
    scripts_root = resolve_scripts_root(root, scripts_dir)
    if scripts_root is None:
        return None
    path = scripts_root / domain
    return path if (path / "registry.py").is_file() else None


@dataclass(frozen=True)
class LocalRegistry:
    """A project-local ``registry.py`` and the digest of the code it would run."""

    domain: str
    directory: Path
    digest: str

    @property
    def path(self) -> Path:
        return self.directory / "registry.py"


def scripts_dir_configured(root: Path | None, settings: Settings) -> bool:
    """Whether ``gerrit.scriptsDir`` comes from config a checkout cannot write (see module doc, rule 1)."""
    scope = settings.scope(SCRIPTS_DIR_KEY)
    if scope in _UNCLONED_SCOPES:
        return True
    if scope not in (ConfigScope.GLOBAL, ConfigScope.SYSTEM):
        return False
    scripts_root = resolve_scripts_root(root, settings.scripts_dir)
    return scripts_root is not None and (root is None or not scripts_root.is_relative_to(root.resolve()))


def trust_store_path() -> Path:
    """``$XDG_CONFIG_HOME/ger/trusted-registries.json``: the digests the user said may run."""
    return xdg_config_home() / "ger" / "trusted-registries.json"


def _read_store() -> dict[str, dict[str, str]]:
    path = trust_store_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("ignoring unreadable %s: %s", path, exc)
        return {}
    trusted = raw.get("trusted") if isinstance(raw, dict) else None
    return {k: v for k, v in trusted.items() if isinstance(v, dict)} if isinstance(trusted, dict) else {}


def digest_trusted(digest: str) -> bool:
    """Whether the user trusted registry code with this :func:`registry_source_digest`."""
    return digest in _read_store()


def record_trust(registry: LocalRegistry) -> None:
    """Record that *registry*'s current contents may run, here and wherever they appear again."""
    store = _read_store()
    store[registry.digest] = {"path": str(registry.path), "domain": registry.domain}
    path = trust_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".trusted-registries-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"version": _STORE_VERSION, "trusted": store}, out, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def local_registry_trusted(root: Path | None, settings: Settings, digest: str) -> bool:
    """Whether a project-local registry whose code has *digest* may run."""
    return scripts_dir_configured(root, settings) or digest_trusted(digest)


def untrusted_local_registries(root: Path | None, settings: Settings) -> list[LocalRegistry]:
    """The project-local registries present under the scripts dir that may not run as they are."""
    if scripts_dir_configured(root, settings):
        return []
    found = []
    for domain in EXTENSION_DOMAINS:
        domain_dir = local_domain_dir(root, settings.scripts_dir, domain)
        if domain_dir is None:
            continue
        digest = registry_source_digest(domain_dir)
        if not digest_trusted(digest):
            found.append(LocalRegistry(domain, domain_dir, digest))
    return found


def untrusted_hint() -> str:
    """How to let an untrusted registry run: the same words wherever ger reports one."""
    return (
        "run a ger command in a terminal to be asked, or set "
        f"`git config {SCRIPTS_DIR_KEY} <dir>` in this repository to trust its registries"
    )

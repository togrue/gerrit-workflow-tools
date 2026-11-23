"""Ask, before a command runs, whether this project's untrusted ``registry.py`` files may run.

The trust model is :mod:`gerrit_workflow_tools.core.registry_trust`; this is its only prompt. A
command that may not ask (no terminal, ``--json``, ``--ai``) runs without the registry and says so.
``GER_ASSUME_YES`` does not answer this question: it exists for agents, which must not trust a
checked-out change's code on the user's behalf.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from gerrit_workflow_tools.core.registry_trust import record_trust, untrusted_hint, untrusted_local_registries
from gerrit_workflow_tools.core.run_context import RunContext


def settle_registry_trust(run: RunContext, *, confirm: Callable[..., bool] | None) -> None:
    """Ask each untrusted project-local registry with *confirm* (``None``: may not ask); warn about the rest."""
    root = run.repo.root if run.settings.in_repository else None
    for registry in untrusted_local_registries(root, run.settings):
        if confirm is not None:
            print(
                f"{registry.path} is code from this project. Running it runs it as you, "
                "with access to your Gerrit credentials.",
                file=sys.stderr,
            )
            if confirm("Trust it and run it (asked again if it changes)?", default=False):
                record_trust(registry)
                continue
        print(f"warning: not running untrusted {registry.path}; {untrusted_hint()}", file=sys.stderr)

# Example CI link strategy for ger

Drop (or copy) this folder to ``.ger/ci/`` in a Gerrit-backed clone when you need
**project-specific** CI URL transforms. ``ger log`` loads ``registry.py`` when
Verified is −1 and runs the strategy for the current ``gerrit.project``.

**Built-in:** Jenkins ``Build Failed`` / Checks URLs are parsed without a local
registry. See ``docu/gerrit-ci-strategies.md`` for message formats and how to
write custom parsers.

See ``registry.py`` for the ``extract_ci_links`` signature and Checks-first policy.

# Example CI link strategy for ger

Drop (or copy) this folder to ``.ger/ci/`` in a Gerrit-backed clone. ``ger log`` loads
``registry.py`` when Verified is −1 and runs the strategy for the current
``gerrit.project``.

See ``registry.py`` for the ``extract_ci_links`` signature and Checks-first policy.

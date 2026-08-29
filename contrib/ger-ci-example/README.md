# Example CI link strategy for ger

**Skip this directory** if built-in Jenkins / Checks parsing already gives you the
URLs you want (most repos).

When you need a custom transform, copy this folder to ``.ger/ci/`` in a
Gerrit-backed clone and edit ``STRATEGIES`` keys to match ``gerrit.project``.

The example rewrites built-in console links to Jenkins **test report** URLs.
See ``docu/gerrit-ci-strategies.md`` for the extension template and lazy-`messages`
behaviour.

# Example `.ger` hooks

Copy the domain directories you need into a Gerrit-backed clone as
`.ger/<domain>/` (or into `~/.config/ger/<host>/<domain>/` for a
host-wide install). Edit `STRATEGIES` keys to match `gerrit.project`.

Built-in Jenkins / Checks behaviour needs **no** registry. Use these
examples only when you customize link discovery or log parsing.

| Directory | Copy to | What it shows |
|-----------|---------|---------------|
| [`ci/`](ci/) | `.ger/ci/` | Custom **CI strategy** (link rewrite) and a **CI content provider** |

Other domains (`ready/`, `attention/`, `reviewers/`) use the same
`registry.py` + `STRATEGIES` shape — see
[Configuration.md § Extension scripts](../../docu/Configuration.md#extension-scripts-ger-and-configger).

Details for the CI seam: [gerrit-ci-strategies.md](../../docu/gerrit-ci-strategies.md).

# `ger bash-completion`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_bash_completion.py` |
| **Requires** | Bash |

Print, install, or uninstall tab-completion for `ger` subcommands.

---

## Usage

```
ger bash-completion [--install | --uninstall] [--rc-file PATH]
```

| Flag | Action |
|------|--------|
| (none) | Print `source "…/ger.bash"` line |
| `--install` | Append marked block to rc file (default `~/.bashrc`) |
| `--uninstall` | Remove marked block |
| `--rc-file PATH` | Target rc file for install/uninstall |

Script: `contrib/completion/ger.bash` (also installed with package).

---

## V1 scope delta

Documented in [Version 1 Scope](../../Version%201%20Scope.md) onboarding: recommended in setup README.

Operational guide: [Completion.md](../../Completion.md).

---

## See also

- [SPEC.md](../../SPEC.md)

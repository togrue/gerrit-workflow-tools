# Bash completion

Bash tab-completes `ger` and its subcommands via a bundled `ger.bash` script; run `ger bash-completion` to print the `source` line, or `ger bash-completion --install` to append that line to `~/.bashrc` (and `--uninstall` to remove it).

## Quick setup

After `pip install`, run:

```bash
ger bash-completion
```

That prints the exact `source "…/ger.bash"` line for your install. To append that line to `~/.bashrc` automatically (with log messages on stderr describing each step):

```bash
ger bash-completion --install
```

Use `ger bash-completion --uninstall` to remove the marked block from the same file. Override the file with `--rc-file PATH` (defaults to `~/.bashrc`).

## Manual install

1. Install the package so `ger` is on your `PATH`.
2. Source the completion script from `~/.bashrc` (or another shell startup file):

```bash
source /path/to/workflow-optimization/contrib/completion/ger.bash
```

After `pip install`, the same file is also available under the installed package:

```
site-packages/gerrit_workflow_tools/completion/ger.bash
```

Example using `python -c` to locate it:

```bash
_pysite="$(python -c 'import gerrit_workflow_tools, pathlib; print(pathlib.Path(gerrit_workflow_tools.__file__).parent)')"
source "$_pysite/completion/ger.bash"
```

## Notes

- Revision arguments (where applicable) use `__git_complete_refs` when Git’s bash completion is loaded.
- The `ger` executable (`ger.exe` on Windows) uses the same completion function.

# Bash completion

Tab completion for `ger` and its subcommands (`ger push`, `ger log`, …) is provided by a small bash script.

## Install

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

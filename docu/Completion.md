# Bash completion

Tab completion for `git gpush`, `git glog`, and the other wrappers is provided by a small bash script.

## Requirements

- **Bash** (Git Bash on Windows is supported).
- Git’s own bash completion must be loaded first so `__git_complete` exists (common on Linux; on macOS install via Homebrew `bash-completion@2`; Git for Windows often ships completion under `/usr/share/git/completion`).

## Install from a git clone

```bash
source /path/to/workflow-optimization/contrib/completion/git-gerrit-workflow-tools.bash
```

Add that line to `~/.bashrc` (or `~/.bash_profile`) after sourcing git’s completion.

## Install after `pip install`

The same file is shipped inside the wheel at:

`site-packages/gerrit_workflow_tools/completion/git-gerrit-workflow-tools.bash`

Example:

```bash
_pysite=$(python -c "import gerrit_workflow_tools, pathlib; print(pathlib.Path(gerrit_workflow_tools.__file__).parent)")
source "$_pysite/completion/git-gerrit-workflow-tools.bash"
```

## What gets completed

- After `git <cmd>`, long options (`--dry-run`, `--show-attributes`, …) for each tool.
- Optional revision arguments use `__git_complete_refs` when Git’s completion provides it.
- Standalone executables (`git-gpush`, …, including `git-gpush.exe` on Windows) use the same logic via a small wrapper.

If `__git_complete` is missing, the script still registers `complete` for `git-*` launchers only.

## See also

- [Documentation index](README.md)

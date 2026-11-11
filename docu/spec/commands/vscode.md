# `ger vscode`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_vscode.py` |
| **Requires** | A ger installed from a release; the editor's command-line program (`code`) |

Install the VS Code extension that came with this ger into your editor.

A release of ger carries the extension built from the same commit, so the extension an editor
gets always matches the `ger serve` it starts. Works outside a git repository.

---

## Usage

```
ger vscode install [--editor COMMAND]
```

| Flag | Effect |
|------|--------|
| (none) | Install into the first of `code`, `code-insiders`, `codium`, `cursor` found on `PATH` |
| `--editor COMMAND` | The editor's command-line program, by name or path |

It runs `<editor> --install-extension <bundled .vsix> --force`; the editor prints the result, and
its exit code is the command's. `--force` makes the same command the update: run it again after
updating ger.

## Refusals

Both exit `2` ([exit codes](../exit-codes.md)):

| Situation | Message says |
|-----------|--------------|
| No editor command on `PATH`, or `--editor` names none | which names were tried, and where the bundled `.vsix` is, for *Extensions: Install from VSIX…* |
| ger was installed from source (no bundled extension) | where the release of ger and the `.vsix` of this version are |

## How a release is built

`python scripts/build_release.py` packages the extension, puts it inside the Python package
(`gerrit_workflow_tools/vscode_extension/ger-vscode.vsix`), builds ger around it and writes
`dist/release/`. It refuses when `pyproject.toml`, `gerrit_workflow_tools/__init__.py` and
`vscode/package.json` state different versions. Pushing a tag `v<version>` runs it in
`.github/workflows/release.yml` and publishes the files as a GitHub release.

The extension goes the other way too: when it cannot find `ger`, *Install ger* installs the
release with the extension's own version ([vscode/README.md](../../../vscode/README.md)).

---

## See also

- [Getting-Started.md](../../Getting-Started.md)
- [serve.md](serve.md)

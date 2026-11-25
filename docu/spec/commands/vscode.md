# `ger vscode`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_vscode.py` |
| **Requires** | The editor's command-line program (`code`); for a ger installed from a checkout, npm |

Install the VS Code extension that goes with this ger into your editor.

A release of ger carries the extension built from the same commit, so the extension an editor
gets always matches the `ger serve` it starts. A ger installed from a checkout
(`uv tool install -e <clone>`) carries none; it builds one from the checkout's `vscode/` folder
with npm (`npm ci` when `node_modules/` is missing, then the same packaging a release runs) and
installs that. Works outside a git repository.

---

## Usage

```
ger vscode install [--editor COMMAND]
```

| Flag | Effect |
|------|--------|
| (none) | Install into the first of `code`, `code-insiders`, `codium`, `cursor` found on `PATH` |
| `--editor COMMAND` | The editor's command-line program, by name or path |

It runs `<editor> --install-extension <.vsix> --force`; the editor prints the result, and
its exit code is the command's. When the extension is built first, a failed npm step installs
nothing and its exit code is the command's. `--force` makes the same command the update: run it again after
updating ger.

## Refusals

All exit non-zero ([exit codes](../exit-codes.md)):

| Situation | Message says |
|-----------|--------------|
| No editor command on `PATH`, or `--editor` names none | which names were tried, and where the bundled `.vsix` is, for *Extensions: Install from VSIX…* |
| Building from a checkout, and no `npm` on `PATH` | that npm is needed, and which checkout it would build |
| Neither a bundled extension nor a checkout (installed from a git URL, say) | the releases page, and how to install ger from a clone instead |

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

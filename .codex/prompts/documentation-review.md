# Documentation review automation

Review the project documentation and verify that it accurately reflects the current repository.

## Scope

Focus on English-language documentation, including Markdown files, contributor guides, examples, tutorials, and documentation-adjacent files. Compare the documentation against the current codebase, configuration, public APIs, CLI behavior, examples, scripts, tests, and project structure.

Treat the current codebase, build configuration, tests, examples, and public CLI/API behavior as the source of truth. Do not change code to match documentation unless a human explicitly asks you to do so.

## Review criteria

Assess whether:

- The documentation reflects the current project accurately.
- Important concepts, setup steps, usage instructions, or workflow details are missing.
- Any sections create understanding gaps for new contributors or users.
- Any sections are unnecessarily verbose, repetitive, outdated, or misleading.
- The writing style matches high-quality software documentation: clear, concise, practical, well-structured, and easy to scan.
- Any text should be removed because it is obsolete, redundant, speculative, or not useful.

## Change policy

Use a moderate threshold for edits:

- Fix obvious inaccuracies, stale examples, broken links, and clearly confusing text.
- Improve structure, wording, missing explanations, and verbosity when the improvement is obvious and low-risk.
- Do not open changes for purely subjective style preferences.
- Do not invent behavior that is not supported by the repository.
- Do not add or retain implementation plans, roadmaps, version-scope trackers, or “planned/deferred” feature lists in user-facing documentation (root `README.md`, `docu/README.md`, `docu/SPEC.md`, `docu/architecture.md`, `docu/Configuration.md`, command specs under `docu/spec/commands/` for shipped commands, and similar). Keep those in separate internal docs only, without linking them from user docs.
- Keep changes focused and conservative.
- Preserve the project's existing documentation style unless it is clearly poor.
- Prefer concise explanations over long prose.
- Update examples only when the codebase clearly supports the change.
- Do not edit generated, vendored, archived, or third-party files if any are present.

## Checks

Run relevant checks when available. At minimum, inspect the resulting documentation diff and run `git diff --check` before finishing.

## Pull request expectations

If obvious improvements are found, edit the documentation directly. The workflow will create the pull request from your file changes.

If no obvious improvements are found, do not make changes.

When changes are made, your final response should include:

- A concise summary of what was outdated or unclear.
- The documentation changes made.
- Checks that were run.
- Any assumptions or areas that still need human review.

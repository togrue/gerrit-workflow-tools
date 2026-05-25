# TODO Before Release (Restructured, Current Open Work)

This file is the consolidated, cleaned backlog across prior TODO variants.
Implemented items were removed.

## Release Goal

Ship a safe, low-friction Gerrit CLI flow where:
- [x] `ger push` gives clear confirmation context
- [x] `ger log` quickly identifies what needs attention
- `ger show` gives clean, non-duplicated commit detail
- docs/completion/help match real behavior
- [x] Bash completion installation hint

## Open Work by Area

## 1) `ger push` safety and UX

### Must finish
- Submodule safety path (detect missing remote submodule commits; define push/fetch prompt flow).
- Better confirmation context (prominent target, reviewers, attribute deltas).

## 2) `ger log` actionable output

### Must finish (if in release scope)
- stronger summary/readiness indicators

### Must decide
- final color control model and consistency

## 3) `ger show` output quality
- Resolve any remaining duplicate detail output.
- Confirm final default output format for fast rework context.

## 4) `ger sha` scope boundary
- Decide whether patchset-resolution support is included now or explicitly deferred.
- Confirm expectations for branch visibility in all-refs mode.

## 5) Command surface and docs
- Align naming/help around canonical command forms.
- Ensure error remediation text points to the best setup command path.
- Keep completion options in sync with released flags.

## Inconsistencies / Decisions Needed

- **Deprecated flags:** decide removal timing vs one-release deprecation window.
- **Interactive push depth:** finalize MVP vs full editor workflow.
- **Scope of new commands:** confirm release status for `ger set-review`.

## Unstructured

## `ger log`

### Verbose output

For the @src/gerrit_workflow_tools/cli_log.py  command there should be the
`-v` option: prints urls and more detailed status information about commits that need attention.

## `ger push`

### Better confirmation output

Print the target branch, the reviewers, topic and wip status.

### Interactive push

### Assign reviewers interactively

1. Fetch the changes from gerrit (what they already have assigned)
2. Keep these reviewers
3. When no reviewers are assigned, set the default reviewers from the branch config
4. Push without reviewer arguments
5. User the gerrit rest api to assign the reviewers





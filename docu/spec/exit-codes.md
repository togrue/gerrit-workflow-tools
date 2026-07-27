# Exit codes

One code per failure **reason**, shared by every `ger` command. The same reason exits the same way whichever command hit it — so a script can branch on the code without knowing which command produced it.

Defined in `cli_common.ExitCode`; applied by `cli_common.run_cli_command`, which is the only place the error-to-code mapping lives.

| Code | Name | Meaning |
|------|------|---------|
| `0` | `OK` | Success |
| `1` | `ATTENTION` | Ran fine, but something wants you: unresolved comments, failed CI, or you declined a prompt. **Not a failure.** |
| `2` | `USAGE` | Bad arguments or invalid input format. Also what argparse itself exits with. |
| `3` | `NOT_FOUND` | A changeish or Change-Id resolved to nothing |
| `4` | `AMBIGUOUS` | Several candidates survived narrowing; `--json` lists them under `alternatives` |
| `5` | `GERRIT` | Gerrit answered badly or not at all: HTTP, auth, unreachable |
| `6` | `CONFIG` | Required git configuration is missing (`gerrit.webUrl`, credentials) |
| `7` | `GIT` | A git command failed |
| `8` | `DUPLICATE_CHANGE_ID` | The same Change-Id appears on more than one local commit |
| `9` | `MISSING_CHANGE_ID` | A local commit has no Change-Id footer |

## What is not remapped

**Child process codes pass through.** `ger edit` and `ger rebase` return `git rebase`'s exit code; `ger push` returns `git push`'s. Those are git's codes, not ours, and the wrapper leaves them alone — so a non-zero exit from those commands may be git's meaning rather than the table above.

**Unmapped exceptions are bugs.** `run_cli_command` catches only the failures in the table. Anything else surfaces as a traceback rather than a tidy exit code, deliberately: a crash that exits `1` is a crash you never fix.

## Attention is not failure

`ger log` and `ger show` exit `1` when a commit needs attention. That is the command working correctly and reporting a state, which is why it shares a code with "you said no at a prompt" rather than with the error codes. Scripts that treat any non-zero as failure will see attention as an error; branch on `1` explicitly.

## History

This replaces three separate and mutually contradictory tables. `3` used to mean "Gerrit API / git resolution error" for resolution commands but "duplicate Change-Id" for `ger sha`; `4` meant "ambiguous" in one and "git error" in the other; `ger change-id --check-duplicates` had a third scheme again. Codes for `ger sha` and `ger change-id` therefore **changed** when this table landed.

Related: [change-and-commit-identifiers.md](change-and-commit-identifiers.md) for how changeishes resolve, and what `NOT_FOUND` and `AMBIGUOUS` mean in that context.

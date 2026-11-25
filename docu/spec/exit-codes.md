# Exit codes

**`0`: the command completed. Non-zero: something went wrong running it.** That is the whole
contract, for every `ger` command. Which non-zero value a failure exits with is not part of it:
users and scripts should not rely on a specific value. Check for `0`, and read the message (or
`--json` where a command has it) for what went wrong.

Two things follow from "completed":

- **A reported state is not a failure.** A command that shows attention, a failed build or
  nothing left to update has done its job and exits `0` ([below](#attention-is-not-failure)).
- **Your own choice at a prompt is not a failure.** Answering no to a command's confirmation
  (`Publish …?`, `Do you want to run git push?`, the push options line) or cancelling it with
  Ctrl-C / Ctrl-D ends the command as you asked, with `cancelled` (or `Push cancelled.`) and
  exit `0`. A prompt for a value the command needs and cannot go on without (an upstream, the
  values `ger setup` asks for) left empty is different: the command could not run, and it
  exits non-zero.

A lookup that finds nothing exits non-zero: an unknown changeish, and an attention walk or a
review chain walk with nothing left to visit (`no commit needs edit attention…`, printed
without `error:`, since a script walking the stack stops on it).

## The codes, for reading

Failures exit with a code from `cli_common.ExitCode`, applied by `cli_common.run_cli_command`,
the only place errors map to codes. They name the kind of failure, which helps when reading a
log; they are not a promise.

| Code | Name | Meaning |
|------|------|---------|
| `0` | `OK` | Success |
| `1` | `REFUSED` | Did not go ahead: ger will not go on without your decision because your commits or branches are in the way (`ger review`, `ger checkout`; raised as `core.usage.Refusal`), or a value it needs was not given at its prompt |
| `2` | `USAGE` | The command cannot run as asked: bad arguments, an invalid input, an unknown command (`ger lgo` suggests `log`), or a state you fix before running it again (no terminal for a required prompt, a rebase in progress, changes in the way, a detached HEAD where the working branch is needed). Also what argparse itself exits with. Raised as `core.usage.UsageError` |
| `3` | `NOT_FOUND` | A changeish, Change-Id or git revision resolved to nothing |
| `4` | `AMBIGUOUS` | Several candidates survived narrowing; the message lists them as `cN (triplet, status)` (how ger prints a change, accepted back as a changeish), `--json` under `alternatives` |
| `5` | `GERRIT` | Gerrit answered badly or not at all: HTTP, auth, unreachable. Never reported as `NOT_FOUND`: only Gerrit's own 404 means "no such change". The message is one line ending in Gerrit's own reason from the response body (`Gerrit HTTP 400: nosuchuser does not identify a registered user or group`), else the HTTP status phrase. A command that can still show local state (`ger log`, `ger show`) shows it, with the Gerrit state unknown, then exits non-zero |
| `6` | `CONFIG` | Required git configuration is missing (`ger.webUrl`, credentials, or the branch's upstream when no prompt can set it; declining the prompt is `REFUSED`; see [SPEC § A missing upstream](../SPEC.md#a-missing-upstream)) |
| `7` | `GIT` | A git command failed, or the command runs outside a git repository (`error: not a git repository`, checked once in `init_cli_runtime`; `ger inbox`, `setup`, `config`, `cache` and `fetch-api` run anywhere) |
| `8` | `DUPLICATE_CHANGE_ID` | The same Change-Id appears on more than one local commit |
| `9` | `MISSING_CHANGE_ID` | A local commit has no Change-Id footer |
| `10` | `NO_EDIT_ATTENTION` | An attention walk (`ger edit`, `show` or `sha` with `-n` or first-attention targeting) or a review chain walk (`ger review --next` / `--prev`) has nothing left to visit. Printed without `error:` |
| `11` | `CI_CONTENT` | A **CI link**'s content could not be fetched or parsed where the command required it (`ger ci`; `ger show --ci` reports it and carries on) |

## What is not remapped

**Child process codes pass through.** `ger edit` and `ger rebase` return `git rebase`'s exit code; `ger push` returns `git push`'s. Those are git's codes, not ours, and the wrapper leaves them alone — so a non-zero exit from those commands may be git's meaning rather than the table above. Either way it is non-zero, which is all the contract says.

**Unmapped exceptions are bugs.** `run_cli_command` catches only the failures in the table. Anything else surfaces as a traceback rather than a tidy exit code, deliberately: a crash that exits `1` is a crash you never fix.

## Attention is not failure

`ger log`, `ger show`, `ger ci` and `ger inbox` exit `0` when a commit or change needs attention. That is the command working correctly and reporting a state; the state is in the output (`--json` for scripts), not the exit code. Any non-zero exit means the command did not do what was asked.

Related: [change-and-commit-identifiers.md](change-and-commit-identifiers.md) for how changeishes resolve.

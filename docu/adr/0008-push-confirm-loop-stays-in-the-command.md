# The push confirm loop stays in the command

The push module returns a **push plan** or a **push refusal**, and accepts a revised plan when reviewers, topic, WIP, private, or the **reviewer strategy** change. It does not ask the user anything. The Y/n/r confirm loop, the `-i` push-options prompt, the upstream prompt, and the save-reviewers question stay in the command.

Core has to stay callable without a terminal: it never prints and never prompts, so a long-running local core can call the same operations the command calls. A confirm loop inside the push module would make that module decide when a person is present.

## Considered options

**One call that owns the loop and calls back for each prompt** keeps "preview once" and the credential check in one place. It was rejected. The callback protocol is as wide as the loop it hides, and the module still decides when to prompt, so it cannot be called with no terminal.

**A prompt port on the push module** was rejected for the same reason. The command would be the only adapter, which is not a real seam.

## Consequences

- The command calls `prepare`, then `revise` when the user edits push options, then `execute`. `execute` is the only call that runs `git push`.
- The commit list on a **push plan** does not change when options are revised, so the command can print the preview once and reprint only the status line.
- Missing credentials for reviewer strategy `lazy` or `overwrite` are a field on the plan. The command prints that and stays in the loop. `execute` is not called until the field is clear.
- `prepare` runs before `-i`. A **push refusal** is printed without asking for reviewers.
- The push-options prompt and its background Gerrit I/O stay with the command ([ADR-0006](0006-push-prompt-gerrit-io-is-background.md)).

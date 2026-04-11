```markdown
# `glog` Command Specification (v2)

## 1. Purpose

`glog` provides a **compact, actionable overview of the current local commit chain with respect to Gerrit**.

The command answers the following questions quickly:

- Which commits are already pushed to Gerrit?
- Which commits still need to be pushed?
- Which commits have CI failures?
- Which commits have review votes?
- Which commits have unresolved comments?
- What is the next action required to move the chain forward?

The output is optimized for:

- **fast visual scanning**
- **fixed-width status columns**
- **minimal noise**
- **clear identification of blocking issues**

The command is designed to **replace the need to manually correlate local commits with the Gerrit UI**.

---

# 2. Default Behavior

Running `glog` without arguments shows a **commit chain overview focused on actionable commits**.

Only commits that **require user attention** are shown by default.

Examples of attention-required states:

- commit is **not yet pushed**
- CI **failed**
- **negative review vote** exists
- **unresolved comments** exist
- commit is **missing required approvals**
- commit is **blocked by an earlier commit in the chain**

This default behavior ensures the command answers:

> *What do I need to do next?*

---

# 3. Commit Range

By default, `glog` inspects the **current stacked commit chain**.

The default range is:

```

<gerrit-base>..HEAD

```

Where `<gerrit-base>` is determined by:

1. configured Gerrit base branch
2. merge-base with upstream branch if configured
3. otherwise the merge-base with `origin/main`

The command processes commits in **chronological order (oldest → newest)**.

This ordering matches Gerrit stacked review dependencies.

---

# 4. Output Format

The default output prints **one primary line per commit**.

Optional **detail lines** may follow.

### Primary line format

```

<sha> <push> <verified> <code-review> <comments> # <summary>

```

Example:

```

a1b2c3d p v+1 cr+2      # Base cleanup
b2c3d4e p v-1 cr+2      # Color scheme
# failed: style
c3d4e5f p v+1 cr+1 com  # Add status characters
# comments: 2 unresolved
d4e5f6a n               # Refactor output formatting

```

---

# 5. Column Definitions

All status columns have **fixed width**.

| Column | Width | Meaning |
|------|------|------|
| SHA | 7 | abbreviated commit hash |
| push | 1 | Gerrit push status |
| verified | 3 | Gerrit Verified vote |
| code-review | 4 | Gerrit Code-Review vote |
| comments | 3 | unresolved comment indicator |

### Status values

#### Push status

| Value | Meaning |
|------|------|
| `p` | commit pushed to Gerrit |
| `n` | commit not yet pushed |

#### Verified (CI)

| Value | Meaning |
|------|------|
| `v+1` | CI passed |
| `v-1` | CI failed |
| `   ` | no vote |

#### Code Review

| Value | Meaning |
|------|------|
| `cr+2` | approved |
| `cr+1` | positive review |
| `cr-1` | review issues |
| `cr-2` | rejected |
| `    ` | no vote |

#### Comments

| Value | Meaning |
|------|------|
| `com` | unresolved comments exist |
| `   ` | no unresolved comments |

Only **unresolved comments** are considered.

Resolved comments are ignored.

---

# 6. Detail Lines

When additional information exists, `glog` prints indented detail lines.

These lines begin with indentation aligned to the summary column.

### Possible detail lines

CI failures:

```

# failed: style, submodule-check

```

Comment summary:

```

# comments: 3 unresolved

```

Gerrit URL (optional):

```

# url: [http://gerrit.example.com/c/project/+/12345](http://gerrit.example.com/c/project/+/12345)

```

Detail lines only appear when relevant.

---

# 7. Color Rules

Colors reinforce meaning but output remains readable without color.

| Status | Color |
|------|------|
| pushed (`p`) | dim |
| not pushed (`n`) | cyan |
| verified `v+1` | green |
| verified `v-1` | red |
| `cr+2` | green |
| `cr+1` | light green |
| `cr-1` | yellow |
| `cr-2` | red |
| comments | yellow |
| CI failure detail lines | red |
| summary text | default terminal color |

Green indicates **progress toward merge**.

Red indicates **blocking issues**.

---

# 8. Attention Detection

A commit requires attention if **any of the following conditions are true**:

### Local state

- commit is **not pushed**

### CI state

- verified vote is `v-1`

### Review state

- `cr-1` or `cr-2` exists
- commit lacks `cr+2`

### Comments

- unresolved comments exist

### Chain blocking

- commit depends on an earlier commit that is not submittable

A commit that satisfies none of the above conditions is considered **stable** and is hidden in default mode.

---

# 9. Command-Line Arguments

## Core options

### `--full`

Show **all commits** in the chain, not only attention-required commits.

### `--oneline`

Print exactly **one line per commit**.

Detail lines are suppressed.

Example:

```

b2c3d4e p v-1 cr+2 # Color scheme # failed: style

```

### `--json`

Output machine-readable JSON.

Each commit object contains:

- `sha`
- `summary`
- `pushed`
- `verified`
- `code_review`
- `comments_unresolved`
- `ci_failures`
- `gerrit_url`
- `submittable`
- `attention_reasons`

### `--range <revset>`

Override the commit range.

Examples:

```

glog --range origin/main..HEAD
glog --range HEAD~5..HEAD

```

### `--no-color`

Disable colored output.

### `--compact`

Use a compact representation:

```

a1b2c3d p +1 +2 .
b2c3d4e p -1 +2 .
c3d4e5f p +1 +1 c
d4e5f6a n . . .

```

---

# 10. Summary Section

At the end of the output, `glog` prints a summary of actionable states.

Example:

```

summary:
ready-to-push: 2
ci-failures: 1
unresolved-comments: 1
awaiting-review: 2

```

This helps determine the **next development step immediately**.

---

# 11. Exit Codes

Exit codes allow integration into scripts and editors.

| Code | Meaning |
|------|------|
| `0` | no attention required |
| `1` | commits require attention |
| `2` | invalid usage |
| `3` | Gerrit/API error |

---

# 12. Example Output

## Default mode

```

b2c3d4e p v-1 cr+2      # Color scheme
# failed: style

c3d4e5f p v+1 cr+1 com  # Add status characters
# comments: 1 unresolved

d4e5f6a n               # Refactor output formatting

summary:
ready-to-push: 1
ci-failures: 1
unresolved-comments: 1
awaiting-review: 1

```

---

# 13. Processing Algorithm

`glog` processes commits in the following order:

1. determine commit range
2. enumerate commits (oldest → newest)
3. extract Change-Id from each commit
4. query Gerrit for matching changes
5. determine push state
6. collect CI votes
7. collect review votes
8. collect unresolved comments
9. determine submittable state
10. determine attention reasons
11. render output

---

# 14. Design Goals

The command must satisfy the following design goals:

### Fast visual parsing

Status columns must remain aligned and predictable.

### Minimal noise

Only relevant commits appear by default.

### Actionable output

The user should immediately see:

- what is broken
- what needs pushing
- what needs review
- what needs response

### Automation friendly

Structured modes (`--json`) enable integration with:

- IDE tooling
- CLI helpers
- automation scripts

---

# 15. Minimal Implementation Requirements

The initial implementation must support:

- default attention mode
- fixed-width status columns
- CI vote detection
- code-review vote detection
- unresolved comment detection
- push detection via Change-Id
- summary section
- options:
  - `--full`
  - `--oneline`
  - `--json`
  - `--range`
  - `--no-color`
  - `--compact`

Additional features may be added later without breaking compatibility.
```

## Testing the `glog` Command

To verify that the `glog` command works as intended, you can use a prepared repository that is already set up and connected to a Gerrit instance.

**Test Repository**:
`/d/projects/external/workflow-optimization/test-git-graph-repo`

This repository should be configured with:
- Valid Gerrit remote pointing to your Gerrit instance
- Authentication (auth token) set up to access Gerrit APIs

### Basic Usage

1. Open a terminal and change to the test repository:

    ```sh
    cd /d/projects/external/workflow-optimization/test-git-graph-repo
    ```

2. Run the basic glog command:

    ```sh
    git glog
    ```

   You should see a table of commits with fixed-width status columns showing the state of each commit (pushed, review status, CI checks, comments, etc.).

### Testing Options

Try the command with various options to validate all supported features:

- Show all (not just those needing attention):

    ```sh
    git glog --full
    ```

- Compact or one-line view (for scripting/integration):

    ```sh
    git glog --oneline
    git glog --compact
    ```

- Machine-readable JSON output:

    ```sh
    git glog --json
    ```

- Avoid ANSI color (when parsing output):

    ```sh
    git glog --no-color
    ```

- Limit to a specific commit range:

    ```sh
    git glog --range main..HEAD
    ```

### Validating Results

- Confirm that each commit status (columns: pushed, verified, code review, comments) is displayed and is accurate per the status in Gerrit.
- For `--json` mode, ensure valid JSON is produced and contains all relevant fields.
- If there are CI failures, unresolved comments, or review votes, these should be visible in the status columns.

### Troubleshooting

- If Gerrit API or authentication fails, check your auth token and Gerrit server URL.
- For new branches or recent commits, verify that you have pushed at least once to ensure Gerrit association.

---

You can iterate and verify all status scenarios (submitted, under review, CI failed, resolved/unresolved comments) by manipulating or creating new commits in the test repository and observing how `git glog` output changes.
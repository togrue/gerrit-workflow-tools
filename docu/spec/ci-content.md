# CI content

| | |
|--|--|
| **Status** | Implemented: [`ger ci`](commands/ci.md) and opt-in [`ger show --ci`](commands/show.md#ci-content). **Introduced findings** remain a follow-up. |
| **Vocabulary** | [CONTEXT.md](../../CONTEXT.md): **CI content provider**, **CI content**, **CI finding**, **introduced findings** (follow-up) |
| **Depends on** | Existing **CI links** / **CI strategy** ([gerrit-ci-strategies.md](../gerrit-ci-strategies.md)) |
| **Modules** | `core/ci_content.py` (shapes, provider seam) · `core/ci_content_service.py` (fetch, credentials, policy) · `core/ci_content_store.py` · `core/ci_findings.py` and its finder modules · `core/ci_log.py` · `core/ci_paths.py` · `core/ci_jenkins.py` · `core/ci_http.py` · `render/ci_content.py` |

Goal: give a clear view of local + Gerrit state that includes *why* CI failed, so a human or agent can fix without hunting consoles. Expensive fetches stay off the default **Gerrit overlay** / `ger log` path.

---

## Layering

```
Checks / change messages
        │
        ▼
  CI strategy  ──►  CI links          (cheap; already in the overlay)
        │
        ▼
  CI content provider  ──►  CI content   (opt-in; host-keyed fetch + parse)
        │
        ├── provenance
        ├── CI findings
        └── optional body
```

| Layer | Keyed by | Owns |
|-------|----------|------|
| **CI strategy** | `gerrit.project` | Discover / rewrite URLs → **CI links** |
| **CI content provider** | URL host / pattern | Fetch + parse → **CI content** |

Do not merge the layers: auth and console URL shape are host properties; link discovery stays project-scoped.

**Not in the overlay.** Annotated stack / `ger log` keep names and links only. Content is a deliberate second hop.

**Built-in Jenkins.** A built-in provider implements the seam for Jenkins consoles. It is a replaceable adapter (fetch + ordered finders), not a concept in the domain model and not entangled with core types beyond **CI content** / **CI finding**. Custom providers may live under the existing `.ger/ci/` registry domain as an additional callable beside link extraction.

**Provider contract** (`CiContentProvider`): an object with `id`, `matches(url) -> bool`, `cache_key(url) -> str` and `fetch(link, ctx) -> CiFetched`. `ctx` carries the HTTP seam (`ctx.http.get_text(url, headers=…)`) and the host's credential. Export them as `CONTENT_PROVIDERS = [...]` from `.ger/ci/registry.py` (local tier, then global); they are tried in order before the built-in Jenkins provider. Every tier's list is used, since providers match by URL, not by `gerrit.project`. A provider that raises becomes an error on that link's provenance. Template: [gerrit-ci-strategies.md § CI content providers](../gerrit-ci-strategies.md#ci-content-providers). Copyable example: [`example/hooks/ci/`](../../example/hooks/ci/).

The built-in Jenkins provider claims any `…/job/<name>[/job/<name>…]/<build>/…` URL, reads `api/json` (running? display name) and then `consoleText`.

---

## CI content shape

One result per fetched **CI link**:

| Part | Role |
|------|------|
| **Provenance** | Final URL, build identity when known, provider id, fetched-at, truncated?, cache verdict, error if fetch/parse failed |
| **CI findings** | Normalized items: tool/kind, severity (`error` / `warning`), path, line (and column when known), message, fingerprint |
| **Body** | Raw log text, omitted or capped unless the caller asks for full |

Views are projections of this result, not separate provider APIs.

### Finding extractors (built-in Jenkins)

One provider; several content-driven finders, in order (pipeline output varies within a single log). Finders are grouped by the **shape** of what they read, not by tool, so a new tool is usually one more pattern in an existing shape:

| # | Shape | What the finder reads | Module |
|---|-------|-----------------------|--------|
| 1 | Test runner | The failed test, located by its own failure section | `core/ci_findings_tests.py`, `core/ci_findings_tests_jvm.py` |
| 2 | Block | A header line, and its place on a line nearby | `core/ci_findings_blocks.py` |
| 3 | Located line | `path:line[:col]: severity: msg`, all on one line | `core/ci_findings_located.py` |
| 4 | Build verdict | A build driver saying *that* a step failed; the generic fallback | `core/ci_findings.py` |

Each **CI finding** carries a tool/kind tag. Precise finders run before generic so lines are not double-counted: a line one finder claims is skipped by the later ones.

| Ecosystem | Tool | Kinds | Recognised |
|-----------|------|-------|------------|
| Python | `pytest` | `failed`, `error` | `FAILED` / `ERROR` summary lines, located by the raising frame of the `FAILURES` section; `detail` holds the `E` lines |
| | `ruff` | `lint`, `syntax`, `format` | the default block (`CODE msg`, then `--> path:line:col`), the concise `path:line:col: CODE msg`, `Would reformat: path` |
| | `pylint` | `convention`, `refactor`, `warning`, `error`, `fatal` | `path:line:col: C0116: msg (symbol)`; only `error` / `fatal` are errors |
| | `mypy` | `error`, `warning` | `path.py:line[:col]: error: msg`, with end positions and `--pretty` wrapping |
| | `python` | `error` | an uncaught `Traceback`: its last line, at the deepest frame outside installed packages |
| C / C++ | `cc` | `error`, `warning` | `file.c:line[:col]: error: msg` (C/C++/ObjC/CUDA extensions); `gcc:` / `clang: error:` driver lines |
| | `clang-format` | `format` | the same shape tagged `-Wclang-format-violations` |
| | `ld` | `error` | `undefined reference to …`, `multiple definition of …`, `ld:` / `collect2:` / `lld:` errors |
| | `cmake` | `error`, `warning` | `CMake Error at CMakeLists.txt:3 (command):` and the indented message; `Configuring incomplete` |
| | `gtest`, `ctest` | `failed` | `[  FAILED  ] Suite.Test`, located by `file:line: Failure`; the rows under `The following tests FAILED:` |
| | `ninja` | `error` | `FAILED: <target>` |
| Rust | `rustc`, `clippy` | `error`, `warning` | `error[E0308]: msg`, then `--> path:line:col`; clippy by its `clippy::` lint name |
| | `cargo-test` | `failed` | `test name ... FAILED`, located by the panic in its `---- name stdout ----` section |
| | `rustfmt` | `format` | `Diff in path:line:`, one finding per file |
| | `cargo` | `error` | ``could not compile `crate` `` |
| JavaScript | `tsc` | `error`, `warning` | `a.ts(12,5): error TS2322: msg` and the pretty `a.ts:12:5 - error TS2322: msg` |
| | `eslint` | `lint` | the default report: a file path, then `line:col  severity  msg  rule` rows |
| | `jest`, `node-test` | `failed` | `●` headings under `FAIL file`; the entries under `✖ failing tests:` |
| | `npm` | `error` | one finding per `npm error` / `npm ERR!` report: its code and first telling line |
| Java | `javac` | `error`, `warning` | `Foo.java:12: error: msg`; through Maven `[ERROR] /path/Foo.java:[12,5] msg` |
| | `junit` | `failed`, `error` | Surefire's `Failures:` / `Errors:` rows; Gradle's `Class > test FAILED` |
| | `maven`, `gradle` | `error` | `Failed to execute goal …`, `Task :x FAILED` |
| Scala | `scalac` | `error`, `warning` | sbt's `[error] /path/Foo.scala:12:5: msg` and Scala 3's `-- [E007] … Error: path:line:col` |
| | `scalatest` | `failed` | `- name *** FAILED ***` and the `(File.scala:12)` line under it |
| | `sbt` | `error` | `[error] (Compile / compileIncremental) Compilation failed` |
| any | `generic` | `error` | `Error:` lines, `make: ***`, non-zero exit statuses, `marked build as failure`, `Finished: FAILURE` |

npm, Maven, Gradle, sbt, Ninja and Make mostly *wrap* another tool: the finding worth reading comes from what they ran, and their own line only says which step failed.

**Line prefixes.** What a CI system puts in front of a tool's output is removed once, before any finder runs: ANSI escapes, Jenkins timestamper prefixes, the `[branch]` tag of a parallel pipeline stage, Docker BuildKit's `#12 3.4` step prefix. A build tool's own level tag (Maven's `[ERROR]`, sbt's `[error]`) stays, because it is the only severity those lines carry.

`find_ci_findings(text)` is public so custom providers can reuse the finders. `fingerprint` hashes tool, kind, path and message (no line numbers, no severity): the key **introduced findings** compare on.

### Severity

Every finding is an `error` or a `warning`, by the tool's own ranking. A warning is rarely why the build failed, so it is listed below the errors and **collapsed**: one count line in human and Markdown output (`ger ci --warnings` lists them), a closed group in the *Change* view. JSON always carries every finding with its `severity`, and `collapse_warnings` per link.

Warnings are collapsed only when some error explains the failure. When the only errors are build verdicts (`generic`, `ninja`, `cmake`, `cargo`, `maven`, `gradle`, `sbt`), the warnings are the best lead there is and stay in view (`CiContent.collapse_warnings`). The agent runbook hands over what is in view, not the collapsed warnings.

### Finding paths

A log names files as the build agent saw them. Two steps make the same file read the same from any agent, so fingerprints compare and a finding can be opened:

1. **In the log.** A path under a workspace directory the log itself names (Jenkins' `Building in workspace …`, `Running on … in …`) is made relative to it. Pure text, part of `find_ci_findings`, and what the store keeps.
2. **Against the commit.** `CiContentService.fetch(..., commit=…)` resolves each path to a file of that commit (`git ls-tree`, once per commit): the longest tail of the logged path that is a tracked file. This covers `job@2`, container mounts, a checkout in a subdirectory, and paths logged relative to a subdirectory or by bare file name (when exactly one tracked file ends with it).

A file outside the repository is left as logged. So that a system header or an installed package is not taken for a file of ours, the directory a path is stripped of must be one several findings agree on, or the match must be more than a bare file name. When the commit is not in the local repository, only step 1 applies.

---

## Caching

Fetched logs can be megabytes; the Gerrit ChangeInfo cache is the wrong store.

1. The provider returns a **cache verdict** on the result: `none` | `findings` | `body`, plus a stable cache key (usually the final URL).
2. **Core** enforces policy: separate CI-content store, size budget / LRU, max body size to retain.
3. Typical finished Jenkins `…/job/…/<n>/console`: immutable → cache **findings**; cache **body** only when the verdict allows it *and* the body is under the size cap.
4. In-progress or floating "latest build" URLs → `none` (or findings-only if the provider can defend it).

Provider decides *whether the response is safe to reuse*; core decides *whether we can afford to keep it*.

As built: one JSON file per entry under `~/.cache/ger/<host>/ci_content/`, keyed by provider id + `cache_key` (Jenkins: the build's base URL, so `console`, `consoleText` and `testReport` links share an entry). Budget 64 MiB, least recently used evicted first; bodies over 2 MiB are dropped and findings kept, so `--full` refetches them. Entries exist only for immutable content, so a hit is valid without a staleness check. `ger --refresh` skips reads; `ger cache clear` empties it.

---

## Credentials

Host-map style, not a single global Jenkins token key:

- `ger.ciCredential.<host>` = secret used by providers that match that host

(Migration from an ad-hoc `gerrit.jenkinstoken` in a personal git config is a setup detail, not a public key name.)

The host is the link URL's hostname, lowercase and without port: `git config ger.ciCredential.ci.example.org bot:apitoken`. `user:token` is sent as HTTP Basic (Jenkins API tokens), anything else as a Bearer token.

---

## Command: `ger ci`

Explicit expensive entry point. Default: **CI findings**, grouped by **CI link** label. Body on `-v` / `--full`.

```
ger ci [options] [CHANGEISH…]
```

| Behaviour | Choice |
|-----------|--------|
| Default output | Findings, grouped by link label; errors first, warnings as a count (`--warnings` lists them) |
| Full log | `--full` (or `-v` for a capped tail + findings; `--full` streams the entire body for agent search) |
| Machine / AI | `--json` is the contract; human and `--ai` markdown are projections |
| Multiple links | Fetch all; group by label |
| Fetch failure | **Hard** — non-zero if any selected link cannot be fetched/parsed when content was required |
| Targets | Changeish list or ranges, `--stack`; default `HEAD` (same resolution as `ger show`) |

`--full` may stream past a body cache miss; it must not force megabyte bodies into `ger show`.

---

## Command: `ger show` enrichment

| Behaviour | Choice |
|-----------|--------|
| Default | Off — comments and Gerrit status only |
| Opt-in | Settings default **and** flag override: `ger.showCiContent` + `--ci` / `--no-ci` |
| What is attached | **Findings only** (no body); warnings collapsed to a count as in `ger ci` |
| Fetch failure | **Soft** — keep Gerrit/comment output; report link + error in the CI section |
| JSON / `--ai` | Same findings embedded in the existing payload / markdown projection |

`ger log` stays unchanged (links/names only).

---

## AI-oriented output

- **JSON findings** are the stable contract for agents (`ger ci --json`; `ger show --json` when CI content is on).
- Markdown / human text: change identity → findings grouped by label → optional short excerpt only where a command already includes body.
- Agents that need to grep the raw log use `ger ci --full` (stdout; a `-o path` may follow), not `ger show`.

---

## Out of scope (this slice)

| Item | Notes |
|------|-------|
| Default overlay / `ger log` fetching | Too expensive |
| **Introduced findings** | Documented follow-up: compare findings to the parent change for richer edit-attention stops |
| Attention / `ger edit` stop changes | Still Verified / Checks-based until introduced findings exist |
| Project-keyed provider matching | Host/pattern for v1; revisit if the same host needs different *fetch* behaviour per project (parse diversity is handled by finders, not by project keys) |

---

## Locked decisions (reference)

1. Name: **CI content provider**
2. `ger show` opt-in: Settings + flag
3. `ger show` attaches findings only
4. `ger ci`: findings by default; body on `-v` / `--full`
5. One result: provenance + findings + optional body
6. Match providers by URL host/pattern (v1)
7. Extend `.ger/ci/`; built-in Jenkins behind the seam
8. Multiple links: fetch all, group by label
9. Findings uncapped in practice; body capped; `--full` escape hatch
10. Host-map credentials (`ger.ciCredential.<host>`)
11. Soft fail in `show`, hard in `ci`
12. Provider cache verdict; core size budget / separate store
13. Ordered finders inside the built-in provider
14. JSON findings as the AI contract
15. Only `ger ci` + `ger show` for now; introduced findings as follow-up

---

## See also

- [gerrit-ci-strategies.md](../gerrit-ci-strategies.md) — **CI links** today
- [`example/hooks/ci/`](../../example/hooks/ci/) — copyable custom strategy + content provider
- [spec/commands/show.md](commands/show.md) — current `ger show`
- [SPEC.md](../SPEC.md) — command registry / planned commands
- [commands/ci.md](commands/ci.md) — `ger ci` options, output, exit codes
- [testenv/README.md](../../testenv/README.md) — Jenkins bench for the built-in provider

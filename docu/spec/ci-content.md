# CI content

| | |
|--|--|
| **Status** | Implemented: [`ger ci`](commands/ci.md) and opt-in [`ger show --ci`](commands/show.md#ci-content). **Introduced findings** remain a follow-up. |
| **Vocabulary** | [CONTEXT.md](../../CONTEXT.md): **CI content provider**, **CI content**, **CI finding**, **introduced findings** (follow-up) |
| **Depends on** | Existing **CI links** / **CI strategy** ([gerrit-ci-strategies.md](../gerrit-ci-strategies.md)) |
| **Modules** | `core/ci_content.py` (shapes, provider seam) · `core/ci_content_service.py` (fetch, credentials, policy) · `core/ci_content_store.py` · `core/ci_findings.py` · `core/ci_jenkins.py` · `core/ci_http.py` · `render/ci_content.py` |

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

**Provider contract** (`CiContentProvider`): an object with `id`, `matches(url) -> bool`, `cache_key(url) -> str` and `fetch(link, ctx) -> CiFetched`. `ctx` carries the HTTP seam (`ctx.http.get_text(url, headers=…)`) and the host's credential. Export them as `CONTENT_PROVIDERS = [...]` from `.ger/ci/registry.py` (local tier, then global); they are tried in order before the built-in Jenkins provider. Every tier's list is used, since providers match by URL, not by `gerrit.project`. A provider that raises becomes an error on that link's provenance. Template: [gerrit-ci-strategies.md § CI content providers](../gerrit-ci-strategies.md#ci-content-providers).

The built-in Jenkins provider claims any `…/job/<name>[/job/<name>…]/<build>/…` URL, reads `api/json` (running? display name) and then `consoleText`.

---

## CI content shape

One result per fetched **CI link**:

| Part | Role |
|------|------|
| **Provenance** | Final URL, build identity when known, provider id, fetched-at, truncated?, cache verdict, error if fetch/parse failed |
| **CI findings** | Normalized items: tool/kind, path, line (and column when known), message, optional fingerprint |
| **Body** | Raw log text, omitted or capped unless the caller asks for full |

Views are projections of this result, not separate provider APIs.

### Finding extractors (built-in Jenkins)

One provider; several content-driven finders, in order (pipeline output varies within a single log):

1. pytest (failed tests, short traceback)
2. ruff / mypy (`path:line:…`)
3. gcc / clang-style (`file:line:col: error: …`), linker / clang-format-style lines as patterns mature
4. generic fallback (`FAILURE`, `Error:`, exit-status blocks)

Each **CI finding** carries a tool/kind tag. Precise finders run before generic so lines are not double-counted.

| Tool | Kinds | Recognised lines |
|------|-------|------------------|
| `pytest` | `failed`, `error` | `FAILED` / `ERROR` summary lines, located by the raising frame of the `FAILURES` section; `detail` holds the `E` lines |
| `ruff` | `lint`, `format` | `path:line:col: CODE msg`, `Would reformat: path` |
| `mypy` | `error`, `warning` | `path.py:line[:col]: error: msg` |
| `cc` | `error`, `warning` | `file.c:line[:col]: error: msg` (C/C++/ObjC/CUDA extensions) |
| `clang-format` | `format` | the same shape tagged `-Wclang-format-violations` |
| `ld` | `error` | `undefined reference to …`, `ld:` / `collect2:` / `lld:` errors |
| `generic` | `error` | `Error:` lines, `make: ***`, non-zero exit statuses, `marked build as failure`, `Finished: FAILURE` |

ANSI escapes and Jenkins timestamper prefixes are stripped first. `find_ci_findings(text)` is public so custom providers can reuse the finders. `fingerprint` hashes tool, kind, path and message (no line numbers): the key **introduced findings** will compare on.

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
| Default output | Findings, grouped by link label |
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
| What is attached | **Findings only** (no body) |
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
- [spec/commands/show.md](commands/show.md) — current `ger show`
- [SPEC.md](../SPEC.md) — command registry / planned commands
- [commands/ci.md](commands/ci.md) — `ger ci` options, output, exit codes
- [testenv/README.md](../../testenv/README.md) — Jenkins bench for the built-in provider

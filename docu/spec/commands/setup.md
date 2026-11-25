# `ger setup`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_setup.py`; checks in `core/gerrit_setup.py` |
| **Requires** | A terminal to ask in, or `--url`, `--user` and `--token-file` |

Configure `ger.webUrl`, `ger.user` and `ger.token` in git config, check them with Gerrit before
saving anything, and say what comes next. A setup that cannot work says so here, not at the next command.

---

## Usage

```
ger setup [--local] [--url URL --user USER --token-file FILE] [--allow-insecure-http] [--no-check]
```

| Flag | Effect |
|------|--------|
| (none) | Write to **global** git config (`git config --global`) |
| `--local` | Write to this repository's git config |
| `--url URL` | The Gerrit web URL. With `--user` and `--token-file` too, nothing is asked, terminal or not |
| `--user USER` | Your Gerrit username |
| `--token-file FILE` | Read the HTTP password from `FILE` (`-`: stdin); never on the command line, where `ps` shows it |
| `--allow-insecure-http` | Accept an `http://` URL: sets `ger.allowInsecureHttp true` at the same scope |
| `--no-check` | Save without asking Gerrit whether the credentials work (Gerrit not reachable from here yet) |

## In a terminal

Prompts, each with a short description; a value saved before (also under its old name `gerrit.webUrl`,
`gerrit.user`, `gerrit.token`) is the default, and leaving the token empty keeps the one you have.
Saving writes the `ger.*` key and removes the old name at that scope
([Renamed keys](../../Configuration.md#renamed-keys-gerrit-inbox-branchnamegerrit)). An empty answer
stops with nothing saved, exit `1`.

- **An answer that will not do is asked again**, with the reason: a URL without a scheme
  (`lenovo-pc:8081`) says so and offers `https://lenovo-pc:8081` as the next default; one with a
  path after the host says it is the base URL only.
- **A plain `http://` URL is explained** (`an http:// URL sends your user and token unencrypted with
  every request: fine for a test Gerrit on your own machine or network, not for a real one`) and
  allowed only on a yes to `Allow plain http for http://lenovo-pc:8081 (ger.allowInsecureHttp true)?
  [y/N]`, the one question setup asks that is not a value: it decides where a secret goes. A no asks
  for the URL again (`Not over plain http, then: give the server's https:// URL.`), `https://`
  offered. An https URL on a port that speaks plain http says so: `Gerrit unreachable at
  lenovo-pc:8081 (it does not speak https on that port: an http:// server?)`.
- **The credentials are checked** with one `GET /accounts/self` through the service of a run opened on
  the values as they would be saved (`gerrit_setup.run_with`, `signed_in`), before anything is
  written. Accepted: `Signed in as Alice Reviewer <alice@example.com> on https://gerrit.example.com`.
  Refused (HTTP 401/403): `Gerrit at lenovo-pc:8081 did not accept user pg-you with that token;
  nothing was saved`, and the user and token are asked again (Enter keeps the user shown): the URL
  is right, Gerrit answered there, and an http opt-in already given stands. Unreachable or any other
  failure: the reason, `nothing was saved`, and every prompt again, your answers the defaults.

## Without a terminal

Nothing is asked. The values come from the flags, else from what the scope already has (so
`ger setup --token-file - <new-token` replaces only the token). A value still missing refuses with
exit `2`, naming the flags: `no terminal to ask in: give --url, --user, --token-file (e.g. ger setup
--url https://gerrit.example.com --user YOU --token-file - <token.txt)`. An `http://` URL refuses
the same way unless `--allow-insecure-http` is given. Credentials Gerrit refuses, or a Gerrit it
cannot reach, exit `5` with nothing saved, naming `--no-check`.

## Once saved

```text
saved to your repository git config: ger.webUrl, ger.user and ger.token
next: `ger log` shows your stack and its reviews; `ger inbox` the changes waiting for you
```

Setup leaves the repository's hooks alone: it installs no `commit-msg` hook. A commit without a
Change-Id gets one from `ger change-id --fix` ([Getting-Started.md](../../Getting-Started.md#4-change-ids)).

---

## See also

- [Getting-Started.md](../../Getting-Started.md)
- [Configuration.md](../../Configuration.md)
- [SPEC.md](../../SPEC.md)

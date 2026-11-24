# Spec: docu/spec/ci-content.md
"""CI content providers, the fetch service (credentials, cache policy, body shape) and the store."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gerrit_workflow_tools.core import ci_content_service
from gerrit_workflow_tools.core.ci_content import CiContent, CiFetchContext, CiFetched, CiFinding, CiProvenance
from gerrit_workflow_tools.core.ci_content_service import TAIL_LINES, CiContentService, parser_fingerprint, shape_body
from gerrit_workflow_tools.core.ci_content_store import CiContentStore, StoredCiContent
from gerrit_workflow_tools.core.ci_http import HttpText
from gerrit_workflow_tools.core.ci_jenkins import JenkinsProvider, parse_jenkins_build_url
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_strategy import load_ci_content_providers
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import clear_extension_registry_cache
from gerrit_workflow_tools.core.gerrit.paths import gerrit_config_dir
from gerrit_workflow_tools.render.ci_content import ci_content_json, format_ci_section_human, format_ci_section_markdown
from tests.ci_fakes import JENKINS, FakeCiHttp, jenkins_build
from tests.fixtures import trust_local_registries


LINK = CiLink(label="jenkins", url=f"{JENKINS}/console", source="message")


def _service(http: FakeCiHttp, tmp_path: Path, **settings: str) -> CiContentService:
    return CiContentService(
        providers=[JenkinsProvider()],
        http=http,
        settings=Settings.from_map(settings),
        store=CiContentStore(tmp_path / "store"),
    )


# --- Jenkins provider --------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "base", "numbered"),
    [
        (f"{JENKINS}/console", JENKINS, True),
        (f"{JENKINS}/", JENKINS, True),
        (
            "https://ci.example.org/jenkins/job/a/job/b/7/testReport/",
            "https://ci.example.org/jenkins/job/a/job/b/7",
            True,
        ),
        (
            "https://ci.example.org/job/Widget/lastFailedBuild/console",
            "https://ci.example.org/job/Widget/lastFailedBuild",
            False,
        ),
    ],
)
def test_jenkins_build_urls(url: str, base: str, numbered: bool) -> None:
    ref = parse_jenkins_build_url(url)
    assert ref is not None
    assert (ref.base, ref.numbered) == (base, numbered)


def test_jenkins_does_not_claim_other_urls() -> None:
    assert not JenkinsProvider().matches("https://ci.example.org/builds/55")


def test_finished_numbered_build_may_cache_its_body() -> None:
    fetched = JenkinsProvider().fetch(LINK, CiFetchContext(http=FakeCiHttp(jenkins_build())))
    assert fetched.cache_verdict == "body"
    assert fetched.build == "Widget #55"
    assert fetched.url == f"{JENKINS}/console"
    assert [f.tool for f in fetched.findings] == ["pytest", "generic"]


def test_running_build_floating_url_or_unknown_state_is_not_cached() -> None:
    running = JenkinsProvider().fetch(LINK, CiFetchContext(http=FakeCiHttp(jenkins_build(building=True))))
    assert running.cache_verdict == "none"
    floating = "https://ci.example.org/job/Widget/lastBuild"
    link = CiLink(label="jenkins", url=f"{floating}/console", source="checks")
    fetched = JenkinsProvider().fetch(link, CiFetchContext(http=FakeCiHttp(jenkins_build(floating))))
    assert fetched.cache_verdict == "none"
    pages = jenkins_build()
    pages[f"{JENKINS}/api/json?tree=building,fullDisplayName,url"] = 500
    unknown = JenkinsProvider().fetch(LINK, CiFetchContext(http=FakeCiHttp(pages)))
    assert (unknown.cache_verdict, unknown.build) == ("none", None)
    assert unknown.findings


# --- service: credentials, errors, cache policy ------------------------------------


def test_credential_for_the_link_host_is_sent(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    _service(http, tmp_path, **{"ger.ciCredential.ci.example.org": "bot:s3cret"}).fetch([LINK])[0]
    assert all(headers.get("Authorization", "").startswith("Basic ") for _url, headers in http.calls)


def test_bearer_credential_and_other_hosts_get_nothing(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    _service(http, tmp_path, **{"ger.ciCredential.ci.example.org": "tok"}).fetch([LINK])[0]
    assert http.calls[0][1] == {"Authorization": "Bearer tok"}
    http = FakeCiHttp(jenkins_build())
    _service(http, tmp_path / "b", **{"ger.ciCredential.other.example.org": "tok"}).fetch([LINK])[0]
    assert http.calls[0][1] == {}


def test_fetch_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    content = _service(FakeCiHttp({}), tmp_path).fetch([LINK])[0]
    assert content.failed
    assert "HTTP 404" in (content.provenance.error or "")
    assert content.findings == []


def test_unmatched_url_names_the_host(tmp_path: Path) -> None:
    link = CiLink(label="x", url="https://builds.example.net/run/1", source="checks")
    content = _service(FakeCiHttp({}), tmp_path).fetch([link])[0]
    assert content.provenance.error == "no CI content provider matches builds.example.net"


def test_finished_build_is_served_from_the_store_next_time(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    first = service.fetch([LINK])[0]
    assert not first.provenance.cached
    calls = len(http.calls)
    again = service.fetch([CiLink(label="jenkins", url=f"{JENKINS}/consoleText", source="checks")], body="full")[0]
    assert len(http.calls) == calls, "same build, same entry; body was under the cap"
    assert again.provenance.cached
    assert again.findings == first.findings
    assert again.body and "FAILED tests/test_calc.py" in again.body


def test_refresh_bypasses_the_store(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    service.fetch([LINK])[0]
    service.refresh = True
    assert not service.fetch([LINK])[0].provenance.cached


def test_oversized_body_keeps_findings_only_and_full_refetches(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    assert service.store is not None
    service.store.max_body_bytes = 10
    service.fetch([LINK])[0]
    calls = len(http.calls)
    assert service.fetch([LINK])[0].provenance.cached
    assert len(http.calls) == calls
    full = service.fetch([LINK], body="full")[0]
    assert not full.provenance.cached
    assert len(http.calls) > calls


def test_running_build_is_never_stored(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build(building=True))
    service = _service(http, tmp_path)
    service.fetch([LINK])[0]
    assert not service.fetch([LINK])[0].provenance.cached


def test_findings_parsed_by_other_ci_code_are_a_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    service.fetch([LINK])[0]
    assert service.fetch([LINK])[0].provenance.cached
    monkeypatch.setattr(ci_content_service, "_GER_CI_DIGEST", "the finders changed")
    assert not service.fetch([LINK])[0].provenance.cached, "an upgrade parses the log again"
    assert service.fetch([LINK])[0].provenance.cached, "and keeps what it parsed"


def test_same_link_on_two_commits_is_downloaded_once(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    [[first], [second]] = service.fetch_each([([LINK], None), ([LINK], None)])
    assert len(http.calls) == 2, "api/json and consoleText, once"
    assert first == second and first.findings


def test_body_shapes() -> None:
    body = "\n".join(str(i) for i in range(TAIL_LINES + 5))
    assert shape_body(body, "none") == (None, 0)
    assert shape_body(body, "full") == (body, 0)
    tail, omitted = shape_body(body, "tail")
    assert omitted == 5
    assert tail is not None and tail.splitlines()[0] == "5"


# --- store -------------------------------------------------------------------------


def _entry(n: int, *, size: int = 0) -> StoredCiContent:
    return StoredCiContent(
        provider="jenkins",
        url=f"u{n}",
        fetched_at="2026-11-09T00:00:00Z",
        findings=[CiFinding(tool="generic", kind="error", message="x")],
        cache_verdict="body",
        body="b" * size,
    )


def test_store_evicts_least_recently_used(tmp_path: Path) -> None:
    store = CiContentStore(tmp_path, budget_bytes=3000, max_body_bytes=10_000)
    store.put("a", _entry(1, size=1000))
    store.put("b", _entry(2, size=1000))
    assert store.get("a") is not None  # a is now more recent than b
    store.put("c", _entry(3, size=1000))
    assert store.get("b") is None
    assert store.get("a") is not None and store.get("c") is not None


def test_store_refuses_verdict_none_and_drops_body_for_findings(tmp_path: Path) -> None:
    store = CiContentStore(tmp_path)
    assert not store.put("n", replace(_entry(1), cache_verdict="none"))
    store.put("f", replace(_entry(1, size=5), cache_verdict="findings"))
    kept = store.get("f")
    assert kept is not None and kept.body is None and kept.findings


def test_store_drops_a_broken_entry(tmp_path: Path) -> None:
    store = CiContentStore(tmp_path)
    store.put("k", _entry(1))
    for path in tmp_path.glob("*.json"):
        path.write_text("{not json", encoding="utf-8")
    assert store.get("k") is None
    assert not list(tmp_path.glob("*.json"))


# --- custom providers from .ger/ci/registry.py -------------------------------------


def test_registry_providers_come_before_the_builtin(stack_repo: Path) -> None:
    d = stack_repo / ".ger" / "ci"
    d.mkdir(parents=True)
    (d / "registry.py").write_text(
        """
from gerrit_workflow_tools.core.ci_content import CiFetched, CiFinding

class Builds:
    id = "builds"
    def matches(self, url):
        return url.startswith("https://builds.example.net/")
    def cache_key(self, url):
        return url
    def fetch(self, link, ctx):
        text = ctx.http.get_text(link.url).text
        return CiFetched(url=link.url, findings=[CiFinding(tool="custom", kind="error", message=text)])

CONTENT_PROVIDERS = [Builds(), "not a provider"]
""",
        encoding="utf-8",
    )
    trust_local_registries(stack_repo)
    clear_extension_registry_cache()
    try:
        providers = load_ci_content_providers(stack_repo, settings=Settings.from_map({}))
        assert [getattr(p, "id", None) for p in providers] == ["builds", "jenkins"]
        url = "https://builds.example.net/run/1"
        service = CiContentService(providers=providers, http=FakeCiHttp({url: "boom"}), settings=Settings.from_map({}))
        content = service.fetch([CiLink(label="b", url=url, source="checks")])[0]
        assert content.provenance.provider == "builds"
        assert [f.message for f in content.findings] == ["boom"]
    finally:
        clear_extension_registry_cache()


_MARKED_PROVIDER = """
from gerrit_workflow_tools.core.ci_content import CiFetched, CiFinding
from .marks import MARK

class Builds:
    id = "builds"
    def matches(self, url):
        return True
    def fetch(self, link, ctx):
        text = ctx.http.get_text(link.url).text
        return CiFetched(url=link.url, findings=[CiFinding(tool="custom", kind="error", message=MARK + text)],
                         cache_verdict="findings")

CONTENT_PROVIDERS = [Builds()]
"""


def _marked_registry(d: Path, mark: str) -> None:
    """A ``.ger/ci/`` whose provider prefixes each finding with *mark*, kept in a helper module."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "registry.py").write_text(_MARKED_PROVIDER, encoding="utf-8")
    (d / "marks.py").write_text(f"MARK = {mark!r}\n", encoding="utf-8")


def test_editing_a_registry_helper_parses_the_log_again(
    stack_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A global registry loads after the local one: the local provider still keys on its own code.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    web_base = "https://g.example"
    _marked_registry(gerrit_config_dir(web_base) / "ci", "global: ")
    local = stack_repo / ".ger" / "ci"
    url = "https://builds.example.net/run/1"
    store = CiContentStore(tmp_path / "store")

    def fetch_in_a_new_process(mark: str) -> CiContent:
        _marked_registry(local, mark)
        trust_local_registries(stack_repo)
        clear_extension_registry_cache()
        providers = load_ci_content_providers(stack_repo, settings=Settings.from_map({}), web_base=web_base)
        service = CiContentService(
            providers=providers, http=FakeCiHttp({url: "boom"}), settings=Settings.from_map({}), store=store
        )
        return service.fetch([CiLink(label="b", url=url, source="checks")])[0]

    try:
        assert not fetch_in_a_new_process("old: ").provenance.cached
        assert fetch_in_a_new_process("old: ").provenance.cached
        # Another length: an edit of the same size in the same second would load the old .pyc.
        again = fetch_in_a_new_process("newer: ")
        assert not again.provenance.cached
        assert [f.message for f in again.findings] == ["newer: boom"]
    finally:
        clear_extension_registry_cache()


def test_a_provider_defined_in_a_helper_module_keys_on_the_registry(stack_repo: Path, tmp_path: Path) -> None:
    d = stack_repo / ".ger" / "ci"
    url = "https://builds.example.net/run/1"
    store = CiContentStore(tmp_path / "store")

    def fetch_in_a_new_process(mark: str) -> CiContent:
        d.mkdir(parents=True, exist_ok=True)
        (d / "registry.py").write_text("from .provider import Builds\nCONTENT_PROVIDERS = [Builds()]\n", "utf-8")
        (d / "provider.py").write_text(_MARKED_PROVIDER.replace("from .marks import MARK", f"MARK = {mark!r}"), "utf-8")
        trust_local_registries(stack_repo)
        clear_extension_registry_cache()
        providers = load_ci_content_providers(stack_repo, settings=Settings.from_map({}))
        service = CiContentService(
            providers=providers, http=FakeCiHttp({url: "boom"}), settings=Settings.from_map({}), store=store
        )
        return service.fetch([CiLink(label="b", url=url, source="checks")])[0]

    try:
        assert not fetch_in_a_new_process("old: ").provenance.cached
        assert fetch_in_a_new_process("old: ").provenance.cached
        again = fetch_in_a_new_process("newer: ")
        assert not again.provenance.cached
        assert [f.message for f in again.findings] == ["newer: boom"]
    finally:
        clear_extension_registry_cache()


def test_a_running_process_keys_on_the_registry_it_loaded(stack_repo: Path, tmp_path: Path) -> None:
    d = stack_repo / ".ger" / "ci"
    _marked_registry(d, "old: ")
    trust_local_registries(stack_repo)
    clear_extension_registry_cache()
    try:
        [provider, _jenkins] = load_ci_content_providers(stack_repo, settings=Settings.from_map({}))
        loaded = parser_fingerprint(provider)
        _marked_registry(d, "newer: ")
        assert parser_fingerprint(provider) == loaded, "the old code still runs: its findings keep the old key"
    finally:
        clear_extension_registry_cache()


def test_a_provider_that_raises_is_a_recorded_error(tmp_path: Path) -> None:
    class Broken:
        id = "broken"

        def matches(self, url: str) -> bool:
            return True

        def cache_key(self, url: str) -> str:
            return url

        def fetch(self, link: CiLink, ctx: CiFetchContext) -> CiFetched:
            raise RuntimeError("parser exploded")

    service = CiContentService(providers=[Broken()], http=FakeCiHttp({}), settings=Settings.from_map({}))
    content = service.fetch([LINK])[0]
    assert (content.provenance.provider, content.provenance.error) == ("broken", "parser exploded")


# --- severity: errors lead, warnings step back -------------------------------------


def _content(*findings: CiFinding) -> CiContent:
    return CiContent(link=LINK, provenance=CiProvenance(url=LINK.url, provider="jenkins"), findings=list(findings))


ERROR = CiFinding(tool="cc", kind="error", message="undeclared identifier", path="src/main.c", line=3)
WARNING = CiFinding(tool="cc", kind="warning", message="unused variable", path="src/main.c", line=2, severity="warning")
VERDICT = CiFinding(tool="generic", kind="error", message="FAILURE")


def test_warnings_collapse_below_an_error_that_explains_the_failure() -> None:
    content = _content(WARNING, ERROR, VERDICT)
    assert content.collapse_warnings
    assert content.leading_findings == [ERROR, VERDICT]
    assert content.collapsed_warnings == [WARNING]
    human = format_ci_section_human([content])
    assert [line.strip() for line in human[1:]] == [
        "cc  src/main.c:3  undeclared identifier",
        "generic  FAILURE",
        "1 warning not listed (ger ci --warnings lists them)",
    ]
    listed = format_ci_section_human([content], warnings=True)
    assert [line.strip() for line in listed[-2:]] == ["warnings:", "cc  src/main.c:2  unused variable"]
    assert "- 1 warning not listed (ger ci --warnings lists them)" in format_ci_section_markdown([content])
    payload = ci_content_json(content)
    assert payload["collapse_warnings"] is True
    findings = payload["findings"]
    assert isinstance(findings, list)
    assert [f["severity"] for f in findings] == ["warning", "error", "error"]


def test_warnings_stay_in_view_when_only_a_build_verdict_stands_beside_them() -> None:
    content = _content(VERDICT, WARNING)
    assert not content.collapse_warnings
    assert content.leading_findings == [VERDICT, WARNING]
    assert content.collapsed_warnings == []
    assert all("not listed" not in line for line in format_ci_section_human([content]))


def test_severity_is_not_part_of_the_fingerprint_and_old_entries_read_back() -> None:
    assert replace(WARNING, severity="error", fingerprint="").fingerprint == WARNING.fingerprint
    old = {key: value for key, value in WARNING.to_dict().items() if key != "severity"}
    assert CiFinding.from_dict(old).severity == "warning"
    assert CiFinding.from_dict({**ERROR.to_dict(), "severity": None}).severity == "error"


# --- fetching many links at once ---------------------------------------------------


class _Gate(FakeCiHttp):
    """Lets no request through until *width* are in flight: a serial fetch would time out."""

    def __init__(self, pages: dict[str, str | int], width: int) -> None:
        super().__init__(pages)
        self.barrier = threading.Barrier(width, timeout=5)

    def get_text(self, url: str, **kwargs: Any) -> HttpText:
        self.barrier.wait()
        return super().get_text(url, **kwargs)


def test_jenkins_asks_state_and_log_at_once() -> None:
    fetched = JenkinsProvider().fetch(LINK, CiFetchContext(http=_Gate(jenkins_build(), width=2)))
    assert fetched.cache_verdict == "body"


def test_every_commit_of_a_stack_is_fetched_at_once_and_comes_back_in_order(tmp_path: Path) -> None:
    bases = [f"https://ci.example.org/job/Widget/{n}" for n in (1, 2, 3)]
    pages: dict[str, str | int] = {}
    for n, base in enumerate(bases):
        pages.update(jenkins_build(base, name=f"Widget #{n}"))
    links = [CiLink(label="jenkins", url=f"{base}/console", source="checks") for base in bases]
    service = _service(_Gate(pages, width=2 * len(bases)), tmp_path)
    per_commit = service.fetch_each([([links[0]], None), ([], None), (links[1:], None)])
    assert [[c.provenance.build for c in contents] for contents in per_commit] == [
        ["Widget #0"],
        [],
        ["Widget #1", "Widget #2"],
    ]

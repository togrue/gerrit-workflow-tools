# Spec: docu/spec/ci-content.md
"""CI content providers, the fetch service (credentials, cache policy, body shape) and the store."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gerrit_workflow_tools.core.ci_content import CiFetchContext, CiFetched, CiFinding
from gerrit_workflow_tools.core.ci_content_service import TAIL_LINES, CiContentService, shape_body
from gerrit_workflow_tools.core.ci_content_store import CiContentStore, StoredCiContent
from gerrit_workflow_tools.core.ci_jenkins import JenkinsProvider, parse_jenkins_build_url
from gerrit_workflow_tools.core.ci_links import CiLink
from gerrit_workflow_tools.core.ci_strategy import load_ci_content_providers
from gerrit_workflow_tools.core.config import Settings
from gerrit_workflow_tools.core.ger_registry import clear_extension_registry_cache
from tests.ci_fakes import JENKINS, FakeCiHttp, jenkins_build


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
    _service(http, tmp_path, **{"ger.ciCredential.ci.example.org": "bot:s3cret"}).fetch_one(LINK)
    assert all(headers.get("Authorization", "").startswith("Basic ") for _url, headers in http.calls)


def test_bearer_credential_and_other_hosts_get_nothing(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    _service(http, tmp_path, **{"ger.ciCredential.ci.example.org": "tok"}).fetch_one(LINK)
    assert http.calls[0][1] == {"Authorization": "Bearer tok"}
    http = FakeCiHttp(jenkins_build())
    _service(http, tmp_path / "b", **{"ger.ciCredential.other.example.org": "tok"}).fetch_one(LINK)
    assert http.calls[0][1] == {}


def test_fetch_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    content = _service(FakeCiHttp({}), tmp_path).fetch_one(LINK)
    assert content.failed
    assert "HTTP 404" in (content.provenance.error or "")
    assert content.findings == []


def test_unmatched_url_names_the_host(tmp_path: Path) -> None:
    link = CiLink(label="x", url="https://builds.example.net/run/1", source="checks")
    content = _service(FakeCiHttp({}), tmp_path).fetch_one(link)
    assert content.provenance.error == "no CI content provider matches builds.example.net"


def test_finished_build_is_served_from_the_store_next_time(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    first = service.fetch_one(LINK)
    assert not first.provenance.cached
    calls = len(http.calls)
    again = service.fetch_one(CiLink(label="jenkins", url=f"{JENKINS}/consoleText", source="checks"), body="full")
    assert len(http.calls) == calls, "same build, same entry; body was under the cap"
    assert again.provenance.cached
    assert again.findings == first.findings
    assert again.body and "FAILED tests/test_calc.py" in again.body


def test_refresh_bypasses_the_store(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    service.fetch_one(LINK)
    service.refresh = True
    assert not service.fetch_one(LINK).provenance.cached


def test_oversized_body_keeps_findings_only_and_full_refetches(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build())
    service = _service(http, tmp_path)
    assert service.store is not None
    service.store.max_body_bytes = 10
    service.fetch_one(LINK)
    calls = len(http.calls)
    assert service.fetch_one(LINK).provenance.cached
    assert len(http.calls) == calls
    full = service.fetch_one(LINK, body="full")
    assert not full.provenance.cached
    assert len(http.calls) > calls


def test_running_build_is_never_stored(tmp_path: Path) -> None:
    http = FakeCiHttp(jenkins_build(building=True))
    service = _service(http, tmp_path)
    service.fetch_one(LINK)
    assert not service.fetch_one(LINK).provenance.cached


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
    clear_extension_registry_cache()
    try:
        providers = load_ci_content_providers(stack_repo, settings=Settings.from_map({}))
        assert [getattr(p, "id", None) for p in providers] == ["builds", "jenkins"]
        url = "https://builds.example.net/run/1"
        service = CiContentService(providers=providers, http=FakeCiHttp({url: "boom"}), settings=Settings.from_map({}))
        content = service.fetch_one(CiLink(label="b", url=url, source="checks"))
        assert content.provenance.provider == "builds"
        assert [f.message for f in content.findings] == ["boom"]
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
    content = service.fetch_one(LINK)
    assert (content.provenance.provider, content.provenance.error) == ("broken", "parser exploded")

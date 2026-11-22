"""Test doubles for the CI HTTP seam, and a Jenkins build log that fails one test."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from gerrit_workflow_tools.core.ci_http import DEFAULT_MAX_BYTES, CiHttpError, HttpText


JENKINS = "https://ci.example.org/job/Widget/55"

FAILING_LOG = """\
Started by Gerrit
+ python3 -m pytest
=================================== FAILURES ===================================
_________________________________ test_divide __________________________________
E       ZeroDivisionError: division by zero
demo/calc.py:6: ZeroDivisionError
=========================== short test summary info ============================
FAILED tests/test_calc.py::test_divide - ZeroDivisionError: division by zero
Finished: FAILURE
"""


@dataclass
class FakeCiHttp:
    """Answers GETs from a URL -> text map; a URL mapped to an int fails with that HTTP status."""

    pages: dict[str, str | int] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def get_text(
        self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> HttpText:
        self.calls.append((url, dict(headers or {})))
        page = self.pages.get(url, 404)
        if isinstance(page, int):
            raise CiHttpError(f"HTTP {page} for {url}", status=page)
        return HttpText(url=url, text=page[:max_bytes], truncated=len(page) > max_bytes)

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


def jenkins_build(
    base: str = JENKINS, *, log: str = FAILING_LOG, building: bool = False, name: str = "Widget #55"
) -> dict[str, str | int]:
    """The pages a Jenkins build serves: ``api/json`` and ``consoleText``."""
    state = json.dumps({"building": building, "fullDisplayName": name, "url": f"{base}/"})
    return {f"{base}/api/json?tree=building,fullDisplayName,url": state, f"{base}/consoleText": log}

"""Where Gerrit's **event stream** comes from: ``ssh … gerrit stream-events``, and the seam tests replace.

Optional by nature: it needs ssh, an ssh key Gerrit accepts and the *Stream Events* capability,
and many users have only HTTP. Nothing here prompts — ssh runs in batch mode without a tty, so
a missing key or an unknown host key is a failure to report, never a question. Reference:
``docu/gerrit/md/download/Documentation/cmd-stream-events.md`` (Gerrit's ``cmd-stream-events``).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from gerrit_workflow_tools.core.config import Settings


logger = logging.getLogger(__name__)

# The events that can move what ger shows (Gerrit has no attention-set event: polling covers it).
SUBSCRIBED_EVENTS = (
    "patchset-created",
    "comment-added",
    "change-merged",
    "change-abandoned",
    "change-restored",
    "change-deleted",
    "reviewer-added",
    "reviewer-deleted",
    "vote-deleted",
    "topic-changed",
    "hashtags-changed",
    "wip-state-changed",
    "private-state-changed",
    "ref-updated",
    "dropped-output",
)
DEFAULT_SSH_PORT = 29418
CONNECT_TIMEOUT_SECONDS = 10
_STDERR_LINES = 20


class EventSourceUnavailable(RuntimeError):
    """The event stream cannot be started at all: no ssh program, or nothing to connect to."""


class EventConnection(Protocol):
    """One open event stream: its lines until it ends, and why it ended."""

    def lines(self) -> Iterator[str]:
        """Each event line as it arrives; returns when the stream ends or is closed."""
        ...

    def failure(self) -> str:
        """Why the stream ended, in a few words, once :meth:`lines` has returned."""
        ...

    def close(self) -> None:
        """End the stream; :meth:`lines` returns soon after. Safe to call more than once."""
        ...


class EventSource(Protocol):
    """Opens event streams; :class:`SshEventSource` in production, a scripted one in tests."""

    @property
    def description(self) -> str:
        """Where the events come from, for status and log lines."""
        ...

    def connect(self) -> EventConnection:
        """Open a stream. Raises :class:`EventSourceUnavailable` when there is no way to try."""
        ...


@dataclass(frozen=True)
class SshTarget:
    """Gerrit's ssh daemon: host, port and user (``None``: what the ssh config says)."""

    host: str
    port: int | None = None
    user: str | None = None

    def describe(self) -> str:
        """``ssh://user@host:port``."""
        user = f"{self.user}@" if self.user else ""
        port = f":{self.port}" if self.port else ""
        return f"ssh://{user}{self.host}{port}"

    def argv(self, ssh: str = "ssh") -> list[str]:
        """The non-interactive ``ssh … gerrit stream-events -s …`` command line."""
        options = ["BatchMode=yes", f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}", "ServerAliveInterval=30"]
        options.append("ServerAliveCountMax=3")
        cmd = [ssh, "-T", *(arg for option in options for arg in ("-o", option))]
        if self.port:
            cmd += ["-p", str(self.port)]
        if self.user:
            cmd += ["-l", self.user]
        cmd += [self.host, "gerrit", "stream-events"]
        return cmd + [arg for event in SUBSCRIBED_EVENTS for arg in ("-s", event)]


def _ssh_remote(url: str) -> SshTarget | None:
    """The ssh daemon an ``ssh://`` or scp-like (``user@host:path``) remote URL names, else ``None``."""
    if url.startswith("ssh://"):
        parts = urlsplit(url)
        try:
            port = parts.port
        except ValueError:
            port = None
        return SshTarget(parts.hostname, port, parts.username) if parts.hostname else None
    if "://" in url or ":" not in url:
        return None
    head = url.split(":", 1)[0]
    user, _, host = head.rpartition("@")
    # A drive letter (C:\repo) or a path (./a:b) is not a host.
    if len(host) < 2 or "/" in host or "\\" in host:
        return None
    return SshTarget(host, None, user or None)


def discover_ssh_target(settings: Settings) -> SshTarget:
    """Gerrit's ssh daemon for this repository, from what ger already knows. No network.

    ``ger.eventStreamHost`` / ``…Port`` / ``…User`` win. Otherwise the ``gerrit.remote`` URL when
    it is an ssh URL, else the host of ``gerrit.webUrl`` on Gerrit's default port 29418 with
    ``gerrit.user``. Raises :class:`EventSourceUnavailable` when no host can be named.
    """
    remote_url = settings.remote_url(settings.gerrit_remote) or ""
    remote = _ssh_remote(remote_url.strip())
    web_host = urlsplit(settings.gerrit_web_url or "").hostname
    host = settings.event_stream_ssh("Host") or (remote.host if remote else web_host)
    if not host:
        raise EventSourceUnavailable("no Gerrit host to connect to: set gerrit.webUrl or ger.eventStreamHost")
    raw_port = settings.event_stream_ssh("Port")
    if raw_port is not None and not (raw_port.isdigit() and 0 < int(raw_port) < 65536):
        raise EventSourceUnavailable(f"ger.eventStreamPort is not a port: {raw_port!r}")
    from_remote = remote is not None and host == remote.host
    if raw_port is not None:
        port: int | None = int(raw_port)
    else:
        port = remote.port if from_remote and remote else DEFAULT_SSH_PORT
    user = (
        settings.event_stream_ssh("User") or (remote.user if from_remote and remote else None) or settings.gerrit_user
    )
    return SshTarget(host, port, user)


class _SshConnection:
    """A running ``ssh … gerrit stream-events``: stdout is the stream, stderr says why it ended."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self._stderr: deque[str] = deque(maxlen=_STDERR_LINES)
        self._drain = threading.Thread(target=self._read_stderr, name="ger-events-ssh-stderr", daemon=True)
        self._drain.start()

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for raw in self._process.stderr:
            text = raw.decode("utf-8", "replace").strip()
            if text:
                self._stderr.append(text)

    def lines(self) -> Iterator[str]:
        """Each line ssh prints on stdout, until it exits."""
        assert self._process.stdout is not None
        for raw in self._process.stdout:
            yield raw.decode("utf-8", "replace").rstrip("\r\n")

    def failure(self) -> str:
        """The last thing ssh (or Gerrit through it) said, or its exit code."""
        try:
            code = self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        self._drain.join(timeout=2)
        said = [line for line in self._stderr if not line.lower().startswith("warning: permanently added")]
        if said:
            return said[-1]
        return f"ssh exited with code {code}" if code else "the stream ended"

    def close(self) -> None:
        """Stop ssh; kill it when it does not stop."""
        process = self._process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


class SshEventSource:
    """Gerrit's event stream over ssh, one ``ssh`` child per connection."""

    def __init__(self, target: SshTarget, *, ssh: str = "ssh") -> None:
        self.target = target
        self._ssh = ssh

    @property
    def description(self) -> str:
        """``ssh://user@host:port``."""
        return self.target.describe()

    def connect(self) -> EventConnection:
        """Start ssh; raises :class:`EventSourceUnavailable` when there is no ssh program."""
        program = shutil.which(self._ssh)
        if program is None:
            raise EventSourceUnavailable("no ssh program on PATH")
        argv = self.target.argv(program)
        logger.debug("event stream: %s", " ".join(argv))
        # No console window for ssh when ger serve runs without one (an editor started it).
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags
            )
        except OSError as error:
            raise EventSourceUnavailable(f"could not run ssh: {error}") from error
        return _SshConnection(process)

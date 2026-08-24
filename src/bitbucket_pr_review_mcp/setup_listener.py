"""The loopback listener that exists only while this server has no usable credential.

ADR-0004: bound to 127.0.0.1 on a port the OS picks, reachable only with a one-time
token, and gone the moment a credential is saved or five minutes pass — whichever comes
first. It is opened on observed facts (no credential, an expired one, a real 401) and
never because something asked for it, which is why `start` is not reachable from any
tool argument. A pull request whose description could raise a credential form would be
phishing aimed at a Reviewer already expecting the tool to do things.

It runs in a daemon thread with its own event loop, because the MCP server owns the main
one and neither should be able to block the other.
"""

from __future__ import annotations

import secrets
import socket
import threading
import time
from collections.abc import Callable

import uvicorn
from loguru import logger

from .credentials import StoredCredential
from .keychain import Keychain
from .setup_app import SETUP_PATH, HostPolicy, Verify, build_setup_app

LIFETIME_SECONDS = 300.0
HOST = "127.0.0.1"

Saved = Callable[[StoredCredential], None]


class SetupListener:
    """Serves the setup page for as long as — and no longer than — it is needed."""

    def __init__(
        self,
        keychain: Keychain,
        verify: Verify,
        lifetime_seconds: float = LIFETIME_SECONDS,
    ) -> None:
        self._keychain = keychain
        self._verify = verify
        self._lifetime = lifetime_seconds
        self._lock = threading.Lock()
        self._url: str | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._deadline: threading.Timer | None = None

    @property
    def running(self) -> bool:
        return self._url is not None

    @property
    def url(self) -> str | None:
        return self._url

    def start(self, on_saved: Saved) -> str:
        """Open the listener if it is closed, and return the URL to hand the Reviewer."""
        with self._lock:
            if self._url is not None:
                return self._url

            token = secrets.token_urlsafe(32)
            bound = self._bind()
            port = bound.getsockname()[1]

            app = build_setup_app(
                one_time_token=token,
                host_policy=HostPolicy(port=port),
                verify=self._verify,
                keychain=self._keychain,
                on_saved=lambda stored: self._saved(stored, on_saved),
            )
            server = uvicorn.Server(
                uvicorn.Config(app, log_config=None, access_log=False, lifespan="off")
            )
            thread = threading.Thread(
                target=server.run,
                kwargs={"sockets": [bound]},
                name="bb-pr-mcp-setup",
                daemon=True,
            )
            thread.start()
            _await_startup(server)

            self._server, self._thread, self._url = server, thread, _url(port, token)
            self._deadline = threading.Timer(self._lifetime, self._expire)
            self._deadline.daemon = True
            self._deadline.start()

            logger.info("Credential setup is open at {} for {:.0f}s.", self._url, self._lifetime)
            return self._url

    def stop(self) -> None:
        """Shut the listener down. Safe to call when it is already closed."""
        with self._lock:
            self._shutdown()

    def _bind(self) -> socket.socket:
        """Bind first, so the port is known before the app that validates it is built."""
        bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bound.bind((HOST, 0))  # 0: the OS picks, so the port is not guessable in advance
        bound.listen(8)
        return bound

    def _saved(self, stored: StoredCredential, on_saved: Saved) -> None:
        on_saved(stored)
        # Shut down from another thread: this one is still writing the "you're connected"
        # response, and uvicorn finishes in-flight responses before it exits.
        threading.Thread(target=self.stop, name="bb-pr-mcp-setup-stop", daemon=True).start()

    def _expire(self) -> None:
        if self._url is not None:
            logger.info("Credential setup closed after {:.0f}s unused.", self._lifetime)
        self.stop()

    def _shutdown(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._deadline, self._server, self._thread, self._url = None, None, None, None


def _url(port: int, token: str) -> str:
    return f"http://{HOST}:{port}{SETUP_PATH}?token={token}"


def _await_startup(server: uvicorn.Server, timeout: float = 10.0) -> None:
    """Wait for uvicorn to be accepting connections, or say so rather than hand out a
    URL that answers nothing."""
    give_up = time.monotonic() + timeout
    while not server.started:
        if time.monotonic() > give_up:
            raise RuntimeError("The credential setup listener did not start.")
        time.sleep(0.01)

"""The listener's lifecycle: where it binds, how it is reached, and when it stops.

These tests use a real socket on loopback, because "binds 127.0.0.1 on a random port"
and "stops on first save" are claims about a running server, and asserting them against
an in-process app would be asserting them about something else. Everything about the
page's behaviour is tested at the ASGI level in test_setup_app.py.
"""

from __future__ import annotations

import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from bitbucket_pr_review_mcp.scopes import review_scopes
from bitbucket_pr_review_mcp.setup_listener import SetupListener
from bitbucket_pr_review_mcp.verify import Identity

GOOD_SCOPES = (
    "read:user:bitbucket, read:repository:bitbucket, write:pullrequest:bitbucket"
)


async def verifier(credential):
    return Identity(
        display_name="Anwar Hossain", account_id="x", scopes=review_scopes(GOOD_SCOPES)
    )


@pytest.fixture
def listener(keychain):
    made = SetupListener(keychain=keychain, verify=verifier, lifetime_seconds=30)
    yield made
    made.stop()


def form(url: str) -> dict[str, str]:
    return {
        "email": "reviewer@streamstech.com",
        "api_token": "a-real-token",
        "expires_on": "2027-01-31",
        "setup_token": parse_qs(urlparse(url).query)["token"][0],
    }


class TestWhereItListens:
    def test_it_binds_loopback_on_a_port_the_os_picked(self, listener):
        url = urlparse(listener.start(lambda _: None))

        assert url.hostname == "127.0.0.1"
        assert url.port and url.port > 0

    def test_the_url_carries_a_one_time_token(self, listener):
        url = listener.start(lambda _: None)

        assert len(parse_qs(urlparse(url).query)["token"][0]) >= 32

    def test_two_ports_in_a_row_are_not_the_same_link(self, keychain):
        first = SetupListener(keychain, verifier)
        second = SetupListener(keychain, verifier)
        try:
            assert first.start(lambda _: None) != second.start(lambda _: None)
        finally:
            first.stop()
            second.stop()

    def test_asking_twice_does_not_open_a_second_listener(self, listener):
        first = listener.start(lambda _: None)

        assert listener.start(lambda _: None) == first


class TestOverARealSocket:
    def test_the_page_answers_on_the_url_it_handed_out(self, listener):
        url = listener.start(lambda _: None)

        page = httpx.get(url, timeout=5)

        assert page.status_code == 200
        assert "Atlassian account email" in page.text

    def test_saving_stores_the_credential_and_closes_the_listener(self, listener, keychain):
        saved = []
        url = listener.start(saved.append)
        root = url.split("/setup")[0]

        httpx.post(f"{root}/verify", data=form(url), timeout=5)
        done = httpx.post(f"{root}/save", data={"setup_token": form(url)["setup_token"]}, timeout=5)

        assert done.status_code == 200
        assert keychain.load() is not None
        assert keychain.load().expires_on == date(2027, 1, 31)
        assert len(saved) == 1
        _until(lambda: not listener.running, "the listener should close on first save")

    def test_nothing_answers_once_it_has_stopped(self, listener):
        url = listener.start(lambda _: None)
        listener.stop()

        with pytest.raises(httpx.ConnectError):
            httpx.get(url, timeout=5)


class TestItDoesNotLinger:
    def test_it_closes_itself_after_its_lifetime(self, keychain):
        brief = SetupListener(keychain=keychain, verify=verifier, lifetime_seconds=0.4)
        brief.start(lambda _: None)

        _until(lambda: not brief.running, "the listener should expire on its own")

    def test_stopping_twice_is_not_an_error(self, listener):
        listener.start(lambda _: None)
        listener.stop()
        listener.stop()

        assert not listener.running


def _until(condition, message: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(message)

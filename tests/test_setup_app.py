"""The credential setup page, exercised as an ASGI app in this process.

Real routing, real header validation, real form parsing — the seam is the transport, as
everywhere else in this project. What is worth reading here is the refusals: this app is
briefly the most dangerous thing in the repository, because it is an HTTP server that
writes credentials and it runs on a Reviewer's laptop. ADR-0004 bounds it to loopback, a
one-time token, and five minutes; these tests are what hold those bounds in place.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from bitbucket_pr_review_mcp.client import BitbucketError, Unauthorized
from bitbucket_pr_review_mcp.scopes import review_scopes
from bitbucket_pr_review_mcp.setup_app import HostPolicy, build_setup_app
from bitbucket_pr_review_mcp.verify import Identity

ONE_TIME = "one-time-token"
PORT = 54321
ORIGIN = f"http://127.0.0.1:{PORT}"
TODAY = date(2026, 8, 24)
GOOD_SCOPES = "read:repository:bitbucket, write:pullrequest:bitbucket"

FORM = {
    "email": "reviewer@streamstech.com",
    "api_token": "a-real-token",
    "expires_on": "2026-12-31",
    "setup_token": ONE_TIME,
}

# The confirm step carries no secret: what was verified waits in the listener's memory
# rather than travelling back through the page.
CONFIRM = {"setup_token": ONE_TIME}


class Verifier:
    """Stands in for Bitbucket. Records what it was asked to verify."""

    def __init__(self, identity=None, raises=None):
        self.identity = identity or Identity(
            display_name="Anwar Hossain",
            account_id="5f8a1b2c",
            scopes=review_scopes(GOOD_SCOPES),
        )
        self.raises = raises
        self.seen = []

    async def __call__(self, credential):
        self.seen.append(credential)
        if self.raises:
            raise self.raises
        return self.identity


@pytest.fixture
def verifier():
    return Verifier()


@pytest.fixture
def saved():
    return []


def make_app(verifier, keychain, saved):
    return build_setup_app(
        one_time_token=ONE_TIME,
        host_policy=HostPolicy(port=PORT),
        verify=verifier,
        keychain=keychain,
        on_saved=saved.append,
        today=lambda: TODAY,
    )


@pytest.fixture
def browser(verifier, keychain, saved):
    app = make_app(verifier, keychain, saved)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


def browser_for(verifier, keychain):
    app = make_app(verifier, keychain, [])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


class TestThePage:
    async def test_names_the_exact_scopes_to_tick(self, browser):
        page = await browser.get(f"/setup?token={ONE_TIME}")

        assert page.status_code == 200
        assert "read:repository:bitbucket" in page.text
        assert "write:pullrequest:bitbucket" in page.text

    async def test_links_to_where_tokens_are_created(self, browser):
        page = await browser.get(f"/setup?token={ONE_TIME}")

        assert "https://id.atlassian.com/manage-profile/security/api-tokens" in page.text

    async def test_labels_the_field_as_the_atlassian_account_email(self, browser):
        page = await browser.get(f"/setup?token={ONE_TIME}")

        assert "Atlassian account email" in page.text

    async def test_sets_no_cors_headers(self, browser):
        page = await browser.get(f"/setup?token={ONE_TIME}")

        assert not [h for h in page.headers if h.lower().startswith("access-control-")]


class TestTheOneTimeToken:
    async def test_a_missing_token_is_refused(self, browser):
        assert (await browser.get("/setup")).status_code == 403

    async def test_a_wrong_token_is_refused(self, browser):
        assert (await browser.get("/setup?token=guessed")).status_code == 403

    async def test_a_used_token_is_refused_afterwards(self, browser):
        await browser.post("/verify", data=FORM)
        await browser.post("/save", data=CONFIRM)

        assert (await browser.get(f"/setup?token={ONE_TIME}")).status_code == 403

    async def test_a_post_without_the_token_is_refused(self, browser, keychain):
        response = await browser.post("/verify", data={**FORM, "setup_token": ""})

        assert response.status_code == 403
        assert keychain.load() is None


class TestOriginAndHost:
    """DNS rebinding: an attacker's page can reach a loopback port, but it cannot make
    the browser lie about the Host it asked for or hide the Origin it came from."""

    async def test_a_foreign_host_header_is_refused(self, browser):
        response = await browser.get(f"/setup?token={ONE_TIME}", headers={"host": "evil.test"})

        assert response.status_code == 403

    async def test_a_foreign_origin_is_refused(self, browser):
        response = await browser.post("/verify", data=FORM, headers={"origin": "https://evil.test"})

        assert response.status_code == 403

    async def test_a_wrong_port_on_the_right_host_is_refused(self, browser):
        response = await browser.get(f"/setup?token={ONE_TIME}", headers={"host": "127.0.0.1:9999"})

        assert response.status_code == 403

    async def test_localhost_is_accepted(self, browser):
        response = await browser.get(
            f"/setup?token={ONE_TIME}", headers={"host": f"localhost:{PORT}"}
        )

        assert response.status_code == 200


class TestVerifyingBeforeStoring:
    async def test_it_shows_the_display_name_back_and_stores_nothing_yet(
        self, browser, keychain, saved
    ):
        response = await browser.post("/verify", data=FORM, headers={"origin": ORIGIN})

        assert response.status_code == 200
        assert "Anwar Hossain" in response.text
        assert keychain.load() is None, "nothing is stored until the Reviewer confirms"
        assert saved == []

    async def test_a_bitbucket_username_is_rejected_at_the_form(self, browser, verifier):
        response = await browser.post("/verify", data={**FORM, "email": "anwar"})

        assert response.status_code == 400
        assert "Atlassian account email" in response.text
        assert verifier.seen == [], "an identifier we know is wrong never reaches Bitbucket"

    async def test_a_wrong_token_fails_here_not_on_the_next_review(self, keychain):
        rejecting = Verifier(raises=Unauthorized("Bitbucket rejected the credential."))

        async with browser_for(rejecting, keychain) as browser:
            response = await browser.post("/verify", data=FORM)

        assert response.status_code == 400
        assert "rejected" in response.text.lower()
        assert keychain.load() is None

    async def test_an_over_broad_token_is_refused_and_not_stored(self, keychain):
        wide = Verifier(
            identity=Identity(
                display_name="Anwar Hossain",
                account_id="x",
                scopes=review_scopes(f"{GOOD_SCOPES}, admin:repository:bitbucket"),
            )
        )

        async with browser_for(wide, keychain) as browser:
            response = await browser.post("/verify", data=FORM)

        assert response.status_code == 400
        assert "admin:repository:bitbucket" in response.text
        assert keychain.load() is None

    async def test_a_bitbucket_outage_is_reported_rather_than_stored_through(self, keychain):
        broken = Verifier(raises=BitbucketError("Bitbucket returned 503 for GET /2.0/user."))

        async with browser_for(broken, keychain) as browser:
            response = await browser.post("/verify", data=FORM)

        assert response.status_code == 400
        assert keychain.load() is None

    @pytest.mark.parametrize("expiry", ["", "soon", "2026-08-01"])
    async def test_a_bad_expiry_date_is_rejected_at_the_form(self, browser, expiry, keychain):
        response = await browser.post("/verify", data={**FORM, "expires_on": expiry})

        assert response.status_code == 400
        assert keychain.load() is None


class TestSaving:
    async def test_it_stores_what_was_verified_a_moment_earlier(self, browser, keychain, verifier):
        await browser.post("/verify", data=FORM, headers={"origin": ORIGIN})
        response = await browser.post("/save", data=CONFIRM, headers={"origin": ORIGIN})

        assert response.status_code == 200
        stored = keychain.load()
        assert stored is not None
        assert stored.credential.email == "reviewer@streamstech.com"
        assert stored.credential.token == "a-real-token"
        assert stored.expires_on == date(2026, 12, 31)
        assert len(verifier.seen) == 1

    async def test_confirming_without_verifying_first_stores_nothing(self, browser, keychain):
        response = await browser.post("/save", data=CONFIRM)

        assert response.status_code == 400
        assert keychain.load() is None

    async def test_it_tells_the_listener_it_is_done(self, browser, saved):
        await browser.post("/verify", data=FORM)
        await browser.post("/save", data=CONFIRM)

        assert len(saved) == 1

    async def test_the_page_closes_itself(self, browser):
        await browser.post("/verify", data=FORM)
        response = await browser.post("/save", data=CONFIRM)

        assert "window.close()" in response.text

    async def test_the_api_token_is_never_written_into_a_page(self, browser):
        page = await browser.get(f"/setup?token={ONE_TIME}")
        verify_page = await browser.post("/verify", data=FORM)
        save_page = await browser.post("/save", data=CONFIRM)

        assert "a-real-token" not in page.text
        assert "a-real-token" not in verify_page.text
        assert "a-real-token" not in save_page.text


class TestNothingElseIsServed:
    async def test_an_unknown_path_is_not_found(self, browser):
        assert (await browser.get(f"/anything?token={ONE_TIME}")).status_code == 404

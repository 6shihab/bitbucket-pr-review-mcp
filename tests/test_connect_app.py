"""Connecting a Bitbucket account on a server several people share.

The property under test is the one ticket 13 exists for: **holding the link is not
enough**. Everything else here — the verification, the display name shown back, the
scope refusal — is the loopback page's behaviour, kept.
"""

from __future__ import annotations

import time
from datetime import date

import httpx
import pytest
from starlette.applications import Starlette

from bitbucket_pr_review_mcp.connect_app import (
    SESSION_COOKIE,
    ConnectHere,
    build_connect_app,
    cookie_secret,
)
from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.oidc import RelyingParty
from bitbucket_pr_review_mcp.scopes import review_scopes
from bitbucket_pr_review_mcp.tokens import SigningKeys, person_id
from bitbucket_pr_review_mcp.vault import CredentialVault, VaultKey
from bitbucket_pr_review_mcp.verify import Identity

from .oauth import ISSUER, KID, Keycloak, new_key, public_jwk

PUBLIC = "https://review.streamstech.com"
CLIENT = "bitbucket-pr-review-web"
SECRET = cookie_secret(b"a key for the cookies, derived from the vault key")

GOOD = (
    "read:user:bitbucket, read:repository:bitbucket, "
    "read:pullrequest:bitbucket, write:pullrequest:bitbucket"
)


@pytest.fixture(scope="module")
def signing():
    return new_key()


@pytest.fixture
def keycloak(signing) -> Keycloak:
    return Keycloak([public_jwk(signing, KID)])


@pytest.fixture
def vault(tmp_path):
    store = CredentialVault.at(tmp_path / "credentials.sqlite3", VaultKey.generate())
    yield store
    store.close()


@pytest.fixture
def verified() -> Identity:
    return Identity(
        account_id="alice-account",
        display_name="Alice Example",
        scopes=review_scopes(GOOD),
    )


@pytest.fixture
def app(keycloak, vault, verified) -> Starlette:
    async def verify(credential: Credential) -> Identity:
        return verified

    party = RelyingParty(
        issuer=ISSUER,
        client_id=CLIENT,
        client_secret="secret",
        redirect_uri=f"{PUBLIC}/connect/callback",
        keys=SigningKeys(ISSUER, keycloak.client()),
        http=keycloak.client(),
    )
    return build_connect_app(
        party=party,
        vault=vault,
        verify=verify,
        secret=SECRET,
        public_url=f"{PUBLIC}/mcp",
        today=lambda: date(2026, 8, 25),
        now=time.time,
    )


@pytest.fixture
async def browser(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as client:
        yield client


def signed_in_as(subject: str = "alice-sub", name: str = "Alice") -> dict:
    """A session cookie for somebody who has already been through Keycloak."""
    from bitbucket_pr_review_mcp.connect_app import _seal

    payload = {"sub": subject, "iss": ISSUER, "name": name, "email": None, "csrf": "csrf-value"}
    return {SESSION_COOKIE: _seal(payload, SECRET, time.time)}


def form(**fields) -> dict:
    return {"csrf": "csrf-value", **fields}


class TestHoldingTheLinkIsNotEnough:
    """The whole point of the ticket. On a public URL the danger is not that somebody
    reads a credential — it is that they *supply* one, into somebody else's slot."""

    async def test_an_anonymous_visitor_is_sent_to_sign_in(self, browser):
        response = await browser.get("/", follow_redirects=False)

        assert response.status_code == 303
        assert (
            "openid-connect/auth" in response.headers["location"]
            or "authorize" in (response.headers["location"])
        )

    async def test_they_cannot_store_a_credential_without_signing_in(self, browser, vault):
        response = await browser.post(
            "/verify",
            data=form(email="attacker@evil.example", api_token="theirs", expires_on="2027-01-01"),
        )

        assert response.status_code == 401
        assert vault.enrolled() == []

    async def test_they_cannot_save_one_either(self, browser, vault):
        assert (await browser.post("/save", data=form())).status_code == 401
        assert vault.enrolled() == []

    async def test_they_cannot_disconnect_anybody(self, browser, vault):
        vault.save(
            person_id(ISSUER, "alice-sub"),
            StoredCredential(
                credential=Credential(email="alice@streamstech.com", token="hers"),
                expires_on=date(2099, 1, 1),
            ),
        )

        assert (await browser.post("/forget", data=form())).status_code == 401
        assert vault.load(person_id(ISSUER, "alice-sub")) is not None

    async def test_a_forged_session_cookie_is_not_a_session(self, browser, vault):
        response = await browser.post(
            "/verify",
            data=form(email="a@b.com", api_token="t", expires_on="2027-01-01"),
            cookies={SESSION_COOKIE: "eyJzdWIiOiJhbGljZSJ9.not-a-real-signature"},
        )

        assert response.status_code == 401
        assert vault.enrolled() == []


class TestSomebodySignedIn:
    async def test_they_are_offered_the_form(self, browser):
        response = await browser.get("/", cookies=signed_in_as())

        assert response.status_code == 200
        assert "Atlassian account email" in response.text

    async def test_the_page_says_who_they_are(self, browser):
        response = await browser.get("/", cookies=signed_in_as(name="Alice Example"))

        assert "Alice Example" in response.text

    async def test_verifying_shows_the_bitbucket_name_back_before_storing(self, browser, vault):
        response = await browser.post(
            "/verify",
            data=form(email="alice@streamstech.com", api_token="t", expires_on="2027-01-01"),
            cookies=signed_in_as(),
        )

        assert "Alice Example" in response.text
        assert vault.enrolled() == [], "nothing is stored until they confirm"

    async def test_confirming_stores_it_against_them_and_nobody_else(self, browser, vault):
        cookies = signed_in_as()
        await browser.post(
            "/verify",
            data=form(email="alice@streamstech.com", api_token="hers", expires_on="2027-01-01"),
            cookies=cookies,
        )

        response = await browser.post("/save", data=form(), cookies=cookies)

        assert response.status_code == 200
        stored = vault.load(person_id(ISSUER, "alice-sub"))
        assert stored.credential.email == "alice@streamstech.com"
        assert stored.credential.token == "hers"

    async def test_the_vault_key_is_the_one_the_token_check_computes(self, browser, vault):
        """If these two ever disagree the credential is written where nothing reads it,
        and the symptom is a person being asked to connect over and over."""
        cookies = signed_in_as()
        await browser.post(
            "/verify",
            data=form(email="alice@streamstech.com", api_token="hers", expires_on="2027-01-01"),
            cookies=cookies,
        )
        await browser.post("/save", data=form(), cookies=cookies)

        assert [row.person for row in vault.enrolled()] == [person_id(ISSUER, "alice-sub")]

    async def test_saving_without_verifying_stores_nothing(self, browser, vault):
        response = await browser.post("/save", data=form(), cookies=signed_in_as())

        assert "verified" in response.text
        assert vault.enrolled() == []

    async def test_one_persons_verification_is_not_anothers_to_save(self, browser, vault):
        await browser.post(
            "/verify",
            data=form(email="alice@streamstech.com", api_token="hers", expires_on="2027-01-01"),
            cookies=signed_in_as("alice-sub"),
        )

        await browser.post("/save", data=form(), cookies=signed_in_as("bob-sub", "Bob"))

        assert vault.load(person_id(ISSUER, "bob-sub")) is None


class TestDisconnecting:
    async def test_it_removes_their_credential(self, browser, vault):
        cookies = signed_in_as()
        await browser.post(
            "/verify",
            data=form(email="alice@streamstech.com", api_token="hers", expires_on="2027-01-01"),
            cookies=cookies,
        )
        await browser.post("/save", data=form(), cookies=cookies)

        await browser.post("/forget", data=form(), cookies=cookies)

        assert vault.load(person_id(ISSUER, "alice-sub")) is None

    async def test_it_says_the_token_still_exists_at_atlassian(self, browser):
        response = await browser.post("/forget", data=form(), cookies=signed_in_as())

        assert "id.atlassian.com" in response.text
        assert "still exists" in response.text

    async def test_it_leaves_other_people_alone(self, browser, vault):
        vault.save(
            person_id(ISSUER, "bob-sub"),
            StoredCredential(
                credential=Credential(email="bob@streamstech.com", token="his"),
                expires_on=date(2099, 1, 1),
            ),
        )

        await browser.post("/forget", data=form(), cookies=signed_in_as("alice-sub"))

        assert vault.load(person_id(ISSUER, "bob-sub")) is not None


class TestWhatTheFormRefuses:
    async def test_a_bitbucket_username_instead_of_an_email(self, browser):
        response = await browser.post(
            "/verify",
            data=form(email="not-an-email", api_token="t", expires_on="2027-01-01"),
            cookies=signed_in_as(),
        )

        assert response.status_code == 400

    async def test_an_expiry_already_past(self, browser):
        response = await browser.post(
            "/verify",
            data=form(email="a@b.com", api_token="t", expires_on="2020-01-01"),
            cookies=signed_in_as(),
        )

        assert response.status_code == 400
        assert "past" in response.text

    async def test_a_token_that_can_do_more_than_review(self, browser, verified, vault):
        wide = Identity(
            account_id="alice-account",
            display_name="Alice Example",
            scopes=review_scopes(f"{GOOD}, admin:repository:bitbucket"),
        )
        object.__setattr__(verified, "scopes", wide.scopes)

        response = await browser.post(
            "/verify",
            data=form(email="a@b.com", api_token="t", expires_on="2027-01-01"),
            cookies=signed_in_as(),
        )

        assert response.status_code == 400
        assert vault.enrolled() == []


class TestCrossSiteRequests:
    async def test_a_post_from_another_origin_is_refused(self, browser, vault):
        response = await browser.post(
            "/verify",
            data=form(email="a@b.com", api_token="t", expires_on="2027-01-01"),
            cookies=signed_in_as(),
            headers={"origin": "https://evil.example"},
        )

        assert response.status_code == 403
        assert vault.enrolled() == []

    async def test_a_post_without_the_form_token_is_refused(self, browser, vault):
        response = await browser.post(
            "/verify",
            data={"email": "a@b.com", "api_token": "t", "expires_on": "2027-01-01"},
            cookies=signed_in_as(),
        )

        assert response.status_code == 403
        assert vault.enrolled() == []


class TestWhatACallerIsTold:
    def test_the_url_it_hands_back_is_the_connect_page(self):
        assert ConnectHere(f"{PUBLIC}/connect").start() == f"{PUBLIC}/connect"

    def test_it_does_not_repeat_the_loopback_promise(self):
        """That page expires in five minutes and works once. This one does neither, and
        saying otherwise would teach people to distrust the message."""
        note = ConnectHere(f"{PUBLIC}/connect").note

        assert "five minutes" not in note
        assert "sign in" in note

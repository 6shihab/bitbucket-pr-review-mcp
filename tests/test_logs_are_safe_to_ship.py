"""Logs from a shared server go somewhere central, and that is the point of them.

Which makes what is *in* them a security property rather than a tidiness one. A token in
a log line is a token in the aggregator, in its backups, in whatever index it builds, and
in the access of everybody who can search it — a much larger set than the people allowed
near the credential store.

So this exercises the paths that handle a secret, especially the failing ones, and goes
looking for every secret afterwards. Failure paths matter most: the happy path rarely
prints anything, and "let me add the response body to the error so we can debug it" is
how a token reaches a log.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

import httpx
import pytest
from loguru import logger
from starlette.applications import Starlette

from bitbucket_pr_review_mcp.connect_app import (
    SESSION_COOKIE,
    build_connect_app,
    cookie_secret,
)
from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.discovery import ProtectedResource
from bitbucket_pr_review_mcp.oidc import Handshake, RelyingParty
from bitbucket_pr_review_mcp.scopes import review_scopes
from bitbucket_pr_review_mcp.tokens import SigningKeys, TokenVerifier, person_id
from bitbucket_pr_review_mcp.vault import CredentialVault, VaultKey
from bitbucket_pr_review_mcp.verify import Identity

from .oauth import ISSUER, KID, RESOURCE, Keycloak, new_key, public_jwk, token_for

# Every secret this server touches, made findable.
BITBUCKET_TOKEN = "ATATT-canary-bitbucket-token-must-never-be-logged"
CLIENT_SECRET = "canary-oidc-client-secret-must-never-be-logged"
AUTH_CODE = "canary-authorization-code-must-never-be-logged"
PR_BODY = "canary-pull-request-body-belongs-in-a-fence-not-a-log"

GOOD_SCOPES = (
    "read:user:bitbucket, read:repository:bitbucket, "
    "read:pullrequest:bitbucket, write:pullrequest:bitbucket"
)


@pytest.fixture(scope="module")
def signing():
    return new_key()


@pytest.fixture
def key() -> VaultKey:
    return VaultKey.generate()


@pytest.fixture
def said():
    """Everything written to the log, at the most talkative level there is."""
    lines: list[str] = []
    sink = logger.add(lines.append, level="TRACE")
    yield lines
    logger.remove(sink)


def secrets_in(lines: list[str], key: VaultKey) -> list[str]:
    spoken = "\n".join(lines)
    return [
        name
        for name, secret in {
            "the Bitbucket token": BITBUCKET_TOKEN,
            "the OIDC client secret": CLIENT_SECRET,
            "the authorization code": AUTH_CODE,
            "the vault key": key.exported(),
            "the pull request body": PR_BODY,
        }.items()
        if secret in spoken
    ]


class TestTheVault:
    async def test_nothing_it_does_says_a_token_or_a_key(self, tmp_path, key, said):
        vault = CredentialVault.at(tmp_path / "credentials.sqlite3", key)
        person = person_id(ISSUER, "alice-sub")

        vault.save(
            person,
            StoredCredential(
                credential=Credential(email="alice@streamstech.com", token=BITBUCKET_TOKEN),
                expires_on=date.today() + timedelta(days=90),
            ),
        )
        vault.load(person)
        vault.enrolled()
        vault.rotate(VaultKey.generate())
        vault.clear(person)
        vault.close()

        assert secrets_in(said, key) == []

    async def test_a_row_it_cannot_read_complains_without_quoting_it(
        self, tmp_path, key, said
    ):
        import sqlite3

        path = tmp_path / "credentials.sqlite3"
        vault = CredentialVault.at(path, key)
        person = person_id(ISSUER, "alice-sub")
        vault.save(
            person,
            StoredCredential(
                credential=Credential(email="alice@streamstech.com", token=BITBUCKET_TOKEN),
                expires_on=date.today() + timedelta(days=90),
            ),
        )
        vault.close()

        damaged = sqlite3.connect(path)
        damaged.execute("UPDATE credentials SET sealed = ? WHERE person = ?", (b"rubbish", person))
        damaged.commit()
        damaged.close()

        reopened = CredentialVault.at(path, key)
        reopened.enrolled()
        reopened.close()

        assert secrets_in(said, key) == []


class TestVerifyingAToken:
    @pytest.fixture
    def verifier(self, signing) -> TokenVerifier:
        keycloak = Keycloak([public_jwk(signing, KID)])
        return TokenVerifier(
            resource=ProtectedResource.of(RESOURCE, ISSUER),
            keys=SigningKeys(ISSUER, keycloak.client()),
        )

    async def test_no_refusal_quotes_the_token_it_refused(self, verifier, signing, key, said):
        from bitbucket_pr_review_mcp.tokens import TokenRejected

        attempts = [
            None,
            f"Bearer {BITBUCKET_TOKEN}",
            f"Bearer {token_for(signing, scope='openid')}",
            f"Bearer {token_for(signing, audience='https://elsewhere.example')}",
        ]
        for presented in attempts:
            with pytest.raises(TokenRejected):
                await verifier.verify(presented)

        assert secrets_in(said, key) == []
        assert BITBUCKET_TOKEN not in "\n".join(said)


class TestSigningSomebodyIn:
    """The token exchange is where a leak would be easiest: the authorization server's
    error body is right there, and it can carry the code."""

    @pytest.fixture
    def party(self, signing) -> RelyingParty:
        class Refusing(Keycloak):
            def handle(self, request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/protocol/openid-connect/token"):
                    return httpx.Response(
                        400,
                        json={"error": "invalid_grant", "seen_code": AUTH_CODE},
                    )
                return super().handle(request)

        realm = Refusing([public_jwk(signing, KID)])
        return RelyingParty(
            issuer=ISSUER,
            client_id="bitbucket-pr-review-web",
            client_secret=CLIENT_SECRET,
            redirect_uri="https://review.streamstech.com/connect/callback",
            keys=SigningKeys(ISSUER, realm.client()),
            http=realm.client(),
        )

    async def test_a_refused_exchange_logs_neither_the_code_nor_the_secret(
        self, party, key, said
    ):
        from bitbucket_pr_review_mcp.oidc import LoginFailed

        handshake = Handshake.begin()
        with pytest.raises(LoginFailed):
            await party.finish(AUTH_CODE, handshake, state=handshake.state)

        assert secrets_in(said, key) == []


class TestTheConnectPage:
    @pytest.fixture
    def vault(self, tmp_path, key):
        store = CredentialVault.at(tmp_path / "credentials.sqlite3", key)
        yield store
        store.close()

    @pytest.fixture
    def app(self, signing, vault) -> Starlette:
        async def verify(credential: Credential) -> Identity:
            return Identity(
                account_id="alice-account",
                display_name="Alice Example",
                scopes=review_scopes(GOOD_SCOPES),
            )

        keycloak = Keycloak([public_jwk(signing, KID)])
        party = RelyingParty(
            issuer=ISSUER,
            client_id="bitbucket-pr-review-web",
            client_secret=CLIENT_SECRET,
            redirect_uri="https://review.streamstech.com/connect/callback",
            keys=SigningKeys(ISSUER, keycloak.client()),
            http=keycloak.client(),
        )
        return build_connect_app(
            party=party,
            vault=vault,
            verify=verify,
            secret=cookie_secret(b"cookie signing material"),
            public_url="https://review.streamstech.com/mcp",
        )

    @pytest.fixture
    async def browser(self, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://review.streamstech.com"
        ) as client:
            yield client

    def session(self) -> dict:
        from bitbucket_pr_review_mcp.connect_app import _seal

        payload = {
            "sub": "alice-sub",
            "iss": ISSUER,
            "name": "Alice",
            "email": None,
            "csrf": "csrf-value",
        }
        secret = cookie_secret(b"cookie signing material")
        return {SESSION_COOKIE: _seal(payload, secret, time.time)}

    async def test_connecting_an_account_never_logs_the_token(self, browser, key, said):
        cookies = self.session()
        await browser.post(
            "/verify",
            data={
                "csrf": "csrf-value",
                "email": "alice@streamstech.com",
                "api_token": BITBUCKET_TOKEN,
                "expires_on": "2027-01-01",
            },
            cookies=cookies,
        )
        await browser.post("/save", data={"csrf": "csrf-value"}, cookies=cookies)
        await browser.post("/forget", data={"csrf": "csrf-value"}, cookies=cookies)

        assert secrets_in(said, key) == []

    async def test_a_refused_credential_is_not_logged_either(self, browser, key, said):
        """The message a person sees comes from Bitbucket. What reaches the log must not
        carry what they typed."""
        await browser.post(
            "/verify",
            data={
                "csrf": "csrf-value",
                "email": "not-an-email",
                "api_token": BITBUCKET_TOKEN,
                "expires_on": "2027-01-01",
            },
            cookies=self.session(),
        )

        assert secrets_in(said, key) == []

    async def test_the_session_cookie_is_not_logged(self, browser, key, said):
        cookies = self.session()
        await browser.get("/", cookies=cookies)

        assert cookies[SESSION_COOKIE] not in "\n".join(said)


class TestStructuredOutput:
    def test_every_line_is_one_json_object(self, capsys):
        from bitbucket_pr_review_mcp.__main__ import configure_logging

        configure_logging("INFO", structured=True)
        logger.info("Credential for {} stored.", "alice@streamstech.com")
        logger.warning("Something an aggregator should index.")

        lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]

        assert lines
        for line in lines:
            record = json.loads(line)["record"]
            assert record["level"]["name"] in {"INFO", "WARNING"}
            assert record["message"]

    def test_it_can_be_turned_off_again(self, capsys):
        from bitbucket_pr_review_mcp.__main__ import configure_logging

        configure_logging("INFO", structured=False)
        logger.info("A person is reading this in a terminal.")

        assert "A person is reading this" in capsys.readouterr().err

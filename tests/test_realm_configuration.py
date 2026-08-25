"""The Keycloak realm, checked against what running it actually taught us.

Every assertion here corresponds to something that was wrong when this realm was first
written and was found by asking Keycloak for a token rather than by reading about it.
A realm file is configuration, which is exactly the kind of thing that rots silently —
so the two findings that cost the most are pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bitbucket_pr_review_mcp.discovery import REVIEW_SCOPE, ProtectedResource

ROOT = Path(__file__).resolve().parents[1]
REALM = json.loads(
    (ROOT / "deploy" / "keycloak" / "realm-streamstech.json").read_text(encoding="utf-8")
)
COMPOSE = (ROOT / "docker-compose.shared.yaml").read_text(encoding="utf-8")
PUBLIC_HOST = "https://review.streamstech.com"

CLIENTS = {client["clientId"]: client for client in REALM["clients"]}
CLIENT = CLIENTS["bitbucket-pr-review"]
WEB = CLIENTS["bitbucket-pr-review-web"]
CLI = CLIENTS["bitbucket-pr-review-cli"]
SCOPES = {scope["name"]: scope for scope in REALM["clientScopes"]}


def mappers(scope: str) -> dict[str, dict]:
    return {mapper["name"]: mapper for mapper in SCOPES[scope].get("protocolMappers", [])}


class TestTheScopeIsAskedForRatherThanAssumed:
    """Finding: as a *default* client scope, Keycloak granted it whether or not it was
    requested — so a token minted with `scope=openid` still carried it, and the check
    this server makes was decorative. Optional is what makes asking mean something."""

    def test_the_review_scope_is_optional(self):
        assert REVIEW_SCOPE in CLIENT["optionalClientScopes"]

    def test_it_is_not_granted_by_default(self):
        assert REVIEW_SCOPE not in CLIENT["defaultClientScopes"]

    def test_it_is_shown_on_the_consent_screen(self):
        """A default scope is granted silently. The point of asking is that somebody is
        told what they are agreeing to."""
        attributes = SCOPES[REVIEW_SCOPE]["attributes"]

        assert attributes["display.on.consent.screen"] == "true"
        assert attributes["consent.screen.text"].strip()


class TestTheAudienceMapper:
    """Keycloak does not implement RFC 8707, so it ignores the `resource` parameter
    Claude sends. Without this mapper no token names this server, and the resource
    server refuses every one of them — correctly, and confusingly."""

    def test_it_exists_on_the_scope_this_server_requires(self):
        assert "mcp-audience" in mappers(REVIEW_SCOPE)

    def test_it_is_an_audience_mapper(self):
        assert mappers(REVIEW_SCOPE)["mcp-audience"]["protocolMapper"] == "oidc-audience-mapper"

    def test_it_puts_the_audience_in_the_access_token(self):
        config = mappers(REVIEW_SCOPE)["mcp-audience"]["config"]

        assert config["access.token.claim"] == "true"

    def test_the_audience_it_adds_is_a_resource_this_server_would_accept(self):
        audience = mappers(REVIEW_SCOPE)["mcp-audience"]["config"]["included.custom.audience"]

        assert ProtectedResource.of(audience, "https://example.com/realms/x").resource == audience

    def test_it_lives_on_the_optional_scope_so_an_unasked_token_has_no_audience(self):
        """Defence in depth, and it fell out of making the scope optional: a token that
        never asked for the review scope also never gets an audience for us, so it is
        refused twice over."""
        assert "mcp-audience" not in mappers("basic")


class TestTheBuiltInScopesAreRestated:
    """Finding: declaring any `clientScopes` in a realm import *replaces* Keycloak's
    built-in set rather than adding to it. The realm came up with only the scope we
    declared, so tokens carried no `sub` and this server rejected all of them."""

    def test_basic_is_declared(self):
        assert "basic" in SCOPES

    def test_it_carries_the_subject(self):
        assert mappers("basic")["sub"]["protocolMapper"] == "oidc-sub-mapper"

    def test_the_subject_reaches_the_access_token(self):
        assert mappers("basic")["sub"]["config"]["access.token.claim"] == "true"

    def test_the_client_is_given_it_by_default(self):
        assert "basic" in CLIENT["defaultClientScopes"]


class TestWhatClaudeNeedsToConnect:
    def test_the_hosted_claude_callback_is_registered(self):
        assert "https://claude.ai/api/mcp/auth_callback" in CLIENT["redirectUris"]

    def test_claude_code_loopback_callbacks_are_registered(self):
        """A native client on an ephemeral port. RFC 8252 says match ignoring the port."""
        for loopback in ("http://localhost/callback", "http://127.0.0.1/callback"):
            assert loopback in CLIENT["redirectUris"]

    def test_pkce_is_required_and_is_s256(self):
        assert CLIENT["attributes"]["pkce.code.challenge.method"] == "S256"

    def test_the_client_is_confidential_so_its_credentials_can_be_pre_registered(self):
        """Pre-registered id and secret, pasted once into the connector's Advanced
        settings, rather than Dynamic Client Registration — which would leave an open
        registration endpoint on a host holding the team's credentials."""
        assert CLIENT["publicClient"] is False

    def test_refresh_tokens_are_obtainable(self):
        assert "offline_access" in CLIENT["optionalClientScopes"]

    def test_the_implicit_flow_is_off(self):
        assert CLIENT["implicitFlowEnabled"] is False


class TestNobodyCanShipThisByAccident:
    @pytest.mark.parametrize(
        "secret",
        [CLIENT["secret"], REALM["users"][0]["credentials"][0]["value"]],
    )
    def test_every_credential_in_it_says_it_is_not_for_production(self, secret):
        assert any(word in secret.lower() for word in ("development", "not-for-production"))

    def test_the_compose_file_says_so_too(self):
        assert "DEVELOPMENT CONFIGURATION" in COMPOSE

    def test_self_registration_is_off(self):
        """Anyone able to reach Keycloak could otherwise make themselves an account."""
        assert REALM["registrationAllowed"] is False

    def test_access_tokens_are_short_lived(self):
        """The blast radius of a stolen one, in seconds. ADR-0002's problem does not go
        away, but this bounds how long a leaked token is worth having."""
        assert REALM["accessTokenLifespan"] <= 900


class TestTheThreeClients:
    """Three, because three different things authenticate, and giving them one client
    would mean one set of redirect URIs and one secret shared by all of them."""

    def test_the_connector_client_is_confidential(self):
        """Anthropic's servers hold its secret; nothing is on anybody's laptop."""
        assert CLIENT["publicClient"] is False

    def test_the_web_client_is_confidential(self):
        """It runs inside this server, which is the only place its secret exists."""
        assert WEB["publicClient"] is False
        assert f"{PUBLIC_HOST}/connect/callback" in WEB["redirectUris"] or any(
            uri.endswith("/connect/callback") for uri in WEB["redirectUris"]
        )

    def test_the_bridge_client_is_public_and_holds_no_secret(self):
        """A secret in a config file on a laptop is not a secret. PKCE is the protection
        a loopback flow actually has."""
        assert CLI["publicClient"] is True
        assert "secret" not in CLI

    def test_the_bridge_client_requires_pkce(self):
        assert CLI["attributes"]["pkce.code.challenge.method"] == "S256"

    def test_it_registers_the_callback_mcp_remote_actually_uses(self):
        """The IP literal, not `localhost`, and `/oauth/callback`, not `/callback`.
        Registering only the other spelling fails at the very last step of the flow."""
        assert "http://127.0.0.1:3334/oauth/callback" in CLI["redirectUris"]

    def test_it_can_ask_for_the_review_scope(self):
        assert REVIEW_SCOPE in CLI["optionalClientScopes"]

    def test_no_client_grants_the_review_scope_without_being_asked(self):
        for name, client in CLIENTS.items():
            assert REVIEW_SCOPE not in client["defaultClientScopes"], name


class TestWhatKeycloakItselfWillAccept:
    @pytest.mark.parametrize(
        "described",
        [*REALM["clients"], *REALM["clientScopes"]],
        ids=lambda item: item.get("clientId") or item["name"],
    )
    def test_a_description_fits_the_column_it_is_stored_in(self, described):
        """Keycloak's CLIENT.DESCRIPTION is VARCHAR(255), and an over-long one does not
        truncate — the whole realm import fails and the server will not start."""
        assert len(described.get("description", "")) <= 255

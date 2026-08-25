"""What this server tells Claude about itself.

Small documents, and the failure mode when one is wrong is "Couldn't reach the MCP
server" with nothing else to go on — so the rules Anthropic's connector documentation is
stricter about than the RFC are asserted here rather than discovered at connection time.
"""

from __future__ import annotations

import pytest

from bitbucket_pr_review_mcp.discovery import (
    REVIEW_SCOPE,
    DiscoveryError,
    ProtectedResource,
)

PUBLIC = "https://review.streamstech.com/mcp"
ISSUER = "https://review.streamstech.com/realms/streamstech"


@pytest.fixture
def resource() -> ProtectedResource:
    return ProtectedResource.of(PUBLIC, ISSUER)


class TestTheDocument:
    def test_it_names_this_server_and_its_authorization_server(self, resource):
        document = resource.document()

        assert document["resource"] == PUBLIC
        assert document["authorization_servers"] == [ISSUER]

    def test_it_advertises_the_scope_a_caller_needs(self, resource):
        assert resource.document()["scopes_supported"] == [REVIEW_SCOPE]

    def test_it_does_not_advertise_offline_access(self, resource):
        """That is how a client asks for a refresh token, not something this resource
        requires, and the MCP specification says a resource server should not list it."""
        assert "offline_access" not in resource.document()["scopes_supported"]

    def test_tokens_arrive_in_a_header(self, resource):
        assert resource.document()["bearer_methods_supported"] == ["header"]


class TestWhereItIsServed:
    def test_the_resource_path_follows_the_well_known_segment(self, resource):
        """RFC 9728 puts it after, which is the form Claude probes first."""
        assert resource.metadata_path() == "/.well-known/oauth-protected-resource/mcp"

    def test_a_resource_with_no_path_gets_the_bare_well_known_path(self):
        bare = ProtectedResource.of("https://review.streamstech.com", ISSUER)

        assert bare.metadata_path() == "/.well-known/oauth-protected-resource"

    def test_the_url_is_absolute(self, resource):
        assert resource.metadata_url() == (
            "https://review.streamstech.com/.well-known/oauth-protected-resource/mcp"
        )


class TestTheChallenge:
    def test_it_points_at_the_metadata(self, resource):
        assert f'resource_metadata="{resource.metadata_url()}"' in resource.challenge()

    def test_it_says_which_scope_is_wanted(self, resource):
        assert f'scope="{REVIEW_SCOPE}"' in resource.challenge()

    def test_it_can_carry_an_error(self, resource):
        challenge = resource.challenge(error="insufficient_scope")

        assert challenge.startswith('Bearer error="insufficient_scope"')

    def test_it_is_a_bearer_challenge(self, resource):
        assert resource.challenge().startswith("Bearer ")


class TestWhatItRefusesToDescribe:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "review.streamstech.com/mcp",
            "https://review.streamstech.com/mcp#fragment",
            "https://review.streamstech.com/mcp?token=x",
            "http://review.streamstech.com/mcp",
        ],
    )
    def test_a_url_claude_would_reject_is_refused_here_instead(self, bad):
        with pytest.raises(DiscoveryError):
            ProtectedResource.of(bad, ISSUER)

    def test_plain_http_is_allowed_on_loopback_for_development(self):
        local = ProtectedResource.of("http://localhost:8080/mcp", "http://localhost:8080/realms/x")

        assert local.resource == "http://localhost:8080/mcp"

    def test_the_issuer_is_held_to_the_same_standard(self):
        with pytest.raises(DiscoveryError):
            ProtectedResource.of(PUBLIC, "not-a-url")

    def test_a_bare_origin_loses_its_trailing_slash(self):
        """The specification prefers the form without it, and the value has to match
        what the Owner typed into Claude — so pick one and be consistent."""
        assert ProtectedResource.of("https://review.streamstech.com/", ISSUER).resource == (
            "https://review.streamstech.com"
        )

    def test_a_real_path_keeps_its_shape(self):
        assert ProtectedResource.of(PUBLIC, ISSUER).resource == PUBLIC

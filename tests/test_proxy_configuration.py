"""The nginx in front of both services, checked for the two ways it has been wrong.

Both were found by deploying behind TLS and watching a browser go somewhere it could not
reach. Neither is visible by reading the file, which is what makes them worth pinning:
one is an nginx inheritance rule that does the opposite of what the layout suggests, and
the other is a variable that is correct on loopback and wrong everywhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NGINX = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

# `location <match> {  ... }` — one level deep, which is all this file has.
LOCATION = re.compile(r"location\s+(?P<match>[^{]+?)\s*\{(?P<body>[^{}]*)\}", re.DOTALL)


def locations() -> dict[str, str]:
    return {found["match"].strip(): found["body"] for found in LOCATION.finditer(NGINX)}


def headers_set(body: str) -> set[str]:
    return {
        found.group(1)
        for found in re.finditer(r"proxy_set_header\s+(\S+)", body)
    }


class TestHeadersAreRepeatedRatherThanInherited:
    """nginx does not merge `proxy_set_header` across levels. A block that sets any one
    of them discards every one declared above it — so a location that sets `Connection`
    to enable streaming silently stops sending `Host`, and nginx substitutes
    `$proxy_host`.

    The server then believes its own address is `127.0.0.1:8000`, and a browser that
    signed in on the real hostname is redirected there. It is a working server that
    nobody can reach, and nothing in the log looks wrong."""

    @pytest.mark.parametrize(
        "match", [match for match, body in locations().items() if headers_set(body)]
    )
    def test_a_location_that_sets_any_header_also_sets_host(self, match):
        assert "Host" in headers_set(locations()[match]), (
            f"`location {match}` sets some proxy_set_header directives, so it inherits "
            "none of the ones above it. It has to repeat Host, or nginx sends "
            "$proxy_host and the upstream builds its own URLs out of an address that is "
            "only reachable from inside this container."
        )

    @pytest.mark.parametrize(
        "match", [match for match, body in locations().items() if headers_set(body)]
    )
    def test_it_also_repeats_the_forwarded_set(self, match):
        """Host alone is not enough: the same discard takes X-Forwarded-Proto with it,
        and that is what tells Keycloak the request was https."""
        assert {"X-Forwarded-Proto", "X-Forwarded-For"} <= headers_set(
            locations()[match]
        )


class TestTheForwardedSchemeSurvivesTwoProxies:
    """TLS is terminated in front of this container, so the connection into it is plain
    http on 8080. `$scheme` is therefore `http` for every request that arrives, however
    the browser got here."""

    def test_the_scheme_is_not_taken_from_this_hop(self):
        assert "proxy_set_header X-Forwarded-Proto $scheme;" not in NGINX, (
            "this overwrites an outer proxy's `https` with `http`, and Keycloak trusts "
            "these headers — a public request then looks insecure and is refused"
        )

    def test_it_prefers_what_arrived_and_falls_back_to_this_hop(self):
        """A map rather than a bare variable, so the stack still works with nothing in
        front of it — which is how it is run locally."""
        assert "map $http_x_forwarded_proto $forwarded_proto" in NGINX
        assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in NGINX

    @pytest.mark.parametrize("name", ["proto", "host", "port"])
    def test_each_forwarded_value_has_a_fallback_for_a_direct_request(self, name):
        """The `""` case. Without it a direct request sends an empty header, which is
        worse than the wrong one: some upstreams treat it as authoritative."""
        block = re.search(
            rf"map \$http_x_forwarded_{name} \$forwarded_{name} \{{(.*?)\}}",
            NGINX,
            re.DOTALL,
        )

        assert block is not None, f"no map for {name}"
        assert '""' in block.group(1), "the empty case has to fall back to this hop"

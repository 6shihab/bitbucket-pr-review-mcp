"""The one HTML shell both credential pages wear.

Two pages ask for an Atlassian API token: the loopback setup page on a laptop, and
the shared server's page behind a Keycloak login. They say different things — one
shuts itself down afterwards and the other does not — but they should not look like
two different products.
"""

from __future__ import annotations

import html


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Bitbucket review server</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.55 system-ui, sans-serif; max-width: 34rem; margin: 3rem auto;
         padding: 0 1.25rem; }}
  h1 {{ font-size: 1.35rem; }}
  h2 {{ font-size: 1rem; margin-top: 1.75rem; }}
  label {{ display: block; margin-top: 1rem; font-weight: 600; }}
  input {{ width: 100%; padding: .55rem; font: inherit; box-sizing: border-box; }}
  button {{ margin-top: 1.25rem; padding: .6rem 1.1rem; font: inherit; cursor: pointer; }}
  .note {{ font-size: .85rem; opacity: .75; }}
  .bad {{ padding: .75rem; border-left: 3px solid #c33; background: rgba(204,51,51,.08); }}
  .identity {{ font-size: 1.2rem; font-weight: 600; }}
  .scopes code {{ font-size: .95rem; }}
  dt {{ font-weight: 600; margin-top: .5rem; }}
</style></head>
<body><h1>{html.escape(title)}</h1>{body}</body></html>
"""

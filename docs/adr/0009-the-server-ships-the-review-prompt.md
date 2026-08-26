# The server ships the review prompt, but still not a reviewer

[ADR-0001](0001-thin-tool-server-no-embedded-model.md) refused to put a model in this
server, and listed its consequence as "the server holds no review prompt and no notion
of what makes code good". The first half of that consequence turned out to be a
separate decision wearing the first one's clothes, and it cost us.

With the criteria on the client side, the review a pull request got depended on which
client was connected. Claude Code with the house skills installed got one review;
Claude on the web, or Cursor, or a colleague who had installed the server and nothing
else, got the tools and no idea what to do with them — the same eleven fetches, and a
review whose standard nobody had chosen. "Improve the review quality" was answerable
only by asking every caller to install something.

So the server now registers one MCP prompt, `review_pull_request`. It carries the read
order, what earns a comment, what to leave alone, the severity ladder, and the rule
that the user sees the review and picks the comments before any of them are posted.

## Why this is not the thing ADR-0001 refused

ADR-0001's reasons were specific and none of them apply to text:

| ADR-0001's objection | A prompt primitive |
|---|---|
| Needs its own API credential | None. Nothing is called. |
| Its cost is invisible to the caller | The caller's own model spends the caller's own tokens |
| A process that reads private repositories and calls a model API is one hop from exfiltration | No outbound call exists; the server never leaves Bitbucket |
| A prompt to version | True, and now accepted — see below |

The reviewing still happens entirely in the Caller's model. The server forms no
opinion at call time; it hands over an opinion formed once, in a file, in review, under
test.

## Consequences

There is now a prompt to version, which is the cost we took on. It is mitigated by
being *only* text and by being tested against the code around it: every `bitbucket_`
tool it names must be registered, and its severity ladder must be `findings.SEVERITIES`
rather than a second vocabulary. A tool renamed without the prompt following it fails
the suite.

It is a prompt, not a policy. A client fetches it or does not, and one that has its own
review conventions is free to ignore it — which is the property that makes shipping it
safe.

ADR-0001 stands otherwise, unamended: no model, no second credential, no outbound call.

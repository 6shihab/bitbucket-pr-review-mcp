# Bitbucket PR Review MCP

An MCP server that lets a language model read a Bitbucket Cloud pull request and
leave comments on it. The server supplies material and posts words; it does not
form opinions.

## Language

**Pull Request**:
A proposed merge of a source branch into a destination branch on Bitbucket Cloud.
_Avoid_: PR (in prose), merge request, change request

**Caller**:
The language model that invokes this server's tools and decides what the review
says.
_Avoid_: client, agent, user

**Reviewer**:
The human whose Atlassian account the server posts under, and who is
accountable for what appears on the pull request. Identified by Atlassian account
email, never by Bitbucket username.
_Avoid_: user, author, reviewer account

**Review**:
The Caller's judgement about a Pull Request. It is performed by the Caller and
merely carried by this server — the server never holds one.
_Avoid_: analysis, audit, report

**Finding**:
One thing the Caller wants to say about one place in the code, carrying a severity
and a category. Findings are formed by the Caller; the server only renders and
carries them.
_Avoid_: issue, comment, problem, violation

**Severity**:
How much a Finding should block: CRITICAL, HIGH, MEDIUM or LOW. The ladder is the
house one — block, warn, info, note — not a second vocabulary.
_Avoid_: priority, level, importance

**Review Basis**:
The source commit the diff was read at. Every posted comment is validated against
it, so a force-push mid-review is refused rather than silently misplaced.
_Avoid_: head, revision, source commit, SHA

**Summary Comment**:
A comment on the Pull Request as a whole, not tied to any file. Exactly one is
canonical per Review.
_Avoid_: general comment, top-level comment, overall comment

**Inline Comment**:
A comment fastened to a specific line of a specific file in the Pull Request.
_Avoid_: line comment, file comment, diff comment

**Anchor**:
The file-and-line coordinate that fastens an Inline Comment to code. An Anchor
that no longer matches the code it was about is *orphaned*.
_Avoid_: location, position, target, inline

**Allowlisted Repository**:
A repository this server is permitted to post into. Membership is configured, not
inferred from what the Reviewer's credential happens to reach.
_Avoid_: allowed repo, whitelist, permitted repository

**Attribution Footer**:
The unremovable notice on every posted comment declaring it machine-generated and
naming the Reviewer who triggered it.
_Avoid_: signature, disclaimer, watermark

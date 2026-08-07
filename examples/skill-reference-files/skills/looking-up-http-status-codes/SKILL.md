---
name: looking-up-http-status-codes
description: Looks up the meaning, cause, and correct client/server handling of an HTTP status code. Use when the user asks what a status code means, why a request returned a given code, or which code to return for a given situation.
---

## Quick reference

| Code | Meaning |
|------|---------|
| 200 OK | Request succeeded |
| 201 Created | Resource created |
| 301 Moved Permanently | Resource permanently relocated |
| 304 Not Modified | Cached response is still valid |
| 400 Bad Request | Malformed request syntax |
| 401 Unauthorized | Missing or invalid credentials |
| 403 Forbidden | Authenticated but not permitted |
| 404 Not Found | Resource does not exist |
| 429 Too Many Requests | Client is rate limited |
| 500 Internal Server Error | Unhandled fault on the server |
| 502 Bad Gateway | Upstream server sent an invalid response |
| 503 Service Unavailable | Server temporarily overloaded or down |

If the code isn't in this table, or the user needs the specific cause and
fix for it (not just the one-line meaning), read the reference file for its
response class rather than guessing:

- 2xx/3xx (success and redirection): see reference/success-and-redirection.md.
- 4xx (client errors) — per-code cause and fix: see
  [reference/client-errors.md](reference/client-errors.md).
- 5xx (server errors): see reference/server-errors.md.

## Instructions

1. If the code is a common one from the table above and the user only asked
   what it means, answer from the table — no need to read a reference file.
2. Otherwise, read the one reference file matching the code's response class
   (1 => none bundled, 2xx/3xx, 4xx, 5xx) and answer from its cause/fix entry
   for that specific code.
3. If the user asks "which code should I return" for a described situation,
   scan the relevant reference file's entries for the closest match and
   explain why, not just name a code.

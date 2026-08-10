# Anthropic Python SDK transport retries

The retry layer you **configure** rather than write. Distinct from retrying your
own tool function, which the SDK never does — see [[tool-failure-taxonomy]].

All source-verified against `anthropic-sdk-python` `main` on 2026-08-10
(`src/anthropic/_base_client.py`, `src/anthropic/_constants.py`).

- Defaults: `DEFAULT_MAX_RETRIES = 2`, `INITIAL_RETRY_DELAY = 0.5`,
  `MAX_RETRY_DELAY = 8.0`. Request timeout defaults to 10 minutes and
  `APITimeoutError` is itself retried.
- `_should_retry()` retries **408, 409, 429, and any >= 500**, plus connection
  errors. A non-standard `x-should-retry: true|false` response header overrides
  the decision in either direction.
- `_calculate_retry_timeout()` honours `retry-after-ms`, then `retry-after`
  (seconds, then HTTP-date) — **only when the parsed value is `0 < v <= 60`**.
  Otherwise: `min(0.5 * 2**n, 8.0)`.
- **Gotcha:** the jitter is `1 - 0.25 * random()`, a *multiplicative* 0.75–1.0
  factor, so the sleep is only ever ≤ the computed delay. The code comment right
  above it claims "plus-or-minus half a second" and is simply wrong. Don't cite
  the comment.
- Configure: `Anthropic(max_retries=0)` client-wide, or per request
  `client.with_options(max_retries=5).messages.create(...)`.

## Status → Python exception

| Status | Type | Exception |
|---|---|---|
| 400 | `invalid_request_error` | `BadRequestError` |
| 401 | `authentication_error` | `AuthenticationError` |
| 403 | `permission_error` | `PermissionDeniedError` |
| 404 | `not_found_error` | `NotFoundError` |
| 409 | `conflict_error` | `ConflictError` |
| 422 | — | `UnprocessableEntityError` |
| 429 | `rate_limit_error` | `RateLimitError` |
| >=500 | `api_error` / `overloaded_error` (529) | `InternalServerError` |
| n/a | — | `APIConnectionError` |

Also: 402 `billing_error`, 413 `request_too_large` (32 MB Messages API),
504 `timeout_error`. Every response carries a `request-id` header, exposed as the
public `message._request_id`; include it in support reports.

Sources: [errors reference](https://platform.claude.com/docs/en/api/errors) and
[Python SDK page](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python)
(both fetched 2026-08-10), plus the SDK source above.

Related: [[anthropic-python-sdk]], [[tool-failure-taxonomy]], [[anthropic-models]]

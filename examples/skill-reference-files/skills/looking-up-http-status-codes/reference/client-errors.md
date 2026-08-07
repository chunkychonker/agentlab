# Client Errors (4xx)

Full reference for the commonly seen 4xx HTTP status codes: what each one
means, why it typically happens, and how to fix it. This file is long
enough (over 100 lines) that it carries a table of contents up top so the
full scope is visible even from a partial read.

## Contents

- 400 Bad Request
- 401 Unauthorized
- 402 Payment Required
- 403 Forbidden
- 404 Not Found
- 405 Method Not Allowed
- 406 Not Acceptable
- 407 Proxy Authentication Required
- 408 Request Timeout
- 409 Conflict
- 410 Gone
- 413 Payload Too Large
- 415 Unsupported Media Type
- 422 Unprocessable Entity
- 423 Locked
- 425 Too Early
- 428 Precondition Required
- 429 Too Many Requests
- 431 Request Header Fields Too Large
- 451 Unavailable For Legal Reasons

### 400 Bad Request

**Cause:** The request itself is malformed — bad syntax, invalid JSON, a
field of the wrong type, or a value that fails basic validation.

**Fix:** Fix the request payload on the client side. If the server's error
body doesn't say which field is invalid, add that detail server-side so
clients can self-correct.

### 401 Unauthorized

**Cause:** The request has no credentials, or the credentials provided are
invalid/expired. (Despite the name, this is about authentication, not
authorization.)

**Fix:** Supply valid credentials (e.g. refresh an expired token) and
retry. A `WWW-Authenticate` header should say which auth scheme is expected.

### 402 Payment Required

**Cause:** Reserved for future use by the HTTP spec; in practice it's used
by some APIs to signal that payment/billing action is needed before the
request can proceed.

**Fix:** Check the API's own docs for what payment action it expects — this
code has no single standardized meaning across services.

### 403 Forbidden

**Cause:** The server understood the request and the client is
authenticated, but the client does not have permission to access this
resource.

**Fix:** This is not a credentials problem — retrying with the same
identity will not help. The client needs different permissions/role, or the
request is legitimately disallowed.

### 404 Not Found

**Cause:** No resource exists at the requested URL, or the server is
deliberately hiding that it exists (some APIs return 404 instead of 403 to
avoid leaking existence).

**Fix:** Confirm the URL/ID is correct. If the resource should exist, check
for a typo in the path or a stale/deleted ID.

### 405 Method Not Allowed

**Cause:** The resource exists, but doesn't support the HTTP method used
(e.g. `DELETE` on a read-only endpoint).

**Fix:** Use a supported method. The response should include an `Allow`
header listing which methods are valid for this resource.

### 406 Not Acceptable

**Cause:** The server can't produce a response matching the `Accept`
header's requested content type/language/encoding.

**Fix:** Relax or correct the `Accept` header, or confirm the endpoint
actually supports the format the client wants.

### 407 Proxy Authentication Required

**Cause:** Like 401, but the client must first authenticate with a proxy
sitting in front of the origin server.

**Fix:** Supply proxy credentials via `Proxy-Authorization`, per the
`Proxy-Authenticate` header the proxy returned.

### 408 Request Timeout

**Cause:** The server timed out waiting for the client to send the rest of
the request.

**Fix:** Usually a transient network issue — safe to retry (this request is
idempotent by definition, since it never fully arrived).

### 409 Conflict

**Cause:** The request conflicts with the current state of the resource
(e.g. a version mismatch on update, or trying to create something that
already exists).

**Fix:** Fetch the current state, reconcile the conflict, and resubmit —
don't blindly retry the same request unchanged.

### 410 Gone

**Cause:** The resource used to exist here but has been permanently
removed, and the server knows that (unlike 404, which doesn't promise
either way).

**Fix:** Stop requesting this URL; update any stored links/bookmarks
pointing at it.

### 413 Payload Too Large

**Cause:** The request body exceeds a size limit the server enforces.

**Fix:** Reduce the payload (e.g. paginate, compress, or upload in chunks
via a dedicated large-upload flow) rather than retrying as-is.

### 415 Unsupported Media Type

**Cause:** The request body's `Content-Type` isn't one the server can
process for this endpoint.

**Fix:** Send the body in a format the endpoint documents support for, and
set `Content-Type` to match it exactly.

### 422 Unprocessable Entity

**Cause:** The request is syntactically valid (unlike 400) but semantically
invalid — e.g. a well-formed JSON body that fails a business-rule
validation.

**Fix:** Fix the offending field per the validation error in the response
body; this is a data problem, not a syntax problem.

### 423 Locked

**Cause:** The resource being accessed is locked (common in WebDAV-style
APIs during a concurrent edit).

**Fix:** Wait for the lock to release and retry, or coordinate with whoever
holds the lock; don't force the write.

### 425 Too Early

**Cause:** The server is unwilling to process a request that might be
replayed, sent in TLS 0-RTT early data before the handshake is confirmed.

**Fix:** Resend the request after the TLS handshake completes, rather than
relying on the early-data fast path for this call.

### 428 Precondition Required

**Cause:** The server requires the request to include a conditional header
(e.g. `If-Match`) to avoid the "lost update" problem, and the client didn't
send one.

**Fix:** Fetch the resource's current version/ETag first, then include it
as a precondition header on the write.

### 429 Too Many Requests

**Cause:** The client sent more requests in a given time window than the
server allows (rate limiting).

**Fix:** Back off and retry after the duration in the `Retry-After` header;
add client-side rate limiting so bursts don't reach the server again.

### 431 Request Header Fields Too Large

**Cause:** The request's headers, individually or combined, exceed a size
limit the server enforces.

**Fix:** Trim header size — common culprits are oversized cookies or an
overlong `Authorization` token; shrink or split them.

### 451 Unavailable For Legal Reasons

**Cause:** The resource is unavailable due to a legal demand (e.g. a
government content-removal order).

**Fix:** Not a bug to retry around — the response body typically names the
legal authority behind the restriction.

# Success and Redirection (2xx/3xx)

Per-code cause and correct handling for the common 2xx (success) and 3xx
(redirection) HTTP status codes. Short enough (under 100 lines) that no
table of contents is needed — read straight through.

### 200 OK

**Meaning:** The request succeeded and the response body carries the
requested representation.

**When to return it:** Any successful `GET`, and successful `PUT`/`PATCH`
requests that return the updated resource.

### 201 Created

**Meaning:** The request succeeded and a new resource was created as a
result.

**When to return it:** A successful `POST` that creates a resource. Include
a `Location` header pointing at the new resource's URL.

### 202 Accepted

**Meaning:** The request was accepted for processing, but processing is not
complete yet (async work).

**When to return it:** Long-running or queued work — pair it with a way for
the client to poll or be notified of completion.

### 204 No Content

**Meaning:** The request succeeded and there is no response body.

**When to return it:** A successful `DELETE`, or a `PUT` that intentionally
returns nothing.

### 301 Moved Permanently

**Meaning:** The resource has permanently moved to a new URL.

**When to return it:** Permanent URL changes. Clients and search engines
should update their stored links; browsers may cache this redirect
indefinitely, so only use it when the move truly is permanent.

### 302 Found

**Meaning:** The resource is temporarily at a different URL.

**When to return it:** Temporary redirects, e.g. after a login flow.
Historically ambiguous about whether the method should be preserved on
redirect — prefer 307 for a method-preserving temporary redirect.

### 304 Not Modified

**Meaning:** The client's cached copy is still valid; no body is sent.

**When to return it:** Conditional `GET` requests (`If-None-Match` /
`If-Modified-Since`) where the resource has not changed since the client
last fetched it.

### 307 Temporary Redirect

**Meaning:** Like 302, but the method and body of the original request must
be preserved on the redirected request.

**When to return it:** Temporary redirects for non-`GET` requests where the
client must not silently switch to `GET`.

### 308 Permanent Redirect

**Meaning:** Like 301, but the method and body must be preserved.

**When to return it:** Permanent redirects for non-`GET` requests, for the
same method-preservation reason as 307.

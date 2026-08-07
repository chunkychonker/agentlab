# Server Errors (5xx)

Per-code cause and fix for the common 5xx HTTP status codes — the server
acknowledges the request was valid but failed to fulfill it. Short enough
(under 100 lines) that no table of contents is needed.

### 500 Internal Server Error

**Cause:** An unhandled exception or unexpected fault somewhere in the
server's own code — the catch-all when nothing more specific applies.

**Fix:** Check server-side logs/stack traces for the actual exception; this
is never something the client can work around by changing its request.

### 501 Not Implemented

**Cause:** The server does not support the functionality required to
fulfill the request (e.g. an unrecognized HTTP method).

**Fix:** Confirm the method/feature is actually supported by this endpoint;
if it should be, this is a server-side gap to implement, not a client bug.

### 502 Bad Gateway

**Cause:** The server, acting as a gateway or proxy, received an invalid
response from an upstream server it was querying.

**Fix:** Check the upstream service's health and logs, not this server's —
the immediate server is only relaying a bad response it received.

### 503 Service Unavailable

**Cause:** The server is temporarily unable to handle the request, usually
due to overload or planned maintenance.

**Fix:** Retry after the duration in the `Retry-After` header if present;
if this happens under normal load, it points at a capacity problem, not a
one-off blip.

### 504 Gateway Timeout

**Cause:** The server, acting as a gateway or proxy, did not receive a
timely response from an upstream server.

**Fix:** Investigate the upstream service's latency; consider raising the
gateway's timeout only after confirming the upstream call is expected to
take that long.

### 507 Insufficient Storage

**Cause:** The server cannot store the representation needed to complete the
request because it is out of storage.

**Fix:** Free up server-side storage or provision more; not something a
client retry will fix.

### 511 Network Authentication Required

**Cause:** The client needs to authenticate to gain network access (common
on captive portals) before the original request can succeed.

**Fix:** Complete the network-level authentication flow described in the
response body, then retry the original request.

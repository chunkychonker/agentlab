"""Serve a one-tool MCP server over Streamable HTTP (mcp 2.x).

Intent: show the lab's MCP server running over HTTP rather than stdio, and
make the most common first-deploy failure reproducible. That failure is the
`421 Misdirected Request` a server returns when the request's `Host` is not
in its allowlist. Out of scope: OAuth, multi-worker sticky routing, CORS, and
connecting Claude Code to the server.

Everything here is pure construction. Nothing reads the environment, opens a
socket, or touches the filesystem except `__main__`, which is the only
imperative layer (`mcp.run(...)` starts uvicorn on PORT).

Note where the kwargs go: `host`, `port`, `streamable_http_path`, and
`transport_security` are arguments to `run()` / `streamable_http_app()`. They
are never arguments to `MCPServer(...)`. Passing `port=` to the constructor
raises `TypeError`.
"""

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

SERVER_NAME = "Notes"
PORT = 3001  # used only by __main__; the self-test never opens a port


def build_server() -> MCPServer:
    """Return a fresh `MCPServer` with a single tool, `add_note`.

    Each call returns a new instance with no shared state, so two apps built
    from it never see each other's sessions. Cannot fail.
    """
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def add_note(text: str) -> str:
        """Acknowledge a note. Pure: it stores nothing and has no side effects."""
        return f"Saved: {text}"

    return server


def build_app(transport_security: TransportSecuritySettings | None) -> Starlette:
    """Return the Streamable HTTP ASGI app (route `/mcp`) for a fresh server.

    With `transport_security=None`, the SDK turns on DNS-rebinding protection
    and allows only `Host` values `127.0.0.1:<port>`, `localhost:<port>`, and
    `[::1]:<port>`. The port is required, so a bare `Host: localhost` is also
    rejected. If an `Origin` header is present, it must be the matching
    `http://` origin. Rejections come back as plain text (not JSON-RPC):
    421 "Invalid Host header" or 403 "Invalid Origin header".

    Failure modes:
      - This function does not validate hostnames. Whatever is in
        `transport_security` is used as given.
      - The returned app has to have its lifespan entered (for example
        `async with app.router.lifespan_context(app)`, or by uvicorn) before
        any request. Without it, the first request raises
        `RuntimeError: Task group is not initialized`, before any Host check
        runs. If you mount this app inside another Starlette app, the
        sub-app's lifespan never runs. The host app has to enter
        `mcp.session_manager.run()` itself.
    """
    return build_server().streamable_http_app(transport_security=transport_security)


def public_host_security(hostname: str) -> TransportSecuritySettings:
    """Allowlist one public hostname, both bare and with any port.

    A reverse proxy on :443 usually forwards `Host: mcp.example.com` with no
    port, but a direct hit forwards `mcp.example.com:3001`. Allowing only one
    of those forms is a common half-fix, so this allows both.

    This list replaces the localhost default rather than adding to it, so
    `localhost:<port>` is rejected afterward. `allowed_origins` is left empty,
    which means any request with an `Origin` header (that is, any browser)
    gets 403. Non-browser MCP clients send no `Origin`.

    Pure. Failure modes: `hostname` is not validated. A value that contains a
    scheme or a port produces an allowlist that matches nothing useful.
    """
    return TransportSecuritySettings(
        allowed_hosts=[hostname, f"{hostname}:*"],
        allowed_origins=[],
    )


if __name__ == "__main__":
    # Serves http://127.0.0.1:3001/mcp with the localhost-only default allowlist.
    build_server().run(transport="streamable-http", port=PORT)

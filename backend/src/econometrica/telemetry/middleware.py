"""One span per HTTP request.

The route template rather than the path, so `/api/runs/{run_id}` groups into one
percentile instead of one bucket per run — a p95 over a thousand distinct paths
describes nothing.

**The metrics endpoint is not traced.** It would only ever measure itself, and
every reading would add a row that changed the next one.
"""

from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from econometrica.telemetry.spans import span

#: Paths whose own traffic is noise. `/api/metrics` reads the span table, so
#: tracing it would make the measurement a function of how often it is read.
UNTRACED = ("/api/metrics",)


class TracingMiddleware:
    """Times each request, and never fails one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "") in UNTRACED:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        method = request.method
        status = {"code": 0}

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        path = request.url.path
        with span(
            f"{method} {path}", attributes={"http.method": method}
        ) as current:
            await self.app(scope, receive, capture)
            if current is not None:
                # Renamed on the way out. Middleware runs *before* routing, so
                # the template is only known once the router has matched — and
                # naming the span after the raw path would give every run id its
                # own bucket, which is a percentile over nothing.
                route = _route_of(scope) or path
                current.update_name(f"{method} {route}")
                current.set_attribute("http.route", route)
                current.set_attribute("http.status_code", status["code"])
                if status["code"] >= 400:
                    # Marked on the span rather than raised: a 404 is a normal
                    # answer to a request, and the middleware has nothing to add
                    # to it beyond saying so in the trace.
                    from opentelemetry.trace import Status, StatusCode

                    current.set_status(
                        Status(StatusCode.ERROR, f"HTTP {status['code']}")
                    )


def _route_of(scope: Scope) -> str:
    """The matched route's template, once the router has resolved one."""
    route: Any = scope.get("route")
    return str(getattr(route, "path", "")) if route is not None else ""

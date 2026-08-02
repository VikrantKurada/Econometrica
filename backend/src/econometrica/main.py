from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from econometrica import __version__
from econometrica.api.routers import (
    chats,
    documents,
    exports,
    health,
    mcp,
    messages,
    metrics,
    projects,
    providers,
    runs,
    uploads,
)
from econometrica.config import get_settings
from econometrica.db.session import SessionLocal
from econometrica.econ import load_tools
from econometrica.telemetry import configure_tracing, reset_tracing
from econometrica.telemetry.middleware import TracingMiddleware
from econometrica.telemetry.writer import SpanWriter

# Tools register as an import side-effect of their family packages, so without
# this the server runs with an empty registry — which nothing noticed while no
# request path resolved a tool by name, and which agents do from Phase 4 on.
load_tools()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start tracing, and stop it without losing what it had queued.

    The writer owns its own sessions rather than borrowing a request's: a span
    outlives the request that produced it, and writing it inside that request's
    transaction would make telemetry able to roll a user's work back.
    """
    settings = get_settings()
    writer = SpanWriter(sessionmaker=SessionLocal)
    configure_tracing(
        sink=writer.submit, otlp_endpoint=settings.otel_exporter_otlp_endpoint
    )
    writer.start()
    app.state.span_writer = writer
    try:
        yield
    finally:
        await writer.stop()
        reset_tracing()


app = FastAPI(title="Econometrica", version=__version__, lifespan=lifespan)

# The Vite dev server is the only browser origin that talks to this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TracingMiddleware)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(chats.router)
app.include_router(providers.router)
app.include_router(messages.router)
app.include_router(runs.router)
app.include_router(runs.traces)
app.include_router(exports.router)
app.include_router(uploads.router)
app.include_router(uploads.uploads)
app.include_router(documents.router)
app.include_router(documents.documents)
app.include_router(mcp.router)
app.include_router(metrics.router)

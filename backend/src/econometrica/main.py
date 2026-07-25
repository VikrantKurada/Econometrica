from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from econometrica import __version__
from econometrica.api.routers import chats, health, messages, projects, providers, runs
from econometrica.econ import load_tools

# Tools register as an import side-effect of their family packages, so without
# this the server runs with an empty registry — which nothing noticed while no
# request path resolved a tool by name, and which agents do from Phase 4 on.
load_tools()

app = FastAPI(title="Econometrica", version=__version__)

# The Vite dev server is the only browser origin that talks to this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(chats.router)
app.include_router(providers.router)
app.include_router(messages.router)
app.include_router(runs.router)
app.include_router(runs.traces)

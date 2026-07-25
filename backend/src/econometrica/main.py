from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from econometrica import __version__
from econometrica.api.routers import chats, health, projects, providers

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

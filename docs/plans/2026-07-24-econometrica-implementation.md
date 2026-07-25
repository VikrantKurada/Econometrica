# Econometrica Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local three-pane web application where a user drives financial
econometrics through chat and receives interactive, reproducible charts and
artifacts produced by a multi-agent GenAI layer over a validated econometrics
core.

**Architecture:** FastAPI backend serving a React/TypeScript frontend, backed by
Postgres with TimescaleDB and pgvector. LLM agents never compute statistics
themselves — they select from a registry of typed, versioned econometric tools.
Deterministic diagnostic and numeric-grounding gates run before any result
reaches the user.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2,
statsmodels, arch, linearmodels, pandas, numpy, scipy · Postgres 16 +
TimescaleDB + pgvector · React 19, TypeScript, Vite, TanStack Query, Zustand,
Tailwind, Radix, Plotly.js · pytest + hypothesis, Vitest + Playwright.

**Design document:** `docs/plans/2026-07-24-econometrica-design.md`

---

## Progress

Updated as phases land. `CLAUDE.md` carries the same table plus the environment
notes a new session needs.

| Phase | State |
|---|---|
| 0 — scaffold | ✅ done |
| 1 — DB, API, three-pane shell | ✅ done |
| 2 — econometrics core | ✅ done — 36 tools, 5 families, gate green, 97% coverage |
| 3 — LLM providers and streaming chat | ✅ done — e2e gate green against a live Ollama |
| 4 — multi-agent orchestration | next — Task 4.1 |
| 5 — charts and artifact canvas | not started |
| 6 — telemetry, uploads, MCP, exports | not started |

**629 backend tests, 65 frontend tests, 2 Playwright e2e.** ruff and
`mypy --strict` clean; `alembic check` reports no drift.

Phase 3 tasks, against the task table further down:

| Task | State |
|---|---|
| 3.1 provider protocol + fake | ✅ |
| 3.2 Ollama adapter | ✅ |
| 3.3 OpenAI + NVIDIA NIM adapters | ✅ |
| 3.4 Anthropic adapter (official SDK) | ✅ |
| 3.5 Gemini adapter | ✅ |
| 3.6 encrypted key store | ✅ |
| 3.7 providers API | ✅ |
| 3.8 message persistence + SSE chat | ✅ |
| 3.9 chat pane UI | ✅ |
| 3.10 Phase 3 e2e | ✅ |

`e2e/chat.spec.ts` closes the phase. It wraps `fetch` before the page loads so
the SSE body can be tee'd and asserted read by read — the rendered text alone
would pass just as happily against an endpoint that buffered the whole reply
and sent it in one frame. Playwright's own `response.text()` cannot be used
for this: on a consumed event stream it fails with "No data found for resource
with given identifier". The spec skips, with a reason, when Ollama is down.

---

## Verified environment (2026-07-24)

Dependency resolution landed well above the version floors below. All 21
econometric code paths that Phase 2 depends on were probed against this exact
combination and passed, so **no version pinning is required**:

| Package | Resolved |
|---|---|
| Python | 3.12.12 |
| pandas | 3.0.5 |
| numpy | 2.5.1 |
| scipy | 1.18.0 |
| statsmodels | 0.14.6 |
| arch | 8.0.0 |
| linearmodels | 7.0 |

Two API changes surfaced by the probe that Phase 2 tools must account for:

- `statsmodels.tsa.stattools.grangercausalitytests` — the `verbose` argument is
  deprecated and warns. Do not pass it; capture the returned dict instead.
- `arch.unitroot.VarianceRatio` and `arch.unitroot.KPSS` — lag selection now
  defaults to a data-dependent method. Pass `lags` explicitly in every call so
  results are deterministic and reproducible across library versions.

If a later task does hit pandas-3 dtype friction, the fallback is
`pandas>=2.2,<3`, but nothing observed so far justifies it.

---

## How this plan is organised

Phases 1 and 2 — the skeleton and the econometrics core — are specified at full
step-by-step granularity below. They are the foundation everything else rests
on, and the econometrics core is the part where correctness matters most and
test-first development pays the highest dividend.

Phases 3 through 6 are specified at task level: exact files, the behaviour each
task must produce, and its acceptance criteria. Their step-level plans are
written as separate documents when each phase is reached, because their detail
depends on interfaces that Phases 1 and 2 will have settled. **Do not treat this
as a reduction in scope** — all six phases ship.

**Conventions used throughout:**

- Every task follows: write failing test → run it and watch it fail → minimal
  implementation → run it and watch it pass → commit.
- Backend commands run from `backend/`. Frontend commands run from `frontend/`.
- `uv run` prefixes every Python command; there is no manual venv activation.
- Commit messages use Conventional Commits.

---

## Phase 0: Repository scaffold

### Task 0.1: Create the directory structure and root files

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `README.md`

**Step 1: Write `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
htmlcov/
.coverage

# Node
node_modules/
dist/
.vite/

# Environment
.env
*.local

# Data
storage/
*.db
```

**Step 2: Write `.env.example`**

```dotenv
# Database
POSTGRES_USER=econometrica
POSTGRES_PASSWORD=change-me-locally
POSTGRES_DB=econometrica
POSTGRES_PORT=5433
DATABASE_URL=postgresql+asyncpg://econometrica:change-me-locally@localhost:5433/econometrica
TEST_DATABASE_URL=postgresql+asyncpg://econometrica:change-me-locally@localhost:5433/econometrica_test

# Application
ECONOMETRICA_STORAGE_DIR=./storage
ECONOMETRICA_SECRET_KEY=generate-with-openssl-rand-hex-32
ECONOMETRICA_LOG_LEVEL=INFO

# LLM providers — all optional, configured in the UI as well
OLLAMA_BASE_URL=http://localhost:11434
NVIDIA_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Telemetry
OTEL_EXPORTER_OTLP_ENDPOINT=
```

**Step 3: Write `docker-compose.yml`**

The `timescaledb-ha` image bundles TimescaleDB *and* pgvector, which is why it
is used rather than the plain Postgres or plain pgvector images. Port 5433 is
mapped on the host to avoid colliding with any existing local Postgres.

```yaml
services:
  db:
    image: timescale/timescaledb-ha:pg16
    container_name: econometrica-db
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-econometrica}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-change-me-locally}
      POSTGRES_DB: ${POSTGRES_DB:-econometrica}
    ports:
      - "${POSTGRES_PORT:-5433}:5432"
    volumes:
      - econometrica-pgdata:/home/postgres/pgdata/data
      - ./infra/initdb:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-econometrica}"]
      interval: 5s
      timeout: 5s
      retries: 20

volumes:
  econometrica-pgdata:
```

**Step 4: Write `infra/initdb/01-extensions.sql`**

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE DATABASE econometrica_test;
\c econometrica_test
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
```

**Step 5: Start the database and verify both extensions load**

```bash
docker compose up -d db
```

Then:

```bash
docker exec econometrica-db psql -U econometrica -d econometrica -c "SELECT extname FROM pg_extension;"
```

Expected: output includes `timescaledb` and `vector`.

**Step 6: Commit**

```bash
git add .gitignore .env.example docker-compose.yml infra/ README.md
git commit -m "chore: add repository scaffold and database compose stack"
```

---

### Task 0.2: Bootstrap the Python backend

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/econometrica/__init__.py`
- Create: `backend/tests/__init__.py`

**Step 1: Write `backend/pyproject.toml`**

Python is pinned to 3.12 deliberately. Python 3.14 is installed on this machine,
but `arch`, `numba` and `linearmodels` do not yet publish 3.14 wheels, and
building them from source on Windows is a long detour for no benefit.

```toml
[project]
name = "econometrica"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
    "python-multipart>=0.0.20",
    "sse-starlette>=2.1",
    "httpx>=0.28",
    "cryptography>=44.0",
    "numpy>=2.1",
    "pandas>=2.2",
    "scipy>=1.14",
    "statsmodels>=0.14.4",
    "arch>=7.2",
    "linearmodels>=6.1",
    "ruptures>=1.1.9",
    "yfinance>=0.2.50",
    "pandas-datareader>=0.10",
    "openpyxl>=3.1",
    "pyarrow>=18.0",
    "opentelemetry-sdk>=1.29",
    "opentelemetry-exporter-otlp-proto-http>=1.29",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "pytest-cov>=6.0",
    "hypothesis>=6.122",
    "ruff>=0.8",
    "mypy>=1.14",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/econometrica"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
filterwarnings = ["ignore::FutureWarning", "ignore::DeprecationWarning"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true
```

**Step 2: Install and verify the scientific stack imports**

```bash
uv sync --extra dev
```

Then:

```bash
uv run python -c "import statsmodels, arch, linearmodels, numpy, pandas, scipy; print('ok', numpy.__version__)"
```

Expected: `ok 2.x.x` with no import errors. If `arch` or `linearmodels` fail to
resolve, that is the Python-version pin not being honoured — check
`uv run python --version` reports 3.12.

**Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests
git commit -m "chore(backend): bootstrap python package with scientific stack"
```

---

## Phase 1: Skeleton — database, API, three-pane shell

### Task 1.1: Settings module

**Files:**
- Create: `backend/src/econometrica/config.py`
- Test: `backend/tests/test_config.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_config.py
from econometrica.config import Settings


def test_settings_read_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5433/db")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://u:p@localhost:5433/db"


def test_settings_storage_dir_defaults_to_local_storage():
    settings = Settings(database_url="postgresql+asyncpg://u:p@h/d")
    assert settings.storage_dir.name == "storage"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'econometrica.config'`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/config.py
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    test_database_url: str = ""
    storage_dir: Path = Path("./storage")
    secret_key: str = "dev-only-insecure-key"
    log_level: str = "INFO"

    ollama_base_url: str = "http://localhost:11434"
    nvidia_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    otel_exporter_otlp_endpoint: str = ""


def get_settings() -> Settings:
    return Settings()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add backend/src/econometrica/config.py backend/tests/test_config.py
git commit -m "feat(backend): add settings module"
```

---

### Task 1.2: Database session and declarative base

**Files:**
- Create: `backend/src/econometrica/db/base.py`
- Create: `backend/src/econometrica/db/session.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_db_session.py`

**Step 1: Write `backend/tests/conftest.py`**

Every database test runs against the real `econometrica_test` database, not
SQLite. TimescaleDB hypertables and pgvector columns have no SQLite equivalent,
so testing against a substitute engine would prove nothing.

```python
# backend/tests/conftest.py
import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from econometrica.db.base import Base

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://econometrica:change-me-locally@localhost:5433/econometrica_test",
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """Each test runs in a transaction that is rolled back afterwards."""
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with maker() as s:
        yield s
    await transaction.rollback()
    await connection.close()
```

**Step 2: Write the failing test**

```python
# backend/tests/test_db_session.py
from sqlalchemy import text


async def test_session_connects_and_extensions_are_available(session):
    result = await session.execute(text("SELECT extname FROM pg_extension"))
    extensions = {row[0] for row in result}
    assert "timescaledb" in extensions
    assert "vector" in extensions
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_db_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'econometrica.db'`

**Step 4: Write minimal implementation**

```python
# backend/src/econometrica/db/base.py
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampedBase(Base):
    """Every domain entity carries an id and audit timestamps."""

    __abstract__ = True

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

```python
# backend/src/econometrica/db/session.py
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from econometrica.config import get_settings

_settings = get_settings()

engine = create_async_engine(_settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_db_session.py -v`
Expected: 1 passed

**Step 6: Commit**

```bash
git add backend/src/econometrica/db backend/tests/conftest.py backend/tests/test_db_session.py
git commit -m "feat(backend): add async database session and declarative base"
```

---

### Task 1.3: Project and Chat models

**Files:**
- Create: `backend/src/econometrica/db/models/__init__.py`
- Create: `backend/src/econometrica/db/models/project.py`
- Create: `backend/src/econometrica/db/models/chat.py`
- Test: `backend/tests/db/test_project_model.py`

**Step 1: Write the failing test**

```python
# backend/tests/db/test_project_model.py
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from econometrica.db.models import Chat, Project


async def test_project_persists_with_defaults(session):
    project = Project(name="Equity Factor Study")
    session.add(project)
    await session.flush()

    assert project.id is not None
    assert project.web_search_enabled is False
    assert project.mcp_enabled is False
    assert project.validation_tier == "critic"


async def test_project_can_be_renamed(session):
    project = Project(name="Old Name")
    session.add(project)
    await session.flush()

    project.name = "New Name"
    await session.flush()

    fetched = await session.scalar(select(Project).where(Project.id == project.id))
    assert fetched.name == "New Name"


async def test_chat_belongs_to_project_and_cascades_on_delete(session):
    project = Project(name="Crypto Efficiency")
    chat = Chat(name="BTC variance ratio", project=project)
    session.add(project)
    await session.flush()

    assert chat.project_id == project.id

    await session.delete(project)
    await session.flush()

    remaining = await session.scalars(select(Chat))
    assert remaining.all() == []


async def test_project_name_cannot_be_empty(session):
    session.add(Project(name=""))
    with pytest.raises(IntegrityError):
        await session.flush()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/db/test_project_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'Project'`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/db/models/project.py
from uuid import UUID

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import TimestampedBase


class Project(TimestampedBase):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_projects_name_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)

    # Capability toggles — chats inherit these and may override.
    web_search_enabled: Mapped[bool] = mapped_column(default=False)
    mcp_enabled: Mapped[bool] = mapped_column(default=False)
    code_sandbox_enabled: Mapped[bool] = mapped_column(default=False)

    validation_tier: Mapped[str] = mapped_column(String(20), default="critic")

    # Per-role model assignment: {"planner": {"provider": "...", "model": "..."}, ...}
    model_assignments: Mapped[dict] = mapped_column(JSONB, default=dict)

    chats: Mapped[list["Chat"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
```

```python
# backend/src/econometrica/db/models/chat.py
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import TimestampedBase


class Chat(TimestampedBase):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_chats_name_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(200))
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    # None means "inherit from project".
    web_search_enabled: Mapped[bool | None] = mapped_column(default=None)
    mcp_enabled: Mapped[bool | None] = mapped_column(default=None)

    project: Mapped["Project"] = relationship(back_populates="chats")
```

```python
# backend/src/econometrica/db/models/__init__.py
from econometrica.db.models.chat import Chat
from econometrica.db.models.project import Project

__all__ = ["Chat", "Project"]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/db/test_project_model.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add backend/src/econometrica/db/models backend/tests/db
git commit -m "feat(backend): add project and chat models with capability toggles"
```

---

### Task 1.4: Capability inheritance resolver

Chats inherit project capability settings and may override them. This logic is
pure and belongs in its own tested function rather than scattered through the
API layer.

**Files:**
- Create: `backend/src/econometrica/services/capabilities.py`
- Test: `backend/tests/services/test_capabilities.py`

**Step 1: Write the failing test**

```python
# backend/tests/services/test_capabilities.py
from econometrica.db.models import Chat, Project
from econometrica.services.capabilities import resolve_capabilities


def test_chat_inherits_project_settings_when_unset():
    project = Project(name="P", web_search_enabled=True, mcp_enabled=False)
    chat = Chat(name="C", web_search_enabled=None, mcp_enabled=None)

    resolved = resolve_capabilities(project, chat)

    assert resolved.web_search is True
    assert resolved.mcp is False


def test_chat_override_beats_project_setting():
    project = Project(name="P", web_search_enabled=True, mcp_enabled=True)
    chat = Chat(name="C", web_search_enabled=False, mcp_enabled=None)

    resolved = resolve_capabilities(project, chat)

    assert resolved.web_search is False
    assert resolved.mcp is True


def test_code_sandbox_is_project_level_only_and_defaults_off():
    project = Project(name="P")
    chat = Chat(name="C")

    resolved = resolve_capabilities(project, chat)

    assert resolved.code_sandbox is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_capabilities.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/services/capabilities.py
from dataclasses import dataclass

from econometrica.db.models import Chat, Project


@dataclass(frozen=True)
class ResolvedCapabilities:
    web_search: bool
    mcp: bool
    code_sandbox: bool
    validation_tier: str


def resolve_capabilities(project: Project, chat: Chat) -> ResolvedCapabilities:
    """Chat settings override project settings; None means inherit."""
    return ResolvedCapabilities(
        web_search=(
            project.web_search_enabled
            if chat.web_search_enabled is None
            else chat.web_search_enabled
        ),
        mcp=project.mcp_enabled if chat.mcp_enabled is None else chat.mcp_enabled,
        # The sandbox is deliberately not overridable per chat — it is the most
        # security-sensitive toggle in the system and stays at project scope.
        code_sandbox=project.code_sandbox_enabled,
        validation_tier=project.validation_tier,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/services/test_capabilities.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add backend/src/econometrica/services backend/tests/services
git commit -m "feat(backend): add capability inheritance resolver"
```

---

### Task 1.5: Alembic migrations

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/` (generated)

**Step 1: Initialise Alembic**

```bash
uv run alembic init -t async alembic
```

**Step 2: Point `alembic/env.py` at the metadata and settings**

Replace the `target_metadata` line and URL configuration:

```python
from econometrica.config import get_settings
from econometrica.db.base import Base
from econometrica.db import models  # noqa: F401 — registers all mappers

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```

**Step 3: Generate the initial migration**

```bash
uv run alembic revision --autogenerate -m "initial schema"
```

**Step 4: Apply it and verify**

```bash
uv run alembic upgrade head
```

Then:

```bash
docker exec econometrica-db psql -U econometrica -d econometrica -c "\dt"
```

Expected: `projects`, `chats` and `alembic_version` tables listed.

**Step 5: Commit**

```bash
git add backend/alembic.ini backend/alembic
git commit -m "feat(backend): add alembic migrations for initial schema"
```

---

### Task 1.6: FastAPI application and health endpoint

**Files:**
- Create: `backend/src/econometrica/main.py`
- Create: `backend/src/econometrica/api/routers/health.py`
- Test: `backend/tests/api/test_health.py`

**Step 1: Write the failing test**

```python
# backend/tests/api/test_health.py
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from econometrica.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_reports_ok_and_version(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'econometrica.main'`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/api/routers/health.py
from fastapi import APIRouter

from econometrica import __version__

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
```

```python
# backend/src/econometrica/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from econometrica.api.routers import health

app = FastAPI(title="Econometrica", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
```

Set `__version__ = "0.1.0"` in `backend/src/econometrica/__init__.py`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_health.py -v`
Expected: 1 passed

**Step 5: Commit**

```bash
git add backend/src/econometrica/main.py backend/src/econometrica/api backend/tests/api
git commit -m "feat(backend): add fastapi application with health endpoint"
```

---

### Task 1.7: Projects CRUD API

**Files:**
- Create: `backend/src/econometrica/schemas/project.py`
- Create: `backend/src/econometrica/api/routers/projects.py`
- Modify: `backend/src/econometrica/main.py`
- Test: `backend/tests/api/test_projects.py`

**Step 1: Write the failing test**

```python
# backend/tests/api/test_projects.py
async def test_create_project_returns_201_with_id(client):
    response = await client.post("/api/projects", json={"name": "FX Carry"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "FX Carry"
    assert body["validation_tier"] == "critic"
    assert body["web_search_enabled"] is False


async def test_list_projects_returns_created_projects(client):
    await client.post("/api/projects", json={"name": "Alpha"})
    await client.post("/api/projects", json={"name": "Beta"})

    response = await client.get("/api/projects")
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert {"Alpha", "Beta"} <= set(names)


async def test_rename_project(client):
    created = (await client.post("/api/projects", json={"name": "Before"})).json()

    response = await client.patch(
        f"/api/projects/{created['id']}", json={"name": "After"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "After"


async def test_toggle_web_search_on_project(client):
    created = (await client.post("/api/projects", json={"name": "P"})).json()

    response = await client.patch(
        f"/api/projects/{created['id']}", json={"web_search_enabled": True}
    )

    assert response.json()["web_search_enabled"] is True


async def test_delete_project_returns_204(client):
    created = (await client.post("/api/projects", json={"name": "Doomed"})).json()

    response = await client.delete(f"/api/projects/{created['id']}")
    assert response.status_code == 204

    follow_up = await client.get(f"/api/projects/{created['id']}")
    assert follow_up.status_code == 404


async def test_create_project_rejects_blank_name(client):
    response = await client.post("/api/projects", json={"name": "   "})
    assert response.status_code == 422
```

The `client` fixture must be promoted to `backend/tests/conftest.py` and must
override the `get_session` dependency so requests use the rolled-back test
session.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_projects.py -v`
Expected: FAIL — all six tests 404.

**Step 3: Write the schemas**

```python
# backend/src/econometrica/schemas/project.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    web_search_enabled: bool | None = None
    mcp_enabled: bool | None = None
    code_sandbox_enabled: bool | None = None
    validation_tier: str | None = None
    model_assignments: dict | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    web_search_enabled: bool
    mcp_enabled: bool
    code_sandbox_enabled: bool
    validation_tier: str
    model_assignments: dict
    created_at: datetime
    updated_at: datetime
```

**Step 4: Write the router**

Standard async CRUD with `select`, `session.add`, `session.delete`, returning
404 via `HTTPException` when a project id does not resolve. Register it in
`main.py` with `app.include_router(projects.router)`.

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_projects.py -v`
Expected: 6 passed

**Step 6: Commit**

```bash
git add backend/src/econometrica/schemas backend/src/econometrica/api/routers/projects.py backend/tests/api/test_projects.py backend/src/econometrica/main.py
git commit -m "feat(backend): add projects crud api"
```

---

### Task 1.8: Chats CRUD API

Mirrors Task 1.7 for chats, nested under a project.

**Files:**
- Create: `backend/src/econometrica/schemas/chat.py`
- Create: `backend/src/econometrica/api/routers/chats.py`
- Test: `backend/tests/api/test_chats.py`

**Behaviour the tests must cover:**

- `POST /api/projects/{project_id}/chats` creates a chat and returns 201.
- `GET /api/projects/{project_id}/chats` lists only that project's chats.
- `PATCH /api/chats/{chat_id}` renames a chat.
- `PATCH /api/chats/{chat_id}` with `{"web_search_enabled": true}` overrides the
  project setting; with `null` it reverts to inheriting.
- `GET /api/chats/{chat_id}/capabilities` returns the resolved capabilities from
  Task 1.4 — this is the endpoint the UI reads to render toggle states.
- `DELETE /api/chats/{chat_id}` returns 204.
- Creating a chat under a non-existent project returns 404.

**Commit:** `feat(backend): add chats crud api with capability resolution`

---

### Task 1.9: Frontend bootstrap

**Files:**
- Create: `frontend/package.json`, `vite.config.ts`, `tsconfig.json`,
  `tailwind.config.ts`, `index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Create: `frontend/src/styles/index.css`

**Step 1: Scaffold**

```bash
npm create vite@latest frontend -- --template react-ts
```

**Step 2: Install dependencies**

```bash
npm install @tanstack/react-query zustand react-resizable-panels clsx tailwind-merge lucide-react @radix-ui/react-dialog @radix-ui/react-dropdown-menu @radix-ui/react-tooltip @radix-ui/react-tabs @radix-ui/react-switch
```

```bash
npm install -D tailwindcss @tailwindcss/vite vitest @testing-library/react @testing-library/user-event jsdom @playwright/test
```

**Step 3: Configure the Vite dev server to proxy the API**

```ts
// frontend/vite.config.ts
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"] },
});
```

**Step 4: Define the design tokens**

```css
/* frontend/src/styles/index.css */
@import "tailwindcss";

@theme {
  --font-sans: "Inter", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;

  --color-accent: oklch(0.55 0.14 250);
  --color-positive: oklch(0.62 0.14 155);
  --color-negative: oklch(0.58 0.19 25);
}

:root {
  --surface-0: oklch(1 0 0);
  --surface-1: oklch(0.985 0 0);
  --surface-2: oklch(0.96 0 0);
  --border: oklch(0.9 0 0);
  --text-primary: oklch(0.2 0 0);
  --text-secondary: oklch(0.5 0 0);
}

:root[data-theme="dark"] {
  --surface-0: oklch(0.16 0.005 250);
  --surface-1: oklch(0.19 0.005 250);
  --surface-2: oklch(0.23 0.005 250);
  --border: oklch(0.3 0.005 250);
  --text-primary: oklch(0.95 0 0);
  --text-secondary: oklch(0.68 0 0);
}
```

**Step 5: Verify it runs**

```bash
npm run dev
```

Expected: dev server on `http://localhost:5173`, default page renders.

**Step 6: Commit**

```bash
git add frontend
git commit -m "chore(frontend): bootstrap vite react typescript app with design tokens"
```

---

### Task 1.10: Three-pane layout shell

**Files:**
- Create: `frontend/src/components/layout/AppShell.tsx`
- Create: `frontend/src/components/layout/SidePane.tsx`
- Create: `frontend/src/components/layout/CanvasPane.tsx`
- Create: `frontend/src/components/layout/ChatPane.tsx`
- Test: `frontend/src/components/layout/AppShell.test.tsx`

**Step 1: Write the failing test**

```tsx
// frontend/src/components/layout/AppShell.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  it("renders all three panes", () => {
    render(<AppShell />);
    expect(screen.getByRole("navigation", { name: /projects/i })).toBeTruthy();
    expect(screen.getByRole("region", { name: /artifacts/i })).toBeTruthy();
    expect(screen.getByRole("complementary", { name: /chat/i })).toBeTruthy();
  });

  it("collapses the projects pane when the toggle is clicked", async () => {
    render(<AppShell />);
    await userEvent.click(screen.getByRole("button", { name: /collapse projects/i }));
    expect(screen.queryByRole("navigation", { name: /projects/i })).toBeNull();
  });
});
```

**Step 2: Run test to verify it fails**

Run: `npm run test -- AppShell`
Expected: FAIL — cannot resolve `./AppShell`

**Step 3: Implement using `react-resizable-panels`**

Three `Panel` elements inside a horizontal `PanelGroup`, with `PanelResizeHandle`
between them. Default sizes 18 / 54 / 28 percent, minimum sizes 12 / 30 / 20.
Collapse state lives in the Zustand store so it survives navigation. Each pane
carries the ARIA role and accessible name the tests assert on.

**Step 4: Run test to verify it passes**

Run: `npm run test -- AppShell`
Expected: 2 passed

**Step 5: Commit**

```bash
git add frontend/src/components/layout
git commit -m "feat(frontend): add resizable collapsible three-pane shell"
```

---

### Task 1.11: Projects and chats tree in the left pane

**Files:**
- Create: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`
- Create: `frontend/src/components/projects/ProjectTree.tsx`
- Create: `frontend/src/components/projects/ProjectItem.tsx`
- Create: `frontend/src/components/projects/InlineRename.tsx`
- Test: `frontend/src/components/projects/ProjectTree.test.tsx`

**Behaviour the tests must cover:**

- Renders projects returned by a mocked `GET /api/projects`.
- Expanding a project lists its chats.
- "New project" button posts and optimistically inserts the row.
- Double-clicking a name enters inline rename; Enter commits, Escape cancels.
- Renaming issues a `PATCH` with only the changed field.
- Deleting prompts for confirmation before issuing `DELETE`.

**Commit:** `feat(frontend): add project and chat tree with inline rename`

---

### Task 1.12: End-to-end smoke test

**Files:**
- Create: `frontend/e2e/skeleton.spec.ts`
- Create: `frontend/playwright.config.ts`

Playwright test against the running stack: create a project, rename it, add a
chat under it, rename the chat, reload the page, and assert both names persisted.
This is the gate that closes Phase 1.

**Commit:** `test(e2e): add skeleton smoke test for project and chat lifecycle`

---

## Phase 2: Econometrics core

This phase contains **no LLM code at all**. It builds and tests the statistical
engine on its own, so that when agents are added in Phase 4 they are selecting
from a body of work already known to be correct.

### Task 2.1: Result types

**Files:**
- Create: `backend/src/econometrica/econ/types.py`
- Test: `backend/tests/econ/test_types.py`

**Step 1: Write the failing test**

```python
# backend/tests/econ/test_types.py
import pytest

from econometrica.econ.types import Diagnostic, Estimate, Manifest, ResultSet


def test_estimate_derives_significance_from_p_value():
    est = Estimate(name="beta", value=1.2, std_error=0.1, t_stat=12.0, p_value=0.001)
    assert est.is_significant(alpha=0.05) is True
    assert est.is_significant(alpha=0.0005) is False


def test_estimate_without_p_value_is_not_significant():
    est = Estimate(name="beta", value=1.2)
    assert est.is_significant() is False


def test_resultset_exposes_estimates_by_name():
    rs = ResultSet(
        tool="capm",
        version="1.0.0",
        params={},
        estimates=[Estimate(name="alpha", value=0.001), Estimate(name="beta", value=1.1)],
        manifest=Manifest(data_fingerprint="abc", tool="capm", tool_version="1.0.0"),
    )
    assert rs.estimate("beta").value == pytest.approx(1.1)
    assert rs.estimate("missing") is None


def test_resultset_collects_all_numeric_values_for_grounding_check():
    """The numeric grounding gate needs every number a narrator may cite."""
    rs = ResultSet(
        tool="capm",
        version="1.0.0",
        params={},
        estimates=[Estimate(name="beta", value=1.1, p_value=0.02)],
        scalars={"r_squared": 0.83},
        manifest=Manifest(data_fingerprint="abc", tool="capm", tool_version="1.0.0"),
    )
    values = rs.all_numeric_values()
    assert 1.1 in values
    assert 0.02 in values
    assert 0.83 in values


def test_diagnostic_passed_is_explicit_not_inferred():
    diag = Diagnostic(name="jarque_bera", statistic=3.1, p_value=0.21, passed=True)
    assert diag.passed is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/econ/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/econ/types.py
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class Estimate(BaseModel):
    """A single estimated coefficient with its inferential statistics."""

    name: str
    value: float
    std_error: float | None = None
    t_stat: float | None = None
    p_value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None

    def is_significant(self, alpha: float = 0.05) -> bool:
        return self.p_value is not None and self.p_value < alpha


class Diagnostic(BaseModel):
    """A deterministic assumption check. `passed` is set by the tool, never inferred."""

    name: str
    statistic: float
    p_value: float | None = None
    critical_values: dict[str, float] = Field(default_factory=dict)
    passed: bool | None = None
    interpretation: str = ""


class Table(BaseModel):
    columns: list[str]
    rows: list[list[Any]]


class Series(BaseModel):
    name: str
    x: list[Any]
    y: list[float | None]


class Manifest(BaseModel):
    """Everything needed to reproduce a result bit-for-bit."""

    data_fingerprint: str
    tool: str
    tool_version: str
    params_hash: str = ""
    library_versions: dict[str, str] = Field(default_factory=dict)
    seed: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResultSet(BaseModel):
    tool: str
    version: str
    params: dict[str, Any]
    estimates: list[Estimate] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    scalars: dict[str, float] = Field(default_factory=dict)
    tables: dict[str, Table] = Field(default_factory=dict)
    series: dict[str, Series] = Field(default_factory=dict)
    manifest: Manifest

    def estimate(self, name: str) -> Estimate | None:
        return next((e for e in self.estimates if e.name == name), None)

    def all_numeric_values(self) -> set[float]:
        """Every number a narrator is permitted to cite."""
        values: set[float] = set(self.scalars.values())
        for est in self.estimates:
            for field in (est.value, est.std_error, est.t_stat, est.p_value,
                          est.ci_low, est.ci_high):
                if field is not None:
                    values.add(field)
        for diag in self.diagnostics:
            values.add(diag.statistic)
            if diag.p_value is not None:
                values.add(diag.p_value)
            values.update(diag.critical_values.values())
        return values
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/econ/test_types.py -v`
Expected: 5 passed

**Step 5: Commit**

```bash
git add backend/src/econometrica/econ backend/tests/econ
git commit -m "feat(econ): add result types with numeric grounding support"
```

---

### Task 2.2: Data fingerprinting

Reproducibility depends on being able to prove two runs saw identical input.

**Files:**
- Create: `backend/src/econometrica/econ/fingerprint.py`
- Test: `backend/tests/econ/test_fingerprint.py`

**Step 1: Write the failing test**

```python
# backend/tests/econ/test_fingerprint.py
import numpy as np
import pandas as pd

from econometrica.econ.fingerprint import fingerprint_frame, fingerprint_params


def test_identical_frames_produce_identical_fingerprints():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    assert fingerprint_frame(df) == fingerprint_frame(df.copy())


def test_different_values_produce_different_fingerprints():
    a = pd.DataFrame({"x": [1.0, 2.0]})
    b = pd.DataFrame({"x": [1.0, 2.000001]})
    assert fingerprint_frame(a) != fingerprint_frame(b)


def test_column_order_affects_fingerprint():
    a = pd.DataFrame({"x": [1.0], "y": [2.0]})
    b = a[["y", "x"]]
    assert fingerprint_frame(a) != fingerprint_frame(b)


def test_index_is_part_of_the_fingerprint():
    a = pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2020-01-01"]))
    b = pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2020-01-02"]))
    assert fingerprint_frame(a) != fingerprint_frame(b)


def test_nan_values_are_handled_deterministically():
    df = pd.DataFrame({"x": [1.0, np.nan]})
    assert fingerprint_frame(df) == fingerprint_frame(df.copy())


def test_param_fingerprint_is_order_independent():
    assert fingerprint_params({"p": 1, "q": 2}) == fingerprint_params({"q": 2, "p": 1})
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/econ/test_fingerprint.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/econ/fingerprint.py
import hashlib
import json
from typing import Any

import pandas as pd


def fingerprint_frame(df: pd.DataFrame) -> str:
    """SHA-256 over the exact values, column order and index of a frame.

    Uses pandas' own hashing so NaN hashes consistently rather than by identity.
    """
    from pandas.util import hash_pandas_object

    hasher = hashlib.sha256()
    hasher.update("|".join(map(str, df.columns)).encode())
    hasher.update(hash_pandas_object(df, index=True).values.tobytes())
    return hasher.hexdigest()


def fingerprint_params(params: dict[str, Any]) -> str:
    """SHA-256 over canonicalised parameters — key order must not matter."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/econ/test_fingerprint.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add backend/src/econometrica/econ/fingerprint.py backend/tests/econ/test_fingerprint.py
git commit -m "feat(econ): add deterministic data and parameter fingerprinting"
```

---

### Task 2.3: Tool registry

**Files:**
- Create: `backend/src/econometrica/econ/registry.py`
- Test: `backend/tests/econ/test_registry.py`

**Step 1: Write the failing test**

```python
# backend/tests/econ/test_registry.py
import pytest
from pydantic import BaseModel

from econometrica.econ.registry import ToolRegistry, get_registry


class DummyParams(BaseModel):
    window: int = 20


def test_registered_tool_is_retrievable_by_name():
    registry = ToolRegistry()

    @registry.register(name="dummy", version="1.0.0", params_model=DummyParams,
                       family="test", summary="A dummy tool")
    def dummy(data, params): ...

    tool = registry.get("dummy")
    assert tool.version == "1.0.0"
    assert tool.family == "test"


def test_registering_a_duplicate_name_raises():
    registry = ToolRegistry()

    @registry.register(name="dup", version="1.0.0", params_model=DummyParams,
                       family="test", summary="")
    def a(data, params): ...

    with pytest.raises(ValueError, match="already registered"):
        @registry.register(name="dup", version="1.0.0", params_model=DummyParams,
                           family="test", summary="")
        def b(data, params): ...


def test_unknown_tool_lookup_raises_keyerror():
    with pytest.raises(KeyError):
        ToolRegistry().get("nope")


def test_registry_emits_json_schema_for_llm_tool_calling():
    """Agents receive the registry as tool definitions; the schema must be complete."""
    registry = ToolRegistry()

    @registry.register(name="dummy", version="1.0.0", params_model=DummyParams,
                       family="test", summary="A dummy tool")
    def dummy(data, params): ...

    schemas = registry.to_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "dummy"
    assert schemas[0]["description"] == "A dummy tool"
    assert "window" in schemas[0]["input_schema"]["properties"]


def test_global_registry_contains_every_shipped_tool_family():
    families = {tool.family for tool in get_registry().all()}
    assert {"pricing", "efficiency", "volatility", "multivariate", "events"} <= families
```

The final test will fail until Phase 2 is complete. That is intentional — it is
the phase's completion gate. Mark it `@pytest.mark.phase_gate` and exclude it
from the default run until Task 2.20.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/econ/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/econ/registry.py
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pydantic import BaseModel

from econometrica.econ.types import ResultSet

ToolFn = Callable[[pd.DataFrame, BaseModel], ResultSet]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    version: str
    family: str
    summary: str
    params_model: type[BaseModel]
    fn: ToolFn
    preconditions: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        version: str,
        family: str,
        summary: str,
        params_model: type[BaseModel],
        preconditions: tuple[str, ...] = (),
    ) -> Callable[[ToolFn], ToolFn]:
        def decorator(fn: ToolFn) -> ToolFn:
            if name in self._tools:
                raise ValueError(f"tool {name!r} is already registered")
            self._tools[name] = RegisteredTool(
                name=name, version=version, family=family, summary=summary,
                params_model=params_model, fn=fn, preconditions=preconditions,
            )
            return fn

        return decorator

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name!r}")
        return self._tools[name]

    def all(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def to_tool_schemas(self) -> list[dict[str, Any]]:
        """Render the registry as LLM tool definitions."""
        return [
            {
                "name": tool.name,
                "description": tool.summary,
                "input_schema": tool.params_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]


_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _REGISTRY
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/econ/test_registry.py -v -m "not phase_gate"`
Expected: 4 passed, 1 deselected

**Step 5: Commit**

```bash
git add backend/src/econometrica/econ/registry.py backend/tests/econ/test_registry.py
git commit -m "feat(econ): add versioned tool registry with llm schema export"
```

---

### Task 2.4: Return construction

Every downstream model depends on this. Getting log-versus-simple returns and
calendar alignment wrong silently corrupts everything after it.

**Files:**
- Create: `backend/src/econometrica/econ/returns.py`
- Test: `backend/tests/econ/test_returns.py`

**Step 1: Write the failing test**

```python
# backend/tests/econ/test_returns.py
import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from econometrica.econ.returns import (
    align_series,
    annualise_return,
    excess_returns,
    to_returns,
)


def test_simple_returns_computed_correctly():
    prices = pd.Series([100.0, 110.0, 99.0])
    result = to_returns(prices, method="simple")
    assert result.iloc[0] == pytest.approx(0.10)
    assert result.iloc[1] == pytest.approx(-0.10)


def test_log_returns_computed_correctly():
    prices = pd.Series([100.0, 110.0])
    result = to_returns(prices, method="log")
    assert result.iloc[0] == pytest.approx(np.log(1.1))


def test_returns_drop_the_first_observation():
    prices = pd.Series([100.0, 110.0, 120.0])
    assert len(to_returns(prices, method="simple")) == 2


def test_log_returns_are_additive_over_time():
    """The defining property of log returns — worth asserting, not assuming."""
    prices = pd.Series([100.0, 110.0, 121.0, 108.9])
    log_ret = to_returns(prices, method="log")
    total = np.log(prices.iloc[-1] / prices.iloc[0])
    assert log_ret.sum() == pytest.approx(total)


@given(st.floats(min_value=1.0, max_value=1000.0, allow_nan=False))
@settings(max_examples=50)
def test_flat_prices_produce_zero_returns(price):
    prices = pd.Series([price] * 5)
    assert to_returns(prices, method="simple").abs().max() == pytest.approx(0.0)


def test_align_series_keeps_only_shared_dates():
    idx_a = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
    idx_b = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    a = pd.Series([1.0, 2.0, 3.0], index=idx_a, name="a")
    b = pd.Series([4.0, 5.0, 6.0], index=idx_b, name="b")

    aligned = align_series({"a": a, "b": b})

    assert list(aligned.index) == list(pd.to_datetime(["2020-01-02", "2020-01-03"]))
    assert list(aligned.columns) == ["a", "b"]


def test_align_series_raises_when_no_overlap():
    a = pd.Series([1.0], index=pd.to_datetime(["2020-01-01"]))
    b = pd.Series([1.0], index=pd.to_datetime(["2021-01-01"]))
    with pytest.raises(ValueError, match="no overlapping"):
        align_series({"a": a, "b": b})


def test_excess_returns_subtract_the_risk_free_rate():
    idx = pd.to_datetime(["2020-01-01", "2020-01-02"])
    asset = pd.Series([0.01, 0.02], index=idx)
    rf = pd.Series([0.001, 0.001], index=idx)
    result = excess_returns(asset, rf)
    assert result.iloc[0] == pytest.approx(0.009)


def test_annualise_return_uses_the_stated_frequency():
    assert annualise_return(0.0005, periods_per_year=252) == pytest.approx(
        (1.0005) ** 252 - 1
    )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/econ/test_returns.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# backend/src/econometrica/econ/returns.py
from typing import Literal

import numpy as np
import pandas as pd

ReturnMethod = Literal["simple", "log"]

PERIODS_PER_YEAR = {"D": 252, "W": 52, "M": 12, "Q": 4, "A": 1}


def to_returns(prices: pd.Series, method: ReturnMethod = "log") -> pd.Series:
    if method == "simple":
        return prices.pct_change().dropna()
    if method == "log":
        return np.log(prices / prices.shift(1)).dropna()
    raise ValueError(f"unknown return method: {method!r}")


def align_series(series: dict[str, pd.Series]) -> pd.DataFrame:
    """Inner-join series on their shared index, preserving insertion order."""
    frame = pd.concat(series.values(), axis=1, join="inner", keys=series.keys())
    frame.columns = list(series.keys())
    frame = frame.dropna()
    if frame.empty:
        raise ValueError("no overlapping observations across the supplied series")
    return frame


def excess_returns(asset: pd.Series, risk_free: pd.Series) -> pd.Series:
    aligned = align_series({"asset": asset, "rf": risk_free})
    return aligned["asset"] - aligned["rf"]


def annualise_return(period_return: float, periods_per_year: int) -> float:
    return (1.0 + period_return) ** periods_per_year - 1.0
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/econ/test_returns.py -v`
Expected: 10 passed

**Step 5: Commit**

```bash
git add backend/src/econometrica/econ/returns.py backend/tests/econ/test_returns.py
git commit -m "feat(econ): add return construction and calendar alignment"
```

---

### Task 2.5: Synthetic data fixtures

Known-answer testing needs data whose true parameters are known. Building these
once, properly, makes every subsequent econometrics task straightforward.

**Files:**
- Create: `backend/tests/econ/fixtures.py`
- Test: `backend/tests/econ/test_fixtures.py`

**Step 1: Write the failing test**

```python
# backend/tests/econ/test_fixtures.py
import numpy as np
import pytest

from tests.econ.fixtures import (
    make_capm_data,
    make_cointegrated_pair,
    make_garch_series,
    make_random_walk,
    make_stationary_ar1,
)


def test_capm_fixture_is_reproducible_under_a_seed():
    a = make_capm_data(beta=1.3, alpha=0.0002, n=500, seed=42)
    b = make_capm_data(beta=1.3, alpha=0.0002, n=500, seed=42)
    assert a.equals(b)


def test_capm_fixture_recovers_beta_under_ols():
    """If the fixture is wrong, every asset-pricing test built on it is wrong."""
    import statsmodels.api as sm

    data = make_capm_data(beta=1.3, alpha=0.0002, n=5000, seed=7, resid_vol=0.005)
    model = sm.OLS(data["asset"], sm.add_constant(data["market"])).fit()
    assert model.params.iloc[1] == pytest.approx(1.3, abs=0.05)


def test_random_walk_has_a_unit_root():
    from statsmodels.tsa.stattools import adfuller

    walk = make_random_walk(n=2000, seed=1)
    assert adfuller(walk)[1] > 0.10


def test_stationary_ar1_rejects_the_unit_root():
    from statsmodels.tsa.stattools import adfuller

    ar1 = make_stationary_ar1(phi=0.5, n=2000, seed=1)
    assert adfuller(ar1)[1] < 0.01


def test_garch_fixture_exhibits_volatility_clustering():
    from statsmodels.stats.diagnostic import het_arch

    series = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=3000, seed=3)
    _, p_value, _, _ = het_arch(series, nlags=10)
    assert p_value < 0.01


def test_cointegrated_pair_has_a_stationary_spread():
    from statsmodels.tsa.stattools import adfuller

    x, y = make_cointegrated_pair(n=2000, seed=5)
    spread = y - 1.5 * x
    assert adfuller(spread)[1] < 0.01
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/econ/test_fixtures.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.econ.fixtures'`

**Step 3: Write the fixtures**

Implement each generator with an explicit `numpy.random.default_rng(seed)` so
results are reproducible. `make_garch_series` iterates the GARCH(1,1) recursion
directly rather than calling `arch`, so the test data is independent of the
library being tested.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/econ/test_fixtures.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add backend/tests/econ/fixtures.py backend/tests/econ/test_fixtures.py
git commit -m "test(econ): add validated synthetic data generators"
```

---

### Tasks 2.6 – 2.19: The econometric tools

Every one of these follows the identical five-step rhythm. Each registers into
the global registry, returns a fully populated `ResultSet` with a `Manifest`,
and attaches its own `Diagnostic` list. Each gets a known-answer test against
the Task 2.5 fixtures plus at least one property-based invariant.

| Task | Module | Tools | Key known-answer test |
|---|---|---|---|
| 2.6 | `econ/pricing/capm.py` | `capm` | Regressing an asset on itself yields β=1, α=0, R²=1 |
| 2.7 | `econ/pricing/factor_models.py` | `ff3`, `ff5`, `carhart4` | On data generated from known factor loadings, loadings are recovered within CI |
| 2.8 | `econ/pricing/robust_errors.py` | Newey-West and White covariance options on all pricing tools | HAC standard errors exceed OLS errors on deliberately autocorrelated residuals |
| 2.9 | `econ/pricing/rolling.py` | `rolling_beta` | A series with a beta break at the midpoint shows the rolling estimate crossing between the two true values |
| 2.10 | `econ/pricing/fama_macbeth.py` | `fama_macbeth` | Recovers a known risk premium on a simulated cross-section |
| 2.11 | `econ/pricing/grs.py` | `grs_test` | Fails to reject when alphas are truly zero; rejects when they are not |
| 2.12 | `econ/efficiency/unit_root.py` | `adf`, `kpss`, `phillips_perron` | Random walk fails to reject ADF; AR(1) with φ=0.5 rejects. KPSS gives the mirror-image verdict |
| 2.13 | `econ/efficiency/variance_ratio.py` | `variance_ratio` | VR(1)=1 exactly; a random walk gives VR≈1 across horizons; a trending series gives VR>1 |
| 2.14 | `econ/efficiency/randomness.py` | `runs_test`, `ljung_box`, `bds`, `hurst` | i.i.d. noise gives Hurst≈0.5; a random walk in levels gives Hurst≈1.0 |
| 2.15 | `econ/efficiency/score.py` | `weak_form_efficiency_score` | Composite over the above; simulated random walk scores as efficient, an AR(1) with φ=0.7 does not |
| 2.16 | `econ/volatility/garch.py` | `garch`, `egarch`, `gjr_garch` | Parameters of a simulated GARCH(1,1) are recovered within their confidence intervals |
| 2.17 | `econ/volatility/risk.py` | `historical_var`, `parametric_var`, `cvar`, `ewma_vol`, `realized_vol`, `drawdown` | Historical VaR at 95% on 1000 sorted points equals the 50th smallest; CVaR ≤ VaR always |
| 2.18 | `econ/volatility/backtests.py` | `kupiec_test`, `christoffersen_test` | A correctly specified VaR passes; one at the wrong quantile is rejected |
| 2.19 | `econ/multivariate/*.py` and `econ/events/study.py` | `var_model`, `vecm`, `johansen`, `engle_granger`, `granger_causality`, `irf`, `fevd`, `markov_switching`, `event_study` | Johansen recovers rank 1 on the Task 2.5 cointegrated pair; Granger causality detects a known lagged driver and finds none in independent series; event study recovers an injected abnormal return |

**Per-task step template:**

1. Write the failing known-answer test plus one property test.
2. Run it: `uv run pytest tests/econ/<family>/test_<tool>.py -v` — expect failure.
3. Implement the tool, registering it with `@get_registry().register(...)`.
4. Run it again — expect pass.
5. Commit: `feat(econ): add <tool> with known-answer tests`.

**Critical implementation note for all of them:** the statsmodels and arch
result objects must never leak out of these modules. Each tool translates its
library result into `ResultSet` at the boundary. Everything downstream — agents,
API, charts, exports — depends only on `ResultSet`, so a library upgrade cannot
ripple through the application.

---

### Task 2.20: Diagnostics engine

**Files:**
- Create: `backend/src/econometrica/econ/diagnostics/engine.py`
- Test: `backend/tests/econ/test_diagnostics_engine.py`

Runs the full assumption battery over any fitted residual series and returns
`list[Diagnostic]`: Jarque-Bera, Breusch-Pagan, White, Durbin-Watson, Ljung-Box,
ARCH-LM, VIF for multi-regressor models, and CUSUM plus Chow structural breaks.

**Tests must cover:** homoskedastic residuals pass Breusch-Pagan while
deliberately heteroskedastic ones fail; autocorrelated residuals fail Ljung-Box;
collinear regressors produce VIF above 10; a series with an injected mean shift
is flagged by the break test.

This is what feeds the Validator agent facts instead of asking it to infer them.

**Commit:** `feat(econ): add deterministic diagnostics engine`

---

### Task 2.21: Phase 2 gate

Enable the `phase_gate` test from Task 2.3 and run the full suite with coverage.

```bash
uv run pytest --cov=econometrica.econ --cov-report=term-missing
```

Expected: all tests pass, `econometrica.econ` coverage at or above 90%.

**Commit:** `test(econ): enable phase gate and confirm core coverage`

---

## Phase 3: Provider layer and single-agent chat

| Task | Files | Acceptance criteria |
|---|---|---|
| 3.1 | `llm/base.py` | `LLMProvider` protocol with `complete`, `stream`, `call_tools`, and a `Capabilities` record (tool calling, JSON mode, streaming, context window). Tests use a fake provider — no network in unit tests |
| 3.2 | `llm/providers/ollama.py` | Lists locally available models, streams tokens, handles a stopped Ollama daemon with a clear error rather than a traceback |
| 3.3 | `llm/providers/openai.py`, `nvidia.py` | NVIDIA NIM reuses the OpenAI-compatible transport with a different base URL; both share one adapter with injected configuration |
| 3.4 | `llm/providers/anthropic.py` | **Load the `claude-api` skill before writing this adapter.** Native tool-use blocks, streaming, prompt caching on the system prompt |
| 3.5 | `llm/providers/gemini.py` | Function-calling and streaming parity with the others |
| 3.6 | `services/keystore.py` | API keys encrypted at rest with Fernet keyed from `ECONOMETRICA_SECRET_KEY`; a test proves the ciphertext on disk does not contain the plaintext key |
| 3.7 | `api/routers/providers.py` | `GET /api/providers` reports which providers are configured and reachable, and lists their models. Never returns key material |
| 3.8 | `db/models/message.py`, `api/routers/messages.py` | Message persistence plus `POST /api/chats/{id}/messages` streaming a reply over SSE |
| 3.9 | `frontend/src/components/chat/*` | Chat pane with streaming render, markdown, stop button, and per-message provider/model badge |
| 3.10 | e2e | Send a message with Ollama selected and receive a streamed reply end to end |

---

## Phase 4: Multi-agent orchestration

| Task | Files | Acceptance criteria |
|---|---|---|
| 4.1 | `agents/schemas.py` | Typed `AnalysisPlan`, `PlanStep`, `DatasetSpec`, `ValidationVerdict`. Malformed LLM output is rejected and retried rather than passed downstream |
| 4.2 | `agents/planner.py` | Turns a prompt plus project context into a validated `AnalysisPlan`. Tested against recorded provider fixtures, not live calls |
| 4.3 | `agents/data_steward.py` | Resolves tickers and uploads, aligns calendars, emits a data-quality report. Flags survivorship and look-ahead risks |
| 4.4 | `agents/econometrician.py` | Binds plan steps to registry tools. **Rejects a plan that violates a tool's declared preconditions** — a test must prove GARCH is refused when ARCH-LM finds no effects |
| 4.5 | `agents/validator.py` | Consumes deterministic diagnostics plus results, emits a verdict with reasons. Must be assignable to a different provider than the Econometrician; a test asserts the orchestrator warns when both are the same |
| 4.6 | `agents/grounding.py` | **The numeric grounding gate.** Extracts every number from narrator prose and matches against `ResultSet.all_numeric_values()` with tolerance. Tests: fabricated statistics are blocked; correctly rounded values pass; years and sample sizes are exempted |
| 4.7 | `agents/narrator.py` | Writes interpretation with artifact and statistic citations; output passes the grounding gate |
| 4.8 | `agents/orchestrator.py` | Runs the full pipeline, honours the three validation tiers, bounds revision loops, and streams step-level progress over SSE |
| 4.9 | `db/models/run.py`, `services/tracing.py` | Run and Step persistence capturing agent, provider, model, tokens, cost, latency, tool-call hashes and parent links |
| 4.10 | e2e | "Test whether Bitcoin follows a random walk" runs end to end and produces validated results with a full trace |

---

## Phase 5: Charts and artifact canvas

| Task | Files | Acceptance criteria |
|---|---|---|
| 5.1 | `charts/spec.py` | Discriminated-union `ChartSpec` over ~22 chart types. An invalid spec from an LLM is rejected with a usable error |
| 5.2 | `agents/visualizer.py` | Selects chart types from result shape. Tests: a GARCH result proposes a conditional volatility overlay; a VAR result proposes an IRF grid |
| 5.3 | `frontend/src/components/charts/*` | **Load the `dataviz` skill before writing any chart code.** Plotly renderer per spec type, themed for light and dark, with a shared partial bundle |
| 5.4 | `frontend/src/components/canvas/*` | Tabbed artifact canvas with pinning, full-screen, and re-run |
| 5.5 | `api/routers/exports.py` | PNG, SVG, PDF, CSV, XLSX, JSON, Markdown and project ZIP with reproducibility manifest |
| 5.6 | e2e | Produce a chart, interact with it, export it, and confirm the downloaded file opens |

---

## Phase 6: Platform

| Task | Files | Acceptance criteria |
|---|---|---|
| 6.1 | `services/ingest.py` | CSV, XLSX and Parquet profiling with schema inference and LLM-assisted column role mapping that the user confirms before ingest |
| 6.2 | `db/models/dataset.py` | Timescale hypertable for observations; original blob retained |
| 6.3 | `data/providers/*` | yfinance, Stooq, FRED and Ken French adapters behind one interface, with caching and offline-friendly failures |
| 6.4 | `services/rag.py` | Document chunking into pgvector with retrieval scoped to the project |
| 6.5 | `tools/web_search.py` | Provider-agnostic search, off by default, results attributed in the trace |
| 6.6 | `mcp/client.py` | MCP client with an explicit tool allowlist. A test proves an unlisted tool cannot be invoked |
| 6.7 | `telemetry/*` | OpenTelemetry spans to Postgres, optional OTLP export, and a metrics endpoint |
| 6.8 | `frontend/src/components/telemetry/*` | Trace viewer rendering the run DAG, and a cost and latency dashboard |
| 6.9 | `sandbox/runner.py` | **Built last.** Subprocess isolation, no network, no filesystem, import allowlist, CPU/memory/wall-clock caps. Tests must prove each restriction actually holds — an escape attempt per restriction |
| 6.10 | e2e | Full regression across all six phases |

---

## Definition of done

- `uv run pytest` passes with `econometrica.econ` coverage at or above 90%.
- `npm run test` and `npm run test:e2e` pass.
- `uv run ruff check` and `uv run mypy src` are clean.
- `docker compose up` followed by the documented start commands yields a working
  application from a clean clone.
- Every number shown in the UI traces to a `ResultSet` with a manifest, and
  Re-run reproduces it.

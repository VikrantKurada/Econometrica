from econometrica.config import Settings


def test_settings_read_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5433/db")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://u:p@localhost:5433/db"


def test_settings_storage_dir_defaults_to_local_storage():
    settings = Settings(database_url="postgresql+asyncpg://u:p@h/d")
    assert settings.storage_dir.name == "storage"

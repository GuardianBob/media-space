import pytest
from fastapi.testclient import TestClient

from mediamanager import config, db
from mediamanager.app import create_app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own data folder, and never sees the real .env."""
    monkeypatch.setenv("MEDIA_MANAGER_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    db.init_db()


@pytest.fixture
def conn():
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture
def client():
    return TestClient(create_app(port=8787), base_url="http://127.0.0.1:8787")

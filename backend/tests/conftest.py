import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    DATA_PROVIDER="synthetic",
    SECRET_KEY="x" * 48,
    OPENROUTER_API_KEY="",
    ALLOW_REGISTRATION="false",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import rate_limiter  # noqa: E402

XHR = {"X-Requested-With": "XMLHttpRequest"}
CREDS = {"email": "bastien@example.com", "password": "un-mot-de-passe-long"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    rate_limiter.reset()
    monkeypatch.setattr("app.services.scanner.fetch_headlines", lambda *a, **k: [])


@pytest.fixture
def client():
    with TestClient(app, headers=XHR) as c:
        yield c


@pytest.fixture
def auth_client(client):
    assert client.post("/api/auth/register", json=CREDS).status_code == 201
    return client

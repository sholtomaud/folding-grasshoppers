import pytest

from cardboard_engine.lifecycle import Lifecycle
from cardboard_engine.server import create_app

TOKEN = "test-token-0123456789abcdef"


@pytest.fixture
def lifecycle():
    return Lifecycle(parent_pid=None)


@pytest.fixture
def app(lifecycle):
    app = create_app(shutdown_token=TOKEN, lifecycle=lifecycle)
    app.testing = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()

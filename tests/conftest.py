import pytest

from app import create_app
from app.models import User, db
from werkzeug.security import generate_password_hash


@pytest.fixture
def app():
    application = create_app()
    application.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        WTF_CSRF_ENABLED=False,
    )

    with application.app_context():
        db.create_all()
        db.session.add(
            User(
                email="test@example.com",
                password_hash=generate_password_hash("unused"),
                display_name="Test User",
            )
        )
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    test_client = app.test_client()
    response = test_client.post(
        "/login",
        data={"email": "test@example.com", "password": "unused"},
    )
    assert response.status_code == 302
    return test_client


@pytest.fixture
def app_context(app):
    with app.app_context() as context:
        yield context

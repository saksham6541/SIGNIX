from datetime import datetime

from app.models import User, UserLocation, db
from werkzeug.security import generate_password_hash


def test_properties_page_requires_login(app):
    response = app.test_client().get("/properties")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_properties_page_lists_only_current_users_locations(app):
    with app.app_context():
        user_a = User.query.filter_by(email="test@example.com").one()
        user_b = User(
            email="properties-other@example.com",
            password_hash=generate_password_hash(
                "properties-password", method="pbkdf2:sha256"
            ),
            display_name="Other User",
        )
        db.session.add(user_b)
        db.session.flush()
        db.session.add_all(
            [
                UserLocation(
                    user_id=user_a.id,
                    address="My saved rooftop",
                    latitude=28.61,
                    longitude=77.20,
                    system_size=3.0,
                    annual_generation=4800,
                    monthly_savings=1500,
                    created_at=datetime(2026, 1, 15),
                ),
                UserLocation(
                    user_id=user_b.id,
                    address="Someone else's rooftop",
                    latitude=19.07,
                    longitude=72.87,
                    system_size=5.0,
                    annual_generation=8000,
                    monthly_savings=2200,
                    created_at=datetime(2026, 2, 15),
                ),
            ]
        )
        db.session.commit()

    client = app.test_client()
    response = client.post(
        "/login",
        data={"email": "test@example.com", "password": "unused"},
    )
    assert response.status_code == 302

    response = client.get("/properties")

    assert response.status_code == 200
    assert b"My saved rooftop" in response.data
    assert response.data.count(b"My saved rooftop") == 1
    assert b"Someone else's rooftop" not in response.data
    assert b"28.61000, 77.20000" in response.data
    assert b"3.0 kW" in response.data
    assert b"4,800 kWh" in response.data
    assert b"15 Jan 2026" in response.data
    assert b"Download PDF" in response.data
    assert b"Open report" in response.data
    assert b"Select for compare" not in response.data
    assert b'id="compare"' not in response.data
    assert b"Scenario comparison" not in response.data


def test_legacy_properties_routes_redirect_to_merged_page(app):
    client = app.test_client()

    assert client.get("/saved-locations").headers["Location"] == "/properties"
    assert client.get("/reports").headers["Location"] == "/properties"
    assert client.get("/compare").status_code == 302


def test_sidebar_has_accessible_my_properties_dropdown(client):
    response = client.get("/properties")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="properties-nav-toggle"' in body
    assert 'aria-expanded="true"' in body
    assert 'aria-controls="properties-nav-panel"' in body
    assert 'href="/properties"' in body
    assert 'href="/compare"' in body
    assert "Saved Homes" in body
    assert "Compare Scenarios" in body

from datetime import datetime

from app.models import User, UserLocation, db
from werkzeug.security import generate_password_hash


def login(test_client, email, password):
    return test_client.post(
        "/login",
        data={"email": email, "password": password},
    )


def make_location(user_id, address, system_size, annual_generation):
    return UserLocation(
        user_id=user_id,
        address=address,
        latitude=28.61,
        longitude=77.20,
        system_size=system_size,
        annual_generation=annual_generation,
        monthly_savings=1500.0,
        payback_years=5.5,
        system_cost=100000.0,
        subsidy_amount=30000.0,
        roof_area_sqm=42.5,
        created_at=datetime(2026, 1, 15),
    )


def test_compare_page_requires_login(app):
    response = app.test_client().get("/compare")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_compare_rejects_another_users_location_id(app):
    with app.app_context():
        user_a = User(
            email="compare-a@example.com",
            password_hash=generate_password_hash("password-a"),
            display_name="Compare A",
        )
        user_b = User(
            email="compare-b@example.com",
            password_hash=generate_password_hash("password-b"),
            display_name="Compare B",
        )
        db.session.add_all([user_a, user_b])
        db.session.flush()
        own_location = make_location(user_a.id, "User A rooftop", 3.0, 4800.0)
        foreign_location = make_location(user_b.id, "User B rooftop", 8.0, 12000.0)
        db.session.add_all([own_location, foreign_location])
        db.session.commit()
        foreign_id = foreign_location.id
        own_id = own_location.id

    test_client = app.test_client()
    assert login(test_client, "compare-a@example.com", "password-a").status_code == 302
    response = test_client.post(
        "/compare",
        data={"location_ids": [str(own_id), str(foreign_id)]},
    )

    assert response.status_code == 200
    assert b"User B rooftop" not in response.data
    assert b"Save at least 2 locations to compare them" in response.data


def test_compare_displays_selected_location_data(app):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        first = make_location(user.id, "First rooftop", 3.25, 5100.0)
        second = make_location(user.id, "Second rooftop", 5.5, 8400.0)
        db.session.add_all([first, second])
        db.session.commit()
        first_id = first.id
        second_id = second.id

    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302
    response = test_client.post(
        "/compare",
        data={"location_ids": [str(first_id), str(second_id)]},
    )

    assert response.status_code == 200
    assert b"First rooftop" in response.data
    assert b"Second rooftop" in response.data
    assert b"3.25 kW" in response.data
    assert b"5.50 kW" in response.data
    assert b"5100 kWh" in response.data
    assert b"&#8377;1500" in response.data

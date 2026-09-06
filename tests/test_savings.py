from app.models import User, UserLocation, db
from werkzeug.security import generate_password_hash


def login(test_client, email, password):
    return test_client.post(
        "/login",
        data={"email": email, "password": password},
    )


def make_location(user_id, address, monthly_savings, co2_reduction_tons, payback_years):
    return UserLocation(
        user_id=user_id,
        address=address,
        latitude=28.61,
        longitude=77.20,
        monthly_savings=monthly_savings,
        co2_reduction_tons=co2_reduction_tons,
        payback_years=payback_years,
    )


def test_savings_page_requires_login(app):
    response = app.test_client().get("/savings")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_savings_aggregation_is_scoped_to_logged_in_user(app):
    with app.app_context():
        user_a = User(
            email="savings-a@example.com",
            password_hash=generate_password_hash("password-a"),
            display_name="Savings A",
        )
        user_b = User(
            email="savings-b@example.com",
            password_hash=generate_password_hash("password-b"),
            display_name="Savings B",
        )
        db.session.add_all([user_a, user_b])
        db.session.flush()
        db.session.add_all(
            [
                make_location(user_a.id, "A rooftop one", 1500, 2, 5.5),
                make_location(user_a.id, "A rooftop two", 2750, 3, 4.5),
                make_location(user_b.id, "B rooftop", 9000, 20, 2.0),
            ]
        )
        db.session.commit()

    test_client = app.test_client()
    assert login(test_client, "savings-a@example.com", "password-a").status_code == 302
    response = test_client.get("/savings")

    assert response.status_code == 200
    assert b"&#8377;4250" in response.data
    assert b"&#8377;51000" in response.data
    assert b"5.00 tons" in response.data
    assert b"A rooftop two" in response.data
    assert b"B rooftop" not in response.data


def test_savings_empty_state_for_user_without_locations(app):
    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302
    response = test_client.get("/savings")

    assert response.status_code == 200
    assert b"Your savings story starts with an estimate" in response.data
    assert b"Run and save your first rooftop estimate" in response.data
    assert b"Start an estimate" in response.data

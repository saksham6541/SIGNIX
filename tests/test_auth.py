from werkzeug.security import generate_password_hash

from app.models import User, UserLocation, db


def login(test_client, email, password):
    return test_client.post(
        "/login",
        data={"email": email, "password": password},
    )


def test_signup_with_valid_data_succeeds(app):
    test_client = app.test_client()

    response = test_client.post(
        "/signup",
        data={
            "display_name": "New User",
            "email": "new@example.com",
            "password": "correct horse battery staple",
            "confirm_password": "correct horse battery staple",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with app.app_context():
        user = User.query.filter_by(email="new@example.com").one()
        assert user.display_name == "New User"
        assert user.password_hash != "correct horse battery staple"


def test_duplicate_email_signup_fails_cleanly(app):
    test_client = app.test_client()

    response = test_client.post(
        "/signup",
        data={
            "display_name": "Duplicate User",
            "email": "test@example.com",
            "password": "correct horse battery staple",
            "confirm_password": "correct horse battery staple",
        },
    )

    assert response.status_code == 200
    assert b"That email is already registered." in response.data
    with app.app_context():
        assert User.query.filter_by(email="test@example.com").count() == 1


def test_login_with_correct_password_succeeds(app):
    response = login(app.test_client(), "test@example.com", "unused")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_login_with_incorrect_password_fails_cleanly(app):
    response = login(app.test_client(), "test@example.com", "wrong-password")

    assert response.status_code == 200
    assert b"Invalid email or password." in response.data


def test_unauthenticated_user_is_redirected_from_protected_routes(app):
    test_client = app.test_client()

    response = test_client.get("/dashboard")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_logged_in_users_only_see_their_own_saved_locations(app):
    with app.app_context():
        user_a = User(
            email="user-a@example.com",
            password_hash=generate_password_hash("password-a"),
            display_name="User A",
        )
        user_b = User(
            email="user-b@example.com",
            password_hash=generate_password_hash("password-b"),
            display_name="User B",
        )
        db.session.add_all([user_a, user_b])
        db.session.flush()
        db.session.add_all(
            [
                UserLocation(
                    user_id=user_a.id,
                    address="User A rooftop",
                    latitude=28.61,
                    longitude=77.20,
                ),
                UserLocation(
                    user_id=user_b.id,
                    address="User B rooftop",
                    latitude=19.07,
                    longitude=72.87,
                ),
            ]
        )
        db.session.commit()

    user_a_client = app.test_client()
    user_b_client = app.test_client()
    assert login(user_a_client, "user-a@example.com", "password-a").status_code == 302
    assert login(user_b_client, "user-b@example.com", "password-b").status_code == 302

    user_a_locations = user_a_client.get("/api/locations")
    user_b_locations = user_b_client.get("/api/locations")

    assert user_a_locations.status_code == 200
    assert user_b_locations.status_code == 200
    assert [row["address"] for row in user_a_locations.get_json()] == ["User A rooftop"]
    assert [row["address"] for row in user_b_locations.get_json()] == ["User B rooftop"]
    assert "User B rooftop" not in user_a_locations.get_data(as_text=True)
    assert "User A rooftop" not in user_b_locations.get_data(as_text=True)


def test_profile_page_requires_login(app):
    response = app.test_client().get("/profile")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_profile_display_name_and_email_update_succeeds(app):
    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302

    response = test_client.post(
        "/profile",
        data={
            "display_name": "Updated User",
            "email": "updated@example.com",
            "profile_action": "profile",
            "submit": "Save changes",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="updated@example.com").one()
        assert user.display_name == "Updated User"


def test_profile_email_update_fails_if_taken_by_another_user(app):
    with app.app_context():
        db.session.add(
            User(
                email="other@example.com",
                password_hash=generate_password_hash("other-password"),
                display_name="Other User",
            )
        )
        db.session.commit()

    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302
    response = test_client.post(
        "/profile",
        data={
            "display_name": "Test User",
            "email": "other@example.com",
            "profile_action": "profile",
            "submit": "Save changes",
        },
    )

    assert response.status_code == 200
    assert b"That email is already registered." in response.data
    with app.app_context():
        assert User.query.filter_by(email="test@example.com").one().display_name == (
            "Test User"
        )


def test_password_change_fails_with_wrong_current_password(app):
    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302

    response = test_client.post(
        "/profile",
        data={
            "current_password": "wrong-password",
            "new_password": "new-password",
            "confirm_new_password": "new-password",
            "password_action": "password",
            "submit": "Change password",
        },
    )

    assert response.status_code == 200
    assert b"Current password is incorrect." in response.data


def test_password_change_succeeds_and_new_password_works_on_next_login(app):
    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302

    response = test_client.post(
        "/profile",
        data={
            "current_password": "unused",
            "new_password": "new-password",
            "confirm_new_password": "new-password",
            "password_action": "password",
            "submit": "Change password",
        },
    )
    assert response.status_code == 302

    test_client.post("/logout")
    assert login(test_client, "test@example.com", "new-password").status_code == 302

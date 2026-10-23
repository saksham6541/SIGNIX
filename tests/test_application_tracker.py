from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.models import ApplicationTrackerEvent, User, UserLocation, db


def _add_location(app, *, owner_email="test@example.com", property_type="residential",
                  inverter_type="on-grid", subsidy_amount=0):
    with app.app_context():
        owner = User.query.filter_by(email=owner_email).one()
        location = UserLocation(
            user_id=owner.id,
            address=f"{property_type.title()} tracker rooftop",
            latitude=28.61,
            longitude=77.20,
            system_size=3.0,
            annual_generation=4500.0,
            system_cost=180000,
            subsidy_amount=subsidy_amount,
            net_investment=180000 - subsidy_amount,
            monthly_savings=4500,
            extras={
                "property_type": property_type,
                "inverter_type": inverter_type,
                "cashflow_25yr": [-100000] + [100000] * 24,
                "generation_25yr": [4500] * 25,
                "environmental_equivalents": {
                    "trees_planted": 10,
                    "km_not_driven": 1000,
                    "fuel_liters_saved": 100,
                },
            },
        )
        db.session.add(location)
        db.session.commit()
        return location.id


@pytest.mark.parametrize(
    ("property_type", "inverter_type", "subsidy_amount", "is_eligible"),
    [
        ("residential", "on-grid", 0, True),
        ("commercial", "on-grid", 1000, False),
        ("remote", "on-grid", 1000, False),
        ("residential", "off-grid", 0, False),
        ("residential", "off-grid", 1000, True),
    ],
)
def test_report_only_shows_scheme_guidance_for_eligible_residential_estimates(
    app, client, property_type, inverter_type, subsidy_amount, is_eligible
):
    location_id = _add_location(
        app,
        property_type=property_type,
        inverter_type=inverter_type,
        subsidy_amount=subsidy_amount,
    )

    response = client.get(f"/report/{location_id}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert ('class="rpt-next-card psg-report-card"' in body) is is_eligible
    assert ('id="psg-tracker-status"' in body) is is_eligible


def test_estimate_guide_is_residential_only_and_auto_waits_for_eligibility(client):
    residential = client.get("/survey/home").get_data(as_text=True)
    commercial = client.get("/survey/business").get_data(as_text=True)

    assert 'id="psg-estimate-guide"' in residential
    assert "inverter.value === 'on-grid' || inverter.value === 'hybrid' || subsidyApplies" in residential
    assert 'id="psg-estimate-guide"' not in commercial


def test_report_has_one_summary_pdf_and_secondary_downloads_in_next_steps(app, client):
    location_id = _add_location(app)

    response = client.get(f"/report/{location_id}")
    body = response.get_data(as_text=True)
    next_steps_position = body.index('id="rpt-next-steps"')

    assert response.status_code == 200
    assert body.count("mode=summary") == 1
    assert body.index("Download Technical PDF") > next_steps_position
    assert body.index("Download installer package") > next_steps_position
    assert body.index("Clone") > next_steps_position


def test_tracker_update_requires_login_and_saves_for_estimate_owner(app, client):
    location_id = _add_location(app)
    payload = {
        "status": "applied",
        "changed_on": "2026-10-23",
        "application_reference": "ARN-123",
        "note": "Submitted through the official portal.",
    }

    anonymous = app.test_client().post(
        f"/report/{location_id}/tracker", data=payload
    )
    assert anonymous.status_code == 302
    assert "/login" in anonymous.headers["Location"]

    response = client.post(f"/report/{location_id}/tracker", data=payload)
    assert response.status_code == 302
    with app.app_context():
        event = ApplicationTrackerEvent.query.filter_by(
            location_id=location_id
        ).one()
        assert event.status == "applied"
        assert event.changed_on == date(2026, 10, 23)
        assert event.application_reference == "ARN-123"
        assert event.note == "Submitted through the official portal."


def test_tracker_update_rejects_another_users_estimate(app):
    with app.app_context():
        other_user = User(
            email="tracker-other@example.com",
            password_hash=generate_password_hash("tracker-password"),
            display_name="Other User",
        )
        db.session.add(other_user)
        db.session.commit()

    location_id = _add_location(app)
    other_client = app.test_client()
    response = other_client.post(
        "/login",
        data={"email": "tracker-other@example.com", "password": "tracker-password"},
    )
    assert response.status_code == 302

    response = other_client.post(
        f"/report/{location_id}/tracker",
        data={"status": "applied", "changed_on": "2026-10-23"},
    )
    assert response.status_code == 404


def test_tracker_post_is_csrf_protected(app, client):
    location_id = _add_location(app)
    app.config["WTF_CSRF_ENABLED"] = True

    response = client.post(
        f"/report/{location_id}/tracker",
        data={"status": "applied", "changed_on": "2026-10-23"},
    )

    app.config["WTF_CSRF_ENABLED"] = False
    assert response.status_code == 400

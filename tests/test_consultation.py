import re
import uuid
from html import unescape
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.exc import SQLAlchemyError

from app.config import Config
from app.models import ConsultationRequest, User, db


def _valid_consultation(**overrides):
    values = {
        "submission_token": str(uuid.uuid4()),
        "segment": "commercial",
        "full_name": "Asha Rao",
        "phone": "9876543210",
        "email": "asha@example.com",
        "business_name": "Rao Textiles",
        "city": "Pune",
        "state": "Maharashtra",
        "pin_code": "411001",
        "monthly_bill": "25000-100000",
        "business_type": "Textile unit",
        "operating_profile": "daytime",
        "requirements": "Review roof area and commercial tariff assumptions.",
        "consent": "yes",
    }
    values.update(overrides)
    return values


def test_consultation_page_is_public_and_renders_form_and_nav(app):
    response = app.test_client().get("/consultation")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="consultation-request-form"' in body
    assert 'value="industrial"' in body
    assert 'class="public-nav-consultation" href="/consultation"' in body
    assert 'aria-current="page"' in body


def test_consultation_page_uses_hindi_catalog(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["language"] = "hi"

    response = client.get("/consultation")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<html lang="hi">' in body
    assert "अपनी छत पर सोलर की ज़रूरतों पर बात करें।" in body
    assert "मुफ़्त परामर्श बुक करें" in body


def test_commercial_landing_renders_capabilities_and_real_ctas(app):
    response = app.test_client().get("/business")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Plan solar around your business roof and energy use." in body
    assert 'href="/business/estimate"' in body
    assert 'href="/business"' in body
    assert body.count('href="/consultation?segment=commercial"') >= 2
    signup_link = next(
        unescape(link)
        for link in re.findall(r'href="([^"]+)"', body)
        if "/signup?" in link
    )
    signup_query = parse_qs(urlsplit(signup_link).query)
    assert urlsplit(signup_link).path == "/signup"
    assert signup_query["next"] == ["/business/estimate"]
    assert signup_query["track"] == ["business"]
    assert 'href="/consultation?segment=commercial"' in body
    assert "Commercial assessment" in body or "commercial planning default" in body


def test_commercial_landing_uses_tariff_service_reference_value(app, monkeypatch):
    monkeypatch.setattr(Config, "DEFAULT_COMMERCIAL_TARIFF", 12.34)

    response = app.test_client().get("/commercial")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "₹12.34 / kWh" in body
    assert "Not an official published tariff." in body
    assert "Not provided for this configurable default." in body


def test_assessment_cta_redirect_preserves_existing_business_route(app):
    response = app.test_client().get("/business/estimate")

    assert response.status_code == 302
    login_query = parse_qs(urlsplit(response.headers["Location"]).query)
    assert login_query["next"] == ["/business/estimate"]


def test_commercial_signup_cta_preserves_business_assessment_destination(app):
    body = app.test_client().get("/business").get_data(as_text=True)
    signup_link = next(
        unescape(link)
        for link in re.findall(r'href="([^"]+)"', body)
        if "/signup?" in link
    )
    signup_query = parse_qs(urlsplit(signup_link).query)

    assert signup_query["next"] == ["/business/estimate"]
    assert signup_query["track"] == ["business"]


def test_signup_authentication_returns_to_commercial_assessment(app):
    client = app.test_client()
    response = client.post(
        "/signup?next=/business/estimate&track=business",
        data={
            "planning_track": "business",
            "display_name": "Commercial User",
            "email": "commercial@example.com",
            "password": "secure password 123",
            "confirm_password": "secure password 123",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/business/estimate"
    with client.session_transaction() as session:
        assert session["planning_track"] == "business"


def test_login_authentication_returns_to_commercial_assessment(app):
    client = app.test_client()
    response = client.post(
        "/login?next=/business/estimate",
        data={
            "planning_track": "business",
            "email": "test@example.com",
            "password": "unused",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/business/estimate"
    with client.session_transaction() as session:
        assert session["planning_track"] == "business"


def test_consultation_validation_rejects_bad_fields_without_persisting(app):
    response = app.test_client().post(
        "/consultation",
        data=_valid_consultation(phone="123", pin_code="not-a-pin", requirements="short"),
    )

    assert response.status_code == 400
    assert b"Enter a valid 10-digit Indian mobile number." in response.data
    assert b"Enter a valid 6-digit PIN code." in response.data
    assert b"Describe what you would like to discuss" in response.data
    with app.app_context():
        assert ConsultationRequest.query.count() == 0


def test_consultation_request_is_persisted_after_commit(app):
    client = app.test_client()
    response = client.post(
        "/consultation",
        data=_valid_consultation(),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Your consultation request has been received." in response.data
    assert b"No appointment has been booked." in response.data
    assert b"Rao Textiles" not in response.data
    with app.app_context():
        saved = ConsultationRequest.query.one()
        assert saved.business_name == "Rao Textiles"
        assert saved.city == "Pune"
        assert saved.requirements.startswith("Review roof area")
        assert saved.consent is True
        assert saved.status == "pending"


def test_duplicate_consultation_submission_is_idempotent(app):
    client = app.test_client()
    form = _valid_consultation()

    first = client.post("/consultation", data=form)
    duplicate = client.post("/consultation", data=form, follow_redirects=True)

    assert first.status_code == 302
    assert duplicate.status_code == 200
    assert b"no duplicate was created" in duplicate.data
    with app.app_context():
        assert ConsultationRequest.query.count() == 1


def test_failed_consultation_database_write_is_reported_without_success(app, monkeypatch):
    def fail_commit():
        raise SQLAlchemyError("simulated write failure")

    monkeypatch.setattr(db.session, "commit", fail_commit)
    response = app.test_client().post(
        "/consultation",
        data=_valid_consultation(),
    )

    assert response.status_code == 503
    assert b"We could not save your request" in response.data
    assert b"Your consultation request has been received." not in response.data
    with app.app_context():
        assert ConsultationRequest.query.count() == 0


def test_consultation_post_requires_csrf_token(app):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    page = client.get("/consultation")
    submission_token = re.search(
        rb'name="submission_token" value="([^"]+)"', page.data
    ).group(1).decode()

    response = client.post(
        "/consultation",
        data=_valid_consultation(submission_token=submission_token),
    )

    assert response.status_code == 400
    with app.app_context():
        assert ConsultationRequest.query.count() == 0


def test_valid_csrf_token_allows_consultation_submission(app):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    page = client.get("/consultation")
    csrf_token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    submission_token = re.search(
        rb'name="submission_token" value="([^"]+)"', page.data
    ).group(1).decode()

    response = client.post(
        "/consultation",
        data=_valid_consultation(
            csrf_token=csrf_token, submission_token=submission_token
        ),
    )

    assert response.status_code == 302
    with app.app_context():
        assert ConsultationRequest.query.count() == 1


def test_consultation_management_is_admin_only(app):
    app.test_client().post("/consultation", data=_valid_consultation())
    with app.app_context():
        request_id = ConsultationRequest.query.one().id
    guest_response = app.test_client().get("/admin/consultations")
    user_client = app.test_client()
    user_client.post("/login", data={"email": "test@example.com", "password": "unused"})

    user_response = user_client.get("/admin/consultations")
    update_response = user_client.post(
        f"/admin/consultations/{request_id}/update",
        data={"status": "scheduled", "admin_notes": "Must remain private."},
    )

    assert guest_response.status_code == 302
    assert user_response.status_code == 403
    assert update_response.status_code == 403


def test_admin_can_review_and_update_consultation_request(app):
    app.test_client().post("/consultation", data=_valid_consultation())
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        user.role = "admin"
        db.session.commit()

    admin = app.test_client()
    admin.post("/login", data={"email": "test@example.com", "password": "unused"})
    review = admin.get("/admin/consultations")
    with app.app_context():
        request_id = ConsultationRequest.query.one().id
    update = admin.post(
        f"/admin/consultations/{request_id}/update",
        data={"status": "contacted", "admin_notes": "Called; awaiting site details."},
        follow_redirects=True,
    )

    assert review.status_code == 200
    assert b"Rao Textiles" in review.data
    assert b"Called; awaiting site details." in update.data
    with app.app_context():
        saved = db.session.get(ConsultationRequest, request_id)
        assert saved.status == "contacted"
        assert saved.admin_notes == "Called; awaiting site details."


def test_commercial_landing_and_consultation_are_localized_in_hindi(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["language"] = "hi"

    business = client.get("/business")
    consultation = client.get("/consultation?segment=commercial")

    assert '<html lang="hi">' in business.get_data(as_text=True)
    assert "अपने व्यवसाय की छत और ऊर्जा उपयोग के अनुसार सौर योजना बनाएँ।" in business.get_data(as_text=True)
    assert '<html lang="hi">' in consultation.get_data(as_text=True)
    assert "आपका अनुरोध अधिकृत कर्मचारियों की समीक्षा के लिए सहेजा जाता है।" in consultation.get_data(as_text=True)
    assert "इससे इंस्टॉलर के साथ जानकारी साझा करने की अनुमति नहीं मिलती।" in consultation.get_data(as_text=True)

"""Installer discovery uses temporary databases and never calls a provider."""
from datetime import datetime, timedelta, timezone
import re
import sqlite3

import pytest
from flask import template_rendered
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.datastructures import MultiDict

from app.models import Project, User, UserLocation, VendorLeadUnlock, VendorSavedLead, db
from app.services.vendor_lead_service import find_leads, parse_filters


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


@pytest.fixture
def leads(app, client):
    with app.app_context():
        owner = User.query.filter_by(email="test@example.com").one()
        vendors = [User(email=f"installer{i}@example.test", display_name=f"Installer {i}", password_hash="unused", role="vendor",
                        vendor_status="approved", vendor_profile={}, vendor_credits_balance=10) for i in range(2)]
        db.session.add_all(vendors)
        ids = []
        for city, pin, size, bill, category, model, days in (
            ("Delhi", "110001", 10, 12000, "Shop", "buy", 2),
            ("Delhi", "110002", 30, 50000, "Office", "loan", 12),
            ("Mumbai", "400001", 60, 90000, "Hotel", "ppa", 45),
            (None, None, 0, None, None, None, 3),
        ):
            extras = {"planning_track": "business", "city": city, "pincode": pin,
                      "locality": "Central" if city else None, "business_type": category,
                      "preferred_model": model, "monthly_bill_rs": bill}
            location = UserLocation(user_id=owner.id, address="PRIVATE EXACT ADDRESS 81",
                latitude=28, longitude=77, system_size=size, roof_area_sqm=222, extras=extras)
            db.session.add(location); db.session.flush()
            project = Project(user_id=owner.id, location_id=location.id, owner="PRIVATE OWNER",
                contact_name="PRIVATE CUSTOMER", phone="9876543210", email="secret@example.test",
                status="open_to_vendors", consent_given=True,
                created_at=datetime.now(timezone.utc) - timedelta(days=days))
            db.session.add(project); db.session.flush(); ids.append(project.id)
        db.session.commit()
        vendor_ids = [v.id for v in vendors]
    login(client, vendor_ids[0])
    return ids, vendor_ids


def result_ids(app, vendor_id, **params):
    with app.app_context():
        filters, errors = parse_filters(MultiDict(params))
        assert not errors
        items, total, page, pages = find_leads(db.session.get(User, vendor_id), filters)
        return [item["id"] for item in items], total


@pytest.mark.parametrize("params,expected", [
    ({"city": "delhi"}, [0, 1]), ({"pincode": "110002"}, [1]),
    ({"locality": "central"}, [0, 1, 2]),
    ({"capacity_min": "20"}, [1, 2]), ({"capacity_max": "20"}, [0]),
    ({"capacity_min": "10", "capacity_max": "30"}, [0, 1]),
    ({"business_type": "Office"}, [1]), ({"preferred_model": "ppa"}, [2]),
    ({"bill_min": "20000", "bill_max": "60000"}, [1]),
    ({"posted": "7"}, [0, 3]), ({"posted": "30"}, [0, 1, 3]),
    ({"city": "Delhi", "pincode": "110001", "capacity_min": "5", "capacity_max": "20",
      "business_type": "Shop", "preferred_model": "buy", "bill_min": "10000", "posted": "7"}, [0]),
    ({"city": "Nowhere"}, []), ({"city": "%"}, []),
])
def test_filters_are_combined_sql_queries(app, client, leads, params, expected):
    ids, vendors = leads
    actual, count = result_ids(app, vendors[0], **params)
    assert set(actual) == {ids[index] for index in expected}
    assert count == len(expected)
    response = client.get("/vendor/board", query_string=params)
    assert response.status_code == 200
    assert f"Results: {len(expected)}" in response.text


@pytest.mark.parametrize("sort,expected", [
    ("newest", [0, 3, 1, 2]), ("oldest", [2, 1, 3, 0]),
    ("capacity_asc", [0, 1, 2, 3]), ("capacity_desc", [2, 1, 0, 3]),
    ("bill_asc", [0, 1, 2, 3]), ("bill_desc", [2, 1, 0, 3]),
])
def test_sorting_unknowns_last(app, leads, sort, expected):
    ids, vendors = leads
    assert result_ids(app, vendors[0], sort=sort)[0] == [ids[i] for i in expected]


@pytest.mark.parametrize("params", [
    {"capacity_min": "nan"}, {"bill_max": "inf"}, {"bill_min": "-1"},
    {"capacity_min": "50", "capacity_max": "10"}, {"bill_min": "x"},
    {"pincode": "123"}, {"posted": "365"}, {"sort": "DROP TABLE"},
    {"business_type": "Imaginary"}, {"preferred_model": "invalid"},
    {"page": "-1"}, {"page": "²"}, {"city": "x" * 101},
])
def test_invalid_filters_return_actionable_400(client, leads, params):
    response = client.get("/vendor/board", query_string=params)
    assert response.status_code == 400
    assert "Check the highlighted filters" in response.text


def test_privacy_and_honest_missing_values(app, client, leads):
    ids, _ = leads
    contexts = []
    def capture(sender, template, context, **kwargs):
        if template.name == "vendor_board.html": contexts.append(context)
    template_rendered.connect(capture, app)
    try:
        response = client.get("/vendor/board")
    finally:
        template_rendered.disconnect(capture, app)
    for secret in ("PRIVATE EXACT", "PRIVATE OWNER", "PRIVATE CUSTOMER", "9876543210", "secret@example.test"):
        assert secret not in response.text
        assert secret not in repr(contexts[0]["leads"])
    assert "As soon as possible" not in response.text
    assert "0.0 kW" not in response.text.replace("10.0 kW", "").replace("30.0 kW", "").replace("60.0 kW", "")
    assert all(not isinstance(value, (Project, UserLocation)) for item in contexts[0]["leads"] for value in item.values())
    detail = client.get(f"/vendor/leads/{ids[0]}")
    assert detail.status_code == 200 and "PRIVATE" not in detail.text


def test_actual_bill_not_derived_units_and_no_invented_capacity(app, leads):
    ids, vendors = leads
    with app.app_context():
        loc = db.session.get(Project, ids[0]).location
        loc.extras = {**loc.extras, "source": "business_estimate_page", "monthly_bill_rs": None, "monthly_bill": 25000}
        db.session.commit()
    assert ids[0] not in result_ids(app, vendors[0], bill_min="0")[0]
    assert ids[3] not in result_ids(app, vendors[0], capacity_min="0")[0]


@pytest.mark.parametrize("bad", ["junk", "50000", True, -1, float("inf")])
def test_invalid_stored_bill_is_not_zero_or_numeric(app, leads, bad):
    ids, vendors = leads
    with app.app_context():
        loc = db.session.get(Project, ids[0]).location
        loc.extras = {**loc.extras, "monthly_bill_rs": bad}
        db.session.commit()
    assert ids[0] not in result_ids(app, vendors[0], bill_min="0")[0]


def save(client, project_id, state=True):
    return client.post(f"/vendor/leads/{project_id}/saved", data={"saved": str(state).lower()}, headers={"Accept": "application/json"})


def test_bookmarks_persist_idempotently_and_are_vendor_private(app, client, leads):
    ids, vendors = leads
    assert "No saved leads yet" in client.get("/vendor/saved-leads").text
    for _ in range(2):
        assert save(client, ids[0]).get_json()["saved"] is True
    with app.app_context():
        assert VendorSavedLead.query.count() == 1
        assert db.session.get(User, vendors[0]).vendor_credits_balance == 10
        assert VendorLeadUnlock.query.count() == 0
        with pytest.raises(IntegrityError):
            with db.session.begin_nested():
                db.session.add(VendorSavedLead(vendor_user_id=vendors[0], project_id=ids[0])); db.session.flush()
    client.get("/logout")
    login(client, vendors[0])
    assert 'data-lead-id="%s"' % ids[0] in client.get("/vendor/saved-leads").text
    login(client, vendors[1])
    assert "No saved leads yet" in client.get("/vendor/saved-leads").text
    assert save(client, ids[0], False).status_code == 200
    with app.app_context(): assert VendorSavedLead.query.count() == 1
    login(client, vendors[0])
    for _ in range(2): assert save(client, ids[0], False).get_json()["saved"] is False
    with app.app_context(): assert VendorSavedLead.query.count() == 0


def test_saved_ineligible_lead_is_masked_but_removable(app, client, leads):
    ids, _ = leads
    assert save(client, ids[0]).status_code == 200
    with app.app_context():
        db.session.get(Project, ids[0]).status = "closed"; db.session.commit()
    html = client.get("/vendor/saved-leads").text
    assert "This lead is no longer available" in html
    assert "Delhi" not in html and "12,000" not in html
    assert client.get(f"/vendor/leads/{ids[0]}").status_code == 404
    assert save(client, ids[0]).status_code == 404
    assert save(client, ids[0], False).status_code == 200


def test_vendor_access_and_real_csrf(app, client, leads):
    ids, vendors = leads
    app.config["WTF_CSRF_ENABLED"] = True
    html = client.get("/vendor/board").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    assert save(client, ids[0]).status_code == 400
    assert client.post(f"/vendor/leads/{ids[0]}/saved", data={"saved": "true", "csrf_token": token}).status_code == 302
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        vendor = db.session.get(User, vendors[0]); vendor.vendor_status = "suspended"; db.session.commit()
    assert "Pending approval" in client.get("/vendor/saved-leads").text
    assert save(client, ids[0]).status_code == 403
    login(client, 1)
    for path in ("/vendor/board", "/vendor/saved-leads", f"/vendor/leads/{ids[0]}"):
        assert client.get(path).status_code == 403
    assert save(client, ids[0]).status_code == 403
    anonymous = app.test_client()
    assert anonymous.get("/vendor/saved-leads").status_code == 302
    assert save(anonymous, ids[0]).status_code == 401


def test_eligibility_consent_track_service_area_and_unlock_cap(app, client, leads):
    ids, vendors = leads
    with app.app_context():
        db.session.get(Project, ids[0]).consent_given = False
        loc = db.session.get(Project, ids[1]).location
        loc.extras = {**loc.extras, "planning_track": "home"}
        db.session.get(User, vendors[0]).vendor_profile = {"service_area": "Mumbai"}
        db.session.commit()
    assert result_ids(app, vendors[0])[0] == [ids[2]]
    assert save(client, ids[0]).status_code == 404
    app.config["VENDOR_MAX_LEADS_PER_PROJECT"] = 1
    with app.app_context():
        db.session.add(VendorLeadUnlock(project_id=ids[2], vendor_user_id=vendors[1], credits_charged=1)); db.session.commit()
    assert result_ids(app, vendors[0])[0] == []
    assert save(client, ids[2]).status_code == 404
    assert ids[2] in result_ids(app, vendors[1])[0]


def test_pagination_and_clear_controls(app, client, leads, monkeypatch):
    from app.services import vendor_lead_service
    monkeypatch.setattr(vendor_lead_service, "PAGE_SIZE", 2)
    response = client.get("/vendor/board?sort=oldest&page=999")
    assert response.status_code == 200
    assert "Results: 4" in response.text and "Page 2 of 2" in response.text
    assert "sort=oldest" in response.text and "Clear Filters" in response.text
    assert response.text.count('data-lead-id=') == 2
    assert "No matching leads" in client.get("/vendor/board?city=Nowhere").text


def test_save_database_failure_is_safe_and_rolled_back(app, client, leads, monkeypatch):
    ids, _ = leads
    with app.app_context():
        def fail(): raise OperationalError("SECRET SQL", {}, Exception("secret"))
        monkeypatch.setattr(db.session, "commit", fail)
        response = save(client, ids[0])
        assert response.status_code == 503
        assert "secret" not in response.text.lower()
        assert VendorSavedLead.query.count() == 0


def test_saved_lead_migration_preserves_records_and_is_idempotent(tmp_path):
    from scripts.migrate_vendor_saved_leads import apply_migration, inspect_database
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, password_hash TEXT)")
        connection.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY, note TEXT)")
        connection.execute("INSERT INTO users VALUES(1, 'preserved hash')")
        connection.execute("INSERT INTO projects VALUES(2, 'preserved project')")
        assert inspect_database(connection)["table_present"] is False
    report = apply_migration(path, tmp_path / "backups")
    with sqlite3.connect(report["backup"]) as backup:
        assert backup.execute("SELECT password_hash FROM users").fetchone() == ("preserved hash",)
        assert not backup.execute("SELECT 1 FROM sqlite_master WHERE name='vendor_saved_leads'").fetchone()
    assert apply_migration(path, tmp_path / "backups")["status"] == "already_applied"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("SELECT note FROM projects").fetchone() == ("preserved project",)
        connection.execute("INSERT INTO vendor_saved_leads VALUES(1,1,2,'2026-10-30')")
        with pytest.raises(sqlite3.IntegrityError): connection.execute("INSERT INTO vendor_saved_leads VALUES(2,1,2,'2026-10-30')")
        with pytest.raises(sqlite3.IntegrityError): connection.execute("INSERT INTO vendor_saved_leads VALUES(3,99,2,'2026-10-30')")


def test_saved_lead_migration_rolls_back_on_failure(tmp_path, monkeypatch):
    from scripts import migrate_vendor_saved_leads as migration
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); CREATE TABLE projects(id INTEGER PRIMARY KEY);")
    monkeypatch.setattr(migration, "INDEXES", {"broken index!": "project_id"})
    with pytest.raises(RuntimeError, match="rolled back"):
        migration.apply_migration(path, tmp_path / "backups")
    with sqlite3.connect(path) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='vendor_saved_leads'").fetchone()
    assert len(list((tmp_path / "backups").glob("*.bak"))) == 1


def test_hindi_lead_controls_and_bookmark_feedback(client, leads):
    ids, _ = leads
    with client.session_transaction() as session:
        session["language"] = "hi"
    html = client.get("/vendor/board").text
    assert "लीड बोर्ड" in html and "न्यूनतम क्षमता" in html and "मासिक बिल" in html
    assert "लीड सहेजी गई" in save(client, ids[0]).get_json()["message"]
    assert "सहेजी गई लीड" in client.get("/vendor/saved-leads").text


def test_duplicate_filters_rejected_and_zero_bill_preserved(app, client, leads):
    ids, vendors = leads
    assert client.get("/vendor/board?city=Delhi&city=Mumbai").status_code == 400
    with app.app_context():
        location = db.session.get(Project, ids[0]).location
        location.extras = {**location.extras, "monthly_bill_rs": 0}
        db.session.commit()
    assert result_ids(app, vendors[0], bill_max="0")[0] == [ids[0]]


def test_existing_unlock_contacts_stay_off_lists(app, client, leads):
    ids, vendors = leads
    with app.app_context():
        db.session.add(VendorLeadUnlock(vendor_user_id=vendors[0], project_id=ids[0], credits_charged=1))
        db.session.commit()
    assert save(client, ids[0]).status_code == 200
    assert "PRIVATE CUSTOMER" not in client.get("/vendor/board").text
    assert "PRIVATE CUSTOMER" not in client.get("/vendor/saved-leads").text
    assert "PRIVATE CUSTOMER" in client.get(f"/vendor/leads/{ids[0]}").text
    login(client, vendors[1])
    assert "PRIVATE CUSTOMER" not in client.get(f"/vendor/leads/{ids[0]}").text


def test_migration_accepts_fresh_model_schema_and_rejects_drift(app, tmp_path):
    from scripts.migrate_vendor_saved_leads import apply_migration
    path = tmp_path / "fresh.db"
    with app.app_context():
        with sqlite3.connect(db.engine.url.database) as source, sqlite3.connect(path) as target:
            source.backup(target)
    assert apply_migration(path, tmp_path / "backups")["status"] == "applied"
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX ix_vendor_saved_leads_project_id")
    with pytest.raises(RuntimeError, match="drift"):
        apply_migration(path, tmp_path / "backups")


def test_concurrent_saves_are_unique_and_credit_neutral(app, leads):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    ids, vendors = leads
    clients = [app.test_client(), app.test_client()]
    for client in clients:
        login(client, vendors[0])
    barrier = Barrier(2)
    def submit(client):
        barrier.wait(timeout=10)
        return save(client, ids[0]).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, clients))
    # SQLite may reject a competing writer with a retryable busy response.
    assert all(status in {200, 503} for status in statuses)
    assert save(clients[0], ids[0]).status_code == 200
    with app.app_context():
        assert VendorSavedLead.query.count() == 1
        assert VendorLeadUnlock.query.count() == 0
        assert db.session.get(User, vendors[0]).vendor_credits_balance == 10


def test_malformed_public_text_is_not_serialized_into_cards(app, client, leads):
    ids, _ = leads
    with app.app_context():
        location = db.session.get(Project, ids[0]).location
        location.extras = {**location.extras, "city": {"private": "PRIVATE"},
                           "locality": ["PRIVATE"], "installation_timeline": True}
        db.session.commit()
    html = client.get("/vendor/board").text
    assert "PRIVATE" not in html

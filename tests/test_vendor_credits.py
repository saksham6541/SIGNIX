"""Part 2 tests: isolated fixture databases, no providers or browser."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import re

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.models import (
    PROJECT_ARCHIVE_NOTE,
    AdminActionLog,
    Project,
    ProjectStatusLog,
    User,
    VendorCreditLedger,
    VendorLeadUnlock,
    VendorRefundRequest,
    db,
)
from app.services import vendor_credit_service as service
from tests.test_vendor_leads import leads, login


def post(client, project_id):
    return client.post(f"/vendor/board/{project_id}/unlock", headers={"Accept": "application/json"})


def test_unlock_is_committed_idempotent_and_private(app, client, leads):
    ids, vendors = leads
    with app.app_context():
        project = db.session.get(Project, ids[0])
        project.location.extras = {
            **project.location.extras,
            "planning_track": "home",
        }
        db.session.commit()
    assert "PRIVATE CUSTOMER" not in client.get(f"/vendor/leads/{ids[0]}").text
    first = post(client, ids[0])
    assert first.status_code == 200 and first.json["result"]["cost"] == 1
    assert post(client, ids[0]).json["result"]["already_unlocked"] is True
    assert "PRIVATE CUSTOMER" in client.get(f"/vendor/leads/{ids[0]}").text
    assert "PRIVATE CUSTOMER" in client.get("/vendor/unlocked-leads").text
    assert "PRIVATE CUSTOMER" not in client.get("/vendor/board").text
    credits_page = client.get("/vendor/credits")
    assert credits_page.status_code == 200
    assert 'class="credit-overview-grid"' in credits_page.text
    assert "Current balance" in credits_page.text
    assert "Historical opening balance" in credits_page.text
    assert "Configured unlock costs" in credits_page.text
    assert "Credit transactions" in credits_page.text
    assert "Request credits" in credits_page.text
    with app.app_context():
        vendor = db.session.get(User, vendors[0])
        assert vendor.vendor_credits_balance == 9
        assert VendorLeadUnlock.query.count() == VendorCreditLedger.query.count() == 1
        row = VendorCreditLedger.query.one()
        assert (row.delta, row.balance_after, row.reason) == (-1, 9, "lead_unlock")
        assert service.ledger_summary(vendor) == {"opening": 10, "balance": 9, "reconciled": True}
    login(client, vendors[1])
    assert "PRIVATE CUSTOMER" not in client.get(f"/vendor/leads/{ids[0]}").text
    assert "PRIVATE CUSTOMER" not in client.get("/vendor/unlocked-leads").text
    assert "No credit transactions yet" in client.get("/vendor/credits").text


@pytest.mark.parametrize("failure", ["balance", "closed", "consent", "track", "area", "unknown_cost", "bad_cost", "suspended"])
def test_failed_unlock_does_not_charge(app, client, leads, failure):
    ids, vendors = leads
    with app.app_context():
        vendor = db.session.get(User, vendors[0]); project = db.session.get(Project, ids[0])
        if failure == "balance": vendor.vendor_credits_balance = 0
        if failure == "closed": project.status = "closed"
        if failure == "consent": project.consent_given = False
        if failure == "track": project.location.extras = {
            **project.location.extras,
            "planning_track": "unsupported",
            "property_type": "unknown",
        }
        if failure == "area": vendor.vendor_profile = {"service_area": "Mumbai"}
        if failure == "unknown_cost": project.location.system_size = 0
        if failure == "suspended": vendor.vendor_status = "suspended"
        db.session.commit()
        before = vendor.vendor_credits_balance
    if failure == "bad_cost": app.config["VENDOR_LEAD_UNLOCK_COSTS"] = {"small": float("nan")}
    response = post(client, ids[0])
    assert response.status_code in {400, 403, 404, 409, 503}
    with app.app_context():
        assert db.session.get(User, vendors[0]).vendor_credits_balance == before
        assert VendorLeadUnlock.query.count() == VendorCreditLedger.query.count() == 0


@pytest.mark.parametrize("point", ["flush", "commit"])
def test_failure_rolls_back_balance_unlock_and_ledger(app, client, leads, monkeypatch, point):
    ids, vendors = leads
    with app.app_context():
        if point == "flush":
            original = db.session.flush
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                if db.session.query(VendorCreditLedger).count():
                    raise OperationalError("secret sql", {}, Exception("secret"))
        else:
            def fail(*args, **kwargs):
                db.session.flush()
                raise OperationalError("secret sql", {}, Exception("secret"))
        monkeypatch.setattr(db.session, point, fail)
        response = post(client, ids[0])
        assert response.status_code == 503 and "secret" not in response.text
        monkeypatch.undo()
        assert db.session.get(User, vendors[0]).vendor_credits_balance == 10
        assert VendorLeadUnlock.query.count() == VendorCreditLedger.query.count() == 0


@pytest.mark.parametrize("scenario", ["same_lead", "same_balance", "last_slot"])
def test_concurrent_unlocks(app, leads, scenario):
    ids, vendors = leads
    with app.app_context():
        if scenario == "same_balance":
            db.session.get(User, vendors[0]).vendor_credits_balance = 2
        db.session.commit()
    if scenario == "last_slot": app.config["VENDOR_MAX_LEADS_PER_PROJECT"] = 1
    clients = [app.test_client(), app.test_client()]
    login(clients[0], vendors[0]); login(clients[1], vendors[1] if scenario == "last_slot" else vendors[0])
    barrier = Barrier(2)
    def submit(index):
        barrier.wait(timeout=10)
        return post(clients[index], ids[1] if scenario == "same_balance" and index else ids[0]).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(submit, range(2)))
    assert 200 in codes and all(code in {200, 404, 409, 503} for code in codes)
    with app.app_context():
        assert VendorLeadUnlock.query.count() == VendorCreditLedger.query.count() == 1
        for vendor_id in vendors:
            vendor = db.session.get(User, vendor_id)
            assert vendor.vendor_credits_balance >= 0
            assert service.ledger_summary(vendor)["reconciled"]


def test_outcome_history_owner_and_global_status(app, client, leads):
    ids, vendors = leads
    assert post(client, ids[0]).status_code == 200
    with app.app_context():
        db.session.get(Project, ids[0]).status = "closed"; db.session.commit()
    for outcome in ("interested", "follow_up", "won", "lost", "lost"):
        response = client.post(f"/vendor/board/{ids[0]}/status", data={"outcome": outcome}, headers={"Accept": "application/json"})
        assert response.status_code == 200
    assert "PRIVATE CUSTOMER" in client.get(f"/vendor/leads/{ids[0]}").text
    assert post(client, ids[0]).json["result"]["already_unlocked"]
    login(client, vendors[1])
    assert client.post(f"/vendor/board/{ids[0]}/status", data={"outcome": "won", "vendor_id": vendors[0]}, headers={"Accept": "application/json"}).status_code == 403
    with app.app_context():
        assert db.session.get(Project, ids[0]).status == "closed"
        events = AdminActionLog.query.filter_by(action="vendor_lead_outcome").order_by(AdminActionLog.id).all()
        assert [e.detail for e in events] == ["interested", "follow_up", "won", "lost"]
        assert all(e.created_at and e.actor_user_id == vendors[0] for e in events)
        db.session.get(Project, ids[0]).consent_given = False; db.session.commit()
    login(client, vendors[0])
    assert client.get(f"/vendor/leads/{ids[0]}").status_code == 404
    assert "PRIVATE CUSTOMER" not in client.get("/vendor/unlocked-leads").text


def test_credit_requests_require_admin_and_reconcile(app, client, leads):
    _, vendors = leads
    for _ in range(2):
        assert client.post("/vendor/credits/request", data={"amount": "20", "reason": "More projects"}).status_code == 302
    with app.app_context():
        item = VendorRefundRequest.query.one(); request_id = item.id
        assert item.issue_type == "credit_request" and item.status == "pending"
        assert db.session.get(User, vendors[0]).vendor_credits_balance == 10
        admin = db.session.get(User, 1); admin.role = "admin"; db.session.commit()
    assert client.post(f"/admin/credit-requests/{request_id}/review", data={"decision": "approved"}).status_code == 403
    login(client, 1)
    assert "More projects" in client.get("/admin/vendors").text
    for _ in range(2):
        assert client.post(f"/admin/credit-requests/{request_id}/review", data={"decision": "approved"}).status_code == 302
    with app.app_context():
        vendor = db.session.get(User, vendors[0])
        assert vendor.vendor_credits_balance == 30
        assert VendorCreditLedger.query.count() == 1
        assert service.ledger_summary(vendor)["reconciled"]
        assert VendorRefundRequest.query.one().reviewed_by_user_id == 1
    login(client, vendors[1])
    assert "More projects" not in client.get("/vendor/credits").text


def test_approval_cannot_refill_spent_starting_credits(app, client, leads):
    _, vendors = leads
    with app.app_context():
        db.session.get(User, 1).role = "admin"
        vendor = db.session.get(User, vendors[0]); vendor.vendor_status = "pending"; vendor.vendor_credits_balance = 0
        db.session.commit()
    login(client, 1)
    for _ in range(2): client.post(f"/admin/vendors/{vendors[0]}/approve")
    with app.app_context():
        assert VendorCreditLedger.query.count() == 1
        with service.transaction():
            vendor = service.locked_user(vendors[0])
            service.change_balance(vendor, -vendor.vendor_credits_balance, "fixture_spend")
        vendor.vendor_status = "suspended"; db.session.commit()
    client.post(f"/admin/vendors/{vendors[0]}/approve")
    with app.app_context():
        assert db.session.get(User, vendors[0]).vendor_credits_balance == 0
        assert VendorCreditLedger.query.filter_by(reason="approved_vendor_credits").count() == 1


def test_csrf_anonymous_and_forged_vendor(app, client, leads):
    ids, vendors = leads
    app.config["WTF_CSRF_ENABLED"] = True
    assert post(client, ids[0]).status_code == 400
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get(f"/vendor/leads/{ids[0]}").text).group(1)
    assert client.post(f"/vendor/board/{ids[0]}/unlock", data={"csrf_token": token, "vendor_id": vendors[1]}, headers={"Accept": "application/json"}).status_code == 200
    with app.app_context(): assert VendorLeadUnlock.query.one().vendor_user_id == vendors[0]
    app.config["WTF_CSRF_ENABLED"] = False
    assert post(app.test_client(), ids[0]).status_code == 401
    login(client, 1)
    assert post(client, ids[0]).status_code == 403


def test_inconsistent_ledger_blocks_debits(app, client, leads):
    ids, vendors = leads
    with app.app_context():
        db.session.add(VendorCreditLedger(vendor_user_id=vendors[0], delta=1, balance_after=100, reason="legacy")); db.session.commit()
    assert post(client, ids[0]).status_code == 409
    assert "Credit history needs administrator review" in client.get("/vendor/credits").text
    with app.app_context(): assert VendorLeadUnlock.query.count() == 0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "junk", "1000001"])
def test_invalid_credit_requests_never_create_records(app, client, leads, value):
    response = client.post("/vendor/credits/request", data={"amount": value, "reason": "Test"}, headers={"Accept": "application/json"})
    assert response.status_code == 400
    with app.app_context():
        assert VendorRefundRequest.query.count() == VendorCreditLedger.query.count() == 0


def test_rejection_and_refund_requests_cannot_grant_credits(app, client, leads):
    _, vendors = leads
    client.post("/vendor/credits/request", data={"amount": "20", "reason": "Review"})
    with app.app_context():
        request_id = VendorRefundRequest.query.one().id
        refund = VendorRefundRequest(vendor_user_id=vendors[0], amount=50, issue_type="duplicate")
        db.session.add(refund); db.session.get(User, 1).role = "admin"; db.session.commit()
        refund_id = refund.id
    login(client, 1)
    for _ in range(2):
        assert client.post(f"/admin/credit-requests/{request_id}/review", data={"decision": "rejected"}, headers={"Accept": "application/json"}).status_code == 200
    assert client.post(f"/admin/credit-requests/{refund_id}/review", data={"decision": "approved"}, headers={"Accept": "application/json"}).status_code == 404
    with app.app_context():
        assert db.session.get(User, vendors[0]).vendor_credits_balance == 10
        assert VendorCreditLedger.query.count() == 0
        assert db.session.get(VendorRefundRequest, request_id).status == "rejected"


def test_credit_approval_failure_is_all_or_nothing(app, client, leads, monkeypatch):
    _, vendors = leads
    client.post("/vendor/credits/request", data={"amount": "20", "reason": "Review"})
    with app.app_context():
        request_id = VendorRefundRequest.query.one().id
        db.session.get(User, 1).role = "admin"; db.session.commit()
    login(client, 1)
    with app.app_context():
        def fail():
            db.session.flush()
            raise OperationalError("private query", {}, Exception("private"))
        monkeypatch.setattr(db.session, "commit", fail)
        response = client.post(f"/admin/credit-requests/{request_id}/review", data={"decision": "approved"}, headers={"Accept": "application/json"})
        assert response.status_code == 503
        monkeypatch.undo()
        assert db.session.get(VendorRefundRequest, request_id).status == "pending"
        assert db.session.get(User, vendors[0]).vendor_credits_balance == 10
        assert VendorCreditLedger.query.count() == 0
        assert AdminActionLog.query.filter_by(action="review_vendor_credits").count() == 0


def test_unrepresentable_float_debit_cannot_unlock_for_free(app, client, leads):
    ids, vendors = leads
    with app.app_context():
        db.session.get(User, vendors[0]).vendor_credits_balance = 1e100
        db.session.commit()
    assert post(client, ids[0]).status_code == 409
    with app.app_context():
        assert VendorLeadUnlock.query.count() == VendorCreditLedger.query.count() == 0


def test_deleting_purchased_project_archives_and_preserves_receipt_ledger_privacy(
    app, client, leads
):
    ids, vendors = leads
    project_id = ids[0]
    assert post(client, project_id).status_code == 200

    with app.app_context():
        owner_id = db.session.get(Project, project_id).user_id
        unlock_id = VendorLeadUnlock.query.filter_by(project_id=project_id).one().id
        ledger_id = VendorCreditLedger.query.filter_by(project_id=project_id).one().id
        balance = db.session.get(User, vendors[0]).vendor_credits_balance

    login(client, owner_id)
    response = client.post(f"/projects/{project_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "Project archived" in response.text

    with app.app_context():
        project = db.session.get(Project, project_id)
        assert project is not None and project.status == "closed"
        assert db.session.get(VendorLeadUnlock, unlock_id).project_id == project_id
        assert db.session.get(VendorCreditLedger, ledger_id).project_id == project_id
        assert db.session.get(User, vendors[0]).vendor_credits_balance == balance

        # The database, not only the route, prevents an accidental hard delete.
        db.session.delete(project)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    listing = client.get("/business/projects")
    assert listing.status_code == 200
    assert "PRIVATE OWNER" in listing.text
    assert "Archived" in listing.text
    assert "Archived to preserve installer unlock receipts." in listing.text
    assert "Closed" in listing.text
    detail = client.get(f"/business/projects/{project_id}")
    assert detail.status_code == 200
    assert "Archived" in detail.text
    assert "Archived to preserve installer unlock receipts." in detail.text

    login(client, vendors[0])
    assert "PRIVATE CUSTOMER" in client.get(f"/vendor/leads/{project_id}").text
    retry = post(client, project_id)
    assert retry.status_code == 200
    assert retry.json["result"]["already_unlocked"] is True
    with app.app_context():
        assert VendorLeadUnlock.query.filter_by(project_id=project_id).count() == 1
        assert VendorCreditLedger.query.filter_by(project_id=project_id).count() == 1
        assert db.session.get(User, vendors[0]).vendor_credits_balance == balance

    login(client, vendors[1])
    assert client.get(f"/vendor/leads/{project_id}").status_code == 404
    assert "PRIVATE CUSTOMER" not in client.get("/vendor/unlocked-leads").text


def test_deleting_unused_project_detaches_but_preserves_historical_ledger(
    app, client, leads
):
    ids, vendors = leads
    project_id = ids[1]
    with app.app_context():
        owner_id = db.session.get(Project, project_id).user_id
        vendor = db.session.get(User, vendors[0])
        db.session.add(
            VendorCreditLedger(
                vendor_user_id=vendor.id,
                project_id=project_id,
                delta=0,
                balance_after=vendor.vendor_credits_balance,
                reason="historical_reference",
            )
        )
        db.session.commit()
        ledger_id = VendorCreditLedger.query.one().id
        balance = vendor.vendor_credits_balance

    login(client, owner_id)
    response = client.post(f"/projects/{project_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "Project deleted" in response.text
    assert client.get(f"/business/projects/{project_id}").status_code == 404
    assert all(
        item["id"] != project_id for item in client.get("/projects").get_json()["projects"]
    )

    with app.app_context():
        assert db.session.get(Project, project_id) is None
        ledger = db.session.get(VendorCreditLedger, ledger_id)
        assert ledger is not None and ledger.project_id is None
        vendor = db.session.get(User, vendors[0])
        assert vendor.vendor_credits_balance == balance
        assert service.ledger_summary(vendor)["reconciled"] is True


def test_deleting_already_closed_purchased_project_records_archive_once(app, client, leads):
    ids, vendors = leads
    project_id = ids[0]
    assert post(client, project_id).status_code == 200

    with app.app_context():
        project = db.session.get(Project, project_id)
        owner_id = project.user_id
        project.transition_status("user", owner_id, "closed", note="Closed before archive")
        db.session.commit()

    login(client, owner_id)
    response = client.post(f"/projects/{project_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "Archived to preserve installer unlock receipts." in response.text

    with app.app_context():
        archive_events = ProjectStatusLog.query.filter_by(
            project_id=project_id,
            from_status="closed",
            to_status="closed",
            note=PROJECT_ARCHIVE_NOTE,
        ).all()
        assert len(archive_events) == 1
        closed_at = db.session.get(Project, project_id).closed_at
        assert VendorLeadUnlock.query.filter_by(project_id=project_id).count() == 1
        assert VendorCreditLedger.query.filter_by(project_id=project_id).count() == 1

    # Repeating the archive request does not create duplicate history events.
    client.post(f"/projects/{project_id}/delete", follow_redirects=True)
    with app.app_context():
        assert ProjectStatusLog.query.filter_by(
            project_id=project_id,
            from_status="closed",
            to_status="closed",
            note=PROJECT_ARCHIVE_NOTE,
        ).count() == 1
        assert db.session.get(Project, project_id).closed_at == closed_at

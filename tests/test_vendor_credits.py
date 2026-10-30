"""Part 2 tests: isolated fixture databases, no providers or browser."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import re

import pytest
from sqlalchemy.exc import OperationalError

from app.models import AdminActionLog, Project, User, VendorCreditLedger, VendorLeadUnlock, VendorRefundRequest, db
from app.services import vendor_credit_service as service
from tests.test_vendor_leads import leads, login


def post(client, project_id):
    return client.post(f"/vendor/board/{project_id}/unlock", headers={"Accept": "application/json"})


def test_unlock_is_committed_idempotent_and_private(app, client, leads):
    ids, vendors = leads
    assert "PRIVATE CUSTOMER" not in client.get(f"/vendor/leads/{ids[0]}").text
    first = post(client, ids[0])
    assert first.status_code == 200 and first.json["result"]["cost"] == 1
    assert post(client, ids[0]).json["result"]["already_unlocked"] is True
    assert "PRIVATE CUSTOMER" in client.get(f"/vendor/leads/{ids[0]}").text
    assert "PRIVATE CUSTOMER" in client.get("/vendor/unlocked-leads").text
    assert "PRIVATE CUSTOMER" not in client.get("/vendor/board").text
    assert client.get("/vendor/credits").status_code == 200
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
        if failure == "track": project.location.extras = {**project.location.extras, "planning_track": "home"}
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

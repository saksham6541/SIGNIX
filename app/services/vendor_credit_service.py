"""Serialized vendor credit operations using the existing ledger and unlocks."""
from contextlib import contextmanager
from datetime import datetime, timezone
import math

from flask import current_app
from sqlalchemy import update

from app.models import AdminActionLog, Project, User, UserLocation, VendorCreditLedger, VendorLeadUnlock, VendorRefundRequest, db
from app.services.vendor_lead_service import eligible_query, public_card

OUTCOMES = ("interested", "follow_up", "won", "lost")


class CreditError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status
        super().__init__(message)


def amount(value, *, positive=False):
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise CreditError("Enter a valid credit amount.", 400) from None
    if isinstance(value, bool) or not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise CreditError("Enter a valid credit amount.", 400)
    return number


@contextmanager
def transaction(*, lock_user_id=None, lock_project_id=None):
    # SQLite's FOR UPDATE is ignored. A no-op write on the active connection
    # acquires its writer lock even if authentication already autobegan a
    # logical session transaction; the lock lasts through commit/rollback.
    session = db.session()
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Credit operations require a clean session")

    try:
        if session.get_bind().dialect.name == "sqlite":
            if lock_user_id is not None:
                session.execute(
                    update(User)
                    .where(User.id == lock_user_id)
                    .values(vendor_credits_balance=User.vendor_credits_balance),
                    execution_options={"synchronize_session": False},
                )
            if lock_project_id is not None:
                session.execute(
                    update(Project)
                    .where(Project.id == lock_project_id)
                    .values(status=Project.status, updated_at=Project.updated_at),
                    execution_options={"synchronize_session": False},
                )
        yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def locked_user(user_id, *, admin=False, approved=True):
    user = (
        db.session.query(User)
        .filter_by(id=user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if user is None or user.role != ("admin" if admin else "vendor") or (not admin and approved and not user.is_vendor_approved):
        raise CreditError("Access denied.", 403)
    return user


def _lock_unlock_rows(vendor_id, project_id):
    session = db.session()
    vendor = locked_user(vendor_id)
    project = (
        session.query(Project)
        .filter_by(id=project_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    return vendor, project


def ledger_summary(vendor):
    """Reconstruct from the first recorded balance, never invent historic grants."""
    rows = VendorCreditLedger.query.filter_by(vendor_user_id=vendor.id).order_by(VendorCreditLedger.id).all()
    balance = amount(vendor.vendor_credits_balance)
    opening = rows[0].balance_after - rows[0].delta if rows else balance
    expected, valid = opening, math.isfinite(opening) and opening >= 0
    for row in rows:
        expected += row.delta
        valid = valid and math.isfinite(row.delta) and math.isfinite(row.balance_after) and row.balance_after >= 0 and math.isclose(expected, row.balance_after, abs_tol=1e-8, rel_tol=0)
    valid = valid and math.isclose(expected, balance, abs_tol=1e-8, rel_tol=0)
    return {"balance": balance, "opening": opening, "reconciled": valid}


def change_balance(vendor, delta, reason, *, project_id=None, note=None):
    if not ledger_summary(vendor)["reconciled"]:
        raise CreditError("Credit history needs administrator review.")
    before = amount(vendor.vendor_credits_balance)
    after = before + delta
    if not math.isfinite(delta) or not math.isfinite(after) or after < 0:
        raise CreditError("You do not have enough credits to unlock this lead.")
    if delta != 0 and after == before:
        raise CreditError("Credit history needs administrator review.")
    # The lock serializes project limits and ledger history; the conditional
    # update independently prevents a stale-balance overwrite/negative debit.
    result = db.session.execute(update(User).where(User.id == vendor.id,
        User.vendor_credits_balance == before, User.vendor_credits_balance + delta >= 0).values(
        vendor_credits_balance=User.vendor_credits_balance + delta), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        raise CreditError("Credit balance changed. Please try again.")
    db.session.expire(vendor, ["vendor_credits_balance"])
    row = VendorCreditLedger(vendor_user_id=vendor.id, project_id=project_id,
                            delta=delta, balance_after=after, reason=reason, note=note)
    db.session.add(row)
    return row


def unlock_lead(vendor_id, project_id):
    with transaction(lock_user_id=vendor_id, lock_project_id=project_id):
        vendor, project = _lock_unlock_rows(vendor_id, project_id)
        if project is None:
            raise CreditError("This lead is no longer available.", 404)
        existing = VendorLeadUnlock.query.filter_by(vendor_user_id=vendor_id, project_id=project_id).first()
        if existing:
            return {"already_unlocked": True, "unlock_id": existing.id, "cost": existing.credits_charged}
        UserLocation.query.filter_by(id=project.location_id).with_for_update().one()
        query, _columns = eligible_query(vendor)
        row = query.filter(Project.id == project_id).first()
        if row is None:
            raise CreditError("This lead is no longer available.", 404)
        try:
            cost = amount(public_card(row, set())["unlock_cost"], positive=True)
        except CreditError:
            raise CreditError("Unlock cost unavailable. Please contact an administrator.", 503) from None
        ledger = change_balance(vendor, -cost, "lead_unlock", project_id=project_id)
        unlock = VendorLeadUnlock(vendor_user_id=vendor_id, project_id=project_id, credits_charged=cost)
        db.session.add(unlock)
        db.session.flush()
        ledger.note = f"Unlock {unlock.id}; project {project_id}"
        result = {"already_unlocked": False, "unlock_id": unlock.id, "cost": cost}
    return result


def record_outcome(vendor_id, project_id, outcome):
    if outcome not in OUTCOMES:
        raise CreditError("Choose a valid lead outcome.", 400)
    with transaction(lock_user_id=vendor_id):
        locked_user(vendor_id)
        unlock = VendorLeadUnlock.query.filter_by(vendor_user_id=vendor_id, project_id=project_id).with_for_update().first()
        if unlock is None:
            raise CreditError("Unlock the lead before marking its status.", 403)
        previous = outcome_history(unlock).first()
        if previous is None or previous.detail != outcome:
            db.session.add(AdminActionLog(actor_user_id=vendor_id, action="vendor_lead_outcome",
                target_type="vendor_lead_unlock", target_id=unlock.id, detail=outcome))


def outcome_history(unlock):
    return AdminActionLog.query.filter_by(actor_user_id=unlock.vendor_user_id, target_type="vendor_lead_unlock",
        target_id=unlock.id, action="vendor_lead_outcome").order_by(AdminActionLog.id.desc())


def request_credits(vendor_id, requested, reason):
    requested = amount(requested, positive=True)
    if requested > 1000000 or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise CreditError("Provide a reason and request at most 1,000,000 credits.", 400)
    with transaction(lock_user_id=vendor_id):
        locked_user(vendor_id)
        pending = VendorRefundRequest.query.filter_by(vendor_user_id=vendor_id, issue_type="credit_request", status="pending").first()
        if pending:
            return pending.id
        item = VendorRefundRequest(vendor_user_id=vendor_id, issue_type="credit_request",
                                   amount=requested, reason=reason.strip())
        db.session.add(item)
        db.session.flush()
        db.session.add(AdminActionLog(actor_user_id=vendor_id, action="request_vendor_credits",
            target_type="vendor_credit_request", target_id=item.id, detail="Submitted for administrator review"))
        request_id = item.id
    return request_id


def review_credit_request(admin_id, request_id, decision):
    if decision not in {"approved", "rejected"}:
        raise CreditError("Choose approve or reject.", 400)
    with transaction(lock_user_id=admin_id):
        locked_user(admin_id, admin=True)
        # Lock the recipient before the request, consistently with submission.
        item = VendorRefundRequest.query.filter_by(id=request_id, issue_type="credit_request").first()
        if item is None:
            raise CreditError("Credit request not found.", 404)
        vendor = locked_user(item.vendor_user_id, approved=False)
        item = VendorRefundRequest.query.filter_by(id=request_id).with_for_update().populate_existing().one()
        if item.status != "pending":
            return
        if decision == "approved":
            if not vendor.is_vendor_approved:
                raise CreditError("Approve the vendor before granting credits.")
            change_balance(vendor, amount(item.amount, positive=True), "credit_request_approved", note=f"Credit request {item.id}")
        item.status, item.reviewed_by_user_id = decision, admin_id
        item.reviewed_at = datetime.now(timezone.utc)
        db.session.add(AdminActionLog(actor_user_id=admin_id, action="review_vendor_credits",
            target_type="vendor_credit_request", target_id=item.id, detail=decision))


def approve_vendor(admin_id, vendor_id):
    with transaction():
        locked_user(admin_id, admin=True)
        vendor = locked_user(vendor_id, approved=False)
        was_approved = vendor.vendor_status == "approved"
        previously_approved = vendor.vendor_status == "approved" or AdminActionLog.query.filter_by(
            action="approve_vendor", target_type="vendor", target_id=vendor.id).first() is not None
        grant = VendorCreditLedger.query.filter_by(vendor_user_id=vendor.id, reason="approved_vendor_credits").first()
        if vendor.vendor_status in {None, "pending"} and not previously_approved and grant is None and vendor.vendor_credits_balance == 0:
            change_balance(vendor, amount(current_app.config["VENDOR_STARTING_CREDITS"]),
                           "approved_vendor_credits", note="Starting vendor credits issued on approval.")
        vendor.vendor_status = "approved"
        if not was_approved:
            db.session.add(AdminActionLog(actor_user_id=admin_id, action="approve_vendor", target_type="vendor", target_id=vendor.id, detail="Approved"))

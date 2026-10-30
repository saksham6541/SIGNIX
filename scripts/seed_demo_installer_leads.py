"""Backed-up, idempotent seed for the localhost customer-to-installer demo.

The script uses the same configured SQLite database as ``python run.py``. It
never creates users, changes credit balances, or creates ledger/unlock rows.
Run without ``--apply`` for a read-only plan.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import make_url  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.models import Project, User, UserLocation, db  # noqa: E402
from app.services.vendor_lead_service import _profile_capacity_range  # noqa: E402


OWNER_EMAIL = "demo.business.local@example.com"
INSTALLER_EMAIL = "demo.installer.local@example.com"
SPECS = (
    ("demo-office-rooftop-v1", "DEMO — Office Rooftop", "Office", 18.0, "110001"),
    ("demo-warehouse-rooftop-v1", "DEMO — Warehouse Rooftop", "Small factory", 35.0, "110020"),
    ("demo-school-rooftop-v1", "DEMO — School Rooftop", "School or institution", 25.0, "110025"),
)


def configured_database():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise RuntimeError("This localhost demo seed supports only the configured SQLite database.")
    return Path(url.database).resolve()


def backup_database(path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target_path = directory / f"{path.name}.demo-leads.{stamp}.{uuid.uuid4().hex[:8]}.bak"
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    check = sqlite3.connect(f"file:{target_path.as_posix()}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("Demo backup integrity check failed; no records were inserted.")
    finally:
        check.close()
    return target_path


def seed(*, apply=False, backup_dir=None):
    path = configured_database()
    if not path.is_file():
        raise RuntimeError(f"Configured database does not exist: {path}")
    app = create_app()
    with app.app_context():
        owner = User.query.filter_by(email=OWNER_EMAIL).one_or_none()
        installer = User.query.filter_by(email=INSTALLER_EMAIL).one_or_none()
        if owner is None or owner.role != "user":
            raise RuntimeError(f"Demo customer account is unavailable: {OWNER_EMAIL}")
        if installer is None or not installer.is_vendor_approved:
            raise RuntimeError(f"Approved demo installer is unavailable: {INSTALLER_EMAIL}")
        profile = installer.vendor_profile if isinstance(installer.vendor_profile, dict) else {}
        service_area = str(profile.get("service_area") or "").strip()
        if not service_area:
            raise RuntimeError("The approved demo installer has no configured service area.")
        capacity_min, capacity_max = _profile_capacity_range(profile)
        capacities = [spec[3] for spec in SPECS]
        if any(size < capacity_min or (capacity_max is not None and size > capacity_max) for size in capacities):
            raise RuntimeError("A demo lead falls outside the installer's configured capacity range.")

        keys = [spec[0] for spec in SPECS]
        existing = UserLocation.query.filter(
            UserLocation.user_id == owner.id,
            UserLocation.extras["demo_seed_key"].as_string().in_(keys),
        ).all()
        by_key = {
            location.extras.get("demo_seed_key"): location
            for location in existing
            if isinstance(location.extras, dict)
        }
        if len(by_key) != len(existing):
            raise RuntimeError("Duplicate demo seed keys require manual review; no changes made.")
        missing = [spec for spec in SPECS if spec[0] not in by_key]
        plan = {
            "database": str(path),
            "customer": OWNER_EMAIL,
            "installer": INSTALLER_EMAIL,
            "existing": len(by_key),
            "to_create": len(missing),
        }
        if not apply or not missing:
            return {**plan, "status": "dry_run" if not apply else "already_seeded"}

        db.session.rollback()
        backup = backup_database(
            path,
            Path(backup_dir or ROOT / "instance" / "demo_backups").resolve(),
        )
        now = datetime.now(timezone.utc)
        try:
            for key, title, business_type, capacity_kw, pincode in missing:
                location = UserLocation(
                    user_id=owner.id,
                    address=f"{title} · FICTIONAL DEMO ONLY · Delhi NCR",
                    latitude=28.6139,
                    longitude=77.2090,
                    scenario_label=title,
                    system_size=capacity_kw,
                    extras={
                        "planning_track": "business",
                        "property_type": "commercial",
                        "source": "localhost_demo_seed",
                        "demo_only": True,
                        "demo_seed_key": key,
                        "calculation_status": "not_calculated_demo_only",
                        "city": "Delhi",
                        "locality": service_area,
                        "pincode": pincode,
                        "location_context": {
                            "city": "Delhi",
                            "locality": service_area,
                            "pincode": pincode,
                        },
                        "business_type": business_type,
                    },
                )
                db.session.add(location)
                db.session.flush()
                project = Project(
                    user_id=owner.id,
                    location_id=location.id,
                    owner="SIGNIX Local Demo Business — DEMO ONLY",
                    status="assessment_done",
                    contact_name="SIGNIX Local Demo Business — DEMO ONLY",
                    email=OWNER_EMAIL,
                    note="FICTIONAL DEMO ONLY. No real customer or installation history.",
                    consent_given=True,
                    consent_timestamp=now,
                    consent_text_version=Config.PROJECT_CONSENT_TEXT_VERSION,
                )
                db.session.add(project)
                db.session.flush()
                project.transition_status("system", owner.id, "under_review", note="DEMO ONLY seed")
                project.transition_status("system", owner.id, "open_to_vendors", note="DEMO ONLY seed published")
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return {
            **plan,
            "status": "applied",
            "created": len(missing),
            "backup": str(backup),
            "credits_unchanged": installer.vendor_credits_balance,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(seed(apply=args.apply, backup_dir=args.backup_dir), indent=2))
    except Exception as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

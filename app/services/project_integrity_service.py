"""Project removal rules that preserve paid marketplace audit records."""

from app.models import PROJECT_ARCHIVE_NOTE, Project, VendorLeadUnlock, db
from app.services.vendor_credit_service import transaction


class ProjectNotFoundError(Exception):
    pass


def _is_business_location(location):
    if location is None:
        return False
    extras = location.extras if isinstance(location.extras, dict) else {}
    track = str(extras.get("planning_track") or "").strip().lower()
    property_type = str(extras.get("property_type") or "").strip().lower()
    return track == "business" or property_type in {
        "commercial", "industrial", "msme", "factory"
    }


def delete_or_archive_project(owner_id, project_id, actor_role):
    """Delete an unused project, or archive it once an unlock receipt exists.

    The project row is locked before checking for receipts. Unlock purchases use
    the same project lock, so deletion cannot race a new purchase. The database
    RESTRICT foreign key remains the final invariant if another code path tries
    to delete a purchased lead.
    """
    with transaction(lock_project_id=project_id):
        project = (
            db.session.query(Project)
            .filter_by(id=project_id, user_id=owner_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if project is None or not _is_business_location(project.location):
            raise ProjectNotFoundError

        location_id = project.location_id
        has_unlock = (
            db.session.query(VendorLeadUnlock.id)
            .filter_by(project_id=project.id)
            .with_for_update()
            .first()
            is not None
        )
        if has_unlock:
            project.archive(
                actor_role,
                owner_id,
                note=PROJECT_ARCHIVE_NOTE,
            )
            db.session.flush()
            return {"action": "archived", "location_id": location_id}

        db.session.delete(project)
        db.session.flush()
        return {"action": "deleted", "location_id": location_id}

import re
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from app.config import Config
from app.models import (
    Project,
    ProjectStatusLog,
    PROJECT_STATUS_PERMISSIONS,
    PROJECT_STATUS_TRANSITIONS,
    User,
    UserLocation,
    VendorCreditLedger,
    VendorLeadUnlock,
    db,
)


def _create_project(app):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="123 Main Road, Bengaluru",
            latitude=12.9716,
            longitude=77.5946,
        )
        db.session.add(location)
        db.session.flush()

        project = Project(
            user_id=user.id,
            location_id=location.id,
            owner="Demo Owner",
            status="assessment_done",
            contact_name="Demo Contact",
            phone="9999999999",
            email="demo@example.com",
            consent_given=False,
        )
        db.session.add(project)
        db.session.commit()
        return db.session.get(Project, project.id)


def test_project_status_permissions_are_defined_in_one_place(app):
    assert PROJECT_STATUS_PERMISSIONS["user"] == {"assessment_done", "under_review", "open_to_vendors", "closed"}
    assert PROJECT_STATUS_PERMISSIONS["admin"] == {
        "under_review",
        "open_to_vendors",
        "vendor_connected",
        "decision",
        "installation",
        "closed",
    }
    assert PROJECT_STATUS_PERMISSIONS["system"] == {"assessment_done", "under_review", "open_to_vendors"}
    assert PROJECT_STATUS_TRANSITIONS["user"]["assessment_done"] == {"under_review", "open_to_vendors", "closed"}
    assert PROJECT_STATUS_TRANSITIONS["admin"]["installation"] == {"closed"}


def test_valid_project_status_transitions(app):
    project = _create_project(app)

    with app.app_context():
        assert Project.is_valid_transition("user", "assessment_done", "under_review") is True
        assert Project.is_valid_transition("user", "under_review", "open_to_vendors") is True
        assert Project.is_valid_transition("admin", "under_review", "open_to_vendors") is True
        assert Project.is_valid_transition("admin", "vendor_connected", "decision") is True
        assert Project.is_valid_transition("system", "assessment_done", "assessment_done") is True

        assert Project.is_valid_transition("user", "assessment_done", "decision") is False
        assert Project.is_valid_transition("admin", "assessment_done", "under_review") is False


def test_transition_status_records_log_and_updates_state(app):
    project = _create_project(app)

    with app.app_context():
        project = db.session.get(Project, project.id)
        before = project.updated_at
        log = project.transition_status("user", project.user_id, "under_review", note="Moved to review")
        db.session.commit()

        refreshed = db.session.get(Project, project.id)
        assert refreshed.status == "under_review"
        assert refreshed.updated_at is not None
        assert refreshed.closed_at is None
        assert log.from_status == "assessment_done"
        assert log.to_status == "under_review"
        assert log.actor_role == "user"
        assert log.actor_user_id == project.user_id
        assert log.note == "Moved to review"
        assert ProjectStatusLog.query.filter_by(project_id=project.id).count() >= 1
        assert refreshed.updated_at >= before


def test_project_creation_requires_consent_and_starts_under_review(app, client):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="1 Business Road, Bengaluru",
            latitude=13.0,
            longitude=77.0,
            extras={"planning_track": "business"},
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.post(
        "/projects/create",
        data={
            "location_id": location_id,
            "contact_name": "Owner",
            "phone": "99999",
            "email": "owner@example.com",
            "note": "Alpha",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assert Project.query.filter_by(location_id=location_id).count() == 0

    response = client.post(
        "/projects/create",
        data={
            "location_id": location_id,
            "contact_name": "Owner",
            "phone": "99999",
            "email": "owner@example.com",
            "note": "Alpha",
            "consent_given": "on",
            "consent_fee": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        project = Project.query.filter_by(location_id=location_id).one()
        assert project.status == "under_review"
        assert project.consent_given is True
        assert project.consent_text_version == Config.PROJECT_CONSENT_TEXT_VERSION
        assert ProjectStatusLog.query.filter_by(project_id=project.id).count() >= 1


def test_business_projects_page_renders_and_filters_for_current_user(app, client):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="2 Business Road, Hyderabad",
            latitude=17.0,
            longitude=78.0,
            extras={"planning_track": "business", "city": "Hyderabad", "pincode": "500001", "business_type": "Shop"},
        )
        other_user = User(
            email="second@example.com",
            password_hash="unused",
            display_name="Second User",
        )
        db.session.add_all([location, other_user])
        db.session.flush()
        other_location = UserLocation(
            user_id=other_user.id,
            address="Other user location",
            latitude=15.0,
            longitude=79.0,
            extras={"planning_track": "business", "city": "Bengaluru", "pincode": "560001", "business_type": "Office"},
        )
        db.session.add(other_location)
        db.session.flush()
        project = Project(
            user_id=user.id,
            location_id=location.id,
            owner="Owner",
            status="under_review",
            contact_name="Owner",
            phone="999",
            email="owner@example.com",
            consent_given=True,
            consent_timestamp=datetime.now(timezone.utc),
        )
        other_project = Project(
            user_id=other_user.id,
            location_id=other_location.id,
            owner="Other Owner",
            status="under_review",
            contact_name="Other Owner",
            phone="888",
            email="other@example.com",
            consent_given=True,
            consent_timestamp=datetime.now(timezone.utc),
        )
        db.session.add_all([project, other_project])
        db.session.commit()
        project_id = project.id

    response = client.get("/business/projects")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Projects" in html
    assert "Owner" in html
    assert "Other Owner" not in html

    detail_response = client.get(f"/business/projects/{project_id}")
    assert detail_response.status_code == 200
    detail_html = detail_response.get_data(as_text=True)
    assert "Assessment summary" in detail_html
    assert "Close project" in detail_html


def test_business_project_portfolio_metrics_filters_and_search_are_persisted(app, client):
    with app.app_context():
        owner = User.query.filter_by(email="test@example.com").one()
        vendor = User(
            email="portfolio-vendor@example.com",
            password_hash="unused",
            display_name="Portfolio Vendor",
            role="vendor",
            vendor_status="approved",
        )
        db.session.add(vendor)
        db.session.flush()
        states = (
            ("North Clinic", "under_review", "Pune", "411001", 10.0),
            ("Warehouse", "open_to_vendors", "Mumbai", "400001", 20.0),
            ("Factory", "vendor_connected", "Chennai", "600001", 15.0),
            ("Plant", "decision", "Delhi", "110001", 5.0),
            ("Office", "installation", "Kochi", "682001", 8.0),
            ("Closed Site", "closed", "Jaipur", "302001", 6.0),
            ("Draft Site", "assessment_done", "Surat", "395001", 4.0),
        )
        projects = []
        for name, status, city, pin, size in states:
            location = UserLocation(
                user_id=owner.id,
                address=f"{name} address",
                latitude=12.0,
                longitude=77.0,
                system_size=size,
                monthly_savings=1000.0 if name == "North Clinic" else None,
                extras={
                    "planning_track": "business",
                    "project_name": name,
                    "city": city,
                    "pincode": pin,
                    "business_type": "Commercial",
                    **(
                        {
                            "annual_net_benefit": 15000.0,
                            "installed_capacity_kw": 4.5,
                        }
                        if name == "North Clinic"
                        else {}
                    ),
                },
            )
            db.session.add(location)
            db.session.flush()
            project = Project(
                user_id=owner.id,
                location_id=location.id,
                owner=name,
                status=status,
                consent_given=True,
            )
            db.session.add(project)
            projects.append(project)

        residential_location = UserLocation(
            user_id=owner.id,
            address="Home",
            latitude=12.0,
            longitude=77.0,
            system_size=100.0,
            extras={"planning_track": "home"},
        )
        other_user = User(
            email="portfolio-other@example.com",
            password_hash="unused",
            display_name="Other Customer",
        )
        db.session.add_all([residential_location, other_user])
        db.session.flush()
        db.session.add_all(
            [
                Project(
                    user_id=owner.id,
                    location_id=residential_location.id,
                    owner="Residential",
                    status="under_review",
                ),
                UserLocation(
                    user_id=other_user.id,
                    address="Other customer address",
                    latitude=12.0,
                    longitude=77.0,
                    extras={
                        "planning_track": "business",
                        "city": "Pune",
                        "pincode": "411001",
                    },
                ),
            ]
        )
        db.session.flush()
        private_location = UserLocation.query.filter_by(
            user_id=other_user.id
        ).one()
        private_project = Project(
            user_id=other_user.id,
            location_id=private_location.id,
            owner="Private Project",
            status="under_review",
        )
        db.session.add(private_project)
        db.session.flush()
        db.session.add(
            VendorLeadUnlock(
                vendor_user_id=vendor.id,
                project_id=projects[1].id,
                credits_charged=2.0,
            )
        )
        db.session.commit()

    response = client.get("/business/projects")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Total projects" in html
    assert "North Clinic" in html
    assert "Private Project" not in html
    assert "Residential" not in html
    assert "15,000" in html
    assert "4.5 kW" in html
    assert "68.0 kW" in html
    assert "<strong>7</strong>" in html
    assert "Warehouse" in html
    assert "Installer interest" in html
    assert "Assessment done" in html
    assert client.get("/business/projects?q=North").get_data(as_text=True).count(
        'class="biz-project-card"'
    ) == 1
    assert client.get("/business/projects?q=Pune").get_data(as_text=True).count(
        'class="biz-project-card"'
    ) == 1
    assert client.get("/business/projects?q=411001").get_data(as_text=True).count(
        'class="biz-project-card"'
    ) == 1
    connected_html = client.get(
        "/business/projects?status=vendor_connected"
    ).get_data(as_text=True)
    assert connected_html.count('class="biz-project-card"') == 3
    assert "68.0 kW" in connected_html
    assert "<strong>7</strong>" in connected_html
    assert client.get("/business/projects?status=under_review").get_data(
        as_text=True
    ).count('class="biz-project-card"') == 1
    assert client.get("/business/projects?status=not-a-status").status_code == 400


def test_business_project_detail_and_transitions_enforce_owner_and_model_rules(
    app, client
):
    with app.app_context():
        owner = User.query.filter_by(email="test@example.com").one()
        location = UserLocation(
            user_id=owner.id,
            address="22 Commercial Road, Pune",
            latitude=18.5,
            longitude=73.8,
            system_size=12.0,
            annual_generation=18000.0,
            roof_area_sqm=240.0,
            usable_area_sqm=200.0,
            monthly_savings=1250.0,
            extras={
                "planning_track": "business",
                "project_name": "Pune Warehouse",
                "city": "Pune",
                "pincode": "411001",
                "business_type": "Warehouse",
                "annual_net_benefit": 18000.0,
                "monthly_bill_rs": 12000.0,
                "monthly_units_kwh": 1500.0,
                "tariff_per_kwh": 8.0,
            },
        )
        other = User(
            email="transition-other@example.com",
            password_hash="unused",
            display_name="Other",
        )
        db.session.add_all([location, other])
        db.session.flush()
        project = Project(
            user_id=owner.id,
            location_id=location.id,
            owner="Pune Warehouse",
            status="under_review",
            consent_given=True,
        )
        other_location = UserLocation(
            user_id=other.id,
            address="Other commercial address",
            latitude=19.0,
            longitude=73.0,
            extras={"planning_track": "business"},
        )
        db.session.add_all([project, other_location])
        db.session.flush()
        other_project = Project(
            user_id=other.id,
            location_id=other_location.id,
            owner="Other Project",
            status="under_review",
            consent_given=True,
        )
        db.session.add(other_project)
        db.session.commit()
        project_id = project.id
        other_project_id = other_project.id
        owner_id = owner.id

    detail = client.get(f"/business/projects/{project_id}")
    detail_html = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert "22 Commercial Road, Pune" in detail_html
    assert "Persisted assessment details" in detail_html
    assert "Available financial estimates" in detail_html
    assert "Project status history" in detail_html
    dashboard = client.get("/business/projects").get_data(as_text=True)
    assert 'href="/business/estimate"' in dashboard
    assert client.get(f"/business/projects/{other_project_id}").status_code == 404

    invalid = client.post(
        f"/business/projects/{project_id}/status",
        data={"status": "vendor_connected", "reason": "Not yet approved"},
    )
    assert invalid.status_code == 302
    assert client.post(
        f"/business/projects/{project_id}/status",
        data={"status": "under_review", "reason": "No change"},
    ).status_code == 400
    assert client.post(
        f"/business/projects/{project_id}/status",
        data={"status": "closed"},
    ).status_code == 400
    with app.app_context():
        project = db.session.get(Project, project_id)
        assert project.status == "under_review"
        assert ProjectStatusLog.query.filter_by(project_id=project_id).count() == 0

    valid = client.post(
        f"/business/projects/{project_id}/status",
        data={"status": "open_to_vendors", "reason": "Review complete"},
    )
    assert valid.status_code == 302
    with app.app_context():
        project = db.session.get(Project, project_id)
        log = ProjectStatusLog.query.filter_by(project_id=project_id).one()
        assert project.status == "open_to_vendors"
        assert log.actor_role == "user"
        assert log.actor_user_id == owner_id
        assert log.from_status == "under_review"
        assert log.to_status == "open_to_vendors"
        assert log.changed_at is not None
        assert log.note == "Review complete"
    history = client.get(f"/business/projects/{project_id}").get_data(as_text=True)
    assert "Review complete" in history

    unauthorized = client.post(
        f"/business/projects/{other_project_id}/status",
        data={"status": "closed", "reason": "Unauthorized"},
    )
    assert unauthorized.status_code == 404

    app.config["WTF_CSRF_ENABLED"] = True
    detail_html = client.get(
        f"/business/projects/{project_id}"
    ).get_data(as_text=True)
    csrf_token = re.search(
        r'name="csrf_token" value="([^"]+)"', detail_html
    ).group(1)
    close_data = {"status": "closed", "reason": "Owner closed the project"}
    assert client.post(
        f"/business/projects/{project_id}/status", data=close_data
    ).status_code == 400
    assert client.post(
        f"/business/projects/{project_id}/status",
        data={**close_data, "csrf_token": csrf_token},
    ).status_code == 302
    with app.app_context():
        project = db.session.get(Project, project_id)
        assert project.status == "closed"
        assert project.closed_at is not None
        assert ProjectStatusLog.query.filter_by(
            project_id=project_id, note="Owner closed the project"
        ).count() == 1

        db.session.get(User, owner_id).role = "vendor"
        db.session.commit()
    assert client.get("/business/projects").status_code == 403


def test_vendor_unlock_charges_once_and_ledger_matches_balance(app):
    with app.app_context():
        buyer = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=buyer.id,
            address="12 MG Road, Bengaluru",
            latitude=12.97,
            longitude=77.59,
            extras={
                "planning_track": "business",
                "city": "Bengaluru",
                "pincode": "560001",
                "business_type": "Shop",
                "preferred_model": "Buy",
                "system_size_kw": 12.0,
                "roof_area_sqm": 120.0,
                "monthly_units_kwh": 1600,
                "monthly_bill_rs": 18000,
            },
        )
        db.session.add(location)
        db.session.flush()
        project = Project(
            user_id=buyer.id,
            location_id=location.id,
            owner="Buyer",
            status="open_to_vendors",
            contact_name="Buyer",
            phone="9999999999",
            email="buyer@example.com",
            consent_given=True,
            consent_timestamp=datetime.now(timezone.utc),
        )
        vendor = User(
            email="vendor101@example.com",
            password_hash=generate_password_hash("unused"),
            display_name="Vendor One",
            role="vendor",
            vendor_status="approved",
            vendor_profile={"service_area": "Bengaluru,560001", "capacity_range": "5-25 kW"},
            vendor_credits_balance=10.0,
        )
        db.session.add_all([project, vendor])
        db.session.commit()
        project_id = project.id
        vendor_id = vendor.id

    vendor_client = app.test_client()
    response = vendor_client.post(
        "/login",
        data={"email": "vendor101@example.com", "password": "unused"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    unlock_response = vendor_client.post(
        f"/vendor/board/{project_id}/unlock",
        data={"csrf_token": "ignored"},
        follow_redirects=False,
    )
    assert unlock_response.status_code == 302

    unlock_response_second = vendor_client.post(
        f"/vendor/board/{project_id}/unlock",
        data={"csrf_token": "ignored"},
        follow_redirects=False,
    )
    assert unlock_response_second.status_code == 302

    with app.app_context():
        assert VendorLeadUnlock.query.filter_by(vendor_user_id=vendor_id, project_id=project_id).count() == 1
        ledger_rows = VendorCreditLedger.query.filter_by(vendor_user_id=vendor_id).all()
        assert len(ledger_rows) == 1
        assert ledger_rows[0].delta == -2.0
        refreshed_vendor = db.session.get(User, vendor_id)
        assert refreshed_vendor.vendor_credits_balance == 8.0
        assert sum(row.delta for row in ledger_rows) == -2.0


def test_fourth_vendor_cannot_unlock_after_max_leads_reached(app):
    with app.app_context():
        buyer = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=buyer.id,
            address="88 High Street, Bengaluru",
            latitude=13.01,
            longitude=77.57,
            extras={
                "planning_track": "business",
                "city": "Bengaluru",
                "pincode": "560001",
                "business_type": "Office",
                "preferred_model": "PPA",
                "system_size_kw": 18.0,
                "roof_area_sqm": 200.0,
            },
        )
        db.session.add(location)
        db.session.flush()
        project = Project(
            user_id=buyer.id,
            location_id=location.id,
            owner="Buyer",
            status="open_to_vendors",
            contact_name="Buyer",
            phone="999",
            email="buyer@example.com",
            consent_given=True,
            consent_timestamp=datetime.now(timezone.utc),
        )
        db.session.add(project)
        db.session.flush()
        for idx in range(3):
            vendor = User(
                email=f"vendormax{idx}@example.com",
                password_hash=generate_password_hash("unused"),
                display_name=f"Vendor {idx}",
                role="vendor",
                vendor_status="approved",
                vendor_profile={"service_area": "Bengaluru", "capacity_range": "5-25 kW"},
                vendor_credits_balance=100.0,
            )
            db.session.add(vendor)
            db.session.flush()
            db.session.add(
                VendorLeadUnlock(
                    vendor_user_id=vendor.id,
                    project_id=project.id,
                    credits_charged=1.0,
                )
            )
        db.session.commit()
        project_id = project.id

        fourth_vendor = User(
            email="vendormax4@example.com",
            password_hash=generate_password_hash("unused"),
            display_name="Vendor Four",
            role="vendor",
            vendor_status="approved",
            vendor_profile={"service_area": "Bengaluru", "capacity_range": "5-25 kW"},
            vendor_credits_balance=100.0,
        )
        db.session.add(fourth_vendor)
        db.session.commit()

    vendor_client = app.test_client()
    response = vendor_client.post(
        "/login",
        data={"email": "vendormax4@example.com", "password": "unused"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    response = vendor_client.post(
        f"/vendor/board/{project_id}/unlock",
        data={"csrf_token": "ignored"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        assert VendorLeadUnlock.query.filter_by(project_id=project_id).count() == 3
        fourth_vendor_db = User.query.filter_by(email="vendormax4@example.com").first()
        assert fourth_vendor_db.vendor_credits_balance == 100.0


def test_project_listing_scopes_to_current_user_and_close_delete_work(app, client):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="2 Business Road, Hyderabad",
            latitude=17.0,
            longitude=78.0,
            extras={"planning_track": "business"},
        )
        other_user = User(
            email="second@example.com",
            password_hash="unused",
            display_name="Second User",
        )
        db.session.add_all([location, other_user])
        db.session.flush()
        other_location = UserLocation(
            user_id=other_user.id,
            address="Other user location",
            latitude=15.0,
            longitude=79.0,
            extras={"planning_track": "business"},
        )
        db.session.add(other_location)
        db.session.flush()
        project = Project(
            user_id=user.id,
            location_id=location.id,
            owner="Owner",
            status="assessment_done",
            contact_name="Owner",
            phone="999",
            email="owner@example.com",
            consent_given=False,
        )
        other_project = Project(
            user_id=other_user.id,
            location_id=other_location.id,
            owner="Other Owner",
            status="assessment_done",
            contact_name="Other Owner",
            phone="888",
            email="other@example.com",
            consent_given=False,
        )
        db.session.add_all([project, other_project])
        db.session.commit()
        project_id = project.id
        other_project_id = other_project.id

    response = client.get("/projects")
    assert response.status_code == 200
    data = response.get_json()
    ids = {item["id"] for item in data["projects"]}
    assert project_id in ids
    assert other_project_id not in ids

    close_response = client.post(f"/projects/{project_id}/close", follow_redirects=False)
    assert close_response.status_code == 302
    with app.app_context():
        refreshed = db.session.get(Project, project_id)
        assert refreshed.status == "closed"
        assert ProjectStatusLog.query.filter_by(project_id=project_id, to_status="closed").count() == 1
    repeated_close = client.post(
        f"/projects/{project_id}/close", follow_redirects=False
    )
    assert repeated_close.status_code == 302
    with app.app_context():
        assert ProjectStatusLog.query.filter_by(
            project_id=project_id, to_status="closed"
        ).count() == 1

    delete_response = client.post(f"/projects/{project_id}/delete", follow_redirects=False)
    assert delete_response.status_code == 302
    with app.app_context():
        assert db.session.get(Project, project_id) is None


def test_public_signup_cannot_create_vendor_or_admin(app):
    guest_client = app.test_client()
    response = guest_client.post(
        "/signup",
        data={
            "planning_track": "home",
            "display_name": "New User",
            "email": "new-user@example.com",
            "password": "secretpass",
            "confirm_password": "secretpass",
            "role": "vendor",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="new-user@example.com").first()
        assert user is not None
        assert user.role == "user"

    guest_client_2 = app.test_client()
    response = guest_client_2.post(
        "/signup",
        data={
            "planning_track": "home",
            "display_name": "Another User",
            "email": "new-admin@example.com",
            "password": "secretpass",
            "confirm_password": "secretpass",
            "role": "admin",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email="new-admin@example.com").first()
        assert user is not None
        assert user.role == "user"


def test_user_cannot_access_vendor_or_admin_pages(app, client):
    response = client.get("/vendor/board", follow_redirects=False)
    assert response.status_code == 403

    response = client.get("/admin/vendors", follow_redirects=False)
    assert response.status_code == 403


def test_unapproved_vendor_is_stopped_on_pending_page(app):
    with app.app_context():
        vendor = User(
            email="vendor@example.com",
            password_hash=generate_password_hash("unused"),
            display_name="Vendor User",
            role="vendor",
            vendor_status="pending",
        )
        db.session.add(vendor)
        db.session.commit()
        vendor_id = vendor.id

    vendor_client = app.test_client()
    response = vendor_client.post(
        "/login",
        data={
            "email": "vendor@example.com",
            "password": "unused",
            "planning_track": "home",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    board_response = vendor_client.get("/vendor/board", follow_redirects=False)
    assert board_response.status_code == 200
    assert "Pending approval" in board_response.get_data(as_text=True)

    admin_client = app.test_client()
    with app.app_context():
        admin = User(
            email="admin@example.com",
            password_hash=generate_password_hash("unused"),
            display_name="Admin User",
            role="admin",
        )
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    response = admin_client.post(
        "/login",
        data={
            "email": "admin@example.com",
            "password": "unused",
            "planning_track": "home",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    review_response = admin_client.get("/admin/vendors", follow_redirects=False)
    assert review_response.status_code == 200
    assert "Vendor review" in review_response.get_data(as_text=True)

    approve_response = admin_client.post(f"/admin/vendors/{vendor_id}/approve", follow_redirects=False)
    assert approve_response.status_code == 302

    with app.app_context():
        refreshed = db.session.get(User, vendor_id)
        assert refreshed.vendor_status == "approved"

    approved_response = vendor_client.get("/vendor/board", follow_redirects=False)
    assert approved_response.status_code == 200
    assert "Approved vendor dashboard" in approved_response.get_data(as_text=True)

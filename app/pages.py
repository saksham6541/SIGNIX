from flask import Blueprint, abort, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from app.report_generator import generate_pdf_report
from app.services import location_service

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    return render_template("index.html")


@pages_bp.route("/dashboard")
@login_required
def dashboard():
    recent = location_service.list_recent_locations(current_user.id)
    return render_template("dashboard.html", locations=recent)


@pages_bp.route("/compare", methods=["GET", "POST"])
@login_required
def compare():
    locations = location_service.list_locations(current_user.id)
    selected_locations = []

    if request.method == "POST":
        try:
            selected_ids = [
                int(location_id) for location_id in request.form.getlist("location_ids")
            ]
        except ValueError:
            selected_ids = []

        locations_by_id = {location.id: location for location in locations}
        selected_locations = [
            locations_by_id[location_id]
            for location_id in selected_ids
            if location_id in locations_by_id
        ][:3]

    return render_template(
        "compare.html",
        locations=locations,
        selected_locations=selected_locations,
    )


@pages_bp.route("/report/<int:location_id>")
@login_required
def report(location_id):
    report_data = location_service.get_report_context(location_id, current_user.id)
    if report_data is None:
        abort(404)
    _, loc_dict = report_data
    return render_template("report.html", loc=loc_dict)


@pages_bp.route("/report/<int:location_id>/pdf")
@login_required
def download_pdf(location_id):
    location = location_service.get_location(location_id, current_user.id)
    if location is None:
        abort(404)
    try:
        pdf_buffer = generate_pdf_report(location)
    except Exception as exc:
        return (
            jsonify(
                {
                    "error": (
                        "PDF generation failed. Install reportlab (`pip install reportlab`) "
                        "or system packages for WeasyPrint. "
                        f"Details: {exc}"
                    )
                }
            ),
            500,
        )

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"solar_report_{location_id}.pdf",
        max_age=0,
    )

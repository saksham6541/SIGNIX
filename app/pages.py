from flask import Blueprint, abort, jsonify, render_template, send_file

from app.report_generator import generate_pdf_report
from app.services import location_service

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    return render_template("index.html")


@pages_bp.route("/dashboard")
def dashboard():
    recent = location_service.list_recent_locations()
    return render_template("dashboard.html", locations=recent)


@pages_bp.route("/report/<int:location_id>")
def report(location_id):
    report_data = location_service.get_report_context(location_id)
    if report_data is None:
        abort(404)
    _, loc_dict = report_data
    return render_template("report.html", loc=loc_dict)


@pages_bp.route("/report/<int:location_id>/pdf")
def download_pdf(location_id):
    location = location_service.get_location(location_id)
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

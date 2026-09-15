from app.models import UserLocation
from app.report_generator import _generate_pdf_reportlab, _enrich_location_data, generate_pdf_report
from flask import render_template


def make_location(rating=None):
    return UserLocation(
        user_id=1,
        address="PDF test rooftop",
        latitude=28.6,
        longitude=77.2,
        system_size=3,
        annual_generation=4000,
        roof_area_sqm=40,
        usable_area_sqm=35,
        net_investment=200000,
        monthly_savings=3000,
        co2_reduction_tons=4,
        monthly_data={"Jan": 300},
        suitability_rating=rating,
    )


def test_pdf_generation_handles_rating_and_missing_rating(app):
    rating = {
        "overall_viability": {"tier": "good", "score": 75},
        "data_confidence": {"tier": "high", "score": 100},
        "user_priority": "no_preference",
        "weights": {"payback": 25},
        "factors": {
            "payback": {"tier": "good", "score": 75},
            "roof_fit": {
                "tier": "excellent",
                "score": 100,
                "usable_area_per_kw": 11.67,
            },
        },
    }

    with app.app_context():
        rated_pdf = generate_pdf_report(make_location(rating))
        unrated_pdf = generate_pdf_report(make_location())

    assert rated_pdf.read(4) == b"%PDF"
    assert unrated_pdf.read(4) == b"%PDF"


def test_rating_visuals_render_for_html_and_reportlab_and_legacy_html_omits_rating(app):
    rating = {
        "overall_viability": {"tier": "excellent", "score": 100},
        "data_confidence": {"tier": "high", "score": 100},
        "user_priority": "fastest_payback",
        "factors": {
            "payback": {"tier": "excellent", "score": 100},
            "roof_fit": {"tier": "good", "score": 75, "usable_area_per_kw": 12},
        },
    }

    with app.app_context():
        rated_html = render_template("pdf_report.html", loc=_enrich_location_data(make_location(rating)))
        legacy_html = render_template("pdf_report.html", loc=_enrich_location_data(make_location()))
        fallback_pdf = _generate_pdf_reportlab(_enrich_location_data(make_location(rating)))

    assert 'class="rating-score rating-excellent"' in rated_html
    assert 'class="rating-bar-fill rating-excellent"' in rated_html
    assert "Suitability rating" not in legacy_html
    assert fallback_pdf.read(4) == b"%PDF"

# filename: app/report_generator.py
"""PDF report generation.

Primary path: WeasyPrint (HTML → PDF).
Fallback: ReportLab pure-Python PDF if WeasyPrint or its system libs are missing.
Both paths receive the same enriched location dict as the HTML report page.
"""

import io
import ipaddress
import math
import os
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from flask import current_app, render_template
from flask_babel import force_locale, format_date, get_locale, gettext as _
from app.localization import ui_label


def _packaged_devanagari_font_uri():
    """Return the packaged Devanagari font as an absolute file URI.

    WeasyPrint does not run through Flask's HTTP server when reports are
    generated in tests, workers, or CLI jobs.  A root-relative ``/static`` URL
    therefore cannot be fetched reliably in those environments.
    """
    font_path = (
        Path(current_app.static_folder)
        / "fonts"
        / "NotoSansDevanagari-Regular.ttf"
    ).resolve()
    if not font_path.is_file():
        raise RuntimeError(f"Packaged Devanagari font is missing: {font_path}")
    return font_path.as_uri()


def _finite_number(value):
    """Return a finite float for report compatibility checks, else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_report_text(value, fallback):
    """Normalize persisted display text without fabricating customer details."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = " ".join(
        "".join(ch for ch in text if ch in "\t\n" or unicodedata.category(ch) != "Cc").split()
    )
    # Replacement glyphs and common double-decoding markers are not useful in
    # a customer-facing header; a truthful fallback is safer than mojibake.
    if not text or "\ufffd" in text or "ï¿½" in text:
        return fallback
    return text


def _is_placeholder_address(value):
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized in {
        "selected rooftop",
        "selected roof",
        "selected location",
        "unknown address",
        "location not provided",
    }


def _public_base_url(value):
    """Return a safe external HTTP(S) origin, excluding local/private hosts."""
    candidate = str(value or "").strip().rstrip("/")
    if not candidate:
        return None
    try:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").strip().lower()
        if parsed.scheme not in {"http", "https"} or not hostname:
            return None
        if hostname == "localhost" or hostname.endswith(".local"):
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
        except ValueError:
            # Host names without a dot are normally local/intranet names.
            if "." not in hostname:
                return None
    except (TypeError, ValueError):
        return None
    return candidate


def _format_report_date(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return format_date(parsed, "dd MMM yyyy")


def _enrich_location_data(location):
    """Build the same dict the HTML report uses (extras + environmental)."""
    from app.solar_logic import _simple_payback, calculate_environmental_equivalents

    data = location.to_dict() if hasattr(location, "to_dict") else dict(location)
    extras = data.get("extras") or {}
    property_type = str(extras.get("property_type") or "").strip().lower()
    data["planning_track"] = data.get("planning_track") or extras.get("planning_track") or (
        "business" if property_type in {"commercial", "industrial", "msme", "factory"} else "home"
    )
    if data["planning_track"] not in {"home", "business"}:
        data["planning_track"] = "home"
    inverter_type = str(extras.get("inverter_type") or "").strip().lower().replace(" ", "-")
    saved_subsidy = _finite_number(data.get("subsidy_amount")) or 0.0
    data["psg_guidance_allowed"] = (
        data["planning_track"] == "home"
        and property_type == "residential"
        and (inverter_type in ("on-grid", "hybrid") or saved_subsidy > 0)
    )

    data["environmental_equivalents"] = extras.get(
        "environmental_equivalents"
    ) or calculate_environmental_equivalents(data.get("co2_reduction_tons") or 0)
    for key in (
        "inverter",
        "inverter_type",
        "bill_sizing",
        "roof_capacity_kw",
        "sizing_method",
        "rule_of_thumb_monthly_units",
        "rule_of_thumb_annual_units",
        "property_type",
        "planning_track",
        "calculation_policy",
        "needs_backup",
        "planning_notes",
        "system_cost_low",
        "system_cost_high",
        "annual_om",
        "annual_savings",
        "year_one_net_savings",
        "performance_ratio",
        "peak_sun_hours_assumed",
        "daily_units_per_kw_range",
        "dc_note",
        "cost_per_kw",
        "cashflow_25yr",
        "generation_25yr",
        "monthly_consumption",
        "financing_comparison",
        "simple_payback_years",
        "nominal_payback_years",
        "discounted_payback_years",
        "discount_rate_pct",
        "npv",
        "lifecycle_projection",
        "financial_provenance",
        "tariff_escalation_pct",
        "emi",
        "sensitivity",
        "sizing",
        "payback_without_subsidy_years",
        "subsidy_adjusted_payback_years",
        "annual_net_benefit",
        "annual_roi_pct",
        "lifetime_net_benefit",
        "demand_charge_savings",
    ):
        if key in extras and extras[key] is not None and data.get(key) is None:
            data[key] = extras[key]

    lifecycle = data.get("lifecycle_projection") or {}
    for key in ("nominal_payback_years", "discounted_payback_years"):
        if data.get(key) is None and lifecycle.get(key) is not None:
            data[key] = lifecycle[key]

    # These names intentionally mirror solar_logic.calculate_financials. Older
    # saved estimates may predate the two explicit residential payback keys,
    # even though all inputs needed by that unchanged formula are present.
    if data.get("simple_payback_years") is None:
        data["simple_payback_years"] = extras.get("simple_payback_years")
    if (
        data["planning_track"] == "home"
        and data.get("subsidy_adjusted_payback_years") is None
    ):
        data["subsidy_adjusted_payback_years"] = data.get("simple_payback_years")

    annual_savings = _finite_number(data.get("annual_savings"))
    if annual_savings is None:
        monthly_savings = _finite_number(data.get("monthly_savings"))
        annual_savings = monthly_savings * 12 if monthly_savings is not None else None
    annual_om = _finite_number(data.get("annual_om")) or 0.0
    if (
        data["planning_track"] == "home"
        and data.get("payback_without_subsidy_years") is None
        and annual_savings is not None
    ):
        gross_investment = (
            (_finite_number(data.get("system_cost")) or 0.0)
            + (_finite_number(data.get("battery_cost")) or 0.0)
        )
        data["payback_without_subsidy_years"] = _simple_payback(
            gross_investment, annual_savings, annual_om
        )

    if data.get("payback_years") is None:
        data["payback_years"] = (
            data.get("nominal_payback_years")
            if data.get("nominal_payback_years") is not None
            else data.get("simple_payback_years")
        )

    net_annual_savings = (
        annual_savings - annual_om if annual_savings is not None else None
    )
    data["payback_unavailable_reason"] = (
        _("Unavailable because year-one net savings are not positive.")
        if net_annual_savings is not None and net_annual_savings <= 0
        else _("Not available in this saved estimate.")
    )

    system_cost = _finite_number(data.get("system_cost"))
    battery_kwh = _finite_number(data.get("battery_kwh"))
    battery_cost = _finite_number(data.get("battery_cost"))
    battery_included = bool(
        (battery_kwh is not None and battery_kwh > 0)
        or (battery_cost is not None and battery_cost > 0)
    )
    battery_cost_available = battery_cost is not None and (
        battery_cost > 0 or not battery_included
    )
    subsidy_value = _finite_number(data.get("subsidy_amount"))
    subsidy = (
        subsidy_value
        if data["psg_guidance_allowed"] and subsidy_value is not None
        else None
    )
    net_investment = _finite_number(data.get("net_investment"))
    components_available = (
        system_cost is not None
        and (not battery_included or battery_cost_available)
        and (not data["psg_guidance_allowed"] or subsidy is not None)
    )
    expected_net = None
    equation_parts = [_("PV cost")]
    if battery_included:
        equation_parts.append(_("battery cost"))
    equation = " + ".join(equation_parts)
    if data["psg_guidance_allowed"]:
        equation += " - " + _("applicable subsidy")
    equation += " = " + _("net investment")
    if components_available:
        expected_net = system_cost
        if battery_included:
            expected_net += battery_cost
        if subsidy is not None:
            expected_net -= subsidy
    data["battery_cost_included"] = battery_included
    data["battery_cost_available"] = battery_cost_available
    data["subsidy_value_available"] = subsidy_value is not None
    data["cost_reconciliation_equation"] = equation
    data["cost_components_total"] = expected_net
    data["costs_reconcile"] = (
        expected_net is not None
        and net_investment is not None
        and abs(expected_net - net_investment) <= 0.5
    )
    data["cost_reconciliation_note"] = None
    if battery_included and not battery_cost_available:
        data["cost_reconciliation_note"] = (
            "Battery storage is included, but its saved cost is unavailable; "
            "the net investment cannot be reconciled from the displayed components."
        )
    elif data["psg_guidance_allowed"] and subsidy is None:
        data["cost_reconciliation_note"] = (
            "Subsidy guidance applies, but the saved subsidy amount is unavailable; "
            "the net investment cannot be reconciled from the displayed components."
        )
    elif expected_net is not None and net_investment is not None and not data["costs_reconcile"]:
        data["cost_reconciliation_note"] = (
            "Saved net investment does not reconcile with the displayed cost components; "
            "verify this estimate before relying on it."
        )

    latitude = _finite_number(data.get("latitude"))
    longitude = _finite_number(data.get("longitude"))
    data["coordinates_display"] = (
        f"{abs(latitude):.5f}° {'N' if latitude >= 0 else 'S'}, "
        f"{abs(longitude):.5f}° {'E' if longitude >= 0 else 'W'}"
        if latitude is not None and longitude is not None
        else None
    )
    location_context = extras.get("location_context") or {}
    if not isinstance(location_context, dict):
        location_context = {}
    address_candidates = (
        data.get("address"),
        extras.get("resolved_address"),
        extras.get("full_address"),
        location_context.get("display_name"),
        location_context.get("address"),
    )
    resolved_address = None
    for candidate in address_candidates:
        if not candidate or _is_placeholder_address(candidate):
            continue
        cleaned_address = _safe_report_text(candidate, "")
        if cleaned_address:
            resolved_address = cleaned_address
            break
    data["address_is_fallback"] = resolved_address is None
    data["location_label"] = _("Address") if resolved_address else _("Location coordinates")
    data["location_display"] = (
        resolved_address
        or data["coordinates_display"]
        or _("Location not provided")
    )
    data["address"] = data["location_display"]
    data["customer_name"] = _safe_report_text(
        data.get("customer_name") or extras.get("customer_name"), _("Not provided")
    )
    data["estimate_reference"] = (
        _("Estimate #%(reference)s", reference=data['id']) if data.get("id") is not None else _("Not assigned")
    )
    data["report_date"] = format_date(date.today(), "dd MMM yyyy")
    data["estimate_date"] = _format_report_date(data.get("created_at"))
    # monthly_data may be None on old rows
    if not data.get("monthly_data"):
        data["monthly_data"] = {}

    # Reuse the report page's validated presentation contract.  This service
    # rejects malformed/non-finite lifecycle data and does not synthesize a
    # monthly load curve from a single bill value.
    from app.services.report_chart_data import build_report_chart_data

    chart_data = build_report_chart_data(data)
    data["chart_data"] = chart_data

    energy = chart_data.get("energy") or {}
    consumption = chart_data.get("consumption") or {}
    generation_rows = energy.get("monthly_generation") or []
    consumption_values = consumption.get("monthly_consumption") or []
    chart_peak = max(
        [row.get("generation_kwh", 0) for row in generation_rows]
        + list(consumption_values)
        + [1]
    )
    data["monthly_chart"] = [
        {
            "month": row.get("month"),
            "generation_kwh": row.get("generation_kwh"),
            "generation_pct": round(row.get("generation_kwh", 0) / chart_peak * 100, 1),
            "consumption_kwh": (
                consumption_values[index]
                if len(consumption_values) == len(generation_rows)
                else None
            ),
            "consumption_pct": (
                round(consumption_values[index] / chart_peak * 100, 1)
                if len(consumption_values) == len(generation_rows)
                else None
            ),
        }
        for index, row in enumerate(generation_rows)
    ]
    data["monthly_consumption_note"] = consumption.get("unavailable_reason")

    financial_chart = chart_data.get("financial") or {}
    data["projection_chart"] = []
    if financial_chart.get("available", True) and financial_chart.get("cumulative_cashflow"):
        full_values = [-financial_chart["initial_investment"]] + list(
            financial_chart["cumulative_cashflow"]
        )
        selected_years = [0, 5, 10, 15, 20, 25]
        projection_peak = max([abs(value) for value in full_values] + [1])
        data["projection_chart"] = [
            {
                "year": year,
                "value": full_values[year],
                "magnitude_pct": round(abs(full_values[year]) / projection_peak * 100, 1),
                "positive": full_values[year] >= 0,
            }
            for year in selected_years
            if year < len(full_values)
        ]
        data["projection_break_even_year"] = financial_chart.get("payback_year")
    else:
        data["projection_break_even_year"] = None

    sensitivity = data.get("sensitivity") or {}
    sensitivity_rows = sensitivity.get("rows") if isinstance(sensitivity, dict) else None
    data["sensitivity_rows"] = sensitivity_rows if isinstance(sensitivity_rows, list) else []

    headline_payback = (
        data.get("nominal_payback_years")
        if data.get("nominal_payback_years") is not None
        else data.get("payback_years")
    )
    data["headline_payback_years"] = headline_payback
    if data["planning_track"] == "business":
        data["report_verdict"] = _(
            "A %(capacity)s kW commercial planning case with estimated annual generation of %(generation)s kWh.",
            capacity=f"{float(data.get('system_size') or 0):.1f}",
            generation=f"{float(data.get('annual_generation') or 0):,.0f}",
        )
    else:
        data["report_verdict"] = _(
            "A %(capacity)s kW residential planning case with estimated annual generation of %(generation)s kWh.",
            capacity=f"{float(data.get('system_size') or 0):.1f}",
            generation=f"{float(data.get('annual_generation') or 0):,.0f}",
        )

    data["assumption_rows"] = []
    for label, value, unit in (
        ("Performance ratio", data.get("performance_ratio"), ""),
        ("Peak sun hours", data.get("peak_sun_hours_assumed"), " h/day"),
        ("Tariff escalation", data.get("tariff_escalation_pct"), "%/year"),
        ("Discount rate", data.get("discount_rate_pct"), "%"),
        ("Annual O&M reserve", data.get("annual_om"), " INR/year"),
    ):
        if _finite_number(value) is not None:
            display_value = (
                f"₹{float(value):,.0f}/year"
                if label == "Annual O&M reserve"
                else f"{value}{unit}"
            )
            data["assumption_rows"].append(
                {"label": label, "value": display_value}
            )
    data["source_notes"] = []
    if data.get("irradiance_source"):
        source_names = {
            "nasa_power": "NASA POWER",
            "pvgis": "PVGIS",
            "mock": "simulated planning data",
            "mock_fallback": "simulated planning data",
        }
        source_value = str(data["irradiance_source"])
        data["source_notes"].append(
            _("Solar resource source: %(source)s.", source=source_names.get(source_value.lower(), source_value))
        )
    provenance = data.get("financial_provenance") or {}
    environmental_factor = (
        provenance.get("environmental_factor")
        if isinstance(provenance, dict)
        else None
    )
    if isinstance(environmental_factor, dict) and environmental_factor.get("source"):
        data["source_notes"].append(
            _("Emissions factor source: %(source)s.", source=environmental_factor['source'])
        )

    return data



def _pdf_assets(location, data, mode="full"):
    """Build packaged brand, map, and QR assets for PDF embedding."""
    import base64
    import time
    from flask import has_request_context, request

    from app.map_snapshot import (
        estimate_live_url,
        make_qr_png,
        render_rooftop_png,
        render_rooftop_svg,
        svg_to_png_bytes,
    )

    t0 = time.perf_counter()
    loc_id = None
    if hasattr(location, "id"):
        loc_id = location.id
    elif isinstance(data, dict):
        loc_id = data.get("id")

    configured_base_url = current_app.config.get("PUBLIC_BASE_URL") or os.environ.get(
        "PUBLIC_BASE_URL"
    )
    base_url = _public_base_url(configured_base_url)
    if not base_url and has_request_context():
        try:
            base_url = _public_base_url(request.url_root)
        except Exception:
            base_url = None

    live_url = estimate_live_url(loc_id, base_url) if loc_id and base_url else None

    poly = data.get("polygon_geojson")
    obs = data.get("obstructions_geojson")
    if poly is None and hasattr(location, "polygon_geojson"):
        poly = location.polygon_geojson
        obs = getattr(location, "obstructions_geojson", None)

    svg = render_rooftop_svg(poly, obs)
    png = render_rooftop_png(poly, obs) or (svg_to_png_bytes(svg) if svg else None)
    qr = make_qr_png(live_url) if live_url else None

    brand_logo_path = (
        Path(current_app.static_folder) / "images" / "signix-logo-full.png"
    ).resolve()
    brand_logo = brand_logo_path.read_bytes() if brand_logo_path.is_file() else None

    svg_b64 = None
    png_b64 = None
    qr_b64 = None
    brand_logo_b64 = None
    if svg:
        svg_b64 = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
    if png:
        png_b64 = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    if qr:
        qr_b64 = "data:image/png;base64," + base64.b64encode(qr).decode("ascii")
    if brand_logo:
        brand_logo_b64 = (
            "data:image/png;base64," + base64.b64encode(brand_logo).decode("ascii")
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"[TIMING] PDF assets (map+QR) elapsed: {elapsed_ms:.2f} ms mode={mode}")
    if elapsed_ms > 500:
        print(
            f"[WARN] PDF asset generation took {elapsed_ms:.0f} ms — "
            "above 500 ms budget; investigate map/QR path if this persists."
        )

    return {
        "map_svg": svg,
        "map_svg_data_uri": svg_b64,
        "map_png_bytes": png,
        "map_png_data_uri": png_b64,
        "qr_png_bytes": qr,
        "qr_png_data_uri": qr_b64,
        "brand_logo_bytes": brand_logo,
        "brand_logo_data_uri": brand_logo_b64,
        "live_estimate_url": live_url,
        "assets_elapsed_ms": elapsed_ms,
    }


def generate_pdf_report(location, locale=None, mode="full"):
    # The PDF locale must also own gettext calls during enrichment and fallback
    # rendering; passing pdf_locale to the template alone only selects its font.
    active_locale = str(locale or get_locale() or "en")
    with force_locale(active_locale):
        return _generate_pdf_report(location, locale=active_locale, mode=mode)


def _generate_pdf_report(location, locale=None, mode="full"):
    """
    location: UserLocation model instance (or dict).
    mode: "full" (technical report) or "summary" (2-page homeowner snapshot).
    Returns: BytesIO containing the rendered PDF.
    """
    pdf_start = time.perf_counter()
    data = _enrich_location_data(location)
    mode = (mode or "full").strip().lower()
    if mode not in ("full", "summary"):
        mode = "full"
    assets = _pdf_assets(location, data, mode=mode)
    data.update({k: v for k, v in assets.items() if k.endswith('_data_uri') or k in (
        'map_svg', 'live_estimate_url', 'assets_elapsed_ms'
    )})
    # Keep raw bytes only for ReportLab (not needed in HTML template context size)
    data['_map_png_bytes'] = assets.get('map_png_bytes')
    data['_qr_png_bytes'] = assets.get('qr_png_bytes')
    data['_brand_logo_bytes'] = assets.get('brand_logo_bytes')
    try:
        active_locale = str(locale or get_locale())
    except Exception:
        active_locale = str(locale or "en")

    template_name = "pdf_summary.html" if mode == "summary" else "pdf_report.html"

    # --- Try WeasyPrint first ---
    try:
        from weasyprint import HTML

        from flask import request

        html_string = render_template(
            template_name,
            loc=data,
            pdf_locale=active_locale,
            pdf_devanagari_font_url=_packaged_devanagari_font_uri(),
        )
        print(
            f"[TIMING] PDF render template elapsed: {(time.perf_counter() - pdf_start) * 1000:.2f} ms"
        )

        pdf_bytes = HTML(string=html_string, base_url=request.url_root).write_pdf()
        elapsed_ms = (time.perf_counter() - pdf_start) * 1000
        print(f"[TIMING] PDF generation total elapsed: {elapsed_ms:.2f} ms")

        buffer = io.BytesIO(pdf_bytes)
        buffer.seek(0)
        return buffer
    except Exception as weasy_err:
        # Fall through to ReportLab
        last_err = weasy_err
        print(
            "[WARN] WeasyPrint PDF generation unavailable; using ReportLab fallback: "
            f"{type(weasy_err).__name__}: {weasy_err}"
        )

    # --- ReportLab fallback (no system cairo/pango needed) ---
    try:
        reportlab_start = time.perf_counter()
        buffer = _generate_pdf_reportlab(data, locale=active_locale, mode=mode)
        elapsed_ms = (time.perf_counter() - reportlab_start) * 1000
        print(
            f"[TIMING] PDF generation fallback (ReportLab) elapsed: {elapsed_ms:.2f} ms"
        )
        return buffer
    except Exception as rl_err:
        raise RuntimeError(
            f"PDF generation failed. WeasyPrint: {last_err}; ReportLab: {rl_err}"
        )


_PDF_CURRENCY_PREFIX = None  # set by _register_pdf_fonts via _inr_prefix()


def _inr_prefix():
    """₹ when DejaVu is available, else 'Rs ' so PDFs never show missing-glyph boxes."""
    global _PDF_CURRENCY_PREFIX
    if _PDF_CURRENCY_PREFIX is None:
        regular, _ = _register_pdf_fonts()
        _PDF_CURRENCY_PREFIX = "₹" if regular.startswith("DejaVu") else "Rs "
    return _PDF_CURRENCY_PREFIX


def _inr(n):
    """Format INR for PDF (rupee symbol when font supports it)."""
    try:
        return f"{_inr_prefix()}{float(n):,.0f}"
    except Exception:
        return f"{_inr_prefix()}0"


def _register_pdf_fonts():
    """Register DejaVu (₹ support) and return (regular, bold) font names.

    Prefers fonts bundled under app/static/fonts so Windows/Mac demos work
    without system font packs.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    from app.config import BASE_DIR

    regular, bold = "Helvetica", "Helvetica-Bold"
    bundled = Path(BASE_DIR) / "app" / "static" / "fonts"
    candidates_reg = [
        bundled / "DejaVuSans.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    ]
    candidates_bold = [
        bundled / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ]
    reg_path = next((c for c in candidates_reg if c.exists()), None)
    bold_path = next((c for c in candidates_bold if c.exists()), None)

    try:
        if reg_path is not None:
            if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DejaVuSans", str(reg_path)))
            regular = "DejaVuSans"
        if bold_path is not None:
            if "DejaVuSans-Bold" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bold_path)))
            bold = "DejaVuSans-Bold"
        elif regular == "DejaVuSans":
            bold = "DejaVuSans"
    except Exception:
        regular, bold = "Helvetica", "Helvetica-Bold"

    # If Unicode font unavailable, callers still use ₹ in strings; Helvetica will
    # show a box. Prefer ASCII fallback only when font missing.
    return regular, bold


def _draw_reportlab_footer(canvas, doc):
    """Draw the same restrained page marker on every fallback PDF page."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    font_name, unused_bold_font = _register_pdf_fonts()
    canvas.saveState()
    canvas.setFillColor(HexColor("#777777"))
    canvas.setFont(font_name, 7.5)
    canvas.drawString(doc.leftMargin, 8 * mm, "SIGNIX · " + _("Indicative planning estimate"))
    canvas.drawRightString(
        A4[0] - doc.rightMargin, 8 * mm, _("Page %(number)s", number=canvas.getPageNumber())
    )
    canvas.restoreState()


def _generate_pdf_reportlab(loc, locale=None, mode="full"):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor, black, white
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        HRFlowable,
        ListFlowable,
        ListItem,
        CondPageBreak,
        KeepTogether,
    )
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from xml.sax.saxutils import escape

    active_locale = str(locale or get_locale())
    if (mode or "full").strip().lower() == "summary":
        return _generate_pdf_reportlab_summary(loc, locale=active_locale)

    font_name, font_bold = _register_pdf_fonts()
    # Hindi optional Devanagari overlay when available
    if active_locale.startswith("hi"):
        try:
            from app.config import BASE_DIR
            font_path = (
                Path(BASE_DIR)
                / "app"
                / "static"
                / "fonts"
                / "NotoSansDevanagari-Regular.ttf"
            )
            if font_path.exists():
                pdfmetrics.registerFont(TTFont("NotoSansDevanagari", str(font_path)))
                font_name = "NotoSansDevanagari"
                font_bold = "NotoSansDevanagari"
        except Exception:
            pass

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleOrange",
            parent=styles["Heading1"],
            textColor=HexColor("#c47a0b"),
            fontName=font_name,
            fontSize=16,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2Purple",
            parent=styles["Heading2"],
            textColor=HexColor("#3d2570"),
            fontName=font_name,
            fontSize=12,
            spaceBefore=12,
            spaceAfter=6,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodySmall",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Muted",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=8,
            textColor=HexColor("#666666"),
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Footer",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=7.5,
            textColor=HexColor("#888888"),
            leading=10,
        )
    )

    story = []
    purple = HexColor("#3d2570")
    orange = HexColor("#c47a0b")
    light = HexColor("#f7f4fc")

    brand_logo = loc.get("_brand_logo_bytes")
    if brand_logo:
        from reportlab.platypus import Image as RLImage

        logo = RLImage(io.BytesIO(brand_logo))
        logo._restrictSize(42 * mm, 15 * mm)
        story.extend([logo, Spacer(1, 3 * mm)])

    if active_locale.startswith("hi"):
        report_title = _("Rooftop Solar Estimation Report")
    else:
        report_title = (
            _("Commercial Rooftop Solar Estimation Report")
            if loc.get("planning_track") == "business"
            else _("Residential Rooftop Solar Estimation Report")
        )
    story.append(Paragraph(report_title, styles["TitleOrange"]))
    story.append(
        Paragraph(
            escape(str(
                (
                    f"{loc.get('location_label')}: {loc.get('location_display')}"
                    if loc.get("address_is_fallback")
                    else loc.get("location_display")
                )
                or _("Location not provided")
            )),
            styles["Muted"],
        )
    )
    meta = Table(
        [
            [_("Customer"), _("Estimate reference"), _("Report date"), _("Planning track")],
            [
                escape(str(loc.get("customer_name") or _("Not provided"))),
                escape(str(loc.get("estimate_reference") or _("Not assigned"))),
                escape(str(loc.get("report_date") or _("Not available"))),
                _("Commercial") if loc.get("planning_track") == "business" else _("Residential"),
            ],
        ],
        colWidths=[42 * mm] * 4,
    )
    meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light),
        ("TEXTCOLOR", (0, 0), (-1, 0), purple),
        ("FONTNAME", (0, 0), (-1, 0), font_bold),
        ("FONTNAME", (0, 1), (-1, 1), font_name),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("FONTSIZE", (0, 1), (-1, 1), 8),
        ("BOX", (0, 0), (-1, -1), 0.4, HexColor("#e0d6f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta)
    qr_bytes = loc.get("_qr_png_bytes")
    if (
        (loc.get("coordinates_display") and not loc.get("address_is_fallback"))
        or qr_bytes
    ):
        site_cells = [
            Paragraph(
                f"<b>Location:</b> {escape(str(loc.get('coordinates_display') or loc.get('location_display') or _('Not available')))}",
                styles["Muted"],
            )
        ]
        if qr_bytes:
            from reportlab.platypus import Image as RLImage

            qr_image = RLImage(io.BytesIO(qr_bytes), width=14 * mm, height=14 * mm)
            site_cells.append(qr_image)
        site_table = Table(
            [site_cells],
            colWidths=[154 * mm, 16 * mm] if qr_bytes else [170 * mm],
        )
        site_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(site_table)
    map_bytes = loc.get("_map_png_bytes")
    if map_bytes:
        from reportlab.platypus import Image as RLImage
        story.append(Spacer(1, 4))
        map_image = RLImage(io.BytesIO(map_bytes))
        map_image._restrictSize(160 * mm, 56 * mm)
        story.append(map_image)
        story.append(Paragraph(
            _("Rooftop outline (purple boundary; amber patches = obstructions)"),
            styles["Muted"],
        ))
        story.append(Spacer(1, 4))
    badges = []
    # Data source + confidence first (scannable trust signal)
    irr_src = loc.get("irradiance_source")
    irr_conf = (loc.get("irradiance_confidence") or "").lower()
    if not irr_conf and isinstance(loc.get("suitability_rating"), dict):
        irr_conf = (
            (loc["suitability_rating"].get("data_confidence") or {}).get("tier") or ""
        ).lower()
    if irr_src:
        src_names = {
            "nasa_power": "NASA POWER",
            "pvgis": "PVGIS",
            "mock": "Mock",
            "mock_fallback": "Mock",
        }
        src_label = src_names.get(str(irr_src).lower(), str(irr_src))
        if irr_conf == "high":
            badges.append(f"Data: {src_label} · High confidence")
        elif irr_conf == "medium":
            badges.append(f"Data: {src_label} · Medium confidence")
        elif irr_conf == "low":
            badges.append(f"Data: {src_label} · Low confidence — verify locally")
        else:
            badges.append(f"Data: {src_label}")
    if loc.get("orientation_label"):
        badges.append(f"Detected orientation: {loc['orientation_label']}")
    if loc.get("battery_kwh"):
        badges.append(f"Battery: {loc['battery_kwh']} kWh")
    if loc.get("inverter_type"):
        badges.append(f"Inverter: {loc['inverter_type']}")
    if badges:
        # Highlight data badge with existing green/orange/warn tones
        data_hex = {
            "high": "#1a7a45",
            "medium": "#a65d0a",
            "low": "#805b17",
        }.get(irr_conf, "#3d2570")
        story.append(
            Paragraph(
                f'<font color="{data_hex}"><b>{badges[0]}</b></font>',
                styles["BodySmall"],
            )
        )
        if len(badges) > 1:
            story.append(Paragraph(" · ".join(badges[1:]), styles["BodySmall"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#e0d6f0")))

    def section(title):
        story.append(Paragraph(title, styles["H2Purple"]))

    def kv_table(rows, min_first_page_rows=1):
        data = [
            [
                Paragraph(_("Item"), styles["BodySmall"]),
                Paragraph(_("Value"), styles["BodySmall"]),
            ]
        ] + [
            [
                Paragraph(escape(str(a)), styles["BodySmall"]),
                Paragraph(escape(str(b)), styles["BodySmall"]),
            ]
            for a, b in rows
        ]
        t = Table(
            data,
            colWidths=[95 * mm, 75 * mm],
            repeatRows=1,
            splitByRow=1,
            rowSplitRange=(1 + min_first_page_rows, -1),
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), light),
                    ("TEXTCOLOR", (0, 0), (-1, 0), purple),
                    ("FONTNAME", (0, 0), (-1, 0), font_bold),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.3, HexColor("#eeeeee")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(t)

    def rating_bar(score, tier):
        score = max(0, min(100, float(score or 0)))
        colors = {
            "excellent": HexColor("#247447"),
            "good": HexColor("#3f9d68"),
            "fair": HexColor("#c47a0b"),
            "poor": HexColor("#b84d55"),
        }
        bar_width = 43 * mm
        drawing = Drawing(bar_width, 5 * mm)
        drawing.add(
            Rect(
                0,
                1.5 * mm,
                bar_width,
                2 * mm,
                fillColor=HexColor("#eee8f7"),
                strokeColor=None,
            )
        )
        drawing.add(
            Rect(
                0,
                1.5 * mm,
                bar_width * score / 100,
                2 * mm,
                fillColor=colors.get(tier, colors["fair"]),
                strokeColor=None,
            )
        )
        return drawing

    def compact_bar_chart(rows, projection=False):
        width, height = 168 * mm, (39 if projection else 52) * mm
        drawing = Drawing(width, height)
        base_y = 12 * mm
        plot_height = height - 20 * mm
        drawing.add(Line(6 * mm, base_y, width - 3 * mm, base_y, strokeColor=HexColor("#cfc5b8"), strokeWidth=0.5))
        column = (width - 12 * mm) / max(len(rows), 1)
        for index, item in enumerate(rows):
            x = 7 * mm + index * column
            if projection:
                height_pct = float(item.get("magnitude_pct") or 0)
                bar_width = 8 * mm
                color = HexColor("#2f6b53") if item.get("positive") else HexColor("#c89139")
                label = _("Start") if item.get("year") == 0 else _("Year %(year)s", year=item.get("year"))
            else:
                height_pct = float(item.get("generation_pct") or 0)
                bar_width = 2.5 * mm
                color = HexColor("#e59a2f")
                label = str(item.get("month") or "")[:3]
            bar_height = plot_height * height_pct / 100
            drawing.add(Rect(x, base_y, bar_width, max(bar_height, 1.2 * mm), fillColor=color, strokeColor=None))
            if not projection and item.get("consumption_pct") is not None:
                load_height = plot_height * float(item.get("consumption_pct") or 0) / 100
                drawing.add(Rect(x + 3 * mm, base_y, 2.5 * mm, max(load_height, 1.2 * mm), fillColor=HexColor("#8e8391"), strokeColor=None))
            drawing.add(String(x, 7 * mm, label, fontName=font_name, fontSize=6.5, fillColor=HexColor("#6b646c")))
        return drawing

    # Overview cards
    section("1. System overview")
    benefit_card = (
        [_("Annual net benefit"), _inr(loc.get("annual_net_benefit"))]
        if loc.get("planning_track") == "business"
        else [_("Monthly savings"), _inr(loc.get("monthly_savings"))]
    )
    cards = [
        [_("System size"), f"{loc.get('system_size', '—')} kW"],
        [_("Annual generation"), f"{loc.get('annual_generation', '—')} kWh"],
        benefit_card,
        [
            _("Payback"),
            f"{loc.get('payback_years')} yrs"
            if loc.get("payback_years") is not None
            else _("Unavailable"),
        ],
    ]
    ct = Table(cards, colWidths=[42 * mm] * 4)
    ct.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), light),
                ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#e0d6f0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, HexColor("#e0d6f0")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TEXTCOLOR", (0, 0), (-1, 0), purple),
            ]
        )
    )
    # rebuild as 2-row: labels then values
    labels = [c[0] for c in cards]
    values = [c[1] for c in cards]
    ct = Table([labels, values], colWidths=[42 * mm] * 4)
    ct.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), light),
                ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#e0d6f0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, HexColor("#e0d6f0")),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("FONTSIZE", (0, 1), (-1, 1), 11),
                ("FONTNAME", (0, 1), (-1, 1), font_bold),
                ("TEXTCOLOR", (0, 1), (-1, 1), orange),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(ct)
    story.append(Spacer(1, 6))

    kv_table(
        [
            (_("Total roof area"), f"{loc.get('roof_area_sqm', '—')} m²"),
            (_("Obstructed area"), f"{loc.get('obstructed_area_sqm') or 0} m²"),
            (_("Usable area"), f"{loc.get('usable_area_sqm', '—')} m²"),
            (
                "Roof capacity",
                f"{loc.get('roof_capacity_kw') or loc.get('system_size')} kW",
            ),
            (_("Sizing method"), loc.get("sizing_method") or "roof_area"),
        ]
    )

    bs = loc.get("bill_sizing")
    if bs:
        section(_("Bill-based sizing"))
        kv_table(
            [
                (_("Monthly bill (input)"), _inr(bs.get("monthly_bill"))),
                (_("Tariff used"), f"{_inr_prefix()}{bs.get('tariff_per_kwh')}/unit"),
                (_("Estimated monthly units"), f"{bs.get('monthly_units')} units"),
                (
                    _("Recommended range"),
                    f"{bs.get('recommended_min_kw')}–{bs.get('recommended_max_kw')} kW",
                ),
                (_("Design size used"), f"{loc.get('system_size')} kW"),
            ]
        )

    rating = loc.get("suitability_rating")
    if rating:
        section(_("Suitability rating"))
        viability = rating.get("overall_viability") or {}
        confidence = rating.get("data_confidence") or {}
        conf_tier = irr_conf or confidence.get("tier") or _("Unknown")
        conf_score = confidence.get("score", 0)
        rating_summary = [
            [
                _("Overall viability"),
                f"{str(viability.get('tier') or _('Unknown')).title()} — {viability.get('score', 0)}/100",
            ],
            [
                _("Estimate confidence"),
                f"{str(conf_tier).title()} ({conf_score}/100)",
            ],
        ]
        if rating.get("user_priority"):
            rating_summary.append(
                [_("Priority"), str(rating["user_priority"]).replace("_", " ").title()]
            )
        summary_table = Table(rating_summary, colWidths=[48 * mm, 122 * mm])
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), HexColor("#faf8ff")),
                    ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#e0d6f0")),
                    ("TEXTCOLOR", (0, 0), (0, -1), purple),
                    ("FONTNAME", (0, 0), (0, -1), font_bold),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(summary_table)
        factor_labels = {
            "payback": _("Payback"),
            "financial_viability": _("Financial viability"),
            "roof_fit": _("Roof fit"),
            "orientation": "Orientation yield factor",
            "co2_reduction": _("CO2 reduction"),
            "backup_capability": _("Backup capability"),
        }
        factor_rows = [[_("Factor"), _("Score"), _("Tier / detail")]]
        for key, label in factor_labels.items():
            factor = (rating.get("factors") or {}).get(key)
            if not factor:
                continue
            if key == "roof_fit":
                detail = f"{factor.get('usable_area_per_kw')} m² usable area per kW"
            elif key == "orientation":
                detected = loc.get("orientation_label") or _("not detected")
                detail = (
                    f"Detected orientation: {detected}; relative yield factor "
                    f"{factor.get('factor')}"
                    if factor.get("factor") is not None
                    else (
                        f"Detected orientation: {detected}; yield factor unavailable. "
                        f"The saved {factor.get('score', 0)}/100 score is retained."
                    )
                )
            elif key == "co2_reduction":
                detail = f"{factor.get('tons')} tons CO2 reduction per year"
            elif key == "backup_capability":
                detail = f"{factor.get('score', 0)}/100 backup capability"
            else:
                detail = str(factor.get("tier") or _("Unavailable")).title()
            tier = str(factor.get("tier") or "fair").lower()
            factor_rows.append(
                [
                    Paragraph(escape(label), styles["BodySmall"]),
                    [
                        rating_bar(factor.get("score", 0), tier),
                        Paragraph(f"{factor.get('score', 0)}/100", styles["Muted"]),
                    ],
                    Paragraph(
                        escape(
                            f"{str(factor.get('tier') or _('Unavailable')).title()} — {detail}"
                        ),
                        styles["BodySmall"],
                    ),
                ]
            )
        if len(factor_rows) > 1:
            factor_table = Table(
                factor_rows,
                colWidths=[35 * mm, 52 * mm, 83 * mm],
                repeatRows=1,
                splitByRow=1,
            )
            factor_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), light),
                        ("TEXTCOLOR", (0, 0), (-1, 0), purple),
                        ("FONTNAME", (0, 0), (-1, 0), font_bold),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.3, HexColor("#eeeeee")),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(factor_table)

    section("2. Performance indicators")
    perf = []
    if loc.get("specific_yield"):
        perf.append((_("Specific yield"), f"{loc['specific_yield']:,.0f} kWh/kWp/year"))
    if loc.get("capacity_factor") is not None:
        perf.append((_("Capacity factor"), f"{loc['capacity_factor']}%"))
    if loc.get("orientation_factor") is not None:
        perf.append(
            (
                "Orientation factor vs South",
                f"{round(loc['orientation_factor'] * 100)}%",
            )
        )
    if loc.get("recommended_tilt_deg") is not None:
        perf.append((_("Recommended tilt"), f"{loc['recommended_tilt_deg']}°"))
    if loc.get("performance_ratio") is not None:
        perf.append((_("Performance ratio (assumed)"), str(loc["performance_ratio"])))
    if loc.get("peak_sun_hours_assumed") is not None:
        perf.append(
            (_("Peak sun hours (assumed)"), f"{loc['peak_sun_hours_assumed']} h/day")
        )
    if loc.get("lcoe") is not None:
        perf.append((_("Discounted LCOE (25 yr)"), f"{_inr_prefix()}{loc['lcoe']}/kWh"))
    if loc.get("lifetime_kwh"):
        perf.append((_("Lifetime energy (25 yr)"), f"{loc['lifetime_kwh']:,.0f} kWh"))
    if loc.get("self_consumption_frac") is not None:
        perf.append(
            (_("Self-consumption share"), f"{int(loc['self_consumption_frac'] * 100)}%")
        )
    if perf:
        kv_table(perf)

    monthly_chart = loc.get("monthly_chart") or []
    if monthly_chart:
        section("3. Monthly generation (kWh)")
        chart_note = "Estimated solar generation by month."
        if not (loc.get("chart_data") or {}).get("consumption", {}).get("monthly_available"):
            chart_note += " " + str(loc.get("monthly_consumption_note") or "")
        story.append(Paragraph(escape(chart_note), styles["Muted"]))
        story.append(compact_bar_chart(monthly_chart))
        legend = "<font color='#e59a2f'>■</font> Solar generation"
        if (loc.get("chart_data") or {}).get("consumption", {}).get("monthly_available"):
            legend += " &nbsp;&nbsp; <font color='#8e8391'>■</font> Recorded consumption"
        story.append(Paragraph(legend, styles["Muted"]))

    if loc.get("projection_chart"):
        projection_note = _("Validated saved cash-flow series; starts with net investment.")
        if loc.get("projection_break_even_year") is not None:
            projection_note += f" Estimated break-even: {loc['projection_break_even_year']} years."
        story.append(KeepTogether([
            Paragraph(_("25-year cumulative net position"), styles["H2Purple"]),
            Paragraph(projection_note, styles["Muted"]),
            compact_bar_chart(loc["projection_chart"], projection=True),
            Paragraph(
                _("Amber bars are below break-even; green bars are at or above break-even."),
                styles["Muted"],
            ),
        ]))

    # Reserve only enough room for the heading, disclaimer, table header and
    # two financial rows. The remainder of the table may split normally.
    story.append(CondPageBreak(38 * mm))
    section("4. Financial summary")
    from app.assumptions import MONETARY_DISCLAIMER

    story.append(Paragraph(MONETARY_DISCLAIMER, styles["Muted"]))
    fin = [
        (_("PV system cost (mid)"), _inr(loc.get("system_cost"))),
    ]
    if loc.get("system_cost_low") and loc.get("system_cost_high"):
        fin.append(
            (
                _("Typical cost band"),
                f"{_inr(loc['system_cost_low'])} – {_inr(loc['system_cost_high'])}",
            )
        )
    if loc.get("battery_cost_included") and loc.get("battery_cost_available"):
        battery_label = _("Battery storage")
        if (_finite_number(loc.get("battery_kwh")) or 0) > 0:
            battery_label += f" ({loc.get('battery_kwh')} kWh)"
        fin.append(
            (battery_label, _inr(loc.get("battery_cost")))
        )
    elif loc.get("battery_cost_included"):
        battery_label = _("Battery storage")
        if (_finite_number(loc.get("battery_kwh")) or 0) > 0:
            battery_label += f" ({loc.get('battery_kwh')} kWh)"
        fin.append((battery_label, _("Cost unavailable")))
    if loc.get("psg_guidance_allowed"):
        fin.append((
            _("PM Surya Ghar subsidy"),
            f"-{_inr(loc.get('subsidy_amount'))}"
            if loc.get("subsidy_value_available")
            else _("Amount unavailable"),
        ))
        from app.assumptions import SUBSIDY_CAVEAT, SUBSIDY_ELIGIBILITY_ASSUMPTION
        fin.append((SUBSIDY_CAVEAT, ""))
        fin.append((SUBSIDY_ELIGIBILITY_ASSUMPTION, ""))
    fin.append((_("Net investment"), _inr(loc.get("net_investment"))))
    if loc.get("costs_reconcile"):
        fin.append((_("Cost check"), loc.get("cost_reconciliation_equation")))
    elif loc.get("cost_reconciliation_note"):
        fin.append((_("Cost check"), loc["cost_reconciliation_note"]))
    if loc.get("planning_track") == "business":
        fin.extend([
            (_("Year-one net operating benefit"), _inr(loc.get("annual_net_benefit"))),
            (_("Year-one ROI"), f"{loc.get('annual_roi_pct'):.2f}%" if loc.get("annual_roi_pct") is not None else "—"),
            (_("25-year net benefit"), _inr(loc.get("lifetime_net_benefit"))),
            ("Demand-charge savings are not modelled", ""),
            (
                "Commercial payback (tariff escalation assumed)",
                f"{loc.get('nominal_payback_years')} years"
                if loc.get("nominal_payback_years") is not None
                else loc.get("payback_unavailable_reason"),
            ),
        ])
    else:
        fin.append((_("Estimated monthly savings"), _inr(loc.get("monthly_savings"))))
        fin.extend([
            (
                "Payback without subsidy (simple, flat tariff)",
                _("%(years)s years", years=loc.get("payback_without_subsidy_years"))
                if loc.get("payback_without_subsidy_years") is not None
                else loc.get("payback_unavailable_reason"),
            ),
            (
                "Subsidy-adjusted payback (simple, flat tariff)",
                _("%(years)s years", years=loc.get("subsidy_adjusted_payback_years"))
                if loc.get("subsidy_adjusted_payback_years") is not None
                else loc.get("payback_unavailable_reason"),
            ),
        ])
    lifecycle = loc.get("lifecycle_projection") or {}
    if lifecycle:
        fin.append((
            _("25-year net operating savings"),
            _inr(lifecycle.get("lifetime_net_savings_before_upfront")),
        ))
        fin.append((
            _("Discounted payback"),
            (
                f"{loc.get('discounted_payback_years')} years"
                if loc.get("discounted_payback_years") is not None
                else _("No break-even in 25 years")
            ),
        ))
        fin.append((
            f"Net present value ({loc.get('discount_rate_pct')}%)",
            _inr(loc.get("npv")),
        ))
    if loc.get("annual_om"):
        fin.append(("Indicative annual O&M", _inr(loc.get("annual_om"))))
    kv_table(fin, min_first_page_rows=2)

    inv = loc.get("inverter")
    if inv:
        section(f"5. Recommended inverter — {inv.get('type', '')}")
        best_for = str(inv.get("best_for") or "")
        if loc.get("planning_track") != "business" or "home" not in best_for.lower():
            story.append(
                Paragraph(
                    f"<b>Best for:</b> {escape(best_for)}", styles["BodySmall"]
                )
            )
        if loc.get("psg_guidance_allowed"):
            elig = (
                _("Eligible, subject to official verification")
                if inv.get("subsidy_eligible")
                else _("Not confirmed")
            )
            story.append(
                Paragraph(f"<b>Scheme eligibility:</b> {elig}", styles["BodySmall"])
            )
        for a in inv.get("advantages") or []:
            if loc.get("planning_track") == "business" and "subsid" in str(a).lower():
                continue
            story.append(Paragraph(f"• {escape(str(a))}", styles["Muted"]))

    section("6. Environmental impact")
    env_rows = [(_("CO₂ reduction / year"), f"{loc.get('co2_reduction_tons')} tons")]
    emissions = (loc.get("financial_provenance") or {}).get("environmental_factor") or {}
    if emissions:
        env_rows.append((
            "Grid emissions factor",
            f"{emissions.get('value')} {emissions.get('units')} (planning estimate)",
        ))
    eq = loc.get("environmental_equivalents") or {}
    if eq:
        env_rows.append((_("Equivalent trees planted"), f"~{eq.get('trees_planted')}"))
        env_rows.append(
            (
                _("Equivalent km not driven"),
                f"~{eq.get('km_not_driven'):,} km" if eq.get("km_not_driven") else "—",
            )
        )
        env_rows.append((_("Equivalent fuel saved"), f"~{eq.get('fuel_liters_saved')} L"))
    kv_table(env_rows)

    story.append(Spacer(1, 8))
    assumption_text = (
        "<b>Key assumptions:</b> Bill path ≈ ₹10/unit · ~135 units/month/kW "
        "(~4–5.5 units/day/kWp). "
    )
    if loc.get("psg_guidance_allowed"):
        assumption_text += (
            "PM Surya Ghar: ₹30,000/kW first 2 kW + ₹18,000 for 2–3 kW step "
            "· max ₹78,000. "
        )
    assumption_text += (
        (
            "Commercial operating-benefit assumptions are used; demand-charge savings "
            "are not modelled. "
            if loc.get("planning_track") == "business"
            else "Residential tariff and household-savings assumptions are used. "
        )
        + "~10 m² usable roof per kWp. 0.5%/yr degradation; annual O&amp;M is subtracted "
        "and discounted metrics use an explicit planning rate. Not a licensed design "
        "or DISCOM quote."
    )
    story.append(Paragraph(assumption_text, styles["Muted"]))

    notes = loc.get("planning_notes") or []
    if notes:
        story.append(Spacer(1, 6))
        story.append(
            Paragraph(_("<b>Important practical points</b>"), styles["BodySmall"])
        )
        for n in notes:
            if loc.get("planning_track") == "business" and (
                "subsid" in str(n).lower()
                or "surya ghar" in str(n).lower()
                or "residential" in str(n).lower()
            ):
                continue
            story.append(Paragraph(f"• {escape(str(n))}", styles["Muted"]))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#dddddd")))
    story.append(
        Paragraph(
            _("Generated by SIGNIX. Indicative planning estimates only. "
            "Confirm MNRE / National Rooftop Solar Portal / local DISCOM rules and vendor quotations before procurement."),
            styles["Footer"],
        )
    )

    doc.build(
        story,
        onFirstPage=_draw_reportlab_footer,
        onLaterPages=_draw_reportlab_footer,
    )
    buffer.seek(0)
    return buffer


def _generate_pdf_reportlab_summary(loc, locale=None):
    """Friendly 2-page homeowner summary (ReportLab fallback)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        HRFlowable,
        PageBreak,
    )
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from xml.sax.saxutils import escape

    try:
        from app.assumptions import (
            MONETARY_DISCLAIMER,
            SUBSIDY_CAVEAT,
            SUBSIDY_ELIGIBILITY_ASSUMPTION,
        )
    except Exception:
        MONETARY_DISCLAIMER = "Indicative only — verify with DISCOM / vendor"
        SUBSIDY_CAVEAT = _("Central subsidy estimate — final amount subject to MNRE / DISCOM approval")
        SUBSIDY_ELIGIBILITY_ASSUMPTION = _("Assumes residential, grid-connected system")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
    )

    font_name, font_bold = _register_pdf_fonts()

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SumEyebrow", fontName=font_bold, fontSize=10,
        textColor=HexColor("#c47a0b"), spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SumTitle", fontName=font_bold, fontSize=22,
        textColor=HexColor("#3d2570"), leading=27, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="SumBody", fontName=font_name, fontSize=11,
        textColor=HexColor("#333333"), leading=14, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SumMuted", fontName=font_name, fontSize=9,
        textColor=HexColor("#777777"), leading=12, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SumH2", fontName=font_bold, fontSize=13,
        textColor=HexColor("#3d2570"), spaceBefore=12, spaceAfter=6,
        keepWithNext=True,
    ))
    styles.add(ParagraphStyle(
        name="SumBig", fontName=font_bold, fontSize=20,
        textColor=HexColor("#3d2570"), leading=24, alignment=1, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="SumCardLabel", fontName=font_bold, fontSize=9,
        textColor=HexColor("#666666"), leading=11, alignment=1, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SumCardUnit", fontName=font_name, fontSize=9,
        textColor=HexColor("#666666"), leading=11, alignment=1,
    ))
    styles.add(ParagraphStyle(
        name="SumVerdict", fontName=font_bold, fontSize=9.5,
        textColor=HexColor("#2a1733"), leading=13, leftIndent=8,
        borderColor=HexColor("#e59a2f"), borderWidth=0, borderPadding=7,
        backColor=HexColor("#f7f3ea"), spaceBefore=7, spaceAfter=8,
    ))

    extras = loc.get("extras") or {}
    nominal = (
        loc.get("nominal_payback_years")
        if loc.get("nominal_payback_years") is not None
        else extras.get("nominal_payback_years")
    )
    if nominal is None:
        nominal = loc.get("payback_years")
    simple = (
        loc.get("simple_payback_years")
        if loc.get("simple_payback_years") is not None
        else extras.get("simple_payback_years")
    )
    rating = loc.get("suitability_rating") or {}
    viability = {}
    if isinstance(rating, dict):
        viability = (
            rating.get("overall_viability")
            or rating.get("viability")
            or {}
        )
        if not isinstance(viability, dict):
            viability = {}
    tier = viability.get("tier") or "fair"
    score = viability.get("score")
    if score is None and isinstance(rating, dict):
        score = rating.get("overall_score")

    def card(label, value, unit):
        return [
            Paragraph(label, styles["SumCardLabel"]),
            Paragraph(str(value), styles["SumBig"]),
            Paragraph(unit, styles["SumCardUnit"]),
        ]

    def monthly_chart_drawing(rows):
        width, height = 168 * mm, 54 * mm
        chart = Drawing(width, height)
        base_y, plot_height = 12 * mm, 34 * mm
        chart.add(Line(7 * mm, base_y, width - 3 * mm, base_y, strokeColor=HexColor("#cfc5b8"), strokeWidth=0.5))
        column = (width - 12 * mm) / max(len(rows), 1)
        for index, item in enumerate(rows):
            x = 8 * mm + index * column
            generation_height = plot_height * float(item.get("generation_pct") or 0) / 100
            chart.add(Rect(x, base_y, 2.4 * mm, max(generation_height, 1.2 * mm), fillColor=HexColor("#e59a2f"), strokeColor=None))
            if item.get("consumption_pct") is not None:
                load_height = plot_height * float(item.get("consumption_pct") or 0) / 100
                chart.add(Rect(x + 2.8 * mm, base_y, 2.4 * mm, max(load_height, 1.2 * mm), fillColor=HexColor("#8e8391"), strokeColor=None))
            chart.add(String(x, 7 * mm, str(item.get("month") or "")[:3], fontName=font_name, fontSize=6.5, fillColor=HexColor("#6b646c")))
        return chart

    def projection_chart_drawing(rows):
        width, height = 168 * mm, 50 * mm
        chart = Drawing(width, height)
        base_y, plot_height = 13 * mm, 27 * mm
        chart.add(Line(7 * mm, base_y, width - 3 * mm, base_y, strokeColor=HexColor("#cfc5b8"), strokeWidth=0.5))
        column = (width - 16 * mm) / max(len(rows), 1)
        for index, item in enumerate(rows):
            x = 10 * mm + index * column
            bar_height = plot_height * float(item.get("magnitude_pct") or 0) / 100
            color = HexColor("#2f6b53") if item.get("positive") else HexColor("#c89139")
            chart.add(Rect(x, base_y, 8 * mm, max(bar_height, 1.2 * mm), fillColor=color, strokeColor=None))
            label = _("Start") if item.get("year") == 0 else _("Year %(year)s", year=item.get("year"))
            chart.add(String(x, 7 * mm, label, fontName=font_name, fontSize=7, fillColor=HexColor("#6b646c")))
        return chart

    def cost_comparison_drawing(gross, net):
        width, height = 168 * mm, 21 * mm
        chart = Drawing(width, height)
        gross_value = max(float(gross or 0), 1.0)
        net_value = max(float(net or 0), 0.0)
        chart.add(String(0, 15 * mm, "Gross displayed cost", fontName=font_name, fontSize=7.5, fillColor=HexColor("#6b646c")))
        chart.add(Rect(38 * mm, 14 * mm, 105 * mm, 3 * mm, fillColor=HexColor("#e59a2f"), strokeColor=None))
        chart.add(String(146 * mm, 14.5 * mm, _inr(gross_value), fontName=font_bold, fontSize=7.5, fillColor=HexColor("#2a1733")))
        chart.add(String(0, 6 * mm, _("Net investment"), fontName=font_name, fontSize=7.5, fillColor=HexColor("#6b646c")))
        chart.add(Rect(38 * mm, 5 * mm, 105 * mm * min(net_value / gross_value, 1.0), 3 * mm, fillColor=HexColor("#2a1733"), strokeColor=None))
        chart.add(String(146 * mm, 5.5 * mm, _inr(net_value), fontName=font_bold, fontSize=7.5, fillColor=HexColor("#2a1733")))
        return chart

    story = []
    brand_logo = loc.get("_brand_logo_bytes")
    if brand_logo:
        from reportlab.platypus import Image as RLImage

        logo = RLImage(io.BytesIO(brand_logo))
        logo._restrictSize(42 * mm, 15 * mm)
        story.extend([logo, Spacer(1, 3 * mm)])
    story.append(Paragraph(
        "BUSINESS SOLAR SUMMARY"
        if loc.get("planning_track") == "business"
        else "HOMEOWNER SUMMARY",
        styles["SumEyebrow"],
    ))
    story.append(Paragraph(
        _("Your commercial rooftop solar plan")
        if loc.get("planning_track") == "business"
        else _("Your residential rooftop solar plan"),
        styles["SumTitle"],
    ))
    story.append(Paragraph(
        escape(str(
            (
                f"{loc.get('location_label')}: {loc.get('location_display')}"
                if loc.get("address_is_fallback")
                else loc.get("location_display")
            )
            or _("Location not provided")
        )),
        styles["SumBody"],
    ))
    meta_rows = [
        [_("Customer"), _("Estimate reference"), _("Report date"), _("Planning track")],
        [
            escape(str(loc.get("customer_name") or _("Not provided"))),
            escape(str(loc.get("estimate_reference") or _("Not assigned"))),
            escape(str(loc.get("report_date") or _("Not available"))),
            _("Commercial") if loc.get("planning_track") == "business" else _("Residential"),
        ],
    ]
    meta = Table(meta_rows, colWidths=[42 * mm] * 4)
    meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#faf8ff")),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#3d2570")),
        ("FONTNAME", (0, 0), (-1, 0), font_bold),
        ("FONTNAME", (0, 1), (-1, 1), font_name),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("FONTSIZE", (0, 1), (-1, 1), 8),
        ("BOX", (0, 0), (-1, -1), 0.4, HexColor("#e0d6f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta)
    story.append(Paragraph(
        escape(str(loc.get("report_verdict") or "Indicative rooftop solar planning estimate.")),
        styles["SumVerdict"],
    ))
    story.append(Spacer(1, 8))
    map_bytes = loc.get("_map_png_bytes")

    hero_data = [[
        card(
            _("Recommended system"),
            f"{float(loc.get('system_size') or 0):.1f}",
            (
                f"kW · approx. {int(loc['panel_count'])} panels"
                if loc.get("panel_count")
                else "kW"
            ),
        ),
        card(_("Yearly generation"), f"{float(loc.get('annual_generation') or 0):,.0f}", _("kWh / year")),
        card(
            _("Annual net benefit") if loc.get("planning_track") == "business" else _("Monthly savings"),
            _inr((loc.get("annual_net_benefit") if loc.get("planning_track") == "business" else loc.get("monthly_savings")) or 0),
            _("indicative"),
        ),
        card(
            _("Commercial payback") if loc.get("planning_track") == "business" else _("Nominal payback"),
            loc.get("headline_payback_years") if loc.get("headline_payback_years") is not None else "—",
            _("years") if loc.get("headline_payback_years") is not None else _("unavailable"),
        ),
    ]]
    hero = Table(hero_data, colWidths=[42 * mm] * 4)
    hero.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f7f4fc")),
        ("BOX", (0, 0), (0, 0), 0.5, HexColor("#e4daf5")),
        ("BOX", (1, 0), (1, 0), 0.5, HexColor("#e4daf5")),
        ("BOX", (2, 0), (2, 0), 0.5, HexColor("#e4daf5")),
        ("BOX", (3, 0), (3, 0), 0.5, HexColor("#e4daf5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(hero)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#dddddd")))
    story.append(Paragraph(_("What it costs"), styles["SumH2"]))
    cost_rows = [[_("PV system cost (mid)"), _inr(loc.get("system_cost") or 0)]]
    if loc.get("battery_cost_included") and loc.get("battery_cost_available"):
        battery_label = _("Battery storage")
        if (_finite_number(loc.get("battery_kwh")) or 0) > 0:
            battery_label += f" ({float(loc.get('battery_kwh')):.1f} kWh)"
        cost_rows.append([
            battery_label,
            _inr(loc.get("battery_cost")),
        ])
    elif loc.get("battery_cost_included"):
        battery_label = _("Battery storage")
        if (_finite_number(loc.get("battery_kwh")) or 0) > 0:
            battery_label += f" ({float(loc.get('battery_kwh')):.1f} kWh)"
        cost_rows.append([
            battery_label,
            _("Cost unavailable"),
        ])
    if loc.get("psg_guidance_allowed"):
        cost_rows.append(
            [
                _("Central subsidy (estimate)"),
                f"-{_inr(loc.get('subsidy_amount'))}"
                if loc.get("subsidy_value_available")
                else _("Amount unavailable"),
            ]
        )
    cost_rows.append([_("Net investment"), _inr(loc.get("net_investment") or 0)])
    if loc.get("costs_reconcile"):
        cost_rows.append([_("Cost check"), loc.get("cost_reconciliation_equation")])
    elif loc.get("cost_reconciliation_note"):
        cost_rows.append([_("Cost check"), loc["cost_reconciliation_note"]])
    ct = Table(cost_rows, colWidths=[100 * mm, 70 * mm])
    ct.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), font_name),
        ("FONTNAME", (1, 0), (1, -1), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(ct)
    gross_displayed_cost = _finite_number(loc.get("system_cost")) or 0
    if loc.get("battery_cost_included") and loc.get("battery_cost_available"):
        gross_displayed_cost += _finite_number(loc.get("battery_cost")) or 0
    if gross_displayed_cost > 0 and _finite_number(loc.get("net_investment")) is not None:
        story.append(cost_comparison_drawing(gross_displayed_cost, loc.get("net_investment")))
    if loc.get("psg_guidance_allowed"):
        story.append(Paragraph(f"{SUBSIDY_CAVEAT} · {SUBSIDY_ELIGIBILITY_ASSUMPTION}", styles["SumMuted"]))

    story.append(Paragraph(_("When it pays back"), styles["SumH2"]))
    if loc.get("planning_track") == "business":
        pb_rows = [
            [_("Year-one net operating benefit"), _inr(loc.get("annual_net_benefit") or 0)],
            [_("Year-one ROI"), f"{loc.get('annual_roi_pct'):.2f}%" if loc.get("annual_roi_pct") is not None else "—"],
            [_("25-year net benefit"), _inr(loc.get("lifetime_net_benefit"))],
            [
                "Commercial payback (tariff escalation assumed)",
                _("%(years)s years", years=nominal) if nominal is not None else loc.get("payback_unavailable_reason"),
            ],
            ["Demand-charge savings", "Not modelled"],
        ]
    else:
        pb_rows = [
            ["Nominal payback (tariff may rise)", _("%(years)s years", years=nominal) if nominal is not None else loc.get("payback_unavailable_reason")],
            [
                "Payback without subsidy (simple, flat tariff)",
                _("%(years)s years", years=loc.get("payback_without_subsidy_years"))
                if loc.get("payback_without_subsidy_years") is not None
                else loc.get("payback_unavailable_reason"),
            ],
            [
                "Subsidy-adjusted payback (simple, flat tariff)",
                _("%(years)s years", years=loc.get("subsidy_adjusted_payback_years"))
                if loc.get("subsidy_adjusted_payback_years") is not None
                else loc.get("payback_unavailable_reason"),
            ],
        ]
    if loc.get("discounted_payback_years") is not None:
        pb_rows.append([_("Discounted payback"), _("%(years)s years", years=loc["discounted_payback_years"])])
    elif loc.get("lifecycle_projection"):
        pb_rows.append([_("Discounted payback"), _("No break-even in 25 years")])
    pt = Table(pb_rows, colWidths=[100 * mm, 70 * mm])
    pt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), font_name),
        ("FONTNAME", (1, 0), (1, -1), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(pt)

    story.append(Paragraph(_("How suitable is this roof?"), styles["SumH2"]))
    suit_text = str(tier).replace("_", " ").title()
    if score is not None:
        suit_text += f"  ·  Score {score}/100"
    story.append(Paragraph(suit_text, styles["SumBody"]))
    qr_bytes = loc.get("_qr_png_bytes")
    story.append(Spacer(1, 6))
    story.append(Paragraph(MONETARY_DISCLAIMER, styles["SumMuted"]))
    story.append(Paragraph(
        _("Business planning summary — not a formal quote")
        if loc.get("planning_track") == "business"
        else _("Summary for homeowners — not a formal quote"),
        styles["SumMuted"],
    ))

    if loc.get("monthly_chart") or loc.get("projection_chart"):
        story.append(PageBreak())
        story.append(Paragraph(_("ENERGY AND VALUE OVER TIME"), styles["SumEyebrow"]))
        story.append(Paragraph(_("How the estimate develops"), styles["SumTitle"]))
        if loc.get("monthly_chart"):
            story.append(Paragraph(_("Monthly solar generation"), styles["SumH2"]))
            consumption_note = ""
            if not (loc.get("chart_data") or {}).get("consumption", {}).get("monthly_available"):
                consumption_note = " " + str(loc.get("monthly_consumption_note") or "")
            story.append(Paragraph(
                _("Estimated generation by month in kWh.") + escape(consumption_note),
                styles["SumMuted"],
            ))
            story.append(monthly_chart_drawing(loc["monthly_chart"]))
            legend = "<font color='#e59a2f'>■</font> Solar generation"
            if (loc.get("chart_data") or {}).get("consumption", {}).get("monthly_available"):
                legend += " &nbsp;&nbsp; <font color='#8e8391'>■</font> Recorded consumption"
            story.append(Paragraph(legend, styles["SumMuted"]))
        if loc.get("projection_chart"):
            story.append(Paragraph(_("25-year cumulative net position"), styles["SumH2"]))
            break_even = loc.get("projection_break_even_year")
            projection_note = "Starts with saved net investment and uses the validated annual cash-flow series."
            if break_even is not None:
                projection_note += f" Estimated break-even: {break_even} years."
            story.append(Paragraph(projection_note, styles["SumMuted"]))
            story.append(projection_chart_drawing(loc["projection_chart"]))
            story.append(Paragraph(
                _("Amber bars are below break-even; green bars are at or above break-even."),
                styles["SumMuted"],
            ))
        sensitivity_rows = loc.get("sensitivity_rows") or []
        if sensitivity_rows:
            story.append(Paragraph(_("Sensitivity (indicative)"), styles["SumH2"]))
            sensitivity_data = [[_("Driver"), "Downside", "Base", "Upside"]]
            for row in sensitivity_rows:
                sensitivity_data.append([
                    str(row.get("driver") or ""),
                    str((row.get("low") or {}).get("simple_payback_years") or "—"),
                    str((row.get("base") or {}).get("simple_payback_years") or "—"),
                    str((row.get("high") or {}).get("simple_payback_years") or "—"),
                ])
            sensitivity_table = Table(sensitivity_data, colWidths=[75 * mm, 31 * mm, 31 * mm, 31 * mm], repeatRows=1)
            sensitivity_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#f7f3ea")),
                ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#2a1733")),
                ("FONTNAME", (0, 0), (-1, 0), font_bold),
                ("FONTNAME", (0, 1), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, HexColor("#e6ded2")),
            ]))
            story.append(sensitivity_table)

    story.append(PageBreak())
    story.append(Paragraph(_("NEXT STEPS"), styles["SumEyebrow"]))
    story.append(Paragraph(_("Before you decide"), styles["SumTitle"]))
    story.append(Paragraph(
        f"{escape(str(loc.get('location_label') or _('Location')))}: "
        f"{escape(str(loc.get('location_display') or _('Location not provided')))}",
        styles["SumBody"],
    ))
    if loc.get("coordinates_display") and not loc.get("address_is_fallback"):
        story.append(Paragraph(
            _("Coordinates: %(coordinates)s", coordinates=escape(str(loc["coordinates_display"]))), styles["SumBody"]
        ))
    if loc.get("orientation_label"):
        story.append(Paragraph(
            _("Detected roof orientation: %(orientation)s", orientation=escape(str(loc.get("orientation_label")))),
            styles["SumBody"],
        ))
    if float(loc.get("battery_kwh") or 0) > 0:
        story.append(Paragraph(_("Battery included: %(capacity)s kWh", capacity=f"{float(loc.get('battery_kwh')):.1f}"), styles["SumBody"]))
    if loc.get("psg_guidance_allowed"):
        subsidy_text = (
            _inr(loc.get("subsidy_amount"))
            if loc.get("subsidy_value_available")
            else _("Amount unavailable")
        )
        story.append(Paragraph(
            _("Subsidy (estimate): %(amount)s", amount=subsidy_text), styles["SumBody"]
        ))
    story.append(Paragraph(_("Suggested next steps"), styles["SumH2"]))
    next_steps = [
        (
            _("Share this summary with site decision-makers.")
            if loc.get("planning_track") == "business"
            else _("Share this summary with your family or building society.")
        )
    ]
    if loc.get("psg_guidance_allowed"):
        next_steps.append(_("Confirm subsidy eligibility with your DISCOM / vendor."))
    next_steps.extend([
        _("Ask installers for a site survey and a firm quote."),
        _("Download the full technical report if you need detailed assumptions."),
    ])
    for index, line in enumerate(next_steps, 1):
        story.append(Paragraph(f"{index}. {line}", styles["SumBody"]))
    story.append(Paragraph(_("Planning assumptions"), styles["SumH2"]))
    track_assumption = (
        _("Commercial operating-benefit assumptions are used. Demand-charge savings are not modelled.")
        if loc.get("planning_track") == "business"
        else _("Residential tariff, household-savings and eligibility-dependent subsidy assumptions are used.")
    )
    story.append(Paragraph(
        track_assumption + " " + _("Confirm current DISCOM rules, vendor pricing and site conditions before procurement."),
        styles["SumMuted"],
    ))
    if loc.get("assumption_rows"):
        assumption_table = Table(
            [[ui_label(item["label"]), item["value"]] for item in loc["assumption_rows"]],
            colWidths=[70 * mm, 98 * mm],
        )
        assumption_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), font_bold),
            ("FONTNAME", (1, 0), (1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, HexColor("#e6ded2")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(assumption_table)
    for note in loc.get("source_notes") or []:
        story.append(Paragraph(escape(str(note)), styles["SumMuted"]))
    if map_bytes:
        from reportlab.platypus import Image as RLImage

        story.append(Paragraph(_("Rooftop reference"), styles["SumH2"]))
        map_image = RLImage(io.BytesIO(map_bytes))
        map_image._restrictSize(160 * mm, 38 * mm)
        story.append(map_image)
        story.append(Paragraph(
            _("Rooftop outline (drawn boundary; amber = obstructions)"),
            styles["SumMuted"],
        ))
    story.append(Paragraph(_("What this estimate excludes"), styles["SumH2"]))
    exclusions = [
        "Final structural, electrical and fire-safety design.",
        "A binding vendor quotation or approval from a DISCOM or subsidy authority.",
    ]
    if loc.get("planning_track") == "business":
        exclusions.append("Demand-charge savings, unless separately modelled outside this estimate.")
    for line in exclusions:
        story.append(Paragraph(f"• {escape(line)}", styles["SumMuted"]))
    story.append(Paragraph(_("Glossary"), styles["SumH2"]))
    glossary = Table(
        [
            [_("Net investment"), "Displayed project cost after the adjustments shown in this estimate."],
            [_("Nominal payback"), "Break-even using modelled annual cash flows and stated escalation assumptions."],
            [_("Discounted payback"), _("Break-even after discounting future cash flows at the stated planning rate.")],
            ["kWp", "Rated DC capacity of the solar array under standard test conditions."],
        ],
        colWidths=[45 * mm, 123 * mm],
    )
    glossary.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), font_bold),
        ("FONTNAME", (1, 0), (1, -1), font_name),
        ("TEXTCOLOR", (0, 0), (0, -1), HexColor("#2a1733")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, HexColor("#e6ded2")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(glossary)
    if qr_bytes:
        from reportlab.platypus import Image as RLImage

        qr_buf = io.BytesIO(qr_bytes)
        story.append(Spacer(1, 8))
        story.append(RLImage(qr_buf, width=22 * mm, height=22 * mm))
        story.append(Paragraph(
            f"Scan to open live estimate: {escape(str(loc.get('live_estimate_url') or ''))}",
            styles["SumMuted"],
        ))
    story.append(Spacer(1, 12))
    story.append(Paragraph(MONETARY_DISCLAIMER, styles["SumMuted"]))
    if loc.get("psg_guidance_allowed"):
        story.append(Paragraph(SUBSIDY_CAVEAT, styles["SumMuted"]))
    story.append(Paragraph(_("Indicative planning aid only"), styles["SumMuted"]))

    doc.build(
        story,
        onFirstPage=_draw_reportlab_footer,
        onLaterPages=_draw_reportlab_footer,
    )
    buffer.seek(0)
    return buffer

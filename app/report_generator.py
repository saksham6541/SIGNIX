# filename: app/report_generator.py
"""PDF report generation.

Primary path: WeasyPrint (HTML → PDF).
Fallback: ReportLab pure-Python PDF if WeasyPrint or its system libs are missing.
Both paths receive the same enriched location dict as the HTML report page.
"""

import io
import time
from pathlib import Path
from flask import current_app, render_template
from flask_babel import get_locale, gettext as _


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


def _enrich_location_data(location):
    """Build the same dict the HTML report uses (extras + environmental)."""
    from app.solar_logic import calculate_environmental_equivalents

    data = location.to_dict() if hasattr(location, "to_dict") else dict(location)
    extras = data.get("extras") or {}
    property_type = str(extras.get("property_type") or "").strip().lower()
    inverter_type = str(extras.get("inverter_type") or "").strip().lower().replace(" ", "-")
    data["psg_guidance_allowed"] = property_type == "residential" and (
        inverter_type in ("on-grid", "hybrid")
        or float(data.get("subsidy_amount") or 0) > 0
    )

    data["environmental_equivalents"] = extras.get(
        "environmental_equivalents"
    ) or calculate_environmental_equivalents(data.get("co2_reduction_tons") or 0)
    if not data.get("payback_years") and data.get("monthly_savings"):
        try:
            data["payback_years"] = round(
                data["net_investment"] / (data["monthly_savings"] * 12), 1
            )
        except Exception:
            data["payback_years"] = None

    for key in (
        "inverter",
        "inverter_type",
        "bill_sizing",
        "roof_capacity_kw",
        "sizing_method",
        "rule_of_thumb_monthly_units",
        "rule_of_thumb_annual_units",
        "property_type",
        "needs_backup",
        "planning_notes",
        "system_cost_low",
        "system_cost_high",
        "annual_om",
        "performance_ratio",
        "peak_sun_hours_assumed",
        "daily_units_per_kw_range",
        "dc_note",
        "cost_per_kw",
        "cashflow_25yr",
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
    ):
        if key in extras and extras[key] is not None and not data.get(key):
            data[key] = extras[key]

    # monthly_data may be None on old rows
    if not data.get("monthly_data"):
        data["monthly_data"] = {}

    return data



def _pdf_assets(location, data, mode="full"):
    """Build map SVG / PNG and QR PNG for PDF embedding. Timed for perf flags."""
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

    base_url = None
    if has_request_context():
        try:
            base_url = request.url_root.rstrip("/")
        except Exception:
            base_url = None
    if not base_url:
        import os
        base_url = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/") or None

    live_url = estimate_live_url(loc_id or 0, base_url) if loc_id else (base_url or "/")

    poly = data.get("polygon_geojson")
    obs = data.get("obstructions_geojson")
    if poly is None and hasattr(location, "polygon_geojson"):
        poly = location.polygon_geojson
        obs = getattr(location, "obstructions_geojson", None)

    svg = render_rooftop_svg(poly, obs)
    png = render_rooftop_png(poly, obs) or (svg_to_png_bytes(svg) if svg else None)
    qr = make_qr_png(live_url)

    svg_b64 = None
    png_b64 = None
    qr_b64 = None
    if svg:
        svg_b64 = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
    if png:
        png_b64 = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    if qr:
        qr_b64 = "data:image/png;base64," + base64.b64encode(qr).decode("ascii")

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
        "live_estimate_url": live_url,
        "assets_elapsed_ms": elapsed_ms,
    }


def generate_pdf_report(location, locale=None, mode="full"):
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
    )
    from reportlab.graphics.shapes import Drawing, Rect

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
        bottomMargin=14 * mm,
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

    story.append(Paragraph(_("Rooftop Solar Estimation Report"), styles["TitleOrange"]))
    story.append(
        Paragraph(str(loc.get("address") or "Selected location"), styles["Muted"])
    )
    map_bytes = loc.get("_map_png_bytes")
    if map_bytes:
        from reportlab.platypus import Image as RLImage
        story.append(Spacer(1, 4))
        story.append(RLImage(io.BytesIO(map_bytes), width=160 * mm, height=98 * mm))
        story.append(Paragraph(
            "Rooftop outline (purple boundary; amber patches = obstructions)",
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
        badges.append(f"Orientation: {loc['orientation_label']}")
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

    def kv_table(rows):
        data = [
            [
                Paragraph(_("Item"), styles["BodySmall"]),
                Paragraph(_("Value"), styles["BodySmall"]),
            ]
        ] + [
            [
                Paragraph(str(a), styles["BodySmall"]),
                Paragraph(str(b), styles["BodySmall"]),
            ]
            for a, b in rows
        ]
        t = Table(data, colWidths=[95 * mm, 75 * mm])
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
        drawing = Drawing(88 * mm, 5 * mm)
        drawing.add(
            Rect(
                0,
                1.5 * mm,
                88 * mm,
                2 * mm,
                fillColor=HexColor("#eee8f7"),
                strokeColor=None,
            )
        )
        drawing.add(
            Rect(
                0,
                1.5 * mm,
                88 * mm * score / 100,
                2 * mm,
                fillColor=colors.get(tier, colors["fair"]),
                strokeColor=None,
            )
        )
        return drawing

    # Overview cards
    section("1. System overview")
    cards = [
        ["System size", f"{loc.get('system_size', '—')} kW"],
        ["Annual generation", f"{loc.get('annual_generation', '—')} kWh"],
        ["Monthly savings", _inr(loc.get("monthly_savings"))],
        [
            "Payback",
            f"{loc.get('payback_years')} yrs" if loc.get("payback_years") else "—",
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
            ("Total roof area", f"{loc.get('roof_area_sqm', '—')} m²"),
            ("Obstructed area", f"{loc.get('obstructed_area_sqm') or 0} m²"),
            ("Usable area", f"{loc.get('usable_area_sqm', '—')} m²"),
            (
                "Roof capacity",
                f"{loc.get('roof_capacity_kw') or loc.get('system_size')} kW",
            ),
            ("Sizing method", loc.get("sizing_method") or "roof_area"),
        ]
    )

    bs = loc.get("bill_sizing")
    if bs:
        section("Bill-based sizing")
        kv_table(
            [
                ("Monthly bill (input)", _inr(bs.get("monthly_bill"))),
                ("Tariff used", f"{_inr_prefix()}{bs.get('tariff_per_kwh')}/unit"),
                ("Estimated monthly units", f"{bs.get('monthly_units')} units"),
                (
                    "Recommended range",
                    f"{bs.get('recommended_min_kw')}–{bs.get('recommended_max_kw')} kW",
                ),
                ("Design size used", f"{loc.get('system_size')} kW"),
            ]
        )

    rating = loc.get("suitability_rating")
    if rating:
        section("Suitability rating")
        viability = rating.get("overall_viability") or {}
        confidence = rating.get("data_confidence") or {}
        conf_tier = irr_conf or confidence.get("tier") or "Unknown"
        conf_score = confidence.get("score", 0)
        rating_summary = [
            [
                "Overall viability",
                f"{str(viability.get('tier') or 'Unknown').title()} — {viability.get('score', 0)}/100",
            ],
            [
                "Estimate confidence",
                f"{str(conf_tier).title()} ({conf_score}/100)",
            ],
        ]
        if rating.get("user_priority"):
            rating_summary.append(
                ["Priority", str(rating["user_priority"]).replace("_", " ").title()]
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
            "payback": "Payback",
            "financial_viability": "Financial viability",
            "roof_fit": "Roof fit",
            "orientation": "Orientation",
            "co2_reduction": "CO2 reduction",
            "backup_capability": "Backup capability",
        }
        factor_rows = [["Factor", "Score", "Tier / detail"]]
        for key, label in factor_labels.items():
            factor = (rating.get("factors") or {}).get(key)
            if not factor:
                continue
            if key == "roof_fit":
                detail = f"{factor.get('usable_area_per_kw')} m² usable area per kW"
            elif key == "orientation":
                detail = f"{factor.get('factor')} relative yield factor"
            elif key == "co2_reduction":
                detail = f"{factor.get('tons')} tons CO2 reduction per year"
            elif key == "backup_capability":
                detail = f"{factor.get('score', 0)}/100 backup capability"
            else:
                detail = str(factor.get("tier") or "Unavailable").title()
            tier = str(factor.get("tier") or "fair").lower()
            factor_rows.append(
                [
                    label,
                    [
                        rating_bar(factor.get("score", 0), tier),
                        Paragraph(f"{factor.get('score', 0)}/100", styles["Muted"]),
                    ],
                    f"{str(factor.get('tier') or 'Unavailable').title()} — {detail}",
                ]
            )
        if len(factor_rows) > 1:
            factor_table = Table(factor_rows, colWidths=[35 * mm, 55 * mm, 80 * mm])
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
        perf.append(("Specific yield", f"{loc['specific_yield']:,.0f} kWh/kWp/year"))
    if loc.get("capacity_factor") is not None:
        perf.append(("Capacity factor", f"{loc['capacity_factor']}%"))
    if loc.get("orientation_factor") is not None:
        perf.append(
            (
                "Orientation factor vs South",
                f"{round(loc['orientation_factor'] * 100)}%",
            )
        )
    if loc.get("recommended_tilt_deg") is not None:
        perf.append(("Recommended tilt", f"{loc['recommended_tilt_deg']}°"))
    if loc.get("performance_ratio") is not None:
        perf.append(("Performance ratio (assumed)", str(loc["performance_ratio"])))
    if loc.get("peak_sun_hours_assumed") is not None:
        perf.append(
            ("Peak sun hours (assumed)", f"{loc['peak_sun_hours_assumed']} h/day")
        )
    if loc.get("lcoe") is not None:
        perf.append(("Discounted LCOE (25 yr)", f"{_inr_prefix()}{loc['lcoe']}/kWh"))
    if loc.get("lifetime_kwh"):
        perf.append(("Lifetime energy (25 yr)", f"{loc['lifetime_kwh']:,.0f} kWh"))
    if loc.get("self_consumption_frac") is not None:
        perf.append(
            ("Self-consumption share", f"{int(loc['self_consumption_frac'] * 100)}%")
        )
    if perf:
        kv_table(perf)

    monthly = loc.get("monthly_data") or {}
    if monthly:
        section("3. Monthly generation (kWh)")
        months = list(monthly.keys())
        vals = [str(monthly[m]) for m in months]
        # split into two rows of 6 if 12 months
        if len(months) == 12:
            t1 = Table([months[:6], vals[:6]], colWidths=[28 * mm] * 6)
            t2 = Table([months[6:], vals[6:]], colWidths=[28 * mm] * 6)
            for t in (t1, t2):
                t.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), light),
                            ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("GRID", (0, 0), (-1, -1), 0.3, HexColor("#eeeeee")),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ]
                    )
                )
            story.append(t1)
            story.append(t2)
        else:
            kv_table(list(zip(months, vals)))

    section("4. Financial summary")
    from app.assumptions import MONETARY_DISCLAIMER

    story.append(Paragraph(MONETARY_DISCLAIMER, styles["Muted"]))
    story.append(Spacer(1, 4))
    fin = [
        ("PV system cost (mid)", _inr(loc.get("system_cost"))),
    ]
    if loc.get("system_cost_low") and loc.get("system_cost_high"):
        fin.append(
            (
                "Typical cost band",
                f"{_inr(loc['system_cost_low'])} – {_inr(loc['system_cost_high'])}",
            )
        )
    if loc.get("battery_cost"):
        fin.append(
            (f"Battery ({loc.get('battery_kwh')} kWh)", _inr(loc.get("battery_cost")))
        )
    if loc.get("psg_guidance_allowed"):
        fin.append(("PM Surya Ghar subsidy", f"-{_inr(loc.get('subsidy_amount'))}"))
        from app.assumptions import SUBSIDY_CAVEAT, SUBSIDY_ELIGIBILITY_ASSUMPTION
        fin.append((SUBSIDY_CAVEAT, ""))
        fin.append((SUBSIDY_ELIGIBILITY_ASSUMPTION, ""))
    fin.append(("Net investment", _inr(loc.get("net_investment"))))
    fin.append(("Estimated monthly savings", _inr(loc.get("monthly_savings"))))
    lifecycle = loc.get("lifecycle_projection") or {}
    if lifecycle:
        fin.append((
            "25-year net operating savings",
            _inr(lifecycle.get("lifetime_net_savings_before_upfront")),
        ))
        fin.append((
            "Discounted payback",
            (
                f"{loc.get('discounted_payback_years')} years"
                if loc.get("discounted_payback_years") is not None
                else "No break-even in 25 years"
            ),
        ))
        fin.append((
            f"Net present value ({loc.get('discount_rate_pct')}%)",
            _inr(loc.get("npv")),
        ))
    if loc.get("annual_om"):
        fin.append(("Indicative annual O&M", _inr(loc.get("annual_om"))))
    kv_table(fin)

    inv = loc.get("inverter")
    if inv:
        section(f"5. Recommended inverter — {inv.get('type', '')}")
        story.append(
            Paragraph(
                f"<b>Best for:</b> {inv.get('best_for', '')}", styles["BodySmall"]
            )
        )
        if loc.get("psg_guidance_allowed"):
            elig = (
                "Eligible, subject to official verification"
                if inv.get("subsidy_eligible")
                else "Not confirmed"
            )
            story.append(
                Paragraph(f"<b>Scheme eligibility:</b> {elig}", styles["BodySmall"])
            )
        for a in inv.get("advantages") or []:
            story.append(Paragraph(f"• {a}", styles["Muted"]))

    section("6. Environmental impact")
    env_rows = [("CO₂ reduction / year", f"{loc.get('co2_reduction_tons')} tons")]
    emissions = (loc.get("financial_provenance") or {}).get("environmental_factor") or {}
    if emissions:
        env_rows.append((
            "Grid emissions factor",
            f"{emissions.get('value')} {emissions.get('units')} (planning estimate)",
        ))
    eq = loc.get("environmental_equivalents") or {}
    if eq:
        env_rows.append(("Equivalent trees planted", f"~{eq.get('trees_planted')}"))
        env_rows.append(
            (
                "Equivalent km not driven",
                f"~{eq.get('km_not_driven'):,} km" if eq.get("km_not_driven") else "—",
            )
        )
        env_rows.append(("Equivalent fuel saved", f"~{eq.get('fuel_liters_saved')} L"))
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
        "Residential cost band ~₹55k–85k/kW. ~10 m² usable roof per kWp. "
        "0.5%/yr degradation; annual O&M is subtracted and discounted metrics "
        "use an explicit planning rate. Not a licensed design or DISCOM quote."
    )
    story.append(Paragraph(assumption_text, styles["Muted"]))

    notes = loc.get("planning_notes") or []
    if notes:
        story.append(Spacer(1, 6))
        story.append(
            Paragraph("<b>Important practical points</b>", styles["BodySmall"])
        )
        for n in notes:
            story.append(Paragraph(f"• {n}", styles["Muted"]))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#dddddd")))
    story.append(
        Paragraph(
            "Generated by SIGNIX. Indicative planning estimates only. "
            "Confirm MNRE / National Rooftop Solar Portal / local DISCOM rules and vendor quotations before procurement.",
            styles["Footer"],
        )
    )

    qr_bytes = loc.get("_qr_png_bytes")
    if qr_bytes:
        from reportlab.platypus import Image as RLImage
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=0.4, color=HexColor("#cccccc")))
        story.append(Spacer(1, 4))
        story.append(RLImage(io.BytesIO(qr_bytes), width=22 * mm, height=22 * mm))
        story.append(Paragraph(
            f"Live estimate: {loc.get('live_estimate_url') or ''}",
            styles["Muted"],
        ))
    doc.build(story)
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

    try:
        from app.assumptions import (
            MONETARY_DISCLAIMER,
            SUBSIDY_CAVEAT,
            SUBSIDY_ELIGIBILITY_ASSUMPTION,
        )
    except Exception:
        MONETARY_DISCLAIMER = "Indicative only — verify with DISCOM / vendor"
        SUBSIDY_CAVEAT = "Central subsidy estimate — final amount subject to MNRE / DISCOM approval"
        SUBSIDY_ELIGIBILITY_ASSUMPTION = "Assumes residential, grid-connected system"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    font_name, font_bold = _register_pdf_fonts()

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SumEyebrow", fontName=font_bold, fontSize=10,
        textColor=HexColor("#c47a0b"), spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SumTitle", fontName=font_bold, fontSize=22,
        textColor=HexColor("#3d2570"), spaceAfter=6,
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
    ))
    styles.add(ParagraphStyle(
        name="SumBig", fontName=font_bold, fontSize=20,
        textColor=HexColor("#3d2570"), alignment=1, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="SumCardLabel", fontName=font_bold, fontSize=9,
        textColor=HexColor("#666666"), alignment=1, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SumCardUnit", fontName=font_name, fontSize=9,
        textColor=HexColor("#666666"), alignment=1,
    ))

    extras = loc.get("extras") or {}
    nominal = loc.get("nominal_payback_years") or extras.get("nominal_payback_years") or loc.get("payback_years")
    simple = loc.get("simple_payback_years") or extras.get("simple_payback_years")
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

    story = []
    story.append(Paragraph("HOMEOWNER SUMMARY", styles["SumEyebrow"]))
    story.append(Paragraph("Your rooftop solar plan", styles["SumTitle"]))
    story.append(Paragraph(str(loc.get("address") or ""), styles["SumBody"]))
    story.append(Spacer(1, 8))
    map_bytes = loc.get("_map_png_bytes")
    if map_bytes:
        from reportlab.platypus import Image as RLImage
        img_buf = io.BytesIO(map_bytes)
        story.append(RLImage(img_buf, width=160*mm, height=98*mm))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Rooftop outline (drawn boundary; amber = obstructions)", styles["SumMuted"]))
        story.append(Spacer(1, 8))

    hero_data = [[
        card(
            "Recommended system",
            f"{float(loc.get('system_size') or 0):.1f}",
            (
                f"kW · approx. {int(loc['panel_count'])} panels"
                if loc.get("panel_count")
                else "kW"
            ),
        ),
        card("Yearly generation", f"{float(loc.get('annual_generation') or 0):,.0f}", "kWh / year"),
        card("Monthly savings", _inr(loc.get("monthly_savings") or 0), "indicative"),
    ]]
    hero = Table(hero_data, colWidths=[55 * mm, 55 * mm, 55 * mm])
    hero.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f7f4fc")),
        ("BOX", (0, 0), (0, 0), 0.5, HexColor("#e4daf5")),
        ("BOX", (1, 0), (1, 0), 0.5, HexColor("#e4daf5")),
        ("BOX", (2, 0), (2, 0), 0.5, HexColor("#e4daf5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(hero)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#dddddd")))
    story.append(Paragraph("What it costs", styles["SumH2"]))
    cost_rows = [["System cost (mid)", _inr(loc.get("system_cost") or 0)]]
    if loc.get("psg_guidance_allowed"):
        cost_rows.append(
            ["Central subsidy (estimate)", f"-{_inr(loc.get('subsidy_amount') or 0)}"]
        )
    cost_rows.append(["Net investment", _inr(loc.get("net_investment") or 0)])
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
    story.append(Paragraph(f"{SUBSIDY_CAVEAT} · {SUBSIDY_ELIGIBILITY_ASSUMPTION}", styles["SumMuted"]))

    story.append(Paragraph("When it pays back", styles["SumH2"]))
    pb_rows = [
        ["Nominal payback (tariff may rise)", f"{nominal} years" if nominal else "—"],
        ["Simple payback (flat tariff)", f"{simple} years" if simple else "—"],
    ]
    pt = Table(pb_rows, colWidths=[100 * mm, 70 * mm])
    pt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), font_name),
        ("FONTNAME", (1, 0), (1, -1), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(pt)

    story.append(Paragraph("How suitable is this roof?", styles["SumH2"]))
    suit_text = str(tier).replace("_", " ").title()
    if score is not None:
        suit_text += f"  ·  Score {score}/100"
    story.append(Paragraph(suit_text, styles["SumBody"]))
    story.append(Spacer(1, 10))
    qr_bytes = loc.get("_qr_png_bytes")
    if qr_bytes:
        from reportlab.platypus import Image as RLImage
        qr_buf = io.BytesIO(qr_bytes)
        story.append(Spacer(1, 8))
        story.append(RLImage(qr_buf, width=28*mm, height=28*mm))
        story.append(Paragraph(
            f"Scan to open live estimate: {loc.get('live_estimate_url') or ''}",
            styles["SumMuted"],
        ))
    story.append(Paragraph(MONETARY_DISCLAIMER, styles["SumMuted"]))
    story.append(Paragraph("SIGNIX · Page 1 · Summary for homeowners — not a formal quote", styles["SumMuted"]))

    story.append(PageBreak())
    story.append(Paragraph("NEXT STEPS", styles["SumEyebrow"]))
    story.append(Paragraph("Before you decide", styles["SumTitle"]))
    story.append(Paragraph(f"Address: {loc.get('address') or '—'}", styles["SumBody"]))
    if loc.get("orientation_label"):
        story.append(Paragraph(f"Roof orientation: {loc.get('orientation_label')}", styles["SumBody"]))
    if float(loc.get("battery_kwh") or 0) > 0:
        story.append(Paragraph(f"Battery included: {float(loc.get('battery_kwh')):.1f} kWh", styles["SumBody"]))
    if loc.get("psg_guidance_allowed"):
        story.append(Paragraph(f"Subsidy (estimate): {_inr(loc.get('subsidy_amount') or 0)}", styles["SumBody"]))
    story.append(Paragraph("Suggested next steps", styles["SumH2"]))
    for line in (
        "1. Share this summary with your family or building society.",
        *(("2. Confirm subsidy eligibility with your DISCOM / vendor.",) if loc.get("psg_guidance_allowed") else ()),
        "3. Ask installers for a site survey and a firm quote.",
        "4. Download the full technical report if you need detailed assumptions.",
    ):
        story.append(Paragraph(line, styles["SumBody"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(MONETARY_DISCLAIMER, styles["SumMuted"]))
    story.append(Paragraph(SUBSIDY_CAVEAT, styles["SumMuted"]))
    story.append(Paragraph("SIGNIX · Page 2 · Indicative planning aid only", styles["SumMuted"]))

    doc.build(story)
    buffer.seek(0)
    return buffer

# filename: app/report_generator.py
"""PDF report generation.

Primary path: WeasyPrint (HTML → PDF).
Fallback: ReportLab pure-Python PDF if WeasyPrint or its system libs are missing.
Both paths receive the same enriched location dict as the HTML report page.
"""

import io
import time
from flask import render_template


def _enrich_location_data(location):
    """Build the same dict the HTML report uses (extras + environmental)."""
    from app.solar_logic import calculate_environmental_equivalents

    data = location.to_dict() if hasattr(location, "to_dict") else dict(location)
    extras = data.get("extras") or {}

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
    ):
        if key in extras and extras[key] is not None and not data.get(key):
            data[key] = extras[key]

    # monthly_data may be None on old rows
    if not data.get("monthly_data"):
        data["monthly_data"] = {}

    return data


def generate_pdf_report(location):
    """
    location: UserLocation model instance (or dict).
    Returns: BytesIO containing the rendered PDF.
    """
    pdf_start = time.perf_counter()
    data = _enrich_location_data(location)

    # --- Try WeasyPrint first ---
    try:
        from weasyprint import HTML

        html_string = render_template("pdf_report.html", loc=data)
        print(
            f"[TIMING] PDF render template elapsed: {(time.perf_counter() - pdf_start) * 1000:.2f} ms"
        )

        pdf_bytes = HTML(string=html_string).write_pdf()
        elapsed_ms = (time.perf_counter() - pdf_start) * 1000
        print(f"[TIMING] PDF generation total elapsed: {elapsed_ms:.2f} ms")

        buffer = io.BytesIO(pdf_bytes)
        buffer.seek(0)
        return buffer
    except Exception as weasy_err:
        # Fall through to ReportLab
        last_err = weasy_err

    # --- ReportLab fallback (no system cairo/pango needed) ---
    try:
        reportlab_start = time.perf_counter()
        buffer = _generate_pdf_reportlab(data)
        elapsed_ms = (time.perf_counter() - reportlab_start) * 1000
        print(
            f"[TIMING] PDF generation fallback (ReportLab) elapsed: {elapsed_ms:.2f} ms"
        )
        return buffer
    except Exception as rl_err:
        raise RuntimeError(
            f"PDF generation failed. WeasyPrint: {last_err}; ReportLab: {rl_err}"
        )


def _inr(n):
    try:
        return f"₹{float(n):,.0f}"
    except Exception:
        return "₹0"


def _generate_pdf_reportlab(loc):
    from reportlab.lib.pagesizes import A4
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
            fontSize=16,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2Purple",
            parent=styles["Heading2"],
            textColor=HexColor("#3d2570"),
            fontSize=12,
            spaceBefore=12,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodySmall",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Muted",
            parent=styles["Normal"],
            fontSize=8,
            textColor=HexColor("#666666"),
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Footer",
            parent=styles["Normal"],
            fontSize=7.5,
            textColor=HexColor("#888888"),
            leading=10,
        )
    )

    story = []
    purple = HexColor("#3d2570")
    orange = HexColor("#c47a0b")
    light = HexColor("#f7f4fc")

    story.append(Paragraph("Rooftop Solar Estimation Report", styles["TitleOrange"]))
    story.append(
        Paragraph(str(loc.get("address") or "Selected location"), styles["Muted"])
    )
    badges = []
    if loc.get("orientation_label"):
        badges.append(f"Orientation: {loc['orientation_label']}")
    if loc.get("battery_kwh"):
        badges.append(f"Battery: {loc['battery_kwh']} kWh")
    if loc.get("inverter_type"):
        badges.append(f"Inverter: {loc['inverter_type']}")
    if badges:
        story.append(Paragraph(" · ".join(badges), styles["BodySmall"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#e0d6f0")))

    def section(title):
        story.append(Paragraph(title, styles["H2Purple"]))

    def kv_table(rows):
        data = [["Item", "Value"]] + [[str(a), str(b)] for a, b in rows]
        t = Table(data, colWidths=[95 * mm, 75 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), light),
                    ("TEXTCOLOR", (0, 0), (-1, 0), purple),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
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
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
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
                ("Tariff used", f"₹{bs.get('tariff_per_kwh')}/unit"),
                ("Estimated monthly units", f"{bs.get('monthly_units')} units"),
                (
                    "Recommended range",
                    f"{bs.get('recommended_min_kw')}–{bs.get('recommended_max_kw')} kW",
                ),
                ("Design size used", f"{loc.get('system_size')} kW"),
            ]
        )

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
        perf.append(("LCOE (25 yr)", f"₹{loc['lcoe']}/kWh"))
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
    fin.append(("PM Surya Ghar subsidy", f"-{_inr(loc.get('subsidy_amount'))}"))
    fin.append(("Net investment", _inr(loc.get("net_investment"))))
    fin.append(("Estimated monthly savings", _inr(loc.get("monthly_savings"))))
    fin.append(
        ("Est. 25-year bill savings", _inr((loc.get("monthly_savings") or 0) * 12 * 25))
    )
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
        elig = (
            "Typically eligible (DISCOM conditions apply)"
            if inv.get("subsidy_eligible")
            else "Usually not eligible"
        )
        story.append(
            Paragraph(f"<b>Subsidy eligibility:</b> {elig}", styles["BodySmall"])
        )
        for a in inv.get("advantages") or []:
            story.append(Paragraph(f"• {a}", styles["Muted"]))

    section("6. Environmental impact")
    env_rows = [("CO₂ reduction / year", f"{loc.get('co2_reduction_tons')} tons")]
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
    story.append(
        Paragraph(
            "<b>Key assumptions:</b> Bill path ≈ ₹10/unit · ~135 units/month/kW (~4–5.5 units/day/kWp). "
            "PM Surya Ghar: ₹30,000/kW first 2 kW + ₹18,000 for 2–3 kW step · max ₹78,000. "
            "Residential cost band ~₹55k–85k/kW. ~10 m² usable roof per kWp. 0.5%/yr degradation. "
            "Not a licensed design or DISCOM quote.",
            styles["Muted"],
        )
    )

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
            "Generated by Solar Setu. Indicative planning estimates only. "
            "Confirm MNRE / National Rooftop Solar Portal / local DISCOM rules and vendor quotations before procurement.",
            styles["Footer"],
        )
    )

    doc.build(story)
    buffer.seek(0)
    return buffer

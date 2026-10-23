"""Validated chart datasets derived from SIGNIX estimation outputs.

This module does not replace the solar or financial engines. It only reshapes
their persisted outputs into presentation-ready series and explicit unavailable
states for the interactive report.
"""

from __future__ import annotations

import math

from app.assumptions import ANNUAL_TARIFF_ESCALATION, DEGRADATION_PCT, get_value
from app.solar_logic import MONTH_NAMES


YEARS = 25


def _number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _rounded(values, digits=2):
    return [round(float(value), digits) for value in values]


def _generation_series(annual_generation, supplied):
    if isinstance(supplied, list) and len(supplied) == YEARS:
        values = [_number(value) for value in supplied]
        if all(value is not None and value >= 0 for value in values):
            return _rounded(values, 1), "estimation_pipeline"
    annual = _number(annual_generation, 0.0)
    if annual <= 0:
        return [], None
    degradation = get_value("degradation_pct", DEGRADATION_PCT.value)
    return (
        [round(annual * ((1 - degradation) ** year), 1) for year in range(YEARS)],
        "derived_with_estimator_degradation",
    )


def _monthly_generation(monthly_data):
    if not isinstance(monthly_data, dict):
        return []
    values = []
    for month in MONTH_NAMES:
        value = _number(monthly_data.get(month))
        if value is None:
            return []
        values.append({"month": month, "generation_kwh": round(value, 2)})
    return values


def _lifecycle_matches_cashflow(lifecycle, cashflow, net_investment):
    if lifecycle is None:
        return True
    rows = lifecycle.get("years") if isinstance(lifecycle, dict) else None
    if not isinstance(rows, list) or len(rows) != YEARS:
        return False
    running = -net_investment
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return False
        annual_flow = _number(row.get("net_cash_flow"))
        cumulative = _number(row.get("cumulative_cash_flow"))
        if annual_flow is None or cumulative is None:
            return False
        running += annual_flow
        if abs(running - cumulative) > 0.1 or abs(cumulative - cashflow[index]) > 0.1:
            return False
    return True


def _financial_series(net_investment, cashflow, lifecycle=None):
    if not isinstance(cashflow, list) or len(cashflow) != YEARS:
        return None
    cumulative_cashflow = [_number(value) for value in cashflow]
    if any(value is None for value in cumulative_cashflow):
        return None
    net = _number(net_investment)
    if net is None or net < 0:
        return None
    if not _lifecycle_matches_cashflow(lifecycle, cumulative_cashflow, net):
        return None
    annual_savings = []
    previous = -net
    for cumulative in cumulative_cashflow:
        annual_savings.append(round(cumulative - previous, 2))
        previous = cumulative
    cumulative_savings = [round(value + net, 2) for value in cumulative_cashflow]
    payback = None
    previous = -net
    for index, (cumulative, saving) in enumerate(
        zip(cumulative_cashflow, annual_savings), start=1
    ):
        if cumulative >= 0 and saving > 0:
            fraction = max(0.0, min(1.0, (-previous) / saving))
            payback = round((index - 1) + fraction, 1)
            break
        previous = cumulative
    return {
        "years": list(range(1, len(cumulative_cashflow) + 1)),
        "annual_savings": _rounded(annual_savings),
        "cumulative_savings": _rounded(cumulative_savings),
        "cumulative_cashflow": _rounded(cumulative_cashflow),
        "initial_investment": round(net, 2),
        "payback_year": payback,
    }


def _cumulative(flows):
    total = 0.0
    output = []
    for value in flows:
        total += float(value)
        output.append(round(total, 2))
    return output


def _financing(financing):
    if not isinstance(financing, dict):
        return {"available": False, "reason": "No persisted financing comparison"}
    capex, dpa = financing.get("capex"), financing.get("dpa")
    if not isinstance(capex, dict) or not isinstance(dpa, dict):
        return {"available": False, "reason": "Financing series is incomplete"}
    capex_flows, dpa_flows = capex.get("cash_flows"), dpa.get("cash_flows")
    if not isinstance(capex_flows, list) or not isinstance(dpa_flows, list):
        return {"available": False, "reason": "Financing cash flows are unavailable"}
    if not capex_flows or len(capex_flows) != len(dpa_flows):
        return {"available": False, "reason": "Financing cash-flow series are inconsistent"}
    capex_flows = [_number(value) for value in capex_flows]
    dpa_flows = [_number(value) for value in dpa_flows]
    if any(value is None for value in capex_flows + dpa_flows):
        return {"available": False, "reason": "Financing cash flows are malformed"}
    return {
        "available": True,
        "years": list(range(0, min(len(capex_flows), len(dpa_flows)))),
        "capex": {
            "upfront_payment": _number(capex.get("down_payment"), 0.0),
            "annual_payment": 0.0,
            "payback_year": capex.get("payback_years"),
            "total_cash_outflow": _number(capex.get("total_cash_outflow"), 0.0),
            "equity_irr_pct": _number(capex.get("equity_irr_pct")),
            "cash_flows": _rounded(capex_flows),
            "cumulative_cashflow": _cumulative(capex_flows),
        },
        "dpa": {
            "upfront_payment": _number(dpa.get("down_payment"), 0.0),
            "annual_payment": round(_number(dpa.get("annual_emi"), 0.0), 2),
            "monthly_payment": round(_number(dpa.get("monthly_emi"), 0.0), 2),
            "loan_amount": round(_number(dpa.get("loan_amount"), 0.0), 2),
            "tenure_years": dpa.get("tenure_years"),
            "annual_rate_pct": _number(dpa.get("annual_rate_pct"), 0.0),
            "total_repayment": round(
                _number(
                    dpa.get("total_repayment"),
                    _number(dpa.get("total_cash_outflow"), 0.0)
                    - _number(dpa.get("down_payment"), 0.0),
                ),
                2,
            ),
            "total_interest": round(
                _number(
                    dpa.get("total_interest"),
                    _number(dpa.get("total_cash_outflow"), 0.0)
                    - _number(dpa.get("down_payment"), 0.0)
                    - _number(dpa.get("loan_amount"), 0.0),
                ),
                2,
            ),
            "payback_year": dpa.get("payback_years"),
            "equity_irr_pct": _number(dpa.get("equity_irr_pct")),
            "total_cash_outflow": _number(dpa.get("total_cash_outflow"), 0.0),
            "cash_flows": _rounded(dpa_flows),
            "cumulative_cashflow": _cumulative(dpa_flows),
        },
        "assumptions": financing.get("assumptions") or {},
    }


def build_report_chart_data(data):
    """Return chart-safe datasets from a report context or estimation result."""
    data = data or {}
    extras = data.get("extras") if isinstance(data.get("extras"), dict) else {}

    def pick(name, default=None):
        value = data.get(name)
        if value is None:
            value = extras.get(name)
        return default if value is None else value

    annual_generation = _number(pick("annual_generation"), 0.0)
    monthly = _monthly_generation(pick("monthly_data"))
    generation_25yr, generation_source = _generation_series(
        annual_generation, pick("generation_25yr")
    )
    lifetime_generation = round(sum(generation_25yr), 1) if generation_25yr else None
    cumulative_generation = _cumulative(generation_25yr) if generation_25yr else []

    bill_sizing = pick("bill_sizing")
    annual_consumption = None
    monthly_consumption = pick("monthly_consumption")
    if isinstance(bill_sizing, dict):
        monthly_units = _number(bill_sizing.get("monthly_units"))
        if monthly_units is not None:
            annual_consumption = round(monthly_units * 12, 2)
    monthly_consumption_series = []
    if isinstance(monthly_consumption, dict):
        for month in MONTH_NAMES:
            value = _number(monthly_consumption.get(month))
            if value is None:
                monthly_consumption_series = []
                break
            monthly_consumption_series.append(round(value, 2))

    self_fraction = _number(pick("self_consumption_frac"))
    exported = _number(pick("exported_kwh"))
    energy_flow = {"available": False, "reason": "Consumption or export data unavailable"}
    self_consumed = _number(pick("self_consumed_kwh"))
    if self_consumed is None and self_fraction is not None:
        self_consumed = round(annual_generation * self_fraction, 2)
    if annual_consumption is not None and self_consumed is not None and exported is not None:
        energy_flow = {
            "available": True,
            "solar_generation_kwh": round(annual_generation, 2),
            "self_consumed_kwh": self_consumed,
            "grid_export_kwh": round(exported, 2),
            "grid_import_kwh": round(max(annual_consumption - self_consumed, 0), 2),
        }

    financial = _financial_series(
        pick("net_investment"), pick("cashflow_25yr"), pick("lifecycle_projection")
    )
    if financial:
        financial["annual_om_reference"] = _number(pick("annual_om"))
    financing = _financing(pick("financing_comparison"))

    electricity_cost = {
        "available": False,
        "reason": "Bill consumption and tariff inputs are required",
    }
    if financial and isinstance(bill_sizing, dict) and annual_consumption is not None:
        tariff = _number(bill_sizing.get("tariff_per_kwh"))
        if tariff is not None and tariff > 0:
            escalation = _number(
                pick("tariff_escalation_pct"),
                get_value(
                    "annual_tariff_escalation", ANNUAL_TARIFF_ESCALATION.value
                )
                * 100,
            ) / 100
            yearly_grid_cost = [
                annual_consumption * tariff * ((1 + escalation) ** year)
                for year in range(YEARS)
            ]
            grid_lifetime = sum(yearly_grid_cost)
            lifetime_savings = financial["cumulative_savings"][-1]
            annual_om = _number(pick("annual_om"), 0.0)
            capex_cost = (
                grid_lifetime
                - lifetime_savings
                + financial["initial_investment"]
            )
            electricity_cost = {
                "available": True,
                "grid_only_lifetime_cost": round(grid_lifetime, 2),
                "solar_capex_lifetime_cost": round(capex_cost, 2),
                "solar_dpa_lifetime_cost": (
                    round(
                        grid_lifetime
                        - lifetime_savings
                        + financing["dpa"]["total_cash_outflow"],
                        2,
                    )
                    if financing.get("available")
                    else None
                ),
                "effective_solar_cost_per_kwh": _number(pick("lcoe")),
                "starting_grid_tariff_per_kwh": tariff,
                "tariff_escalation_pct": round(escalation * 100, 2),
                "includes_annual_om": annual_om > 0,
            }

    monthly_bill_impact = {"available": False, "reason": "Monthly bill input unavailable"}
    if isinstance(bill_sizing, dict):
        current_bill = _number(bill_sizing.get("monthly_bill"))
        monthly_savings = _number(pick("monthly_savings"))
        if current_bill is not None and monthly_savings is not None and current_bill > 0:
            solar_bill = max(current_bill - monthly_savings, 0)
            monthly_bill_impact = {
                "available": True,
                "current_bill": round(current_bill, 2),
                "estimated_bill_with_solar": round(solar_bill, 2),
                "savings": round(current_bill - solar_bill, 2),
                "savings_pct": round((current_bill - solar_bill) / current_bill * 100, 1),
                "scope": "representative_month",
            }

    roof_area = _number(pick("roof_area_sqm"), 0.0)
    obstruction_area = max(0.0, _number(pick("obstructed_area_sqm"), 0.0))
    usable_area = max(0.0, _number(pick("usable_area_sqm"), roof_area))
    roof_utilization = {
        "total_area_sqm": round(roof_area, 2),
        "obstruction_area_sqm": round(obstruction_area, 2),
        "usable_area_sqm": round(usable_area, 2),
        "usable_pct": round(usable_area / roof_area * 100, 1) if roof_area else None,
        "setback_area_sqm": None,
        "panel_covered_area_sqm": None,
        "panel_count": pick("panel_count"),
        "system_size_kw": _number(pick("system_size"), 0.0),
    }

    equivalents = pick("environmental_equivalents") or {}
    environment = {
        "annual_co2_tons": _number(pick("co2_reduction_tons"), 0.0),
        "lifetime_co2_tons": None,
        "trees_planted": _number(equivalents.get("trees_planted"), 0.0),
        "km_not_driven": _number(equivalents.get("km_not_driven"), 0.0),
        "fuel_liters_saved": _number(equivalents.get("fuel_liters_saved"), 0.0),
    }
    financial_provenance = pick("financial_provenance") or {}
    emissions = financial_provenance.get("environmental_factor") or {}
    emissions_factor = _number(emissions.get("value"))
    if lifetime_generation is not None and emissions_factor is not None:
        environment["lifetime_co2_tons"] = round(
            lifetime_generation * emissions_factor / 1000.0, 2
        )
    environment["provenance"] = emissions or None

    return {
        "schema_version": "1.0",
        "energy": {
            "monthly_generation": monthly,
            "annual_generation": round(annual_generation, 2),
            "generation_25yr": generation_25yr,
            "cumulative_generation_25yr": cumulative_generation,
            "generation_25yr_source": generation_source,
            "lifetime_generation": lifetime_generation,
            "degradation_pct": round(
                get_value("degradation_pct", DEGRADATION_PCT.value) * 100, 2
            ),
        },
        "consumption": {
            "annual_consumption": annual_consumption,
            "monthly_consumption": monthly_consumption_series,
            "monthly_available": bool(monthly_consumption_series),
            "unavailable_reason": None if monthly_consumption_series else (
                "A representative monthly bill does not provide a defensible 12-month load profile."
            ),
        },
        "energy_flow": energy_flow,
        "financial": financial or {
            "available": False,
            "reason": "Persisted annual cashflow series unavailable",
        },
        "financing": financing,
        "electricity_cost": electricity_cost,
        "monthly_bill_impact": monthly_bill_impact,
        "roof_utilization": roof_utilization,
        "orientation": {
            "azimuth_degrees": _number(pick("orientation_deg")),
            "label": pick("orientation_label"),
            "factor": _number(pick("orientation_factor")),
            "recommended_tilt_degrees": _number(pick("recommended_tilt_deg")),
            "shading_loss_pct": None,
        },
        "environment": environment,
        "diagnostics": {
            "monthly_generation_sum": round(
                sum(item["generation_kwh"] for item in monthly), 2
            ) if monthly else None,
            "annual_generation_delta": round(
                sum(item["generation_kwh"] for item in monthly) - annual_generation,
                2,
            ) if monthly else None,
            "stored_lifetime_generation": _number(pick("lifetime_kwh")),
        },
    }

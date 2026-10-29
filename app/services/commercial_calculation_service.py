"""Commercial calculation policy for the shared SIGNIX solar engine."""

from __future__ import annotations

import math

from app.assumptions import PRACTICAL_MAX_KWP
from app.config import Config

PLANNING_TRACK = "business"
DEMAND_CHARGE_NOTE = "Demand charge savings are not included."
DEPRECIATION_NOTE = (
    "Subject to ownership, business use and your tax regime. "
    "Confirm with your CA. Verify current rules."
)
SIZE_CAP_MESSAGE = (
    "Your roof may fit more than this. Create a project and installers will design it."
)


def _coerce_float(value, default=0.0):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("Expected a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected a finite number") from exc
    if not math.isfinite(result):
        raise ValueError("Expected a finite number")
    return result


def validate_finance_preferences(payload):
    """Absent values use published planning defaults; supplied values must be valid."""
    limits = {
        "ppa_rate_rs_per_kwh": (None, 0, None),
        "ppa_tenure_years": (10, 1, 25),
        "ppa_escalation_pct": (0, 0, 100),
        "loan_rate_pct": (10, 0, 100),
        "loan_tenure_years": (5, 1, 25),
        "down_payment_pct": (20, 0, 100),
        "tax_rate_pct": (0, 0, 100),
    }
    values = {}
    for name, (default, minimum, maximum) in limits.items():
        try:
            value = _coerce_float(payload.get(name), default)
        except ValueError as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if value is not None:
            if value < minimum or (maximum is not None and value > maximum):
                raise ValueError(f"{name} is outside the supported range")
            if name == "ppa_rate_rs_per_kwh" and value <= 0:
                raise ValueError("ppa_rate_rs_per_kwh must be positive, or left blank for a quote")
            if name.endswith("tenure_years") and int(value) != value:
                raise ValueError(f"{name} must be a whole number of years")
        values[name] = value
    enabled = payload.get("include_depreciation_tax_benefit", False)
    if enabled not in (True, False, "on", "1", "0", "true", "false", "", None):
        raise ValueError("include_depreciation_tax_benefit must be a boolean")
    values["include_depreciation_tax_benefit"] = enabled in (True, "on", "1", "true")
    return values


def _cumulative_after_years(cashflows, years):
    # Never present a short contract as a complete 25-year projection.
    if len(cashflows) <= years:
        return None
    return round(sum(cashflows[:years + 1]), 2)


def resolve_policy(load_profile=None):
    return {
        "planning_track": PLANNING_TRACK,
        "property_type": "commercial",
        "default_tariff_per_kwh": float(Config.DEFAULT_COMMERCIAL_TARIFF),
        "tariff_basis": "user_confirmed_commercial_rate_or_configurable_default",
        "cost_per_kw": float(Config.COMMERCIAL_COST_PER_KW),
        "cost_per_kw_low": float(Config.COMMERCIAL_COST_PER_KW_LOW),
        "cost_per_kw_high": float(Config.COMMERCIAL_COST_PER_KW_HIGH),
        "default_load_profile": str(load_profile or "day_heavy"),
        "residential_subsidy_may_apply": False,
        "demand_charge_savings_supported": False,
        "policy_version": "2026-10-25",
        "assumption_source": "SIGNIX configurable planning assumptions; user tariff confirmation required",
    }


def policy_snapshot():
    policy = resolve_policy()
    policy["effective_from"] = "2026-10-25"
    policy["source"] = policy["assumption_source"]
    return policy


def cost_range_for_system_size(system_size_kw):
    size_kw = max(0.0, _coerce_float(system_size_kw, 0.0))
    low = size_kw * float(Config.COMMERCIAL_COST_PER_KW_LOW)
    base = size_kw * float(Config.COMMERCIAL_COST_PER_KW)
    high = size_kw * float(Config.COMMERCIAL_COST_PER_KW_HIGH)
    return {
        "low_inr": round(low, 2),
        "base_inr": round(base, 2),
        "high_inr": round(high, 2),
        "currency": "INR",
    }


def practical_size_cap_status(roof_capacity_kw, system_size_kw=None):
    roof_capacity = max(0.0, _coerce_float(roof_capacity_kw, 0.0))
    selected_size = max(0.0, _coerce_float(system_size_kw, roof_capacity))
    cap_kw = float(PRACTICAL_MAX_KWP.value)
    fits_more = roof_capacity > cap_kw and selected_size < roof_capacity
    return {
        "flag": bool(fits_more),
        "practical_cap_kw": round(cap_kw, 2),
        "roof_capacity_kw": round(roof_capacity, 2),
        "message": SIZE_CAP_MESSAGE if fits_more else "",
    }


def calculate_commercial_project(
    system_size_kw=None, annual_generation_kwh=None, tariff_per_kwh=None,
    roof_capacity_kw=None, down_payment_pct=20.0, loan_rate_pct=10.0,
    loan_tenure_years=5, ppa_rate_rs_per_kwh=None, ppa_tenure_years=10,
    ppa_escalation_pct=0.0, tax_rate_pct=0.0,
    include_depreciation_tax_benefit=False, annual_tariff_escalation=None,
    *, financials=None,
):
    """Compare contracts using one authoritative energy valuation and lifecycle.

    annual_tariff_escalation is a percentage (4 means 4%). When financials are
    supplied, their persisted lifecycle is authoritative; it is never recomputed.
    """
    from app.solar_logic import (
        build_financing_comparison, build_lifecycle_projection, calculate_financials,
    )

    preferences = validate_finance_preferences(locals())
    size = _coerce_float(system_size_kw, None)
    generation = _coerce_float(annual_generation_kwh, None)
    tariff = _coerce_float(tariff_per_kwh, None)
    if size is None or generation is None or tariff is None or min(size, generation, tariff) <= 0:
        raise ValueError("Calculated capacity, generation and tariff must be available and positive")
    if financials is None:
        financials = calculate_financials(size, generation, tariff, planning_track="business",
                                         property_type="commercial", load_profile="day_heavy")
        if annual_tariff_escalation is not None:
            escalation = _coerce_float(annual_tariff_escalation)
            if not 0 <= escalation <= 100:
                raise ValueError("annual_tariff_escalation must be between 0 and 100 percent")
            first = financials["lifecycle_projection"]["years"][0]
            financials["lifecycle_projection"] = build_lifecycle_projection(
                financials["net_investment"], first["avoided_retail_value"],
                first["export_value"], first["om_cost"], generation,
                tariff_escalation=escalation / 100,
            )
    elif annual_tariff_escalation is not None:
        raise ValueError("Use the persisted lifecycle assumptions for saved estimates")
    if financials.get("planning_track") != "business" or financials.get("subsidy_amount", 0) not in (0, 0.0):
        raise ValueError("Commercial comparisons require commercial financials without residential subsidy")
    rows = (financials.get("lifecycle_projection") or {}).get("years")
    if not isinstance(rows, list) or len(rows) != 25:
        raise ValueError("A complete calculated lifecycle projection is required")
    upfront = _coerce_float(financials.get("net_investment"), None)
    if upfront is None or upfront < 0:
        raise ValueError("Calculated investment is unavailable")
    flows = []
    cumulative = -upfront
    for year, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("year") != year:
            raise ValueError("Invalid lifecycle projection")
        for key in ("net_cash_flow", "gross_savings", "generation_kwh", "om_cost", "cumulative_cash_flow"):
            if _coerce_float(row.get(key), None) is None:
                raise ValueError("Incomplete lifecycle projection")
        if min(row["gross_savings"], row["generation_kwh"], row["om_cost"]) < 0:
            raise ValueError("Invalid lifecycle costs or generation")
        if abs(row["gross_savings"] - row["om_cost"] - row["net_cash_flow"]) > 0.02:
            raise ValueError("Inconsistent lifecycle operating benefit")
        cumulative += row["net_cash_flow"]
        if abs(cumulative - row["cumulative_cash_flow"]) > 0.15:
            raise ValueError("Inconsistent lifecycle cumulative cash flow")
        flows.append(float(row["net_cash_flow"]))
    financing = build_financing_comparison(
        net_investment=upfront, annual_savings=rows[0]["gross_savings"],
        monthly_savings=rows[0]["gross_savings"] / 12, annual_generation_kwh=generation,
        down_payment_pct=preferences["down_payment_pct"],
        dpa_rate_pct=preferences["loan_rate_pct"],
        dpa_tenure_years=preferences["loan_tenure_years"],
        annual_net_cashflows=flows, annual_om=rows[0]["om_cost"],
    )
    saved_escalation = financials.get("tariff_escalation_pct")
    if annual_tariff_escalation is not None:
        financing["assumptions"]["tariff_escalation_pct"] = float(annual_tariff_escalation)
    elif saved_escalation is not None:
        financing["assumptions"]["tariff_escalation_pct"] = saved_escalation
    models = {}
    for key, source, name in (("buy", "capex", "Buy"), ("loan", "dpa", "Buy with loan")):
        item = financing[source]
        cashflows = item["cash_flows"]
        models[key] = {
            "name": name, "model_label": "CAPEX" if key == "buy" else "Loan",
            "upfront_cost_inr": upfront if key == "buy" else item["down_payment"],
            "year_one_savings_inr": cashflows[1],
            "annual_operating_benefit_inr": flows[0],
            "annual_om_inr": rows[0]["om_cost"],
            "payback_years": item["payback_years"],
            "cash_flows": cashflows,
            "total_after_10_years": _cumulative_after_years(cashflows, 10),
            "total_after_25_years": _cumulative_after_years(cashflows, 25),
        }
        if key == "loan":
            models[key].update(loan_amount_inr=item["loan_amount"],
                               monthly_emi_inr=item["monthly_emi"],
                               total_repayment_inr=item["total_repayment"])
    rate = preferences["ppa_rate_rs_per_kwh"]
    resco = {
        "name": "RESCO / PPA", "model_label": "Installer quote required" if rate is None else "Indicative PPA",
        "upfront_cost_inr": None if rate is None else 0.0,
        "year_one_savings_inr": None, "payback_years": None,
        "ppa_rate_rs_per_kwh": rate,
        "ppa_rate_basis": "quote_required" if rate is None else "user_supplied",
        "total_after_10_years": None, "total_after_25_years": None,
        "cash_flows": [], "annual_ppa_cost_inr": None,
        "contract_years": int(preferences["ppa_tenure_years"]),
    }
    if rate is not None:
        contract = rows[:resco["contract_years"]]
        costs = [round(row["generation_kwh"] * rate *
                       (1 + preferences["ppa_escalation_pct"] / 100) ** i, 2)
                 for i, row in enumerate(contract)]
        # Grid value retains the estimate's self-consumption/export policy.
        cashflows = [0.0] + [round(row["gross_savings"] - cost, 2)
                             for row, cost in zip(contract, costs)]
        resco.update(cash_flows=cashflows, annual_ppa_cost_inr=costs[0],
                     year_one_savings_inr=cashflows[1],
                     total_after_10_years=_cumulative_after_years(cashflows, 10),
                     total_after_25_years=_cumulative_after_years(cashflows, 25))
    resco["notes"] = [
        "PPA assumes payment for all generated energy and no upfront investment; investment payback is not applicable.",
        "No post-contract savings, transfer value, or residential subsidy is assumed. Installer terms must be verified.",
    ]
    models["resco"] = resco
    return {
        "planning_track": "business", "property_type": "commercial",
        "system_size_kw": size, "annual_generation_kwh": generation,
        "tariff_per_kwh": tariff,
        "cost_range_inr": {"low_inr": financials.get("system_cost_low"),
                           "base_inr": financials.get("system_cost"),
                           "high_inr": financials.get("system_cost_high")},
        "demand_charge_note": DEMAND_CHARGE_NOTE,
        "depreciation_enabled": False,
        "depreciation_requested": preferences["include_depreciation_tax_benefit"],
        "depreciation_note": "Tax benefits are not modelled: a verified depreciation schedule and eligibility are required. " + DEPRECIATION_NOTE,
        "size_cap_status": practical_size_cap_status(roof_capacity_kw, size),
        "models": models,
        "financing_comparison": financing,
        "notes": [DEMAND_CHARGE_NOTE],
    }


def project_for_location(location):
    """Read-only adapter: never substitute consumption or roof area for output."""
    extras = location.extras or {}
    if extras.get("planning_track") != "business":
        raise ValueError("A commercial estimate is required")
    financials = {**extras, "net_investment": location.net_investment,
                  "system_cost": location.system_cost, "subsidy_amount": location.subsidy_amount}
    return calculate_commercial_project(
        system_size_kw=location.system_size, annual_generation_kwh=location.annual_generation,
        tariff_per_kwh=extras.get("tariff_per_kwh"),
        roof_capacity_kw=extras.get("roof_capacity_kw"), financials=financials,
        **validate_finance_preferences(extras),
    )


calculate_business_models = calculate_commercial_project
commercial_financial_models = calculate_commercial_project


def sync_saved_financing(location, result=None):
    """Keep custom loan terms on recalculation; use the newly calculated lifecycle."""
    extras = location.extras or {}
    if extras.get("planning_track") != "business" or "loan_rate_pct" not in extras:
        return
    comparison = project_for_location(location)
    location.extras = {**extras, "financing_comparison": comparison["financing_comparison"]}
    if result is not None:
        result["financing_comparison"] = comparison["financing_comparison"]

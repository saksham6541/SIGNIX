"""Commercial calculation policy for the shared SIGNIX solar engine."""

from __future__ import annotations

import math
<<<<<<< HEAD
from typing import Any, Dict, Optional

from app.assumptions import ANNUAL_TARIFF_ESCALATION, PRACTICAL_MAX_KWP
=======

from app.assumptions import PRACTICAL_MAX_KWP
>>>>>>> bf9e4f7 (Complete commercial solar workflow and financial repairs)
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
<<<<<<< HEAD
        return float(default)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _as_yearly_series(base_value, years, escalation_pct=0.0):
    escalation = _coerce_float(escalation_pct, 0.0) / 100.0
    return [
        base_value * ((1.0 + escalation) ** (year_index - 1))
        for year_index in range(1, years + 1)
    ]


def _cumulative_after_years(cashflows, years):
    if not cashflows:
        return None
    period = min(len(cashflows), max(0, years))
    total = sum(float(value) for value in cashflows[: period + 1])
    return round(total, 2)


def _depreciation_adjustment(cost_inr, tax_rate_pct, enabled):
    if not enabled:
        return 0.0
    tax_rate = _coerce_float(tax_rate_pct, 0.0)
    if tax_rate <= 0.0:
        return 0.0
    return (cost_inr / 10.0) * (tax_rate / 100.0)
=======
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
>>>>>>> bf9e4f7 (Complete commercial solar workflow and financial repairs)


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


<<<<<<< HEAD
def _yearly_cashflow_for_buy(cost_inr, first_year_savings, escalation_pct):
    yearly_cashflow = [-float(cost_inr)]
    for value in _as_yearly_series(first_year_savings, 25, escalation_pct):
        yearly_cashflow.append(float(value))
    return yearly_cashflow


def _summarise_cashflow(cashflows, years):
    if not cashflows:
        return {"total_after_10_years": None, "total_after_25_years": None}
    return {
        "total_after_10_years": _cumulative_after_years(cashflows, 10),
        "total_after_25_years": _cumulative_after_years(cashflows, 25),
    }


def _build_buy_model(system_size_kw, annual_generation_kwh, tariff_per_kwh, tax_rate_pct, include_depreciation_tax_benefit, escalation_pct):
    cost_range = cost_range_for_system_size(system_size_kw)
    cost_inr = float(cost_range["base_inr"])
    tariff = max(0.0, _coerce_float(tariff_per_kwh, Config.DEFAULT_COMMERCIAL_TARIFF))
    annual_generation = max(0.0, _coerce_float(annual_generation_kwh, system_size_kw * 1620.0))
    first_year_savings = annual_generation * tariff
    tax_shield = _depreciation_adjustment(
        cost_inr, tax_rate_pct, include_depreciation_tax_benefit
    )
    first_year_savings += tax_shield if include_depreciation_tax_benefit else 0.0
    yearly_cashflows = _yearly_cashflow_for_buy(cost_inr, first_year_savings, escalation_pct)
    payback_years = None
    running_total = 0.0
    for year_index, value in enumerate(yearly_cashflows):
        running_total += float(value)
        if payback_years is None and running_total >= 0 and year_index > 0:
            prior = running_total - float(value)
            if value == 0:
                payback_years = float(year_index)
            else:
                fraction = max(0.0, min(1.0, (-prior) / value))
                payback_years = float((year_index - 1) + fraction)
    model = {
        "name": "Buy",
        "upfront_cost_inr": round(cost_inr, 2),
        "year_one_savings_inr": round(first_year_savings, 2),
        "cost_range_inr": {
            "low": round(cost_range["low_inr"], 2),
            "base": round(cost_range["base_inr"], 2),
            "high": round(cost_range["high_inr"], 2),
        },
        "payback_years": round(payback_years, 2) if payback_years is not None else None,
        "annual_savings_after_tax_inr": round(first_year_savings, 2),
        "depreciation_tax_benefit_inr": round(tax_shield, 2) if include_depreciation_tax_benefit else 0.0,
        "model_label": "CAPEX",
    }
    model.update(_summarise_cashflow(yearly_cashflows, 25))
    if include_depreciation_tax_benefit:
        model["tax_note"] = DEPRECIATION_NOTE
    else:
        model["tax_note"] = "Depreciation tax benefit not applied."
    return model


def _build_loan_model(system_size_kw, annual_generation_kwh, tariff_per_kwh, loan_rate_pct, loan_tenure_years, down_payment_pct, tax_rate_pct, include_depreciation_tax_benefit, escalation_pct):
    from app.solar_logic import build_financing_comparison

    cost_range = cost_range_for_system_size(system_size_kw)
    cost_inr = float(cost_range["base_inr"])
    annual_generation = max(0.0, _coerce_float(annual_generation_kwh, system_size_kw * 1620.0))
    tariff = max(0.0, _coerce_float(tariff_per_kwh, Config.DEFAULT_COMMERCIAL_TARIFF))
    base_savings = annual_generation * tariff
    tax_shield = _depreciation_adjustment(
        cost_inr, tax_rate_pct, include_depreciation_tax_benefit
    )
    if include_depreciation_tax_benefit:
        base_savings += tax_shield
    loan_result = build_financing_comparison(
        net_investment=cost_inr,
        annual_savings=base_savings,
        monthly_savings=base_savings / 12.0,
        annual_generation_kwh=annual_generation,
        down_payment_pct=down_payment_pct,
        dpa_tenure_years=loan_tenure_years or 5,
        dpa_rate_pct=loan_rate_pct or 10.0,
        annual_om=0.0,
    )
    dpa = loan_result["dpa"]
    cashflows = list(dpa.get("cash_flows") or [])
    payback_years = dpa.get("payback_years")
    model = {
        "name": "Buy with loan",
        "upfront_cost_inr": round(float(dpa.get("down_payment") or 0.0), 2),
        "year_one_savings_inr": round(base_savings, 2),
        "cost_range_inr": {
            "low": round(cost_range["low_inr"], 2),
            "base": round(cost_range["base_inr"], 2),
            "high": round(cost_range["high_inr"], 2),
        },
        "loan_amount_inr": round(float(dpa.get("loan_amount") or 0.0), 2),
        "monthly_emi_inr": round(float(dpa.get("monthly_emi") or 0.0), 2),
        "payback_years": round(float(payback_years), 2) if payback_years is not None else None,
        "annual_savings_after_tax_inr": round(base_savings, 2),
        "depreciation_tax_benefit_inr": round(tax_shield, 2) if include_depreciation_tax_benefit else 0.0,
        "model_label": "DPA",
    }
    model.update(_summarise_cashflow(cashflows, 25))
    model["tax_note"] = DEPRECIATION_NOTE if include_depreciation_tax_benefit else "Depreciation tax benefit not applied."
    return model


def _build_resco_model(system_size_kw, annual_generation_kwh, tariff_per_kwh, ppa_rate_rs_per_kwh, ppa_tenure_years, ppa_escalation_pct, escalation_pct):
    cost_range = cost_range_for_system_size(system_size_kw)
    annual_generation = max(0.0, _coerce_float(annual_generation_kwh, system_size_kw * 1620.0))
    tariff = max(0.0, _coerce_float(tariff_per_kwh, Config.DEFAULT_COMMERCIAL_TARIFF))
    ppa_rate = _coerce_float(ppa_rate_rs_per_kwh, None)
    if ppa_rate is None or ppa_rate <= 0.0:
        return {
            "name": "RESCO / PPA",
            "upfront_cost_inr": 0.0,
            "year_one_savings_inr": None,
            "cost_range_inr": {
                "low": round(cost_range["low_inr"], 2),
                "base": round(cost_range["base_inr"], 2),
                "high": round(cost_range["high_inr"], 2),
            },
            "ppa_rate_rs_per_kwh": "Installers will quote",
            "ppa_rate_basis": "quote_required",
            "payback_years": None,
            "total_after_10_years": None,
            "total_after_25_years": None,
            "model_label": "Indicative. Installers will quote the actual rate.",
            "tax_note": "",
            "notes": ["Installers will quote the PPA rate."],
            "quote_note": "Installers will quote",
        }
    ppa_escalation = max(0.0, _coerce_float(ppa_escalation_pct, 0.0))
    power_cost = annual_generation * ppa_rate
    first_year_savings = annual_generation * (tariff - ppa_rate)
    if first_year_savings < 0:
        first_year_savings = 0.0
    cashflows = [0.0]
    years = max(1, int(ppa_tenure_years or 25))
    for year_index in range(1, years + 1):
        escalated = first_year_savings * ((1.0 + (ppa_escalation / 100.0)) ** (year_index - 1))
        cashflows.append(float(escalated))
    while len(cashflows) < 26:
        cashflows.append(float(cashflows[-1]))
    payback_years = None
    running_total = 0.0
    for year_index, value in enumerate(cashflows):
        running_total += float(value)
        if payback_years is None and running_total >= 0 and year_index > 0:
            prior = running_total - float(value)
            if value == 0:
                payback_years = float(year_index)
            else:
                fraction = max(0.0, min(1.0, (-prior) / value))
                payback_years = float((year_index - 1) + fraction)
    return {
        "name": "RESCO / PPA",
        "upfront_cost_inr": 0.0,
        "year_one_savings_inr": round(first_year_savings, 2),
        "cost_range_inr": {
            "low": 0.0,
            "base": 0.0,
            "high": 0.0,
        },
        "ppa_rate_rs_per_kwh": round(ppa_rate, 2),
        "ppa_rate_basis": "user_supplied",
        "payback_years": round(float(payback_years), 2) if payback_years is not None else None,
        "total_after_10_years": _cumulative_after_years(cashflows, 10),
        "total_after_25_years": _cumulative_after_years(cashflows, 25),
        "model_label": "Indicative. Installers will quote the actual rate.",
        "tax_note": "",
        "notes": [DEMAND_CHARGE_NOTE],
        "annual_ppa_cost_inr": round(power_cost, 2),
    }


def calculate_commercial_project(
    system_size_kw=None,
    annual_generation_kwh=None,
    tariff_per_kwh=None,
    roof_capacity_kw=None,
    down_payment_pct=20.0,
    loan_rate_pct=10.0,
    loan_tenure_years=5,
    ppa_rate_rs_per_kwh=None,
    ppa_tenure_years=10,
    ppa_escalation_pct=0.0,
    tax_rate_pct=0.0,
    include_depreciation_tax_benefit=False,
    annual_tariff_escalation=None,
):
    size_kw = max(0.0, _coerce_float(system_size_kw, 0.0))
    if size_kw <= 0.0:
        size_kw = max(0.0, _coerce_float(annual_generation_kwh, 0.0) / 1620.0)
    escalation_pct = _coerce_float(
        annual_tariff_escalation if annual_tariff_escalation is not None else ANNUAL_TARIFF_ESCALATION.value,
        4.0,
    )
    annual_generation = max(0.0, _coerce_float(annual_generation_kwh, size_kw * 1620.0))
    tariff = max(0.0, _coerce_float(tariff_per_kwh, Config.DEFAULT_COMMERCIAL_TARIFF))
    buy_model = _build_buy_model(
        size_kw,
        annual_generation,
        tariff,
        tax_rate_pct,
        include_depreciation_tax_benefit,
        escalation_pct,
    )
    loan_model = _build_loan_model(
        size_kw,
        annual_generation,
        tariff,
        loan_rate_pct,
        loan_tenure_years,
        down_payment_pct,
        tax_rate_pct,
        include_depreciation_tax_benefit,
        escalation_pct,
    )
    resco_model = _build_resco_model(
        size_kw,
        annual_generation,
        tariff,
        ppa_rate_rs_per_kwh,
        ppa_tenure_years,
        ppa_escalation_pct,
        escalation_pct,
    )
    return {
        "planning_track": PLANNING_TRACK,
        "property_type": "commercial",
        "system_size_kw": round(size_kw, 2),
        "annual_generation_kwh": round(annual_generation, 2),
        "tariff_per_kwh": round(tariff, 2),
        "cost_range_inr": cost_range_for_system_size(size_kw),
        "demand_charge_note": DEMAND_CHARGE_NOTE,
        "depreciation_note": DEPRECIATION_NOTE,
        "depreciation_enabled": bool(include_depreciation_tax_benefit),
        "size_cap_status": practical_size_cap_status(roof_capacity_kw, size_kw),
        "models": {
            "buy": buy_model,
            "loan": loan_model,
            "resco": resco_model,
        },
        "notes": [
            DEMAND_CHARGE_NOTE,
            (
                DEPRECIATION_NOTE
                if include_depreciation_tax_benefit
                else "Depreciation tax benefit not applied."
            ),
        ],
    }
=======
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
>>>>>>> bf9e4f7 (Complete commercial solar workflow and financial repairs)


calculate_business_models = calculate_commercial_project
commercial_financial_models = calculate_commercial_project
<<<<<<< HEAD
=======


def sync_saved_financing(location, result=None):
    """Keep custom loan terms on recalculation; use the newly calculated lifecycle."""
    extras = location.extras or {}
    if extras.get("planning_track") != "business" or "loan_rate_pct" not in extras:
        return
    comparison = project_for_location(location)
    location.extras = {**extras, "financing_comparison": comparison["financing_comparison"]}
    if result is not None:
        result["financing_comparison"] = comparison["financing_comparison"]
>>>>>>> bf9e4f7 (Complete commercial solar workflow and financial repairs)

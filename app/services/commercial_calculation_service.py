"""Commercial calculation policy for the shared SIGNIX solar engine."""

from app.config import Config


PLANNING_TRACK = "business"


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

"""Residential calculation policy for the shared SIGNIX solar engine."""

from app.config import Config


PLANNING_TRACK = "home"
ALLOWED_PROPERTY_TYPES = frozenset({"residential", "remote"})


def normalize_property_type(value):
    property_type = str(value or "residential").strip().lower()
    return property_type if property_type in ALLOWED_PROPERTY_TYPES else "residential"


def resolve_policy(property_type=None, load_profile=None):
    resolved_property_type = normalize_property_type(property_type)
    return {
        "planning_track": PLANNING_TRACK,
        "property_type": resolved_property_type,
        "default_tariff_per_kwh": float(Config.DEFAULT_RESIDENTIAL_TARIFF),
        "tariff_basis": "state_residential_default_or_user_confirmed_rate",
        "cost_per_kw": float(Config.SYSTEM_COST_PER_KW),
        "cost_per_kw_low": float(Config.SYSTEM_COST_PER_KW_LOW),
        "cost_per_kw_high": float(Config.SYSTEM_COST_PER_KW_HIGH),
        "default_load_profile": str(load_profile or "flat"),
        "residential_subsidy_may_apply": resolved_property_type == "residential",
        "demand_charge_savings_supported": False,
        "policy_version": "2026-10-25",
        "assumption_source": "SIGNIX configurable planning assumptions; user/DISCOM verification required",
    }


def policy_snapshot():
    policy = resolve_policy()
    policy["effective_from"] = "2026-10-25"
    policy["source"] = policy["assumption_source"]
    return policy

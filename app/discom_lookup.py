"""Best-effort DISCOM + scheme lookup for Indian locations.

Maps state (and, where known, major city/district) to a likely electricity
distribution company and the central / state scheme context that SIGNIX should
pre-fill. Not an official boundary product — users can always override.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Central scheme always applicable for residential rooftop estimates in SIGNIX.
CENTRAL_SCHEMES = [
    {
        "code": "pm_surya_ghar",
        "name": "PM Surya Ghar Muft Bijli Yojana",
        "scope": "central",
        "note": "Central residential rooftop subsidy (MNRE); final amount subject to DISCOM / MNRE approval.",
    }
]

# State → default DISCOM + optional state top-up note.
# Rates/tariffs still come from TariffTable when seeded.
_STATE_DISCOM: Dict[str, Dict[str, Any]] = {
    "Delhi": {
        "discoms": [
            {"code": "BRPL", "name": "BSES Rajdhani Power Ltd", "areas": ["South Delhi", "West Delhi"]},
            {"code": "BYPL", "name": "BSES Yamuna Power Ltd", "areas": ["East Delhi", "Central Delhi"]},
            {"code": "TPDDL", "name": "Tata Power Delhi Distribution Ltd", "areas": ["North Delhi"]},
        ],
        "default_discom": "BRPL",
        "state_schemes": [
            {
                "code": "delhi_rooftop",
                "name": "Delhi rooftop solar facilitation (DISCOM net-metering)",
                "scope": "state",
                "note": "Net metering via local DISCOM; no separate state capital subsidy modelled.",
            }
        ],
    },
    "Maharashtra": {
        "discoms": [
            {"code": "MSEDCL", "name": "Maharashtra State Electricity Distribution Co. Ltd", "areas": ["State-wide (most districts)"]},
            {"code": "BEST", "name": "BEST Undertaking", "areas": ["Mumbai Island City"]},
            {"code": "ADANI_MUMBAI", "name": "Adani Electricity Mumbai Ltd", "areas": ["Mumbai suburbs"]},
            {"code": "TATA_MUMBAI", "name": "Tata Power (Mumbai)", "areas": ["Parts of Mumbai"]},
        ],
        "default_discom": "MSEDCL",
        "state_schemes": [
            {
                "code": "maha_net_metering",
                "name": "Maharashtra net-metering framework",
                "scope": "state",
                "note": "Rooftop solar under MSEDCL / local Mumbai DISCOM net-metering rules.",
            }
        ],
    },
    "Karnataka": {
        "discoms": [
            {"code": "BESCOM", "name": "Bangalore Electricity Supply Company", "areas": ["Bengaluru Urban", "Bengaluru Rural", "Tumakuru"]},
            {"code": "MESCOM", "name": "Mangalore Electricity Supply Company", "areas": ["Coastal Karnataka"]},
            {"code": "HESCOM", "name": "Hubli Electricity Supply Company", "areas": ["North Karnataka"]},
            {"code": "GESCOM", "name": "Gulbarga Electricity Supply Company", "areas": ["Kalaburagi region"]},
            {"code": "CESC", "name": "Chamundeshwari Electricity Supply Corporation", "areas": ["Mysuru region"]},
        ],
        "default_discom": "BESCOM",
        "state_schemes": [
            {
                "code": "ka_rooftop",
                "name": "Karnataka rooftop solar / net-metering",
                "scope": "state",
                "note": "DISCOM net-metering; state facilitation via KREDL / ESCOMs.",
            }
        ],
    },
    "Tamil Nadu": {
        "discoms": [
            {"code": "TANGEDCO", "name": "Tamil Nadu Generation and Distribution Corporation", "areas": ["State-wide"]},
        ],
        "default_discom": "TANGEDCO",
        "state_schemes": [
            {
                "code": "tn_rooftop",
                "name": "Tamil Nadu rooftop solar programme",
                "scope": "state",
                "note": "TANGEDCO interconnection; export often at feed-in style credit.",
            }
        ],
    },
    "Uttar Pradesh": {
        "discoms": [
            {"code": "PVVNL", "name": "Paschimanchal Vidyut Vitran Nigam Ltd", "areas": ["West UP", "Noida", "Ghaziabad", "Meerut"]},
            {"code": "MVVNL", "name": "Madhyanchal Vidyut Vitran Nigam Ltd", "areas": ["Central UP", "Lucknow"]},
            {"code": "PuVVNL", "name": "Purvanchal Vidyut Vitran Nigam Ltd", "areas": ["East UP", "Varanasi"]},
            {"code": "DVVNL", "name": "Dakshinanchal Vidyut Vitran Nigam Ltd", "areas": ["South UP", "Agra"]},
            {"code": "KESCO", "name": "Kanpur Electricity Supply Company", "areas": ["Kanpur"]},
        ],
        "default_discom": "PVVNL",
        "state_schemes": [
            {
                "code": "up_rooftop",
                "name": "Uttar Pradesh rooftop solar / net-metering",
                "scope": "state",
                "note": "UPNEDA / DISCOM rooftop solar facilitation.",
            }
        ],
    },
    "Gujarat": {
        "discoms": [
            {"code": "UGVCL", "name": "Uttar Gujarat Vij Company Ltd", "areas": ["North Gujarat"]},
            {"code": "MGVCL", "name": "Madhya Gujarat Vij Company Ltd", "areas": ["Central Gujarat"]},
            {"code": "PGVCL", "name": "Paschim Gujarat Vij Company Ltd", "areas": ["Saurashtra", "Kutch"]},
            {"code": "DGVCL", "name": "Dakshin Gujarat Vij Company Ltd", "areas": ["South Gujarat"]},
            {"code": "TORRENT_AHD", "name": "Torrent Power (Ahmedabad/Surat)", "areas": ["Ahmedabad", "Surat licensed areas"]},
        ],
        "default_discom": "UGVCL",
        "state_schemes": [
            {
                "code": "gj_surya",
                "name": "Gujarat Surya / rooftop facilitation",
                "scope": "state",
                "note": "Strong state push for rooftop; DISCOM net-metering.",
            }
        ],
    },
    "Rajasthan": {
        "discoms": [
            {"code": "JVVNL", "name": "Jaipur Vidyut Vitran Nigam Ltd", "areas": ["Jaipur", "Ajmer region parts"]},
            {"code": "AVVNL", "name": "Ajmer Vidyut Vitran Nigam Ltd", "areas": ["Ajmer"]},
            {"code": "JDVVNL", "name": "Jodhpur Vidyut Vitran Nigam Ltd", "areas": ["Jodhpur", "Western Rajasthan"]},
        ],
        "default_discom": "JVVNL",
        "state_schemes": [],
    },
    "West Bengal": {
        "discoms": [
            {"code": "WBSEDCL", "name": "West Bengal State Electricity Distribution Co. Ltd", "areas": ["State-wide (most areas)"]},
            {"code": "CESC", "name": "CESC Ltd", "areas": ["Kolkata", "Howrah licensed area"]},
        ],
        "default_discom": "WBSEDCL",
        "state_schemes": [],
    },
}

# City / district keywords → preferred DISCOM code (within state).
_CITY_DISCOM = {
    "bengaluru": ("Karnataka", "BESCOM"),
    "bangalore": ("Karnataka", "BESCOM"),
    "mysuru": ("Karnataka", "CESC"),
    "mysore": ("Karnataka", "CESC"),
    "mangaluru": ("Karnataka", "MESCOM"),
    "mangalore": ("Karnataka", "MESCOM"),
    "mumbai": ("Maharashtra", "ADANI_MUMBAI"),
    "thane": ("Maharashtra", "MSEDCL"),
    "pune": ("Maharashtra", "MSEDCL"),
    "nagpur": ("Maharashtra", "MSEDCL"),
    "chennai": ("Tamil Nadu", "TANGEDCO"),
    "coimbatore": ("Tamil Nadu", "TANGEDCO"),
    "noida": ("Uttar Pradesh", "PVVNL"),
    "greater noida": ("Uttar Pradesh", "PVVNL"),
    "ghaziabad": ("Uttar Pradesh", "PVVNL"),
    "lucknow": ("Uttar Pradesh", "MVVNL"),
    "kanpur": ("Uttar Pradesh", "KESCO"),
    "varanasi": ("Uttar Pradesh", "PuVVNL"),
    "agra": ("Uttar Pradesh", "DVVNL"),
    "new delhi": ("Delhi", "BRPL"),
    "delhi": ("Delhi", "BRPL"),
    "south delhi": ("Delhi", "BRPL"),
    "east delhi": ("Delhi", "BYPL"),
    "north delhi": ("Delhi", "TPDDL"),
    "ahmedabad": ("Gujarat", "TORRENT_AHD"),
    "surat": ("Gujarat", "TORRENT_AHD"),
    "jaipur": ("Rajasthan", "JVVNL"),
    "jodhpur": ("Rajasthan", "JDVVNL"),
    "kolkata": ("West Bengal", "CESC"),
    "howrah": ("West Bengal", "CESC"),
}

# Pincode prefix (first 2–3 digits) → state (India PIN system).
_PIN_PREFIX_STATE = {
    "11": "Delhi",
    "40": "Maharashtra",  # Mumbai region often 400xxx
    "41": "Maharashtra",
    "42": "Maharashtra",
    "44": "Maharashtra",
    "56": "Karnataka",
    "57": "Karnataka",
    "58": "Karnataka",
    "59": "Karnataka",
    "60": "Tamil Nadu",
    "61": "Tamil Nadu",
    "62": "Tamil Nadu",
    "63": "Tamil Nadu",
    "20": "Uttar Pradesh",
    "21": "Uttar Pradesh",
    "22": "Uttar Pradesh",
    "23": "Uttar Pradesh",
    "24": "Uttar Pradesh",
    "25": "Uttar Pradesh",
    "26": "Uttar Pradesh",
    "27": "Uttar Pradesh",
    "28": "Uttar Pradesh",
    "30": "Rajasthan",
    "31": "Rajasthan",
    "32": "Rajasthan",
    "33": "Rajasthan",
    "34": "Rajasthan",
    "36": "Gujarat",
    "37": "Gujarat",
    "38": "Gujarat",
    "39": "Gujarat",
    "70": "West Bengal",
    "71": "West Bengal",
    "72": "West Bengal",
    "73": "West Bengal",
}


def _norm(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _extract_pincode(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b([1-9][0-9]{5})\b", text)
    return m.group(1) if m else None


def resolve_state(
    state: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    pincode: Optional[str] = None,
) -> Optional[str]:
    if state:
        # Normalize casing to our keys
        for key in _STATE_DISCOM:
            if key.lower() == state.strip().lower():
                return key
        # Common aliases
        aliases = {
            "nct of delhi": "Delhi",
            "delhi ncr": "Delhi",
            "bangalore urban": "Karnataka",
            "tn": "Tamil Nadu",
            "up": "Uttar Pradesh",
            "mh": "Maharashtra",
            "ka": "Karnataka",
            "gj": "Gujarat",
            "rj": "Rajasthan",
            "wb": "West Bengal",
        }
        return aliases.get(_norm(state))

    pin = pincode or _extract_pincode(address)
    if pin:
        for n in (3, 2):
            st = _PIN_PREFIX_STATE.get(pin[:n])
            if st:
                return st

    blob = _norm(" ".join(filter(None, [city, address])))
    for key in _STATE_DISCOM:
        if key.lower() in blob:
            return key
    for city_key, (st, _) in _CITY_DISCOM.items():
        if city_key in blob:
            return st
    return None


def resolve_discom(
    state: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    pincode: Optional[str] = None,
) -> Dict[str, Any]:
    """Return best-effort DISCOM + scheme context for a location."""
    resolved_state = resolve_state(state, city, address, pincode)
    pin = pincode or _extract_pincode(address)
    blob = _norm(" ".join(filter(None, [city, address])))

    result: Dict[str, Any] = {
        "state": resolved_state,
        "pincode": pin,
        "discom_code": None,
        "discom_name": None,
        "discom_confidence": "low",
        "central_schemes": list(CENTRAL_SCHEMES),
        "state_schemes": [],
        "note": "Best-effort DISCOM match from public service areas — verify with your bill / DISCOM.",
        "found": False,
    }
    if not resolved_state or resolved_state not in _STATE_DISCOM:
        return result

    meta = _STATE_DISCOM[resolved_state]
    code = meta["default_discom"]
    confidence = "medium"

    # City-level override
    for city_key, (st, dcode) in _CITY_DISCOM.items():
        if st == resolved_state and city_key in blob:
            code = dcode
            confidence = "high"
            break

    discom_name = code
    for d in meta["discoms"]:
        if d["code"] == code:
            discom_name = d["name"]
            break

    result.update(
        {
            "state": resolved_state,
            "discom_code": code,
            "discom_name": discom_name,
            "discom_confidence": confidence,
            "state_schemes": list(meta.get("state_schemes") or []),
            "available_discoms": meta["discoms"],
            "found": True,
        }
    )
    return result


def lookup_location_context(
    state: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    pincode: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    planning_track: str = "home",
) -> Dict[str, Any]:
    """DISCOM + tariff defaults combined for UI pre-fill.

    latitude/longitude reserved for future boundary polygons; currently unused.
    """
    planning_track = "business" if planning_track == "business" else "home"
    discom = resolve_discom(state=state, city=city, address=address, pincode=pincode)
    if planning_track == "business":
        # Do not present residential incentive programmes as commercial inputs.
        discom["central_schemes"] = []
    tariff = {
        "rate_per_kwh": None,
        "net_metering_mode": None,
        "export_credit_rate": None,
        "utility_name": None,
    }
    if discom.get("state"):
        try:
            from app.services.estimation_service import lookup_tariff_defaults

            td = lookup_tariff_defaults(
                discom["state"], planning_track=planning_track
            )
            tariff = {
                "rate_per_kwh": td["rate_per_kwh"],
                "net_metering_mode": td["net_metering_mode"],
                "export_credit_rate": td["export_credit_rate"],
                "utility_name": td.get("utility_name"),
                "found": td.get("found", False),
                "basis": td.get("basis"),
            }
        except Exception:
            pass

    return {
        **discom,
        "tariff": tariff,
        "latitude": latitude,
        "longitude": longitude,
        "planning_track": planning_track,
    }

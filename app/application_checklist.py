"""Dynamic rooftop-solar application checklist by state / system / property type."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask_babel import gettext as _

from app.config import Config

NATIONAL_PORTAL = {
    "name": "National Portal for Rooftop Solar",
    "url": "https://pmsuryaghar.gov.in/",
    "note": "Apply for PM Surya Ghar central subsidy and track application status.",
}


def build_pm_surya_ghar_guidance(
    system_size_kw: Optional[float] = None,
    subsidy_amount: Optional[float] = None,
    discom_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Return shared, translated residential scheme guidance for web pages."""
    return {
        "system_size_kw": system_size_kw,
        "subsidy_amount": subsidy_amount,
        "discom_name": discom_name,
        "portal_url": "https://pmsuryaghar.gov.in/",
        "subsidy_rule": {
            "per_kw_first_2": Config.SUBSIDY_PER_KW_FIRST_2,
            "per_kw_extra_2_to_3": Config.SUBSIDY_EXTRA_2_TO_3,
            "cap": Config.SUBSIDY_CAP,
        },
        "steps": [
            {
                "title": _("Register on the official portal"),
                "detail": _("Create an account using your residential electricity connection."),
            },
            {
                "title": _("Submit the rooftop application"),
                "detail": _("Select your DISCOM and wait for feasibility approval before installation."),
            },
            {
                "title": _("Choose a DISCOM-registered vendor"),
                "detail": _("Agree the price directly with the vendor and complete installation."),
            },
            {
                "title": _("Complete inspection and claim"),
                "detail": _("Follow the portal and DISCOM process for net metering, inspection, and any eligible assistance."),
            },
        ],
        "keep_ready": [
            _("Consumer number"),
            _("Registered mobile"),
            _("Latest electricity bill"),
            _("Sanctioned load"),
            _("Roof ownership proof"),
        ],
    }


# Best-effort state energy / DISCOM portal links (public sites; may change).
STATE_PORTALS: Dict[str, Dict[str, str]] = {
    "Delhi": {
        "name": "IPGCL / Delhi DISCOM rooftop information",
        "url": "https://www.ipgcl-ppcl.com/",
    },
    "Maharashtra": {
        "name": "MSEDCL net-metering / rooftop",
        "url": "https://www.mahadiscom.in/",
    },
    "Karnataka": {
        "name": "KREDL / BESCOM rooftop solar",
        "url": "https://bescom.karnataka.gov.in/",
    },
    "Tamil Nadu": {
        "name": "TANGEDCO consumer portal",
        "url": "https://www.tangedco.org/",
    },
    "Uttar Pradesh": {
        "name": "UPNEDA / UP DISCOM portals",
        "url": "https://upneda.org.in/",
    },
    "Gujarat": {
        "name": "Gujarat Energy Development Agency (GEDA)",
        "url": "https://geda.gujarat.gov.in/",
    },
    "Rajasthan": {
        "name": "Rajasthan Renewable Energy Corporation",
        "url": "https://energy.rajasthan.gov.in/rrecl/",
    },
    "West Bengal": {
        "name": "WBSEDCL consumer portal",
        "url": "https://www.wbsedcl.in/",
    },
}


def build_application_checklist(
    state: Optional[str] = None,
    system_size_kw: Optional[float] = None,
    property_type: Optional[str] = None,
    discom_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ordered checklist steps with optional deep links."""
    prop = (property_type or "residential").lower()
    size = float(system_size_kw or 0)
    state_key = None
    if state:
        for k in STATE_PORTALS:
            if k.lower() == state.strip().lower():
                state_key = k
                break

    steps: List[Dict[str, Any]] = []

    # 1. National portal — residential / subsidy-eligible framing
    if prop in ("residential", "home", "domestic") and size > 0:
        steps.append(
            {
                "id": "national_portal",
                "title": "Apply on the National Portal",
                "detail": (
                    "Register on the National Portal for Rooftop Solar (PM Surya Ghar) "
                    "to start the central subsidy process."
                    + (
                        f" Your ~{size:.1f} kW system is within the residential planning range."
                        if size <= 10
                        else f" Your ~{size:.1f} kW system may need additional DISCOM review."
                    )
                ),
                "link": NATIONAL_PORTAL,
                "required": True,
            }
        )
    else:
        steps.append(
            {
                "id": "national_portal_info",
                "title": "Check central scheme eligibility",
                "detail": (
                    "PM Surya Ghar is aimed at residential consumers. "
                    "Commercial / other property types should confirm current MNRE / DISCOM schemes."
                ),
                "link": NATIONAL_PORTAL,
                "required": False,
            }
        )

    # 2. DISCOM feasibility
    discom_label = discom_name or (f"{state_key} DISCOM" if state_key else "your local DISCOM")
    steps.append(
        {
            "id": "discom_feasibility",
            "title": "Get DISCOM feasibility approval",
            "detail": (
                f"Submit a rooftop solar application to {discom_label} for technical "
                "feasibility / load feasibility on your service connection."
            ),
            "link": STATE_PORTALS.get(state_key) if state_key else None,
            "required": True,
        }
    )

    # 3. Vendor / installation
    steps.append(
        {
            "id": "choose_installer",
            "title": "Choose a vendor and complete installation",
            "detail": (
                "Select any empanelled or local installer of your choice. "
                "Use your SIGNIX installer-ready package (GeoJSON + metrics + PDF) when sharing the roof outline."
            ),
            "link": None,
            "required": True,
        }
    )

    # 4. Net meter
    steps.append(
        {
            "id": "net_meter",
            "title": "Schedule net-meter installation",
            "detail": (
                "After plant installation, request net-meter / bi-directional meter "
                f"installation and commissioning with {discom_label}."
            ),
            "link": STATE_PORTALS.get(state_key) if state_key else None,
            "required": True,
        }
    )

    # 5. Subsidy claim (residential)
    if prop in ("residential", "home", "domestic"):
        steps.append(
            {
                "id": "subsidy_claim",
                "title": "Claim central subsidy (if eligible)",
                "detail": (
                    "Complete portal steps after commissioning so subsidy can be processed. "
                    "Final amount is subject to MNRE / DISCOM approval."
                ),
                "link": NATIONAL_PORTAL,
                "required": False,
            }
        )

    # Size-specific note
    if size > 3:
        steps.append(
            {
                "id": "size_note",
                "title": "Confirm capacity limits with DISCOM",
                "detail": (
                    f"At ~{size:.1f} kW, confirm sanctioned load, transformer capacity, "
                    "and any state caps before finalizing the plant size."
                ),
                "link": STATE_PORTALS.get(state_key) if state_key else None,
                "required": False,
            }
        )

    return {
        "state": state_key or state,
        "property_type": prop,
        "system_size_kw": size,
        "national_portal": NATIONAL_PORTAL,
        "state_portal": STATE_PORTALS.get(state_key) if state_key else None,
        "steps": steps,
        "disclaimer": (
            "Checklist is guidance only — procedures and links change. "
            "Confirm current steps with your DISCOM and the National Portal."
        ),
    }

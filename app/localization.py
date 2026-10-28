"""Catalog markers for application-owned labels arriving as saved enum values.

Only presentation calls use these labels. Stored keys, user text, and provider
place names are never rewritten. Include ``-k N_`` when extracting messages.
"""
from flask_babel import gettext


def N_(message):
    return message


UI_TERMS = (
    N_("January"), N_("February"), N_("March"), N_("April"), N_("May"), N_("June"),
    N_("July"), N_("August"), N_("September"), N_("October"), N_("November"), N_("December"),
    N_("North"), N_("South"), N_("East"), N_("West"), N_("North-East"), N_("North-West"),
    N_("South-East"), N_("South-West"), N_("High"), N_("Medium"), N_("Low"), N_("Unknown"),
    N_("Excellent"), N_("Good"), N_("Fair"), N_("Poor"), N_("Very poor"), N_("Unavailable"),
    N_("Payback"), N_("Financial viability"), N_("Roof fit"), N_("Orientation"),
    N_("CO₂ reduction"), N_("Backup capability"),
    N_("Fastest payback"), N_("Maximum long-term savings"), N_("Environmental impact"),
    N_("Backup power"), N_("No strong preference"),
    N_("auto"), N_("roof max"), N_("bill offset 100%"), N_("bill offset 80%"),
    N_("bill offset 50%"), N_("fastest payback"), N_("max savings"),
    N_("flat"), N_("gable"), N_("hip"), N_("shed"), N_("mixed"),
    N_("on-grid"), N_("off-grid"), N_("hybrid"), N_("residential"), N_("commercial"),
    N_("Performance ratio"), N_("Peak sun hours"), N_("Tariff escalation"),
    N_("Discount rate"), N_("Annual O&M reserve"),
    N_("A representative monthly bill does not provide a defensible 12-month load profile."),
    N_("Strong lifetime savings outlook"), N_("Orientation is only fair for solar"),
    N_("Location coordinates"), N_("Location not provided"), N_("Not provided"),
    N_("Jan"), N_("Feb"), N_("Mar"), N_("Apr"), N_("Jun"), N_("Jul"),
    N_("Aug"), N_("Sep"), N_("Oct"), N_("Nov"), N_("Dec"),
    N_("No persisted financing comparison"), N_("Financing series is incomplete"),
    N_("Financing cash flows are unavailable"), N_("Financing cash-flow series are inconsistent"),
    N_("Financing cash flows are malformed"), N_("Consumption or export data unavailable"),
    N_("Bill consumption and tariff inputs are required"), N_("Monthly bill input unavailable"),
    N_("Persisted annual cashflow series unavailable"),
)


def ui_label(value):
    """Translate an application label without changing the corresponding key."""
    text = str(value or "")
    normalized = text.replace("_", " ")
    # Accept the saved ASCII enum while displaying the canonical Unicode label.
    if normalized.casefold() == "co2 reduction":
        normalized = "CO₂ reduction"
    canonical = next((term for term in UI_TERMS if term.casefold() == normalized.casefold()), text)
    return gettext(canonical)

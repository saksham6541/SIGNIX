from datetime import datetime, timezone
from threading import Lock

from flask import Blueprint, current_app, jsonify, request
from flask_babel import get_locale, gettext as _
from flask_login import current_user, login_required

from app.services.location_service import list_recent_locations

assistant_bp = Blueprint("assistant", __name__)

SYSTEM_PROMPT = """
You are the SIGNIX general FAQ assistant. SIGNIX helps people in India explore
rooftop solar options by estimating usable roof area, system size, generation,
savings, payback, environmental impact, and applicable subsidy. Do not access,
infer, or discuss any user's saved locations, profile, bills, or other personal
data except the explicit RATING_CONTEXT supplied for that authenticated user;
use that context only to explain their own suitability rating. Otherwise answer
general questions about how SIGNIX works and rooftop solar in India.

SIGNIX estimates use real solar irradiance data from NASA POWER or PVGIS when
available. If both services are unavailable, SIGNIX uses a location-sensitive
offline fallback based on the requested coordinates and clearly labels the
result as estimated fallback. The PM Surya Ghar Muft Bijli Yojana subsidy is a
central residential subsidy generally estimated as Rs 30,000 per kW for the
first 2 kW and Rs 18,000 for the additional kW from 2 to 3 kW, subject to the
published maximum of Rs 78,000 and the scheme's eligibility and implementation
rules. Explain that any subsidy shown by SIGNIX is an estimate, not a guarantee.

All SIGNIX figures are indicative estimates only. Users should verify current
eligibility, rates, approvals, and subsidy details with their DISCOM and MNRE
before procurement or installation. Keep answers short, normally 2-4
sentences unless genuinely more detail is needed. Stay on-topic to SIGNIX and
rooftop solar in India. Politely decline unrelated requests.
""".strip()

_count_lock = Lock()

RATING_EXPLANATION_PROMPT = """
When RATING_CONTEXT is present, explain the user's specific suitability rating,
not how ratings work in general. Start with the strongest positive factor and
the strongest limiting factor, then give only the supporting detail needed to
make those points clear; do not recite every input as a data dump. Mention an
actionable improvement only when the supplied factor and input data support it
(for example, a weak orientation factor or a low backup score with no battery
may support a targeted suggestion). Do not infer shading, obstruction effects,
or improvement from data that is not present. Preserve technical terms such as
kWh, kW, DISCOM, PM Surya Ghar, and tariff exactly in English in both English
and Hindi responses.
""".strip()

RATING_QUESTION_TERMS = (
    "my rating",
    "suitability score",
    "suitability rating",
    "my score",
    "roof good for solar",
    "house good for solar",
    "home good for solar",
    "solar work well here",
    "suitable for solar",
    "solar suitable here",
    "solar fit",
    "roof suitable",
    "रेटिंग",
    "स्कोर",
    "उपयुक्तता",
    "छत सोलर",
    "घर सोलर",
    "सोलर के लिए ठीक",
    "सोलर के लिए उपयुक्त",
    "यहां सोलर",
)


def _asks_about_rating(message):
    normalized = message.casefold()
    return any(term in normalized for term in RATING_QUESTION_TERMS)


def _latest_rating_context(user_id):
    location = next(
        (
            candidate
            for candidate in list_recent_locations(user_id, limit=100)
            if candidate.suitability_rating
        ),
        None,
    )
    if location is None:
        return None

    rating = location.suitability_rating
    extras = location.extras or {}
    bill_sizing = extras.get("bill_sizing") or {}
    return {
        "address": location.address,
        "overall": rating.get("overall_viability"),
        "confidence": rating.get("data_confidence"),
        "priority": rating.get("user_priority"),
        "weights": rating.get("weights"),
        "factors": rating.get("factors"),
        "inputs": {
            "orientation": location.orientation_label,
            "orientation_factor": location.orientation_factor,
            "roof_area_sqm": location.roof_area_sqm,
            "usable_area_sqm": location.usable_area_sqm,
            "obstructed_area_sqm": location.obstructed_area_sqm,
            "system_size_kw": location.system_size,
            "monthly_bill": bill_sizing.get("monthly_bill"),
            "tariff_per_kwh": bill_sizing.get("tariff_per_kwh"),
            "property_type": extras.get("property_type"),
            "battery_kwh": location.battery_kwh,
            "inverter_type": extras.get("inverter_type"),
            "irradiance_source": location.irradiance_source,
        },
    }


def _rating_prompt_context(message, user_id):
    if not _asks_about_rating(message):
        return ""
    import json

    rating_context = _latest_rating_context(user_id)
    if rating_context is None:
        return (
            "RATING_STATUS: The user has no saved estimate with a suitability "
            "rating yet. Tell them clearly that no rating is available yet and "
            "do not invent or provide a generic score."
        )
    return (
        "RATING_CONTEXT: This is the authenticated user's latest saved estimate. "
        "Explain only this rating and its actual factors; do not expose unrelated "
        "saved data.\n" + json.dumps(rating_context, ensure_ascii=False, sort_keys=True)
    )


def build_system_prompt(locale, rating_explanation=False):
    language = "Hindi" if str(locale).split("_")[0] == "hi" else "English"
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Respond in {language}. Keep technical names, official scheme names, "
        "units, and acronyms such as PM Surya Ghar, DISCOM, MNRE, kW, and "
        "kWh unchanged unless the user explicitly asks for an explanation."
    )
    if rating_explanation:
        prompt = f"{prompt}\n\n{RATING_EXPLANATION_PROMPT}"
    return prompt


def _consume_daily_message(user_id):
    today = datetime.now(timezone.utc).date()
    key = (user_id, today)
    with _count_lock:
        count_dates = current_app.extensions["assistant_count_dates"]
        message_counts = current_app.extensions["assistant_message_counts"]
        if count_dates.get(user_id) != today:
            count_dates[user_id] = today
            message_counts[key] = 0
        limit = current_app.config["ASSISTANT_DAILY_MESSAGE_LIMIT"]
        if message_counts[key] >= limit:
            return False
        message_counts[key] += 1
        return True


def _create_gemini_client(api_key):
    from google import genai

    return genai.Client(api_key=api_key)


@assistant_bp.post("/api/assistant")
@login_required
def assistant():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message")
    current_app.logger.info(
        "Assistant request user_id=%s transcript=%r",
        current_user.id,
        message,
    )
    if not isinstance(message, str) or not message.strip():
        current_app.logger.warning("Assistant request rejected: empty transcript")
        return jsonify(error=_("A non-empty message is required")), 400

    if not _consume_daily_message(current_user.id):
        current_app.logger.warning(
            "Assistant request rate-limited user_id=%s transcript=%r",
            current_user.id,
            message,
        )
        return jsonify(error=_("Daily assistant message limit reached")), 429

    rating_context = _rating_prompt_context(message, current_user.id)
    if rating_context.startswith("RATING_STATUS:"):
        return jsonify(
            response=_(
                "You do not have a saved suitability rating yet. Create an estimate first, and I can explain its score."
            )
        )

    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        current_app.logger.error(
            "Assistant request unavailable: missing API key transcript=%r",
            message,
        )
        return jsonify(error=_("The assistant is not configured")), 503

    try:
        client = _create_gemini_client(api_key)
        prompt_contents = message.strip()
        if rating_context:
            prompt_contents = f"{prompt_contents}\n\n{rating_context}"
        response = client.models.generate_content(
            model=current_app.config["GEMINI_MODEL"],
            contents=prompt_contents,
            config={
                "system_instruction": build_system_prompt(
                    get_locale(), rating_explanation=bool(rating_context)
                )
            },
        )
    except Exception as error:
        current_app.logger.error(
            "Gemini assistant exception transcript=%r error=%s: %s",
            message,
            type(error).__name__,
            str(error),
            exc_info=True,
        )
        current_app.logger.exception("Gemini assistant request failed")
        return jsonify(error=_("The assistant is temporarily unavailable")), 502

    current_app.logger.info(
        "Assistant response succeeded user_id=%s transcript=%r",
        current_user.id,
        message,
    )
    return jsonify(response=response.text)

from datetime import datetime, timezone
from threading import Lock

from flask import Blueprint, current_app, jsonify, request
from flask_babel import get_locale, gettext as _
from flask_login import current_user, login_required

assistant_bp = Blueprint("assistant", __name__)

SYSTEM_PROMPT = """
You are the SIGNIX general FAQ assistant. SIGNIX helps people in India explore
rooftop solar options by estimating usable roof area, system size, generation,
savings, payback, environmental impact, and applicable subsidy. Do not access,
infer, or discuss any user's saved locations, profile, bills, or other personal
data; answer only general questions about how SIGNIX works and rooftop solar
in India.

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


def build_system_prompt(locale):
    language = "Hindi" if str(locale).split("_")[0] == "hi" else "English"
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Respond in {language}. Keep technical names, official scheme names, "
        "units, and acronyms such as PM Surya Ghar, DISCOM, MNRE, kW, and "
        "kWh unchanged unless the user explicitly asks for an explanation."
    )


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

    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        current_app.logger.error(
            "Assistant request unavailable: missing API key transcript=%r",
            message,
        )
        return jsonify(error=_("The assistant is not configured")), 503

    try:
        client = _create_gemini_client(api_key)
        response = client.models.generate_content(
            model=current_app.config["GEMINI_MODEL"],
            contents=message.strip(),
            config={"system_instruction": build_system_prompt(get_locale())},
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

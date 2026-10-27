import json
import math
import re
import time
from datetime import datetime, timezone
from threading import Lock

from flask import Blueprint, current_app, jsonify, request
from flask_babel import get_locale, gettext as _
from flask_login import current_user, login_required

from app.services.assistant_ai_service import (
    ChatProviderError,
    create_openai_compatible_completion,
)
from app.services.location_service import list_recent_locations

assistant_bp = Blueprint("assistant", __name__)

SYSTEM_PROMPT = """
You are the SIGNIX general FAQ assistant. SIGNIX helps people in India explore
rooftop solar options by estimating usable roof area, system size, generation,
savings, payback, environmental impact, and applicable subsidy. Do not access,
infer, or discuss any user's saved locations, profile, bills, or other personal
data except the explicit RATING_CONTEXT or ESTIMATE_CONTEXT supplied by the
server for that authenticated user. Use only the supplied values, preserve
their planning track, and never calculate or infer missing financial or
technical figures. Otherwise answer general questions about how SIGNIX works
and rooftop solar in India.

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
    "रेटिंग",
    "स्कोर",
    "उपयुक्तता",
)
RATING_CONTEXT_PHRASES = (
    ("roof", "solar"),
    ("house", "solar"),
    ("home", "solar"),
    ("solar", "work well"),
    ("solar", "good for"),
    ("solar", "suitable"),
    ("solar", "fit"),
    ("छत", "सोलर"),
    ("छत", "सौर"),
    ("घर", "सोलर"),
    ("घर", "सौर"),
    ("सोलर", "ठीक"),
    ("सौर", "उपयुक्त"),
)
ESTIMATE_QUESTION_TERMS = (
    "my estimate",
    "my system",
    "my savings",
    "my subsidy",
    "my payback",
    "my generation",
    "my solar plan",
    "मेरे अनुमान",
    "मेरी बचत",
    "मेरी सब्सिडी",
    "मेरा सिस्टम",
    "मेरा सोलर",
)


def _asks_about_rating(message):
    normalized = message.casefold()
    return any(term in normalized for term in RATING_QUESTION_TERMS) or any(
        all(part in normalized for part in phrase)
        for phrase in RATING_CONTEXT_PHRASES
    )


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
    # Reuse shared factor narrative; backfill for older saved ratings
    top_pos = rating.get("top_positive_factor")
    top_lim = rating.get("top_limiting_factor")
    if not top_pos or not top_lim:
        try:
            from app.solar_logic import derive_top_factors
            p, lim = derive_top_factors(rating.get("factors") or {}, rating.get("weights"))
            top_pos = top_pos or p
            top_lim = top_lim or lim
        except Exception:
            pass
    return {
        "address": location.address,
        "overall": rating.get("overall_viability"),
        "confidence": rating.get("data_confidence"),
        "priority": rating.get("user_priority"),
        "weights": rating.get("weights"),
        "factors": rating.get("factors"),
        "top_positive_factor": top_pos,
        "top_limiting_factor": top_lim,
        "rating_algorithm_version": rating.get("rating_algorithm_version") or "1.0",
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


def _asks_about_estimate(message):
    normalized = message.casefold()
    return any(term in normalized for term in ESTIMATE_QUESTION_TERMS)


def _estimate_prompt_context(message, user_id):
    """Return only persisted, presentation-safe values for estimate questions."""
    if not _asks_about_estimate(message) or _asks_about_rating(message):
        return ""
    location = next(iter(list_recent_locations(user_id, limit=1)), None)
    if location is None:
        return (
            "ESTIMATE_STATUS: The user has no saved estimate yet. Say that clearly "
            "and do not invent a system size, savings, subsidy, cost, or payback."
        )

    extras = location.extras if isinstance(location.extras, dict) else {}
    planning_track = extras.get("planning_track")
    if planning_track not in {"home", "business"}:
        planning_track = (
            "business"
            if str(extras.get("property_type") or "").strip().lower()
            in {"commercial", "industrial", "msme", "factory"}
            else "home"
        )

    def finite_number(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    context = {
        "planning_track": planning_track,
        "address": location.address,
        "system_size_kw": finite_number(location.system_size),
        "annual_generation_kwh": finite_number(location.annual_generation),
        "system_cost": finite_number(location.system_cost),
        "battery_cost": finite_number(location.battery_cost),
        "net_investment": finite_number(location.net_investment),
        "monthly_savings": finite_number(location.monthly_savings),
        "payback_years": finite_number(location.payback_years),
        "annual_net_benefit": finite_number(extras.get("annual_net_benefit")),
        "annual_roi_pct": finite_number(extras.get("annual_roi_pct")),
        "discounted_payback_years": finite_number(
            extras.get("discounted_payback_years")
        ),
        "calculation_status": extras.get("calculation_status"),
        "financial_status": extras.get("financial_status"),
    }
    if planning_track == "home":
        context["subsidy_amount"] = finite_number(location.subsidy_amount)
    return (
        "ESTIMATE_CONTEXT: These are persisted values from the authenticated "
        "user's latest saved estimate. Use only non-null values, distinguish "
        "Home from Business, and do not infer missing results.\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
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
    """Increment DB-backed daily counter; return False if limit reached.

    Rows are keyed by (user_id, UTC date). A new calendar day naturally starts
    at zero because no row exists yet for that date.
    """
    from datetime import date

    from app.models import AssistantDailyUsage, db

    today = date.today()
    limit = current_app.config["ASSISTANT_DAILY_MESSAGE_LIMIT"]
    # Atomic-ish upsert: fetch-or-create, then increment under a short transaction.
    row = AssistantDailyUsage.query.filter_by(
        user_id=user_id, usage_date=today
    ).first()
    if row is None:
        row = AssistantDailyUsage(user_id=user_id, usage_date=today, message_count=0)
        db.session.add(row)
        try:
            db.session.flush()
        except Exception:
            # Concurrent insert race — re-read
            db.session.rollback()
            row = AssistantDailyUsage.query.filter_by(
                user_id=user_id, usage_date=today
            ).first()
            if row is None:
                row = AssistantDailyUsage(
                    user_id=user_id, usage_date=today, message_count=0
                )
                db.session.add(row)
                db.session.flush()

    if row.message_count >= limit:
        db.session.rollback()
        return False
    row.message_count += 1
    db.session.commit()
    return True


def _create_gemini_client(api_key, timeout_seconds):
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=max(1, timeout_seconds) * 1000),
    )


SUPPORTED_CHAT_PROVIDERS = {"groq", "openai", "gemini"}
_MODEL_NAME = re.compile(r"[A-Za-z0-9_.:/-]{1,120}")


def _provider_configuration(provider, config=None):
    config = current_app.config if config is None else config
    provider = str(provider or "").strip().lower()
    if provider not in SUPPORTED_CHAT_PROVIDERS:
        raise ChatProviderError(provider or "unknown", "provider_configuration")

    prefix = provider.upper()
    api_key = config.get(f"{prefix}_API_KEY")
    model = config.get(f"{prefix}_MODEL")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ChatProviderError(provider, "missing_configuration")
    if not isinstance(model, str) or not _MODEL_NAME.fullmatch(model.strip()):
        raise ChatProviderError(provider, "provider_configuration")
    return api_key.strip(), model.strip()


def _assistant_configuration_diagnostics(config):
    provider = str(config.get("AI_PROVIDER") or "").strip().lower()
    prefix = provider.upper() if provider else ""
    key_present = bool(prefix and str(config.get(f"{prefix}_API_KEY") or "").strip())
    try:
        _provider_configuration(provider, config=config)
    except ChatProviderError as error:
        category = error.category
    else:
        category = "configured"
    return {
        "provider": provider or "missing",
        "key_present": key_present,
        "category": category,
    }


@assistant_bp.record_once
def _log_assistant_configuration(state):
    diagnostics = _assistant_configuration_diagnostics(state.app.config)
    state.app.logger.info(
        "Assistant configuration provider=%s key_present=%s category=%s",
        diagnostics["provider"],
        diagnostics["key_present"],
        diagnostics["category"],
    )


def _is_recoverable_provider_error(error):
    if isinstance(error, ChatProviderError):
        return error.recoverable
    code = getattr(error, "code", None)
    error_name = type(error).__name__.lower()
    return (
        code in {408, 409, 425, 429, 500, 502, 503, 504}
        or "timeout" in error_name
        or any(term in error_name for term in ("connection", "network", "transport"))
    )


def _call_provider_once(provider, *, api_key, model, system_prompt, user_prompt):
    timeout_seconds = current_app.config.get("ASSISTANT_REQUEST_TIMEOUT", 8)
    if provider in {"groq", "openai"}:
        return create_openai_compatible_completion(
            provider=provider,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_seconds=timeout_seconds,
        )

    client = _create_gemini_client(api_key, timeout_seconds)
    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config={"system_instruction": system_prompt},
    )
    try:
        response_text = response.text
    except Exception as error:
        raise ChatProviderError(provider, "malformed_response") from error
    if not isinstance(response_text, str):
        raise ChatProviderError(provider, "malformed_response")
    if not response_text.strip():
        raise ChatProviderError(provider, "empty_response")
    return response_text.strip()


def _call_provider_with_retries(
    provider, *, system_prompt, user_prompt, max_retries_override=None
):
    api_key, model = _provider_configuration(provider)
    try:
        configured_retries = int(current_app.config.get("ASSISTANT_MAX_RETRIES", 1))
    except (TypeError, ValueError):
        configured_retries = 1
    if max_retries_override is not None:
        configured_retries = max_retries_override
    max_retries = max(0, min(configured_retries, 2))

    for attempt in range(max_retries + 1):
        try:
            return (
                _call_provider_once(
                    provider,
                    api_key=api_key,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                ),
                model,
            )
        except Exception as error:
            if attempt >= max_retries or not _is_recoverable_provider_error(error):
                raise
            current_app.logger.warning(
                "Assistant provider retry provider=%s attempt=%s error=%s",
                provider,
                attempt + 1,
                type(error).__name__,
            )
            time.sleep(0.15 * (attempt + 1))


def _provider_error_diagnostics(error):
    """Return only structured, non-sensitive identifiers from a provider error."""
    details = getattr(error, "details", None)
    if isinstance(details, dict) and isinstance(details.get("error"), dict):
        details = details["error"]
    if not isinstance(details, dict):
        details = {}

    provider_status = getattr(error, "status", None) or details.get("status")
    provider_reason = details.get("reason")
    safe_identifier = re.compile(r"[A-Za-z0-9_.-]{1,80}")
    if not isinstance(provider_status, str) or not safe_identifier.fullmatch(
        provider_status
    ):
        provider_status = None
    if not isinstance(provider_reason, str) or not safe_identifier.fullmatch(
        provider_reason
    ):
        provider_reason = None
    provider_error_code = None

    rejected_fields = set()
    for detail in details.get("details", []):
        if not isinstance(detail, dict):
            continue
        if provider_error_code is None:
            candidate_code = detail.get("code")
            if isinstance(candidate_code, str) and safe_identifier.fullmatch(
                candidate_code
            ):
                provider_error_code = candidate_code
        if provider_reason is None:
            candidate_reason = detail.get("reason")
            if isinstance(candidate_reason, str) and safe_identifier.fullmatch(
                candidate_reason
            ):
                provider_reason = candidate_reason
        violations = detail.get("fieldViolations", detail.get("field_violations", []))
        if not isinstance(violations, list):
            continue
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            field = violation.get("field")
            if isinstance(field, str) and re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_.\[\]-]{0,79}", field
            ):
                rejected_fields.add(field)

    if provider_error_code is None:
        candidate_code = details.get("code")
        if isinstance(candidate_code, str) and safe_identifier.fullmatch(
            candidate_code
        ):
            provider_error_code = candidate_code

    return (
        provider_status,
        provider_reason,
        provider_error_code,
        sorted(rejected_fields),
    )


def _provider_error_response(error):
    """Map provider failures to stable, non-sensitive assistant API errors."""
    if isinstance(error, ChatProviderError):
        normalized = {
            "missing_configuration": (
                _("The assistant is not configured"),
                503,
                "missing_configuration",
            ),
            "provider_configuration": (
                _("The assistant configuration is invalid."),
                503,
                "provider_configuration",
            ),
            "provider_auth": (
                _("The assistant provider is not configured correctly."),
                503,
                "provider_auth",
            ),
            "provider_rate_limited": (
                _("The assistant provider is busy. Please try again later."),
                503,
                "provider_rate_limited",
            ),
            "provider_timeout": (
                _("The assistant request timed out. Please try again."),
                504,
                "provider_timeout",
            ),
            "provider_connection": (
                _("Unable to reach the assistant provider."),
                502,
                "provider_connection",
            ),
            "provider_unavailable": (
                _("The assistant provider is temporarily unavailable."),
                502,
                "provider_unavailable",
            ),
            "provider_request_rejected": (
                _("The assistant is temporarily unavailable."),
                502,
                "provider_request_rejected",
            ),
            "malformed_response": (
                _("The assistant returned an invalid response"),
                502,
                "malformed_response",
            ),
            "empty_response": (
                _("The assistant returned an empty response"),
                502,
                "empty_response",
            ),
        }
        return normalized.get(
            error.category,
            (_("The assistant is temporarily unavailable."), 502, "provider_unavailable"),
        )
    code = getattr(error, "code", None)
    error_name = type(error).__name__.lower()
    provider_status, _provider_reason, _provider_error_code, rejected_fields = (
        _provider_error_diagnostics(error)
    )
    model_rejected = (
        any(field == "model" or field.startswith("model.") for field in rejected_fields)
        or (code == 404 and provider_status in {"NOT_FOUND", "MODEL_NOT_FOUND"})
    )

    if code in {401, 403}:
        return _("The assistant provider is not configured correctly."), 503, "provider_auth"
    if code == 429:
        return _("The assistant provider is busy. Please try again later."), 503, "provider_rate_limited"
    if code in {408, 504} or "timeout" in error_name:
        return _("The assistant request timed out. Please try again."), 504, "provider_timeout"
    if code is not None and 400 <= code < 500:
        if model_rejected:
            return _("The assistant configuration is invalid."), 503, "provider_configuration"
        return _("The assistant is temporarily unavailable."), 502, "provider_request_rejected"
    if code is not None and code >= 500:
        return _("The assistant provider is temporarily unavailable."), 502, "provider_unavailable"
    if any(term in error_name for term in ("connection", "network", "transport")):
        return _("Unable to reach the assistant provider."), 502, "provider_connection"
    return _("The assistant is temporarily unavailable."), 502, "provider_unavailable"


@assistant_bp.post("/api/assistant")
@login_required
def assistant():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}
    message = payload.get("message")
    current_app.logger.info("Assistant request user_id=%s", current_user.id)
    if not isinstance(message, str) or not message.strip():
        current_app.logger.warning("Assistant request rejected: empty transcript")
        return jsonify(error=_("A non-empty message is required")), 400
    if len(message.strip()) > 2000:
        current_app.logger.warning("Assistant request rejected: message too long")
        return jsonify(error=_("Message must be 2,000 characters or fewer")), 400

    rating_context = _rating_prompt_context(message, current_user.id)
    if rating_context.startswith("RATING_STATUS:"):
        return jsonify(
            response=_(
                "You do not have a saved suitability rating yet. Create an estimate first, and I can explain its score."
            )
        )
    estimate_context = _estimate_prompt_context(message, current_user.id)
    if estimate_context.startswith("ESTIMATE_STATUS:"):
        return jsonify(
            response=_(
                "You do not have a saved estimate yet. Create an estimate first, and I can explain its saved results."
            )
        )

    provider = str(current_app.config.get("AI_PROVIDER") or "").strip().lower()
    try:
        _provider_configuration(provider)
    except Exception as error:
        message_text, response_status, response_code = _provider_error_response(error)
        diagnostics = _assistant_configuration_diagnostics(current_app.config)
        current_app.logger.error(
            "Assistant configuration rejected provider=%s key_present=%s category=%s",
            diagnostics["provider"],
            diagnostics["key_present"],
            diagnostics["category"],
        )
        return jsonify(error=message_text, code=response_code), response_status

    if not _consume_daily_message(current_user.id):
        current_app.logger.warning(
            "Assistant request rate-limited user_id=%s",
            current_user.id,
        )
        return jsonify(error=_("Daily assistant message limit reached")), 429

    prompt_contents = message.strip()
    prompt_context = rating_context or estimate_context
    if prompt_context:
        prompt_contents = f"{prompt_contents}\n\n{prompt_context}"
    system_prompt = build_system_prompt(
        get_locale(), rating_explanation=bool(rating_context)
    )

    fallback_used = False
    response_provider = provider
    try:
        response_text, model = _call_provider_with_retries(
            provider,
            system_prompt=system_prompt,
            user_prompt=prompt_contents,
        )
    except Exception as primary_error:
        openai_key = current_app.config.get("OPENAI_API_KEY")
        may_fallback = (
            provider != "openai"
            and isinstance(openai_key, str)
            and bool(openai_key.strip())
            and _is_recoverable_provider_error(primary_error)
        )
        if may_fallback:
            current_app.logger.warning(
                "Assistant primary provider unavailable; trying configured fallback "
                "provider=%s fallback=openai error=%s",
                provider,
                type(primary_error).__name__,
            )
            try:
                response_text, model = _call_provider_with_retries(
                    "openai",
                    system_prompt=system_prompt,
                    user_prompt=prompt_contents,
                    max_retries_override=0,
                )
                fallback_used = True
                response_provider = "openai"
            except Exception as fallback_error:
                error = fallback_error
            else:
                error = None
        else:
            error = primary_error

        if error is not None:
            message_text, response_status, response_code = _provider_error_response(error)
            provider_status, provider_reason, provider_error_code, rejected_fields = (
                _provider_error_diagnostics(error)
            )
            provider_http_status = (
                error.code if type(getattr(error, "code", None)) is int else "unknown"
            )
            provider_request_id = getattr(error, "request_id", None) or "unknown"
            failed_provider = getattr(error, "provider", response_provider)
            safe_model = current_app.config.get(f"{str(failed_provider).upper()}_MODEL")
            safe_model = safe_model if isinstance(safe_model, str) and _MODEL_NAME.fullmatch(safe_model) else "invalid"
            current_app.logger.error(
                "Assistant provider request failed provider=%s error=%s "
                "response_status=%s provider_http_status=%s provider_status=%s "
                "provider_error_code=%s provider_reason=%s provider_request_id=%s "
                "rejected_fields=%s model=%s",
                failed_provider,
                type(error).__name__,
                response_status,
                provider_http_status,
                provider_status or "unknown",
                provider_error_code if provider_error_code is not None else "unknown",
                provider_reason or "unknown",
                provider_request_id,
                ",".join(rejected_fields) or "unknown",
                safe_model,
            )
            return jsonify(error=message_text, code=response_code), response_status

    current_app.logger.info(
        "Assistant response succeeded user_id=%s provider=%s fallback=%s model=%s",
        current_user.id,
        response_provider,
        fallback_used,
        model,
    )
    return jsonify(
        response=response_text,
        provider=response_provider,
        fallback_used=fallback_used,
    )

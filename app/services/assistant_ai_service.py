"""OpenAI-compatible chat transport used by the SIGNIX assistant.

Provider credentials remain server-side.  Errors are normalized before they
reach the route so response bodies and authentication details are never
returned to the browser or written to application logs.
"""

import re

import requests


OPENAI_COMPATIBLE_PROVIDERS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}
RECOVERABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,120}")


class ChatProviderError(RuntimeError):
    """A provider failure containing only safe, normalized diagnostics."""

    def __init__(
        self,
        provider,
        category,
        *,
        status_code=None,
        recoverable=False,
        request_id=None,
    ):
        super().__init__(category)
        self.provider = provider
        self.category = category
        self.code = status_code
        self.recoverable = bool(recoverable)
        self.request_id = request_id if _safe_identifier(request_id) else None


def _safe_identifier(value):
    return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else None


def _request_id(response):
    for header in ("x-request-id", "groq-request-id", "request-id"):
        value = _safe_identifier(response.headers.get(header))
        if value:
            return value
    return None


def _category_for_status(status_code):
    if status_code in {401, 403}:
        return "provider_auth"
    if status_code == 429:
        return "provider_rate_limited"
    if status_code in {408, 504}:
        return "provider_timeout"
    if status_code in {400, 404, 422}:
        return "provider_configuration"
    if status_code >= 500:
        return "provider_unavailable"
    return "provider_request_rejected"


def create_openai_compatible_completion(
    *,
    provider,
    api_key,
    model,
    system_prompt,
    user_prompt,
    timeout_seconds,
):
    """Make one bounded Chat Completions request and return response text."""
    endpoint = OPENAI_COMPATIBLE_PROVIDERS.get(provider)
    if endpoint is None:
        raise ChatProviderError(provider, "provider_configuration")

    timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_completion_tokens": 500,
            },
            timeout=(min(3.0, timeout_seconds), timeout_seconds),
        )
    except requests.Timeout as error:
        raise ChatProviderError(
            provider, "provider_timeout", status_code=504, recoverable=True
        ) from error
    except requests.ConnectionError as error:
        raise ChatProviderError(
            provider, "provider_connection", recoverable=True
        ) from error
    except requests.RequestException as error:
        raise ChatProviderError(
            provider, "provider_unavailable", recoverable=True
        ) from error

    if not response.ok:
        status_code = int(response.status_code)
        raise ChatProviderError(
            provider,
            _category_for_status(status_code),
            status_code=status_code,
            recoverable=status_code in RECOVERABLE_STATUS_CODES,
            request_id=_request_id(response),
        )

    try:
        payload = response.json()
        choices = payload.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
    except (AttributeError, IndexError, TypeError, ValueError) as error:
        raise ChatProviderError(provider, "malformed_response") from error

    if not isinstance(content, str):
        raise ChatProviderError(provider, "malformed_response")
    if not content.strip():
        raise ChatProviderError(provider, "empty_response")
    return content.strip()

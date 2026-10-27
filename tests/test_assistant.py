from unittest.mock import Mock, PropertyMock, patch

import pytest

from app.assistant import _asks_about_rating
from app.services.assistant_ai_service import ChatProviderError

from app.models import UserLocation, db


class ProviderFailure(Exception):
    def __init__(self, code, status=None, details=None):
        super().__init__("provider detail that must not reach the client")
        self.code = code
        self.status = status
        self.details = details


@pytest.fixture(autouse=True)
def configure_retained_gemini_path(app):
    """Keep legacy Gemini tests explicit now that production defaults to Groq."""
    app.config["AI_PROVIDER"] = "gemini"
    app.config["ASSISTANT_MAX_RETRIES"] = 0


def _system_instruction(gemini_client):
    return gemini_client.models.generate_content.call_args.kwargs["config"][
        "system_instruction"
    ]


def test_assistant_requires_login(app):
    response = app.test_client().post(
        "/api/assistant", json={"message": "How does SIGNIX work?"}
    )

    assert response.status_code == 401
    assert response.get_json() == {
        "error": "Authentication required. Please log in and try again.",
        "login_url": "/login",
    }


def test_assistant_returns_gemini_response(client, app):
    gemini_response = Mock(text="SIGNIX provides indicative rooftop solar estimates.")
    gemini_client = Mock()
    gemini_client.models.generate_content.return_value = gemini_response

    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post(
            "/api/assistant", json={"message": "How does SIGNIX work?"}
        )

    assert response.status_code == 200
    assert response.get_json()["response"] == (
        "SIGNIX provides indicative rooftop solar estimates."
    )
    assert response.get_json()["provider"] == "gemini"
    assert response.get_json()["fallback_used"] is False
    gemini_client.models.generate_content.assert_called_once()
    assert gemini_client.models.generate_content.call_args.kwargs["contents"] == (
        "How does SIGNIX work?"
    )
    assert "Respond in English." in _system_instruction(gemini_client)
    assert "PM Surya Ghar, DISCOM, MNRE, kW, and kWh unchanged" in _system_instruction(
        gemini_client
    )


def test_assistant_missing_credentials_is_explicit(client, app):
    app.config["GEMINI_API_KEY"] = None

    response = client.post("/api/assistant", json={"message": "How does solar work?"})

    assert response.status_code == 503
    assert response.get_json() == {
        "code": "missing_configuration",
        "error": "The assistant is not configured",
    }


@pytest.mark.parametrize(
    "payload",
    [None, [], "message", {"message": None}, {"message": "   "}],
)
def test_assistant_rejects_malformed_or_empty_user_messages(client, payload):
    response = client.post("/api/assistant", json=payload)

    assert response.status_code == 400
    assert response.get_json()["error"] == "A non-empty message is required"


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (ProviderFailure(401), 503, "provider_auth"),
        (ProviderFailure(429), 503, "provider_rate_limited"),
        (TimeoutError("late"), 504, "provider_timeout"),
        (ProviderFailure(503), 502, "provider_unavailable"),
    ],
)
def test_assistant_maps_provider_failures_without_leaking_details(
    client, app, failure, status, code
):
    gemini_client = Mock()
    gemini_client.models.generate_content.side_effect = failure
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == status
    assert response.get_json()["code"] == code
    assert "provider detail" not in response.get_json()["error"]


def test_assistant_identifies_model_rejection_without_logging_secrets(
    client, app, caplog
):
    failure = ProviderFailure(
        400,
        status="INVALID_ARGUMENT",
        details={
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "details": [
                    {
                        "code": "INVALID_MODEL",
                        "reason": "MODEL_NOT_FOUND",
                        "fieldViolations": [
                            {
                                "field": "model",
                                "description": "private provider details",
                            }
                        ]
                    }
                ],
            }
        },
    )
    gemini_client = Mock()
    gemini_client.models.generate_content.side_effect = failure
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-api-secret"
        app.config["GEMINI_MODEL"] = "gemini-invalid"
        response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 503
    assert response.get_json()["code"] == "provider_configuration"
    assert "provider=gemini" in caplog.text
    assert "model=gemini-invalid" in caplog.text
    assert "provider_http_status=400" in caplog.text
    assert "provider_status=INVALID_ARGUMENT" in caplog.text
    assert "provider_error_code=INVALID_MODEL" in caplog.text
    assert "provider_reason=MODEL_NOT_FOUND" in caplog.text
    assert "rejected_fields=model" in caplog.text
    assert "test-api-secret" not in caplog.text
    assert "private provider details" not in caplog.text


def test_assistant_does_not_label_unidentified_provider_400_as_configuration(
    client, app
):
    gemini_client = Mock()
    gemini_client.models.generate_content.side_effect = ProviderFailure(400)
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 502
    assert response.get_json()["code"] == "provider_request_rejected"
    assert response.get_json()["error"] != "The assistant configuration is invalid."


def test_assistant_rejects_empty_model_configuration_safely(client, app, caplog):
    app.config["GEMINI_API_KEY"] = "test-key"
    app.config["GEMINI_MODEL"] = " "

    response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 503
    assert response.get_json()["code"] == "provider_configuration"
    assert "configuration rejected provider=gemini" in caplog.text
    assert "test-key" not in caplog.text


@pytest.mark.parametrize("provider_text", [None, "", "   "])
def test_assistant_rejects_empty_provider_output(client, app, provider_text):
    gemini_client = Mock()
    gemini_client.models.generate_content.return_value = Mock(text=provider_text)
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 502
    assert response.get_json()["code"] == "empty_response"


def test_assistant_rejects_malformed_provider_output(client, app):
    provider_response = Mock()
    type(provider_response).text = PropertyMock(side_effect=ValueError("bad response"))
    gemini_client = Mock()
    gemini_client.models.generate_content.return_value = provider_response
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 502
    assert response.get_json()["code"] == "malformed_response"


def test_assistant_rejects_oversized_message_without_provider_call(client, app):
    gemini_client = Mock()
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post("/api/assistant", json={"message": "x" * 2001})

    assert response.status_code == 400
    assert "2,000" in response.get_json()["error"]
    gemini_client.models.generate_content.assert_not_called()


def test_assistant_uses_hindi_for_hindi_locale(client, app):
    gemini_response = Mock(text="SIGNIX सोलर अनुमान प्रदान करता है।")
    gemini_client = Mock()
    gemini_client.models.generate_content.return_value = gemini_response

    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        assert client.get("/language/hi").status_code == 302
        response = client.post(
            "/api/assistant", json={"message": "SIGNIX कैसे काम करता है?"}
        )

    assert response.status_code == 200
    assert response.get_json()["response"] == "SIGNIX सोलर अनुमान प्रदान करता है।"
    assert "Respond in Hindi." in _system_instruction(gemini_client)
    assert "PM Surya Ghar, DISCOM, MNRE, kW, and kWh unchanged" in _system_instruction(
        gemini_client
    )


def test_assistant_rejects_missing_csrf_token_when_csrf_is_enabled(client, app):
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["GEMINI_API_KEY"] = "test-key"

    response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 400


def test_assistant_blocks_21st_message_for_same_user(client, app):
    gemini_response = Mock(text="A short answer.")
    gemini_client = Mock()
    gemini_client.models.generate_content.return_value = gemini_response

    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        messages = ["Voice transcript: How does solar work?"] + ["Question"] * 20
        responses = [
            client.post("/api/assistant", json={"message": message})
            for message in messages
        ]

    assert [response.status_code for response in responses[:20]] == [200] * 20
    assert responses[20].status_code == 429
    assert gemini_client.models.generate_content.call_count == 20


def test_assistant_explains_authenticated_users_saved_rating(client, app):
    rating = {
        "overall_viability": {"tier": "good", "score": 72},
        "data_confidence": {"tier": "high", "score": 100},
        "user_priority": "fastest_payback",
        "weights": {"payback": 40, "orientation": 10},
        "factors": {
            "payback": {"tier": "good", "score": 75},
            "orientation": {"tier": "fair", "score": 50, "factor": 0.72},
        },
    }
    location = UserLocation(
        user_id=1,
        address="My rooftop",
        latitude=28.6,
        longitude=77.2,
        system_size=3,
        annual_generation=4000,
        roof_area_sqm=40,
        usable_area_sqm=32,
        obstructed_area_sqm=8,
        net_investment=200000,
        monthly_savings=3000,
        co2_reduction_tons=4,
        orientation_label="East",
        orientation_factor=0.72,
        battery_kwh=5,
        irradiance_source="pvgis",
        suitability_rating=rating,
        extras={
            "property_type": "residential",
            "inverter_type": "Hybrid",
            "bill_sizing": {"monthly_bill": 5000, "tariff_per_kwh": 10},
        },
    )
    with app.app_context():
        db.session.add(location)
        db.session.commit()

    gemini_response = Mock(text="Your 72/100 rating is good.")
    gemini_client = Mock()
    gemini_client.models.generate_content.return_value = gemini_response
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post(
            "/api/assistant", json={"message": "Why is my suitability score 72?"}
        )

    assert response.status_code == 200
    contents = gemini_client.models.generate_content.call_args.kwargs["contents"]
    assert "RATING_CONTEXT" in contents
    assert '"score": 72' in contents
    assert '"factor": 0.72' in contents
    assert '"orientation": "East"' in contents
    assert '"obstructed_area_sqm": 8.0' in contents
    instruction = _system_instruction(gemini_client)
    assert "strongest positive factor" in instruction
    assert "Do not infer shading" in instruction
    assert "kWh, kW, DISCOM, PM Surya Ghar, and tariff" in instruction


def test_assistant_rating_question_without_estimate_is_clear(client, app):
    gemini_client = Mock()
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        response = client.post(
            "/api/assistant", json={"message": "What is my suitability rating?"}
        )

    assert response.status_code == 200
    assert (
        "do not have a saved suitability rating yet" in response.get_json()["response"]
    )
    gemini_client.models.generate_content.assert_not_called()


@pytest.mark.parametrize(
    "message",
    [
        "is my roof good for solar",
        "is my house good for solar",
        "will solar work well here",
        "क्या मेरी छत सोलर के लिए ठीक है",
    ],
)
def test_natural_suitability_phrasing_is_rating_intent(message):
    assert _asks_about_rating(message) is True


def test_rating_explanations_change_with_different_profiles(client, app):
    def add_rating(**values):
        location = UserLocation(
            user_id=1,
            address="Profile rooftop",
            latitude=28.6,
            longitude=77.2,
            system_size=values["system_size"],
            annual_generation=4000,
            roof_area_sqm=values["roof_area"],
            usable_area_sqm=values["usable_area"],
            obstructed_area_sqm=values["obstructed_area"],
            net_investment=200000,
            monthly_savings=3000,
            co2_reduction_tons=4,
            orientation_label=values["orientation"],
            orientation_factor=values["orientation_factor"],
            suitability_rating=values["rating"],
            extras={"property_type": "residential"},
        )
        db.session.add(location)
        db.session.commit()

    with app.app_context():
        add_rating(
            system_size=3,
            roof_area=35,
            usable_area=30,
            obstructed_area=5,
            orientation="South",
            orientation_factor=1.0,
            rating={
                "overall_viability": {"tier": "excellent", "score": 90},
                "data_confidence": {"tier": "high", "score": 100},
                "factors": {
                    "orientation": {"tier": "excellent", "score": 100, "factor": 1.0},
                    "roof_fit": {"tier": "good", "score": 75, "usable_area_per_kw": 10},
                },
            },
        )

    gemini_client = Mock()

    def explain(*args, **kwargs):
        contents = kwargs["contents"]
        if '"orientation": "South"' in contents:
            return Mock(
                text="Your strongest positive is the South orientation, which supports high yield."
            )
        return Mock(
            text="Your strongest limitation is roof fit; the high obstruction leaves less usable area per kW."
        )

    gemini_client.models.generate_content.side_effect = explain
    with patch("app.assistant._create_gemini_client", return_value=gemini_client):
        app.config["GEMINI_API_KEY"] = "test-key"
        positive_response = client.post(
            "/api/assistant", json={"message": "Explain my suitability rating"}
        )

        with app.app_context():
            add_rating(
                system_size=3,
                roof_area=35,
                usable_area=12,
                obstructed_area=23,
                orientation="East",
                orientation_factor=0.65,
                rating={
                    "overall_viability": {"tier": "poor", "score": 35},
                    "data_confidence": {"tier": "high", "score": 100},
                    "factors": {
                        "orientation": {"tier": "poor", "score": 25, "factor": 0.65},
                        "roof_fit": {
                            "tier": "poor",
                            "score": 25,
                            "usable_area_per_kw": 4,
                        },
                    },
                },
            )
        limiting_response = client.post(
            "/api/assistant", json={"message": "Explain my suitability rating"}
        )

    assert (
        positive_response.get_json()["response"]
        != limiting_response.get_json()["response"]
    )
    assert "South orientation" in positive_response.get_json()["response"]
    assert "roof fit" in limiting_response.get_json()["response"]


def test_assistant_uses_groq_openai_compatible_provider(client, app):
    app.config.update(
        AI_PROVIDER="groq",
        GROQ_API_KEY="test-groq-key",
        GROQ_MODEL="openai/gpt-oss-20b",
    )
    with patch(
        "app.assistant.create_openai_compatible_completion",
        return_value="A grounded SIGNIX answer.",
    ) as completion:
        response = client.post("/api/assistant", json={"message": "How does SIGNIX work?"})

    assert response.status_code == 200
    assert response.get_json()["provider"] == "groq"
    assert response.get_json()["fallback_used"] is False
    assert completion.call_args.kwargs["model"] == "openai/gpt-oss-20b"
    assert "test-groq-key" == completion.call_args.kwargs["api_key"]


def test_assistant_falls_back_to_openai_only_for_recoverable_failure(client, app):
    app.config.update(
        AI_PROVIDER="groq",
        GROQ_API_KEY="test-groq-key",
        GROQ_MODEL="openai/gpt-oss-20b",
        OPENAI_API_KEY="test-openai-key",
        OPENAI_MODEL="gpt-5-mini",
        ASSISTANT_MAX_RETRIES=0,
    )

    def complete(**kwargs):
        if kwargs["provider"] == "groq":
            raise ChatProviderError(
                "groq",
                "provider_rate_limited",
                status_code=429,
                recoverable=True,
            )
        return "OpenAI fallback answer."

    with patch("app.assistant.create_openai_compatible_completion", side_effect=complete):
        response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 200
    assert response.get_json() == {
        "response": "OpenAI fallback answer.",
        "provider": "openai",
        "fallback_used": True,
    }


def test_assistant_does_not_fallback_for_provider_auth_failure(client, app):
    app.config.update(
        AI_PROVIDER="groq",
        GROQ_API_KEY="test-groq-key",
        OPENAI_API_KEY="test-openai-key",
        ASSISTANT_MAX_RETRIES=0,
    )
    with patch(
        "app.assistant.create_openai_compatible_completion",
        side_effect=ChatProviderError(
            "groq", "provider_auth", status_code=401, recoverable=False
        ),
    ) as completion:
        response = client.post("/api/assistant", json={"message": "Question"})

    assert response.status_code == 503
    assert response.get_json()["code"] == "provider_auth"
    assert completion.call_count == 1

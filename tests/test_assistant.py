from unittest.mock import Mock, patch

from app.assistant import _asks_about_rating
from app.models import UserLocation, db


def _system_instruction(gemini_client):
    return gemini_client.models.generate_content.call_args.kwargs["config"][
        "system_instruction"
    ]


def test_assistant_requires_login(app):
    response = app.test_client().post(
        "/api/assistant", json={"message": "How does SIGNIX work?"}
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


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
    assert response.get_json() == {
        "response": "SIGNIX provides indicative rooftop solar estimates."
    }
    gemini_client.models.generate_content.assert_called_once()
    assert gemini_client.models.generate_content.call_args.kwargs["contents"] == (
        "How does SIGNIX work?"
    )
    assert "Respond in English." in _system_instruction(gemini_client)
    assert "PM Surya Ghar, DISCOM, MNRE, kW, and kWh unchanged" in _system_instruction(
        gemini_client
    )


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


def test_natural_rating_rephrasings_use_rating_intent():
    prompts = [
        "is my house good for solar",
        "will solar work well here",
        "क्या मेरी छत सोलर के लिए ठीक है",
        "is my roof suitable for solar",
        "क्या यहां सोलर उपयुक्त है",
    ]

    assert all(_asks_about_rating(prompt) for prompt in prompts)

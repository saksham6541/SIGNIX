from unittest.mock import Mock, patch


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

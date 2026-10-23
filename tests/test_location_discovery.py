from unittest.mock import Mock, patch

from app.services import location_service


def _response(payload, url="https://example.test/"):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    response.url = url
    return response


def test_geocode_provider_failure_never_returns_hardcoded_location():
    with patch.object(
        location_service.requests,
        "get",
        side_effect=[_response([]), _response({"features": []})],
    ):
        result = location_service.geocode("A rooftop that is not a provider result")

    assert result["results"] == []
    assert result["source"] == "unavailable"
    assert "unavailable" in result["error"].lower()
    assert "28.6139" not in str(result)
    assert "77.209" not in str(result)


def test_geocode_preserves_provider_coordinates_and_address_metadata():
    provider_row = {
        "display_name": "Connaught Place, New Delhi, Delhi, India",
        "lat": "28.63150",
        "lon": "77.21670",
        "type": "commercial",
        "importance": 0.7,
        "address": {
            "road": "Connaught Place",
            "city": "New Delhi",
            "state": "Delhi",
            "postcode": "110001",
        },
    }
    with patch.object(
        location_service.requests,
        "get",
        return_value=_response([provider_row]),
    ):
        result = location_service.geocode("Connaught Place")

    assert result["source"] == "nominatim"
    assert result["results"][0]["lat"] == 28.6315
    assert result["results"][0]["lon"] == 77.2167
    assert result["results"][0]["state"] == "Delhi"
    assert result["results"][0]["postcode"] == "110001"


def test_parse_maps_url_preserves_latitude_longitude_order():
    with patch.object(location_service, "reverse_address", return_value="New Delhi"):
        data, status = location_service.parse_maps_url(
            "https://www.google.com/maps/@28.6315,77.2167,19z"
        )

    assert status == 200
    assert data["lat"] == 28.6315
    assert data["lon"] == 77.2167
    assert data["source"] == "maps_url"


def test_reports_route_redirects_to_properties(client):
    response = client.get("/reports")

    assert response.status_code == 302
    assert response.headers["Location"] == "/properties"


def test_saved_properties_page_has_combined_property_actions_and_compare(client):
    response = client.get("/properties")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "My properties" in body
    assert "Compare scenarios" in body
    assert 'id="properties-nav-toggle"' in body
    assert 'href="/properties#compare"' in body


def test_saved_locations_route_redirects_to_properties(client):
    response = client.get("/saved-locations")

    assert response.status_code == 302
    assert response.headers["Location"] == "/properties"


def test_dashboard_uses_persisted_summary_values(client):
    response = client.get("/dashboard")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Your solar planning workspace" in body
    assert "Saved rooftops" in body
    assert "Planned capacity" in body
    assert 'href="/survey/home"' in body


def test_survey_contains_accessible_location_states(client):
    response = client.get("/survey/home")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="location-status"' in body
    assert 'aria-live="polite"' in body
    assert 'id="search-clear-btn"' in body
    assert 'id="map-feedback"' in body
    assert 'id="cancel-draw-btn"' in body
    assert 'id="estimate-action-status"' in body


def test_public_landing_shows_marketing_nav_without_sidebar(app):
    response = app.test_client().get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="public-top-nav"' in body
    assert 'id="primary-sidebar"' not in body
    assert 'href="/plan/home"' in body
    assert 'href="/plan/business"' in body
    assert 'id="address-search"' not in body
    assert 'id="draw-btn"' not in body
    assert "leaflet.js" not in body
    assert "main.js" not in body
    assert "https://pmsuryaghar.gov.in/" in body
    assert 'class="subsidy-official-link"' in body
    assert 'class="truth-note public-truth-note"' in body
    assert '<div class="footer-nav-col">' in body
    assert "up to 90%" not in body.lower()
    assert 'href="/signup"' in body


def test_authenticated_landing_remains_a_public_page_without_sidebar(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="primary-sidebar"' not in body
    assert 'id="public-top-nav"' in body
    assert 'id="address-search"' not in body
    assert 'href="/dashboard"' in body


def test_authenticated_survey_uses_app_shell_and_shared_workspace(client):
    response = client.get("/survey/home")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="primary-sidebar"' in body
    assert 'id="public-top-nav"' not in body
    assert 'id="address-search"' in body
    assert 'id="draw-btn"' in body
    assert body.count('id="map"') == 1
    assert body.count("leaflet@1.9.4/dist/leaflet.js") == 1
    assert body.count("leaflet-draw@1.0.4/dist/leaflet.draw.js") == 1
    assert body.index("leaflet@1.9.4/dist/leaflet.js") < body.index("js/map.js")
    assert body.index("js/map.js") < body.index("js/main.js")
    assert 'id="mode-roof-btn"' in body
    assert 'id="mode-obstruction-btn"' in body
    assert 'id="area-readout"' in body
    assert 'value="residential" selected' in body
    assert 'href="/dashboard"' in body
    assert 'href="/survey/home"' in body


def test_plan_pages_keep_home_and_business_rules_distinct(app):
    test_client = app.test_client()
    home = test_client.get("/plan/home")
    business = test_client.get("/plan/business")

    assert home.status_code == 200
    assert business.status_code == 200
    assert b"RESIDENTIAL PLANNING" in home.data
    assert b"PM Surya Ghar guidance" in home.data
    assert b"COMMERCIAL PLANNING" in business.data
    assert b"PM Surya Ghar residential subsidy is not applied" in business.data
    assert b"tax benefits" in business.data
    assert b'href="/login?next=/survey/home"' in home.data
    assert b'href="/login?next=/survey/business"' in business.data
    assert home.data.count(b'class="truth-note plan-truth-note"') == 1
    assert business.data.count(b'class="truth-note plan-truth-note"') == 1
    assert b'<svg viewBox="0 0 24 24" aria-hidden="true">' in home.data
    assert b'<div class="footer-nav-col">' in home.data


def test_survey_requires_login_and_preserves_track_in_redirect(app):
    response = app.test_client().get("/survey/business")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")
    assert response.headers["Location"].endswith("next=/survey/business")


def test_business_survey_fixes_commercial_context(client):
    response = client.get("/survey/business")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "COMMERCIAL SURVEY WORKSPACE" in body
    assert '<option value="commercial" selected>' in body
    assert '<option value="residential"' not in body
    assert '<option value="day_heavy" selected>' in body


def test_invalid_planning_track_is_rejected(client):
    assert client.get("/plan/industrial").status_code == 404
    assert client.get("/survey/industrial").status_code == 404

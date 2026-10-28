"""Focused checks for server/client locale agreement and untranslated survey copy."""
import json
import re


def _client_catalog(html):
    payload = re.search(r'<script type="application/json" id="signix-translations">(.*?)</script>', html, re.S)
    assert payload is not None
    return json.loads(payload.group(1))


def test_hindi_survey_translates_static_and_client_copy(client):
    client.get('/language/hi')
    response = client.get('/survey/home')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<html lang="hi">' in html
    assert 'घर की छत का सर्वेक्षण करें।' in html
    assert 'अपनी छत खोजें' in html
    assert 'छत की सीमा' in html
    assert 'घर का औसत मासिक बिजली बिल' in html
    # Labels may translate, but the submitted product and drawing keys may not.
    assert 'value="residential"' in html
    assert 'id="mode-roof-btn"' in html
    catalog = _client_catalog(html)
    assert catalog['Street Map'] == 'सड़क नक्शा'
    assert catalog['Searching for “%(query)s”…'] == '“%(query)s” खोजा जा रहा है…'


def test_english_switch_restores_english_and_empty_client_catalog(client):
    client.get('/language/hi')
    client.get('/language/en')
    html = client.get('/survey/home').get_data(as_text=True)
    assert '<html lang="en">' in html
    assert 'Survey a home rooftop.' in html
    assert 'Find your rooftop' in html
    assert _client_catalog(html) == {}


def test_hindi_selection_survives_navigation_and_post(client):
    client.get('/language/hi')
    for path in ('/dashboard', '/compare', '/properties', '/savings', '/survey/business'):
        response = client.get(path)
        assert response.status_code == 200
        assert '<html lang="hi">' in response.get_data(as_text=True)
    response = client.post('/compare', data={'location_ids': []})
    assert '<html lang="hi">' in response.get_data(as_text=True)
    with client.session_transaction() as state:
        assert state['language'] == 'hi'

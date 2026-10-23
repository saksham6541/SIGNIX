def test_consultation_page_is_public_and_renders_form_and_nav(app):
    response = app.test_client().get("/consultation")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="consultation-request-form"' in body
    assert 'value="industrial"' in body
    assert 'class="public-nav-consultation" href="/consultation"' in body
    assert 'aria-current="page"' in body


def test_consultation_page_uses_hindi_catalog(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["language"] = "hi"

    response = client.get("/consultation")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<html lang="hi">' in body
    assert "अपनी छत पर सोलर की ज़रूरतों पर बात करें।" in body
    assert "मुफ़्त परामर्श बुक करें" in body

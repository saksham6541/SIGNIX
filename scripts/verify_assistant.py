"""Verify the authenticated SIGNIX assistant endpoint against a running app."""

import argparse
import os
import sys
from html.parser import HTMLParser
from pathlib import Path

import requests
from dotenv import load_dotenv


class _CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attributes = dict(attrs)
        if attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


def _login(session, base_url, email, password):
    login_response = session.get(f"{base_url}/login", timeout=15)
    login_response.raise_for_status()

    parser = _CsrfTokenParser()
    parser.feed(login_response.text)
    if not parser.token:
        raise RuntimeError("Could not find the login form's CSRF token")

    response = session.post(
        f"{base_url}/login",
        data={
            "csrf_token": parser.token,
            "email": email,
            "password": password,
            "submit": "Log in",
        },
        timeout=15,
        allow_redirects=True,
    )
    response.raise_for_status()
    if response.url.rstrip("/").endswith("/login"):
        raise RuntimeError(
            "Login failed; check SIGNIX_TEST_EMAIL and SIGNIX_TEST_PASSWORD"
        )
    return parser.token


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("SIGNIX_BASE_URL", "http://localhost:5000"),
        help="Running SIGNIX base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--message",
        default="How does SIGNIX estimate the PM Surya Ghar subsidy?",
        help="General FAQ question to send",
    )
    args = parser.parse_args()

    email = os.getenv("SIGNIX_TEST_EMAIL")
    password = os.getenv("SIGNIX_TEST_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "Set SIGNIX_TEST_EMAIL and SIGNIX_TEST_PASSWORD in the environment or .env"
        )

    base_url = args.base_url.rstrip("/")
    with requests.Session() as session:
        csrf_token = _login(session, base_url, email, password)
        request_url = f"{base_url}/api/assistant"
        request_body = {"message": args.message}
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": csrf_token,
        }
        print(f"POST {request_url}")
        print(f"Request headers: {request_headers}")
        print(f"Request JSON body: {request_body}")
        print(
            f"CSRF token included in request: {bool(request_headers.get('X-CSRFToken'))}"
        )
        response = session.post(
            request_url,
            headers=request_headers,
            json=request_body,
            timeout=60,
        )

    print(f"Response status: {response.status_code}")
    print(f"Response body: {response.text}")

    try:
        payload = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError("Assistant returned a non-JSON response")

    if not response.ok:
        raise RuntimeError(
            f"Assistant request failed with HTTP {response.status_code}: "
            f"{payload.get('error', payload)}"
        )

    print(payload["response"])


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, RuntimeError, KeyError) as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        sys.exit(1)

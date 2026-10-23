"""Sign in with Google, Microsoft, Apple and workspace single sign-on.

The identity providers are simulated: discovery documents and signing keys
are served from memory and the code exchange returns ID tokens signed with a
test RSA key, so every check AIVA makes on a real token runs for real
(signature, issuer, audience, expiry, nonce, state).
"""

import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services import oidc

API = "/api/v1"
PASSWORD = "Integration#2026"
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key-1"
GOOGLE_ISSUER = "https://accounts.google.com"
IDP_ISSUER = "https://idp.acme-sso.test"


def _jwk() -> dict:
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(RSA_KEY.public_key()))
    return {**data, "kid": KID, "alg": "RS256", "use": "sig"}


def _discovery(issuer: str) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
        "scopes_supported": ["openid", "email", "profile"],
    }


class FakeProviders:
    """Stands in for Google, Microsoft and a workspace identity provider."""

    def __init__(self):
        self.next_claims: dict = {}

    async def fetch_json(self, url: str):
        for issuer in (GOOGLE_ISSUER, IDP_ISSUER, "https://login.microsoftonline.com/common/v2.0"):
            if url == f"{issuer}/.well-known/openid-configuration":
                doc = _discovery(issuer)
                if "microsoftonline" in issuer:
                    doc["issuer"] = "https://login.microsoftonline.com/{tenantid}/v2.0"
                return doc
            if url == f"{issuer}/jwks":
                return {"keys": [_jwk()]}
        raise oidc.OAuthError(f"unexpected fetch {url}")

    async def exchange_code(self, p, code, verifier):
        assert code == "good-code"
        now = int(time.time())
        claims = {"aud": p.client_id, "iat": now, "exp": now + 300, **self.next_claims}
        return {"id_token": jwt.encode(claims, RSA_KEY, algorithm="RS256", headers={"kid": KID})}


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "google-client")
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(settings, "microsoft_client_id", "ms-client")
    monkeypatch.setattr(settings, "microsoft_client_secret", "ms-secret")
    providers = FakeProviders()
    monkeypatch.setattr(oidc, "fetch_json", providers.fetch_json)
    monkeypatch.setattr(oidc, "exchange_code", providers.exchange_code)
    return providers


def _signup(client, company="Sign-in Co", email=None):
    email = email or f"owner-{uuid.uuid4().hex[:10]}@signin.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Sam Owner", "email": email, "password": PASSWORD, "company_name": company})
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    if response.status_code >= 500:
        pytest.skip("PostgreSQL not available for integration tests.")
    data = response.json()
    return {
        "email": email,
        "org": data["organization_id"],
        "user_id": data["user_id"],
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"]},
    }


def _start(client, provider="google", intent="login", email=None, headers=None):
    if headers:
        response = client.post(f"{API}/auth/oauth/link", json={"provider": provider}, headers=headers)
    else:
        response = client.post(f"{API}/auth/oauth/start", json={"provider": provider, "intent": intent, "next": "/leads", "email": email})
    assert response.status_code == 200, response.text
    data = response.json()
    query = parse_qs(urlparse(data["authorization_url"]).query)
    assert query["redirect_uri"][0].endswith(f"/api/session/oauth/{provider}/callback")
    if provider != "apple":
        assert query["code_challenge_method"] == ["S256"]
    return {"token": data["state_token"], "state": query["state"][0], "nonce": query["nonce"][0]}


def _finish(client, fake, attempt, provider="google", **claims):
    fake.next_claims = {"nonce": attempt["nonce"], **claims}
    response = client.post(
        f"{API}/auth/oauth/callback",
        json={"provider": provider, "code": "good-code", "state": attempt["state"], "state_token": attempt["token"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _google(sub, email, verified=True, name="Gale Google"):
    return {"iss": GOOGLE_ISSUER, "sub": sub, "email": email, "email_verified": verified, "name": name}


def test_providers_report_configuration(client, fake):
    data = client.get(f"{API}/auth/providers").json()
    assert data["google"] and data["microsoft"] and data["sso"]
    assert data["apple"] is False


def test_unconfigured_provider_explains(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "apple_client_id", "")
    response = client.post(f"{API}/auth/oauth/start", json={"provider": "apple"})
    assert response.status_code == 400
    assert "isn't set up" in response.json()["detail"]


def test_google_signup_then_login(client, fake):
    sub, email = uuid.uuid4().hex, f"gale-{uuid.uuid4().hex[:8]}@newco-{uuid.uuid4().hex[:6]}.com"
    first = _finish(client, fake, _start(client, intent="signup"), **_google(sub, email))
    assert first["status"] == "session", first
    assert first["next"] == "/leads"
    session = first["session"]
    headers = {"Authorization": f"Bearer {session['access_token']}", "X-Organization-ID": session["organization_id"]}
    me = client.get(f"{API}/auth/me", headers=headers).json()
    assert me["user"]["email"] == email
    assert me["subscription"]["plan"] == "trial"
    assert me["organization"]["name"].startswith("Newco")

    again = _finish(client, fake, _start(client), **_google(sub, email))
    assert again["status"] == "session"
    assert again["session"]["user_id"] == session["user_id"]
    assert again["session"]["organization_id"] == session["organization_id"]

    identities = client.get(f"{API}/auth/identities", headers=headers).json()
    assert identities["has_password"] is False
    assert [i["provider"] for i in identities["items"]] == ["google"]
    # The only way in can't be removed.
    assert client.delete(f"{API}/auth/identities/{identities['items'][0]['id']}", headers=headers).status_code == 409


def test_google_links_to_existing_password_account(client, fake):
    ws = _signup(client)
    result = _finish(client, fake, _start(client), **_google(uuid.uuid4().hex, ws["email"]))
    assert result["status"] == "session"
    assert result["session"]["user_id"] == ws["user_id"]
    identities = client.get(f"{API}/auth/identities", headers=ws["headers"]).json()
    assert identities["has_password"] is True and len(identities["items"]) == 1
    assert client.delete(f"{API}/auth/identities/{identities['items'][0]['id']}", headers=ws["headers"]).status_code == 204


def test_unverified_email_is_not_trusted(client, fake):
    ws = _signup(client)
    result = _finish(client, fake, _start(client), **_google(uuid.uuid4().hex, ws["email"], verified=False))
    assert result["status"] == "error"
    assert "hasn't confirmed" in result["message"]


def test_state_and_nonce_are_checked(client, fake):
    attempt = _start(client)
    bad_state = {**attempt, "state": "someone-elses-state"}
    assert _finish(client, fake, bad_state, **_google("x", "x@example.com"))["status"] == "error"
    attempt = _start(client)
    fake.next_claims = {**_google("x", "x@example.com"), "nonce": "replayed"}
    response = client.post(
        f"{API}/auth/oauth/callback",
        json={"provider": "google", "code": "good-code", "state": attempt["state"], "state_token": attempt["token"]},
    ).json()
    assert response["status"] == "error" and "already used" in response["message"]
    # A token signed by someone else is rejected.
    attempt = _start(client)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode({**_google("y", "y@example.com"), "aud": "google-client", "iat": now, "exp": now + 60, "nonce": attempt["nonce"]}, other, algorithm="RS256", headers={"kid": KID})

    async def forged_exchange(p, code, verifier):
        return {"id_token": forged}

    original = oidc.exchange_code
    oidc.exchange_code = forged_exchange
    try:
        response = client.post(
            f"{API}/auth/oauth/callback",
            json={"provider": "google", "code": "good-code", "state": attempt["state"], "state_token": attempt["token"]},
        ).json()
    finally:
        oidc.exchange_code = original
    assert response["status"] == "error" and "isn't valid" in response["message"]


def test_mfa_still_required(client, fake):
    from app.core import totp

    ws = _signup(client)
    setup = client.post(f"{API}/auth/mfa/setup", headers=ws["headers"]).json()
    code = totp.current_code(setup["secret"])
    assert client.post(f"{API}/auth/mfa/enable", json={"code": code}, headers=ws["headers"]).status_code == 200
    result = _finish(client, fake, _start(client), **_google(uuid.uuid4().hex, ws["email"]))
    assert result["status"] == "mfa" and result["mfa_token"]
    code = totp.current_code(setup["secret"])
    verified = client.post(f"{API}/auth/mfa/verify", json={"mfa_token": result["mfa_token"], "code": code})
    assert verified.status_code == 200, verified.text


def test_microsoft_work_account_uses_sign_in_name(client, fake):
    tid, oid = str(uuid.uuid4()), str(uuid.uuid4())
    upn = f"mo-{uuid.uuid4().hex[:8]}@contoso-{uuid.uuid4().hex[:6]}.com"
    claims = {
        "iss": f"https://login.microsoftonline.com/{tid}/v2.0",
        "sub": "pairwise-sub",
        "tid": tid,
        "oid": oid,
        "preferred_username": upn,
        "email": "spoofed@victim.example",
        "name": "Mo Microsoft",
    }
    result = _finish(client, fake, _start(client, provider="microsoft", intent="signup"), provider="microsoft", **claims)
    assert result["status"] == "session", result
    assert result["session"]["user"]["email"] == upn
    # A token from a tenant other than the one in its issuer is refused.
    bad = {**claims, "iss": f"https://login.microsoftonline.com/{uuid.uuid4()}/v2.0"}
    assert _finish(client, fake, _start(client, provider="microsoft"), provider="microsoft", **bad)["status"] == "error"


def test_link_from_settings(client, fake):
    ws = _signup(client)
    attempt = _start(client, headers=ws["headers"])
    result = _finish(client, fake, attempt, **_google(uuid.uuid4().hex, f"different-{uuid.uuid4().hex[:6]}@gmail.com"))
    assert result == {"status": "linked", "provider": "google"}
    identities = client.get(f"{API}/auth/identities", headers=ws["headers"]).json()
    assert len(identities["items"]) == 1 and identities["items"][0]["email"].startswith("different-")


def test_workspace_single_sign_on(client, fake):
    ws = _signup(client, "Acme SSO")
    domain = f"acme-{uuid.uuid4().hex[:8]}.com"
    saved = client.put(
        f"{API}/sso",
        json={"name": "Acme Okta", "issuer": IDP_ISSUER, "client_id": "acme-client", "client_secret": "acme-secret", "domains": [domain, "gmail.com"]},
        headers=ws["headers"],
    )
    assert saved.status_code == 422 and "public email" in saved.json()["detail"]
    saved = client.put(
        f"{API}/sso",
        json={"name": "Acme Okta", "issuer": IDP_ISSUER, "client_id": "acme-client", "client_secret": "acme-secret", "domains": [domain]},
        headers=ws["headers"],
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["has_secret"] and "client_secret" not in saved.json()

    # Another workspace can't claim the same domain.
    other = _signup(client, "Squatter")
    clash = client.put(
        f"{API}/sso",
        json={"name": "Other", "issuer": IDP_ISSUER, "client_id": "c", "client_secret": "s", "domains": [domain]},
        headers=other["headers"],
    )
    assert clash.status_code == 409

    found = client.post(f"{API}/auth/sso/discover", json={"email": f"pat@{domain}"})
    assert found.status_code == 200 and found.json()["organization_name"] == "Acme SSO"
    assert client.post(f"{API}/auth/sso/discover", json={"email": "pat@nowhere.example"}).status_code == 404

    email = f"pat@{domain}"
    attempt = _start(client, provider="sso", email=email)
    result = _finish(client, fake, attempt, provider="sso", iss=IDP_ISSUER, sub="okta-pat", email=email, name="Pat Okta")
    assert result["status"] == "session", result
    session = result["session"]
    assert session["organization_id"] == ws["org"]
    assert [o["id"] for o in session["organizations"]] == [ws["org"]]

    headers = {"Authorization": f"Bearer {session['access_token']}", "X-Organization-ID": ws["org"]}
    me = client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["role"] == "member"

    # The session only opens that workspace, even if someone edits the header.
    elsewhere = {**headers, "X-Organization-ID": other["org"]}
    assert client.get(f"{API}/auth/me", headers=elsewhere).status_code == 403

    # An email outside the connection's domains is refused.
    attempt = _start(client, provider="sso", email=email)
    refused = _finish(client, fake, attempt, provider="sso", iss=IDP_ISSUER, sub="okta-x", email="x@elsewhere.example")
    assert refused["status"] == "error"

    # Someone whose account also belongs to other workspaces can't be linked to this workspace's SSO,
    # even after this workspace invites them.
    outsider = _signup(client, "Outsider Co", email=f"out@{domain}")
    attempt = _start(client, provider="sso", email=email)
    blocked = _finish(client, fake, attempt, provider="sso", iss=IDP_ISSUER, sub="okta-outsider", email=outsider["email"])
    assert blocked["status"] == "error" and "invite" in blocked["message"]
    invite = client.post(
        f"{API}/admin/users/invite", json={"email": outsider["email"], "full_name": "Out Sider", "role": "member"}, headers=ws["headers"]
    )
    assert invite.status_code == 201, invite.text
    attempt = _start(client, provider="sso", email=email)
    blocked = _finish(client, fake, attempt, provider="sso", iss=IDP_ISSUER, sub="okta-outsider", email=outsider["email"])
    assert blocked["status"] == "error" and "other workspaces" in blocked["message"]

    # Turning auto-create off stops new people.
    client.put(
        f"{API}/sso",
        json={"name": "Acme Okta", "issuer": IDP_ISSUER, "client_id": "acme-client", "domains": [domain], "auto_provision": False},
        headers=ws["headers"],
    )
    attempt = _start(client, provider="sso", email=f"new@{domain}")
    denied = _finish(client, fake, attempt, provider="sso", iss=IDP_ISSUER, sub="okta-new", email=f"new@{domain}")
    assert denied["status"] == "error" and "invite" in denied["message"]

    assert client.delete(f"{API}/sso", headers=ws["headers"]).status_code == 204
    assert client.post(f"{API}/auth/sso/discover", json={"email": email}).status_code == 404


def test_apple_client_secret_is_signed():
    settings = get_settings()
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    saved = (settings.apple_client_id, settings.apple_team_id, settings.apple_key_id, settings.apple_private_key)
    settings.apple_client_id, settings.apple_team_id, settings.apple_key_id = "com.aiva.web", "TEAM123", "KEY123"
    settings.apple_private_key = pem.replace("\n", "\\n")
    try:
        token = oidc.apple_client_secret()
    finally:
        settings.apple_client_id, settings.apple_team_id, settings.apple_key_id, settings.apple_private_key = saved
    claims = jwt.decode(token, key.public_key(), algorithms=["ES256"], audience="https://appleid.apple.com")
    assert claims["iss"] == "TEAM123" and claims["sub"] == "com.aiva.web"
    assert jwt.get_unverified_header(token)["kid"] == "KEY123"


def test_workspace_names_from_email():
    assert oidc.workspace_name("ana@acme-corp.com", "Ana Diaz") == "Acme Corp"
    assert oidc.workspace_name("ana@gmail.com", "Ana Diaz") == "Ana's workspace"

"""Microsoft Entra ID and Okta: single sign-on set-up, SCIM 2.0 provisioning,
group mapping, require single sign-on and domain verification.

Nothing calls Microsoft or Okta: OpenID discovery and the code exchange are
served from memory (ID tokens are signed with a test RSA key so every real check
runs), Microsoft Graph and the Okta API go through an ``httpx.MockTransport``,
and DNS answers come from a stub.
"""

import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.integrations.providers import _idp_http as idp_http
from app.integrations.providers import _idp_policy as idp_policy
from app.integrations.providers import _idp_store as idp_store
from app.main import app
from app.services import oidc

API = "/api/v1"
SCIM = f"{API}/public/scim/v2"
PASSWORD = "Integration#2026"
TENANT = str(uuid.uuid4())
ENTRA_ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
GRAPH_CLIENT = str(uuid.uuid4())
SSO_CLIENT = str(uuid.uuid4())
OKTA = "acme-aiva-test.okta.com"
USER = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "idp-test-key"


def _discovery(issuer: str) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
        "scopes_supported": ["openid", "email", "profile", "offline_access"],
    }


class FakeIdp:
    """Microsoft (login + Graph) and Okta (org server + API), in memory."""

    def __init__(self):
        self.next_claims: dict = {}
        self.graph_calls = 0

    async def fetch_json(self, url: str):
        if url.endswith("/jwks"):
            data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(RSA_KEY.public_key()))
            return {"keys": [{**data, "kid": KID, "alg": "RS256", "use": "sig"}]}
        suffix = "/.well-known/openid-configuration"
        if url.startswith("https://login.microsoftonline.com/") and url.endswith(f"/v2.0{suffix}"):
            tenant = url.split("/")[3]
            if tenant not in {TENANT, "contoso-test.onmicrosoft.com"}:
                raise oidc.OAuthError("login.microsoftonline.com answered 400.")
            return _discovery(ENTRA_ISSUER)
        if url.startswith(f"https://{OKTA}") and url.endswith(suffix):
            return _discovery(url[: -len(suffix)])
        if url == f"https://accounts.google.com{suffix}":
            return _discovery("https://accounts.google.com")
        raise oidc.OAuthError(f"unexpected fetch {url}")

    async def exchange_code(self, p, code, verifier):
        now = int(time.time())
        claims = {"aud": p.client_id, "iat": now, "exp": now + 300, **self.next_claims}
        return {"id_token": jwt.encode(claims, RSA_KEY, algorithm="RS256", headers={"kid": KID})}

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "login.microsoftonline.com" and path.endswith("/oauth2/v2.0/token"):
            form = parse_qs(request.content.decode())
            assert form["scope"] == ["https://graph.microsoft.com/.default"]
            if form["client_secret"] != ["graph-secret"]:
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."})
            return httpx.Response(200, json={"access_token": "graph-token", "token_type": "Bearer", "expires_in": 3599})
        if host == "graph.microsoft.com" and path == "/v1.0/groups":
            assert request.headers["authorization"] == "Bearer graph-token"
            self.graph_calls += 1
            if "skiptoken" in str(request.url):
                return httpx.Response(200, json={"value": [{"id": "g-2", "displayName": "AIVA Admins", "description": None}]})
            return httpx.Response(200, json={
                "value": [{"id": "g-1", "displayName": "Sales EMEA", "description": "Sellers"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups?$skiptoken=abc",
            })
        if host == OKTA and path == "/api/v1/groups":
            if request.headers.get("authorization") != "SSWS okta-token":
                return httpx.Response(401, json={"errorSummary": "Invalid token provided"})
            return httpx.Response(200, json=[{"id": "00g1", "profile": {"name": "Everyone", "description": "All users"}}, {"id": "00g2", "profile": {"name": "Sales"}}])
        return httpx.Response(404, json={})


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdp()
    monkeypatch.setattr(oidc, "fetch_json", fake.fetch_json)
    monkeypatch.setattr(oidc, "exchange_code", fake.exchange_code)
    monkeypatch.setattr(idp_http, "TRANSPORT", httpx.MockTransport(fake.handle))
    idp_store._REQUESTS.clear()
    idp_store._FAILURES.clear()
    return fake


def signup(client, company="Identity Co", email=None) -> dict:
    email = email or f"owner-{uuid.uuid4().hex[:10]}@identity.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Olivia Owner", "email": email, "password": PASSWORD, "company_name": company})
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    if response.status_code >= 500:
        pytest.skip("PostgreSQL not available for integration tests.")
    assert response.status_code == 201, response.text
    data = response.json()
    return {
        "email": email,
        "org": data["organization_id"],
        "user_id": data["user_id"],
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"], "X-Session-ID": data["session_id"]},
    }


def invite(client, ws, email, role="member") -> dict:
    created = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": email.split("@")[0].title(), "role": role}, headers=ws["headers"])
    assert created.status_code == 201, created.text
    token = created.json()["invite_url"].split("token=")[1]
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    assert joined.status_code == 200, joined.text
    data = joined.json()
    return {"user_id": created.json()["user_id"], "access": data["access_token"], "refresh": data["refresh_token"],
            "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": ws["org"]}}


def connect(client, ws, key="entra_id", **values):
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/{key}/toggle", json={"enabled": True}, headers=h).status_code == 200
    if not values:
        values = {"tenant_id": TENANT, "client_id": GRAPH_CLIENT, "client_secret": "graph-secret"} if key == "entra_id" else {"okta_domain": OKTA, "api_token": "okta-token"}
    response = client.put(f"{API}/integration-hub/{key}/credentials", json={"values": values}, headers=h)
    assert response.status_code == 200, response.text
    return response.json()


def scim_token(client, ws, key="entra_id") -> str:
    response = client.post(f"{API}/identity/{key}/scim-token", headers=ws["headers"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["token"]


def S(token: str, **extra) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json", **extra}


def entra_user(user_name: str, email: str, external: str | None = None, given="Grace", family="Hopper") -> dict:
    return {
        "schemas": [USER, ENTERPRISE],
        "externalId": external or str(uuid.uuid4()),
        "userName": user_name,
        "active": True,
        "emails": [{"primary": True, "type": "work", "value": email}],
        "meta": {"resourceType": "User"},
        "name": {"formatted": f"{given} {family}", "familyName": family, "givenName": given},
        "roles": [],
        ENTERPRISE: {"department": "Sales", "employeeNumber": "E-100"},
    }


def patch(*operations) -> dict:
    return {"schemas": [PATCH], "Operations": list(operations)}


def member_row(client, ws, user_id) -> dict:
    users = client.get(f"{API}/admin/users", headers=ws["headers"]).json()
    return next(u for u in users if u["id"] == user_id)


# ---------------------------------------------------------------------------
# Connecting and single sign-on set-up
# ---------------------------------------------------------------------------


def test_entra_credentials_sso_values_and_directory_groups(client, idp):
    ws = signup(client, "Entra Co")
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/entra_id/toggle", json={"enabled": True}, headers=h).status_code == 200
    bad = client.put(f"{API}/integration-hub/entra_id/credentials", json={"values": {"tenant_id": "common"}}, headers=h)
    assert bad.status_code == 422 and "common" in bad.json()["detail"]
    unknown = client.put(f"{API}/integration-hub/entra_id/credentials", json={"values": {"tenant_id": str(uuid.uuid4())}}, headers=h)
    assert unknown.status_code == 422
    half = client.put(f"{API}/integration-hub/entra_id/credentials", json={"values": {"tenant_id": TENANT, "client_id": GRAPH_CLIENT}}, headers=h)
    assert half.status_code == 422 and "both" in half.json()["detail"]
    wrong = client.put(f"{API}/integration-hub/entra_id/credentials", json={"values": {"tenant_id": TENANT, "client_id": GRAPH_CLIENT, "client_secret": "nope"}}, headers=h)
    assert wrong.status_code == 422 and "AADSTS7000215" in wrong.json()["detail"]

    # A verified domain name works as the tenant too; AIVA keeps the GUID.
    detail = connect(client, ws, tenant_id="contoso-test.onmicrosoft.com", client_id=GRAPH_CLIENT, client_secret="graph-secret")
    assert detail["status"] == "connected"
    assert detail["connection"]["config"]["tenant_id"] == TENANT
    assert "client_secret" not in json.dumps(detail["connection"]["config"]) and "graph-secret" not in json.dumps(detail)
    extra = detail["extra"]
    assert extra["sso"]["issuer"] == ENTRA_ISSUER
    assert extra["sso"]["redirect_uri"].endswith("/api/session/oauth/sso/callback")
    assert extra["scim"]["base_url"].endswith("/api/aiva/public/scim/v2") and extra["scim"]["has_token"] is False
    assert extra["directory_api"] is True

    groups = client.get(f"{API}/identity/entra_id/directory-groups", headers=h)
    assert groups.status_code == 200, groups.text
    assert [g["name"] for g in groups.json()["items"]] == ["AIVA Admins", "Sales EMEA"]  # both pages, sorted
    assert idp.graph_calls == 2

    # Single sign-on from the integration: exact issuer, reusing Settings › Single sign-on.
    domain = f"entra-{uuid.uuid4().hex[:8]}.com"
    bad_client = client.put(f"{API}/identity/entra_id/sso", json={"client_id": "not-a-guid", "client_secret": "s", "domains": [domain]}, headers=h)
    assert bad_client.status_code == 422
    saved = client.put(f"{API}/identity/entra_id/sso", json={"client_id": SSO_CLIENT, "client_secret": "sso-secret", "domains": [domain]}, headers=h)
    assert saved.status_code == 200, saved.text
    assert saved.json()["issuer"] == ENTRA_ISSUER and "client_secret" not in saved.json()["connection"]
    sso = client.get(f"{API}/sso", headers=h).json()["connection"]
    assert sso["issuer"] == ENTRA_ISSUER and sso["client_id"] == SSO_CLIENT and sso["domains"] == [domain] and sso["name"] == "Microsoft Entra ID"
    check = client.post(f"{API}/identity/entra_id/sso/check", headers=h)
    assert check.status_code == 200 and check.json()["saved"] is True and check.json()["scopes_ok"] is True
    detail = client.get(f"{API}/integration-hub/entra_id", headers=h).json()
    assert detail["extra"]["sso"]["matches"] is True and detail["extra"]["sso"]["connection"]["has_secret"] is True

    tested = client.post(f"{API}/integration-hub/entra_id/test", headers=h)
    assert tested.status_code == 200 and "Directory API works" in tested.json()["message"]


def test_okta_credentials_and_custom_authorization_server(client, idp):
    ws = signup(client, "Okta Co")
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/okta/toggle", json={"enabled": True}, headers=h).status_code == 200
    bad = client.put(f"{API}/integration-hub/okta/credentials", json={"values": {"okta_domain": "http://localhost"}}, headers=h)
    assert bad.status_code == 422
    wrong = client.put(f"{API}/integration-hub/okta/credentials", json={"values": {"okta_domain": OKTA, "api_token": "bad"}}, headers=h)
    assert wrong.status_code == 422 and "API token" in wrong.json()["detail"]
    detail = connect(client, ws, "okta", okta_domain=f"https://{OKTA.replace('.okta.com', '-admin.okta.com')}/admin/dashboard", api_token="okta-token")
    assert detail["connection"]["config"]["okta_domain"] == OKTA
    assert detail["extra"]["sso"]["issuer"] == f"https://{OKTA}"
    groups = client.get(f"{API}/identity/okta/directory-groups", headers=h).json()["items"]
    assert [g["name"] for g in groups] == ["Everyone", "Sales"]

    domain = f"okta-{uuid.uuid4().hex[:8]}.com"
    saved = client.put(
        f"{API}/identity/okta/sso",
        json={"client_id": "0oa-client", "client_secret": "s3cret", "domains": [domain], "authorization_server": "default", "default_role": "viewer"},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["issuer"] == f"https://{OKTA}/oauth2/default"
    assert client.get(f"{API}/sso", headers=h).json()["connection"]["default_role"] == "viewer"
    assert client.put(f"{API}/identity/okta/sso", json={"client_id": "x", "domains": [domain], "authorization_server": "../evil"}, headers=h).status_code == 422


# ---------------------------------------------------------------------------
# SCIM tokens and tenant isolation
# ---------------------------------------------------------------------------


def test_scim_token_lifecycle_and_access_rules(client, idp):
    ws = signup(client, "Token Co")
    h = ws["headers"]
    assert client.post(f"{API}/identity/entra_id/scim-token", headers=h).status_code == 409  # integration off
    connect(client, ws)
    token = scim_token(client, ws)
    assert token.startswith("aiva_scim_") and len(token) == len("aiva_scim_") + 86

    detail = client.get(f"{API}/integration-hub/entra_id", headers=h).json()
    assert token not in json.dumps(detail) and token[10:53] not in json.dumps(detail["extra"])
    info = detail["extra"]["scim"]["token"]
    assert info["hint"].endswith(token[-4:]) and info["created_by"] == "Olivia Owner" and info["last_used_at"] is None

    assert client.get(f"{SCIM}/Users", headers=S(token)).status_code == 200
    assert client.get(f"{API}/integration-hub/entra_id", headers=h).json()["extra"]["scim"]["token"]["last_used_at"]

    # Without a token, with a wrong one, or with an API-style bearer: 401 in the SCIM error format.
    for headers in ({}, S("aiva_scim_" + "x" * 86), S(token[:-1] + ("A" if token[-1] != "A" else "B")), {"Authorization": h["Authorization"]}):
        refused = client.get(f"{SCIM}/Users", headers=headers)
        assert refused.status_code == 401, headers
        assert refused.json()["schemas"] == [ERROR] and refused.headers["content-type"].startswith("application/scim+json")

    # Members can't see or manage tokens.
    member = invite(client, ws, f"member-{uuid.uuid4().hex[:6]}@identity.example")
    assert client.post(f"{API}/identity/entra_id/scim-token", headers=member["headers"]).status_code == 403
    assert client.get(f"{API}/identity/policy", headers=member["headers"]).status_code == 403
    member_view = client.get(f"{API}/integration-hub/entra_id", headers=member["headers"]).json()["extra"]
    assert "token" not in member_view["scim"] and "policy" not in member_view and "groups" not in member_view

    # Rotating replaces the token; revoking stops provisioning.
    rotated = scim_token(client, ws)
    assert client.get(f"{SCIM}/Users", headers=S(token)).status_code == 401
    assert client.get(f"{SCIM}/Users", headers=S(rotated)).status_code == 200
    assert client.delete(f"{API}/identity/entra_id/scim-token", headers=h).status_code == 200
    assert client.get(f"{SCIM}/Users", headers=S(rotated)).status_code == 401

    # Switching the integration off pauses provisioning without deleting anyone.
    token = scim_token(client, ws)
    paused_email = f"paused-{uuid.uuid4().hex[:8]}@x.example"
    created = client.post(f"{SCIM}/Users", json=entra_user(paused_email, paused_email), headers=S(token))
    assert created.status_code == 201
    client.put(f"{API}/integration-hub/entra_id/toggle", json={"enabled": False}, headers=h)
    paused = client.get(f"{SCIM}/Users", headers=S(token))
    assert paused.status_code == 403 and "turned off" in paused.json()["detail"]
    assert any(u["email"] == paused_email and u["status"] == "active" for u in client.get(f"{API}/admin/users", headers=h).json())
    client.put(f"{API}/integration-hub/entra_id/toggle", json={"enabled": True}, headers=h)
    assert client.get(f"{SCIM}/Users", headers=S(token)).status_code == 200

    # Disconnecting deletes the token.
    assert client.delete(f"{API}/integration-hub/entra_id/connection", headers=h).status_code == 200
    assert client.get(f"{SCIM}/Users", headers=S(token)).status_code == 401


def test_scim_token_only_reaches_its_own_workspace(client, idp):
    a, b = signup(client, "Tenant A"), signup(client, "Tenant B")
    connect(client, a)
    connect(client, b, "okta")
    token_a, token_b = scim_token(client, a), scim_token(client, b, "okta")
    bee = f"bee-{uuid.uuid4().hex[:8]}@b.example"
    in_b = client.post(f"{SCIM}/Users", json=entra_user(bee, bee), headers=S(token_b)).json()

    # A's token never sees or changes B's people, whatever headers are sent.
    sneaky = S(token_a, **{"X-Organization-ID": b["org"]})
    listed = client.get(f"{SCIM}/Users", headers=sneaky).json()
    assert {r["userName"] for r in listed["Resources"]} == {a["email"]}
    assert client.get(f"{SCIM}/Users/{in_b['id']}", headers=sneaky).status_code == 404
    assert client.patch(f"{SCIM}/Users/{in_b['id']}", json=patch({"op": "replace", "value": {"active": False}}), headers=sneaky).status_code == 404
    assert client.delete(f"{SCIM}/Users/{in_b['id']}", headers=sneaky).status_code == 404
    assert client.get(f"{SCIM}/Users/{b['user_id']}", headers=sneaky).status_code == 404
    assert client.get(f"{SCIM}/Users", params={"filter": f'userName eq "{bee}"'}, headers=sneaky).json()["totalResults"] == 0
    # A group in A can't take B's people as members.
    group = client.post(f"{SCIM}/Groups", json={"schemas": [GROUP], "displayName": "Mixed", "members": [{"value": in_b["id"]}, {"value": a["user_id"]}]}, headers=S(token_a))
    assert [m["value"] for m in group.json()["members"]] == [a["user_id"]]
    assert client.get(f"{SCIM}/Users/{in_b['id']}", headers=S(token_b)).json()["active"] is True


# ---------------------------------------------------------------------------
# Users: Entra and Okta request shapes
# ---------------------------------------------------------------------------


def test_scim_users_as_entra_sends_them(client, idp, dns):
    ws = signup(client, "Entra Users")
    connect(client, ws)
    token = scim_token(client, ws)
    upn = f"Test_User_{uuid.uuid4()}"
    email = f"grace-{uuid.uuid4().hex[:6]}@entra.example"

    # Test Connection: a random lookup gives an empty ListResponse.
    empty = client.get(f"{SCIM}/Users", params={"filter": f'userName eq "{uuid.uuid4()}"'}, headers=S(token))
    assert empty.status_code == 200 and empty.json() == {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"], "totalResults": 0, "startIndex": 1, "itemsPerPage": 0, "Resources": []}

    body = entra_user(upn, email, external="ext-grace")
    created = client.post(f"{SCIM}/Users", json=body, headers=S(token))
    assert created.status_code == 201, created.text
    user = created.json()
    assert created.headers["location"] == user["meta"]["location"] and user["meta"]["location"].endswith(f"/Users/{user['id']}")
    assert created.headers["content-type"].startswith("application/scim+json")
    assert user["userName"] == upn and user["externalId"] == "ext-grace" and user["active"] is True
    assert user["name"] == {"formatted": "Grace Hopper", "familyName": "Hopper", "givenName": "Grace"}
    assert user[ENTERPRISE] == {"department": "Sales", "employeeNumber": "E-100"} and ENTERPRISE in user["schemas"]
    assert "password" not in user and None not in user.values()
    row = member_row(client, ws, user["id"])
    assert row["email"] == email and row["full_name"] == "Grace Hopper" and row["role"] == "member" and row["status"] == "active"

    duplicate = client.post(f"{SCIM}/Users", json=entra_user(upn, f"other-{email}"), headers=S(token))
    assert duplicate.status_code == 409 and duplicate.json()["scimType"] == "uniqueness"

    for query in (f'userName eq "{upn.upper()}"', 'externalId eq "ext-grace"', f'emails[type eq "work"].value eq "{email}"',
                  f'emails.value eq "{email}"', f'urn:ietf:params:scim:schemas:core:2.0:User:userName eq "{upn}"', f'userName eq "{upn}" and externalId eq "ext-grace"'):
        found = client.get(f"{SCIM}/Users", params={"filter": query}, headers=S(token)).json()
        assert found["totalResults"] == 1 and found["Resources"][0]["id"] == user["id"], query
    assert client.get(f"{SCIM}/Users", params={"filter": 'title co "x"'}, headers=S(token)).json()["scimType"] == "invalidFilter"

    # Multi-valued and sub-attribute replaces, capitalised op names.
    new_email = f"updated-{email}"
    updated = client.patch(f"{SCIM}/Users/{user['id']}", json=patch(
        {"op": "Replace", "path": 'emails[type eq "work"].value', "value": new_email},
        {"op": "Replace", "path": "name.familyName", "value": "Brewster"},
        {"op": "Add", "path": 'phoneNumbers[type eq "mobile"].value', "value": "55555555555"},
        {"op": "Add", "path": f"{ENTERPRISE}:manager", "value": {"value": "mgr-1"}},
        {"op": "Replace", "path": "title", "value": "Rear Admiral"},
    ), headers=S(token))
    assert updated.status_code == 200, updated.text
    updated = updated.json()
    assert updated["emails"] == [{"primary": True, "type": "work", "value": new_email}]
    assert updated["name"]["familyName"] == "Brewster" and updated["phoneNumbers"] == [{"type": "mobile", "value": "55555555555"}]
    assert updated[ENTERPRISE]["manager"] == {"value": "mgr-1"} and updated["title"] == "Rear Admiral"
    # The sign-in email only moves to a verified domain of this workspace; the rest of the profile follows.
    row = member_row(client, ws, user["id"])
    assert row["email"] == email and row["job_title"] == "Rear Admiral"
    domain = f"entra-{uuid.uuid4().hex[:8]}.com"
    dns[domain] = [client.post(f"{API}/identity/domains", json={"domain": domain}, headers=ws["headers"]).json()["domains"][0]["record_value"]]
    assert client.post(f"{API}/identity/domains/{domain}/verify", headers=ws["headers"]).json()["check"]["ok"] is True
    new_email = f"grace@{domain}"
    moved = client.patch(f"{SCIM}/Users/{user['id']}", json=patch({"op": "Replace", "path": 'emails[type eq "work"].value', "value": new_email}), headers=S(token))
    assert moved.status_code == 200 and moved.json()["emails"][0]["value"] == new_email
    assert member_row(client, ws, user["id"])["email"] == new_email

    renamed = client.patch(f"{SCIM}/Users/{user['id']}", json=patch({"op": "Replace", "path": "userName", "value": f"{upn}@entra.example"}), headers=S(token))
    assert renamed.json()["userName"] == f"{upn}@entra.example"
    no_manager = client.patch(f"{SCIM}/Users/{user['id']}", json=patch({"op": "Remove", "path": f"{ENTERPRISE}:manager", "value": [{"$ref": None, "value": "mgr-1"}]}), headers=S(token))
    assert "manager" not in no_manager.json()[ENTERPRISE]

    # Entra's soft delete (sometimes a string) and restore.
    disabled = client.patch(f"{SCIM}/Users/{user['id']}", json=patch({"op": "Replace", "path": "active", "value": "False"}), headers=S(token))
    assert disabled.status_code == 200 and disabled.json()["active"] is False
    assert member_row(client, ws, user["id"])["status"] == "deactivated"
    assert client.get(f"{SCIM}/Users/{user['id']}", headers=S(token)).json()["active"] is False
    enabled = client.patch(f"{SCIM}/Users/{user['id']}", json=patch({"op": "Replace", "path": "active", "value": True}), headers=S(token))
    assert enabled.json()["active"] is True and member_row(client, ws, user["id"])["status"] == "active"

    bad = client.patch(f"{SCIM}/Users/{user['id']}", json=patch({"op": "Replace", "path": "active", "value": "maybe"}), headers=S(token))
    assert bad.status_code == 400 and bad.json()["scimType"] == "invalidValue"
    assert client.patch(f"{SCIM}/Users/{user['id']}", json=patch({"op": "Remove", "path": "userName"}), headers=S(token)).status_code == 400
    assert client.patch(f"{SCIM}/Users/{user['id']}", json={"Operations": []}, headers=S(token)).json()["scimType"] == "invalidSyntax"
    assert client.patch(f"{SCIM}/Users/{user['id']}", content=b"{not json", headers=S(token)).json()["scimType"] == "invalidSyntax"

    # Hard delete: the person leaves this workspace; GET then answers 404.
    assert client.delete(f"{SCIM}/Users/{user['id']}", headers=S(token)).status_code == 204
    assert client.get(f"{SCIM}/Users/{user['id']}", headers=S(token)).status_code == 404
    assert client.delete(f"{SCIM}/Users/{user['id']}", headers=S(token)).status_code == 404
    assert all(u["id"] != user["id"] for u in client.get(f"{API}/admin/users", headers=ws["headers"]).json())

    # Everything was logged and audited.
    log = client.get(f"{API}/identity/entra_id/provisioning", headers=ws["headers"]).json()
    actions = {i["action"] for i in log["items"]}
    assert {"scim_user_created", "scim_user_updated", "scim_email_kept", "scim_user_deactivated", "scim_user_reactivated", "scim_user_removed"} <= actions
    audit = client.get(f"{API}/admin/audit-logs", headers=ws["headers"], params={"limit": 100}).json()
    entries = audit["items"] if isinstance(audit, dict) else audit
    assert {"scim_provision", "scim_update", "scim_remove"} <= {e["action"] for e in entries}


def test_scim_users_as_okta_sends_them(client, idp):
    ws = signup(client, "Okta Users")
    connect(client, ws, "okta")
    token = scim_token(client, ws, "okta")
    emails = [f"okta{i}-{uuid.uuid4().hex[:6]}@okta.example" for i in range(3)]
    ids = []
    for email in emails:
        body = {
            "schemas": [USER], "userName": email, "name": {"givenName": "Ola", "familyName": "Okta"},
            "emails": [{"primary": True, "value": email, "type": "work"}], "displayName": "Ola Okta", "locale": "en-US",
            "externalId": f"00u{uuid.uuid4().hex[:10]}", "groups": [], "password": "1mz050nq", "active": True,
        }
        created = client.post(f"{SCIM}/Users", json=body, headers=S(token))
        assert created.status_code == 201, created.text
        assert "password" not in created.json() and "groups" not in created.json()
        ids.append(created.json()["id"])

    # Import paging: owner + 3 people, in a stable order, integers only.
    first = client.get(f"{SCIM}/Users", params={"startIndex": 1, "count": 2}, headers=S(token)).json()
    second = client.get(f"{SCIM}/Users", params={"startIndex": 3, "count": 2}, headers=S(token)).json()
    assert first["totalResults"] == 4 and first["itemsPerPage"] == 2 and second["startIndex"] == 3
    assert [r["id"] for r in first["Resources"] + second["Resources"]] == [ws["user_id"], *ids]
    assert client.get(f"{SCIM}/Users", params={"count": 0}, headers=S(token)).json()["Resources"] == []

    # Okta updates profiles with PUT (the whole user).
    current = client.get(f"{SCIM}/Users/{ids[0]}", headers=S(token)).json()
    current["name"]["familyName"] = "Updated"
    current["displayName"] = "Ola Updated"
    replaced = client.put(f"{SCIM}/Users/{ids[0]}", json=current, headers=S(token))
    assert replaced.status_code == 200 and replaced.json()["name"]["familyName"] == "Updated"
    assert member_row(client, ws, ids[0])["full_name"] == "Ola Updated"

    # ...and deactivates with a value-only replace.
    off = client.patch(f"{SCIM}/Users/{ids[0]}", json=patch({"op": "replace", "value": {"active": False}}), headers=S(token))
    assert off.status_code == 200 and off.json()["active"] is False
    on = client.patch(f"{SCIM}/Users/{ids[0]}", json=patch({"op": "replace", "value": {"active": True}}), headers=S(token))
    assert on.json()["active"] is True
    value_only = client.patch(f"{SCIM}/Users/{ids[1]}", json=patch({"op": "replace", "value": {"name.givenName": "Olga", "title": "AE"}}), headers=S(token))
    assert value_only.json()["name"]["givenName"] == "Olga" and value_only.json()["title"] == "AE"

    # The workspace owner already existed: Okta's lookup matches them by email, and a PUT adopts them.
    owner = client.get(f"{SCIM}/Users", params={"filter": f'userName eq "{ws["email"]}"'}, headers=S(token)).json()
    assert owner["totalResults"] == 1 and owner["Resources"][0]["id"] == ws["user_id"]


def test_deprovisioning_revokes_access_here_only(client, idp):
    a = signup(client, "Deprov A")
    b = signup(client, "Deprov B")
    connect(client, a)
    token = scim_token(client, a)
    email = f"leaver-{uuid.uuid4().hex[:6]}@deprov.example"
    in_a = invite(client, a, email, role="admin")
    in_b = invite(client, b, email)
    key = client.post(f"{API}/api-keys", json={"name": "Leaver's key"}, headers=in_a["headers"])
    assert key.status_code == 201, key.text
    key_headers = {"Authorization": f"Bearer {key.json()['key']}", "X-Organization-ID": a["org"]}
    assert client.get(f"{API}/auth/me", headers=key_headers).status_code == 200

    # Entra matches the existing member by userName, then disables them.
    found = client.get(f"{SCIM}/Users", params={"filter": f'userName eq "{email}"'}, headers=S(token)).json()["Resources"][0]
    assert found["id"] == in_a["user_id"] and found["active"] is True
    off = client.patch(f"{SCIM}/Users/{found['id']}", json=patch({"op": "Replace", "path": "active", "value": False}), headers=S(token))
    assert off.status_code == 200 and off.json()["active"] is False

    # Signed out of A, A's API key revoked...
    assert client.get(f"{API}/auth/me", headers=in_a["headers"]).status_code == 403
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": in_a["refresh"]}).status_code == 401
    assert client.get(f"{API}/auth/me", headers=key_headers).status_code == 401
    # ...but B is untouched: membership, session and refresh all keep working.
    assert member_row(client, b, in_b["user_id"])["status"] == "active"
    assert client.get(f"{API}/auth/me", headers=in_b["headers"]).status_code == 200
    refreshed = client.post(f"{API}/auth/refresh", json={"refresh_token": in_b["refresh"]})
    assert refreshed.status_code == 200 and refreshed.json()["organization_id"] == b["org"]
    assert client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD}).json()["organization_id"] == b["org"]

    # People who belong to other workspaces keep their AIVA profile; SCIM stores the provider's values.
    renamed = client.patch(f"{SCIM}/Users/{found['id']}", json=patch({"op": "replace", "path": "displayName", "value": "Renamed By A"}), headers=S(token))
    assert renamed.json()["displayName"] == "Renamed By A"
    assert member_row(client, b, in_b["user_id"])["full_name"] != "Renamed By A"

    # Removing them from A (DELETE) never touches B.
    assert client.delete(f"{SCIM}/Users/{found['id']}", headers=S(token)).status_code == 204
    assert member_row(client, b, in_b["user_id"])["status"] == "active"


def test_scim_never_moves_someone_elses_sign_in(client, idp, dns):
    """Whoever controls the provider can't point the owner's (or any existing member's) sign-in at their own mailbox."""

    domain = f"takeover-{uuid.uuid4().hex[:8]}.com"
    ws = signup(client, "Takeover Co", email=f"owner@{domain}")
    h = ws["headers"]
    connect(client, ws)
    token = scim_token(client, ws)
    dns[domain] = [client.post(f"{API}/identity/domains", json={"domain": domain}, headers=h).json()["domains"][0]["record_value"]]
    assert client.post(f"{API}/identity/domains/{domain}/verify", headers=h).json()["check"]["ok"] is True
    admin = invite(client, ws, f"admin@{domain}", role="admin")

    for user_id, email in ((ws["user_id"], ws["email"]), (admin["user_id"], f"admin@{domain}")):
        current = client.get(f"{SCIM}/Users/{user_id}", headers=S(token)).json()
        hijack = {**current, "userName": f"attacker-{user_id[:8]}@{domain}", "emails": [{"primary": True, "type": "work", "value": f"attacker@{domain}"}],
                  "name": {"givenName": "Mallory", "familyName": "Attacker"}, "displayName": "Mallory Attacker"}
        assert client.put(f"{SCIM}/Users/{user_id}", json=hijack, headers=S(token)).status_code == 200
        patched = client.patch(f"{SCIM}/Users/{user_id}", json=patch({"op": "replace", "path": "emails[type eq \"work\"].value", "value": f"attacker2@{domain}"}), headers=S(token))
        assert patched.status_code == 200
        assert member_row(client, ws, user_id)["email"] == email
    # The owner keeps their whole profile; the adopted admin's name follows the provider, never the email.
    assert member_row(client, ws, ws["user_id"])["full_name"] == "Olivia Owner"
    assert member_row(client, ws, admin["user_id"])["full_name"] == "Mallory Attacker"
    assert client.post(f"{API}/auth/login", json={"email": ws["email"], "password": PASSWORD}).status_code == 200
    assert client.post(f"{API}/auth/login", json={"email": f"attacker@{domain}", "password": PASSWORD}).status_code == 401
    kept = [i for i in client.get(f"{API}/identity/entra_id/provisioning", headers=h).json()["items"] if i["action"] == "scim_email_kept"]
    assert len(kept) == 2 and all("wasn't created by provisioning" in i["message"] for i in kept)

    # Someone provisioning created moves within verified domains only.
    created = client.post(f"{SCIM}/Users", json=entra_user(f"new@{domain}", f"new@{domain}"), headers=S(token)).json()
    elsewhere = client.patch(f"{SCIM}/Users/{created['id']}", json=patch({"op": "replace", "path": "emails[type eq \"work\"].value", "value": "new@unverified-elsewhere.com"}), headers=S(token))
    assert elsewhere.status_code == 200 and member_row(client, ws, created["id"])["email"] == f"new@{domain}"
    renamed = client.patch(f"{SCIM}/Users/{created['id']}", json=patch({"op": "replace", "path": "emails[type eq \"work\"].value", "value": f"renamed@{domain}"}), headers=S(token))
    assert renamed.status_code == 200 and member_row(client, ws, created["id"])["email"] == f"renamed@{domain}"


def test_identity_switches_need_the_app(client, idp):
    """API keys can't turn sign-in and provisioning off, change its settings or disconnect it."""

    ws = signup(client, "App Only Co")
    connect(client, ws)
    key = client.post(f"{API}/api-keys", json={"name": "Automation"}, headers=ws["headers"]).json()["key"]
    k = {"Authorization": f"Bearer {key}", "X-Organization-ID": ws["org"]}
    for provider in ("entra_id", "okta"):
        assert client.put(f"{API}/integration-hub/{provider}/toggle", json={"enabled": False}, headers=k).status_code == 403
        assert client.put(f"{API}/integration-hub/{provider}/settings", json={"config": {"default_role": "admin"}}, headers=k).status_code == 403
        assert client.put(f"{API}/integration-hub/{provider}/credentials", json={"values": {}}, headers=k).status_code == 403
        assert client.delete(f"{API}/integration-hub/{provider}/connection", headers=k).status_code == 403
    detail = client.get(f"{API}/integration-hub/entra_id", headers=ws["headers"]).json()
    assert detail["enabled"] is True and detail["connection"] is not None
    # Other integrations still work with a key.
    assert client.put(f"{API}/integration-hub/slack/toggle", json={"enabled": True}, headers=k).status_code == 200


def test_last_owner_is_never_deprovisioned(client, idp):
    ws = signup(client, "Owner Co")
    connect(client, ws)
    token = scim_token(client, ws)
    refused = client.patch(f"{SCIM}/Users/{ws['user_id']}", json=patch({"op": "replace", "value": {"active": False}}), headers=S(token))
    assert refused.status_code == 400 and refused.json()["scimType"] == "mutability" and "owner" in refused.json()["detail"]
    assert client.delete(f"{SCIM}/Users/{ws['user_id']}", headers=S(token)).status_code == 400
    assert client.get(f"{API}/auth/me", headers=ws["headers"]).status_code == 200
    # Provisioning never hands out the owner role either.
    assert client.put(f"{API}/identity/entra_id/mapping", json={"default_role": "owner"}, headers=ws["headers"]).status_code == 422
    mapped = client.put(f"{API}/identity/entra_id/mapping", json={"group_mappings": [{"group_name": "Owners", "role": "owner"}]}, headers=ws["headers"])
    assert mapped.status_code == 422


# ---------------------------------------------------------------------------
# Groups → roles and teams
# ---------------------------------------------------------------------------


def test_groups_map_to_roles_and_teams(client, idp):
    ws = signup(client, "Groups Co")
    h = ws["headers"]
    connect(client, ws)
    token = scim_token(client, ws)
    team = client.post(f"{API}/admin/teams", json={"name": "EMEA Sales"}, headers=h).json()["id"]
    saved = client.put(
        f"{API}/identity/entra_id/mapping",
        json={"default_role": "viewer", "group_mappings": [
            {"group_id": "obj-admins", "group_name": "AIVA Admins", "role": "admin"},
            {"group_name": "Sales EMEA", "role": "manager", "team_id": team},
        ]},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    people = [client.post(f"{SCIM}/Users", json=entra_user(f"p{i}-{uuid.uuid4().hex[:6]}@groups.example", f"p{i}-{uuid.uuid4().hex[:6]}@groups.example"), headers=S(token)).json() for i in range(2)]
    one, two = people[0]["id"], people[1]["id"]
    assert member_row(client, ws, one)["role"] == "viewer"  # the default role

    # Entra: create the group (externalId = the group's object id), then add members.
    admins = client.post(f"{SCIM}/Groups", json={"schemas": [GROUP], "externalId": "obj-admins", "displayName": "AIVA Admins", "meta": {"resourceType": "Group"}}, headers=S(token))
    assert admins.status_code == 201 and admins.json()["members"] == []
    gid = admins.json()["id"]
    assert client.post(f"{SCIM}/Groups", json={"schemas": [GROUP], "displayName": "aiva admins"}, headers=S(token)).status_code == 409
    added = client.patch(f"{SCIM}/Groups/{gid}", json=patch({"op": "Add", "path": "members", "value": [{"$ref": None, "value": one}, {"value": two}]}), headers=S(token))
    assert added.status_code == 204 and not added.content
    assert member_row(client, ws, one)["role"] == "admin" and member_row(client, ws, two)["role"] == "admin"

    lookup = client.get(f"{SCIM}/Groups", params={"excludedAttributes": "members", "filter": 'displayName eq "AIVA Admins"'}, headers=S(token)).json()
    assert lookup["totalResults"] == 1 and "members" not in lookup["Resources"][0]
    full = client.get(f"{SCIM}/Groups/{gid}", headers=S(token)).json()
    assert {m["value"] for m in full["members"]} == {one, two} and all(m["display"] for m in full["members"])
    assert "members" not in client.get(f"{SCIM}/Groups/{gid}", params={"excludedAttributes": "members"}, headers=S(token)).json()

    # Entra removes with a value list; Okta with a path filter. Both go back to the default role.
    client.patch(f"{SCIM}/Groups/{gid}", json=patch({"op": "Remove", "path": "members", "value": [{"$ref": None, "value": one}]}), headers=S(token))
    assert member_row(client, ws, one)["role"] == "viewer" and member_row(client, ws, two)["role"] == "admin"
    client.patch(f"{SCIM}/Groups/{gid}", json=patch({"op": "remove", "path": f'members[value eq "{two}"]'}), headers=S(token))
    assert member_row(client, ws, two)["role"] == "viewer"

    # A role an admin sets by hand is kept when the person isn't in a role-mapped group.
    assert client.patch(f"{API}/admin/users/{two}", json={"role": "member"}, headers=h).status_code == 200
    client.patch(f"{SCIM}/Groups/{gid}", json=patch({"op": "replace", "path": "displayName", "value": "AIVA Admins (renamed)"}), headers=S(token))
    assert member_row(client, ws, two)["role"] == "member"

    # Okta group push: create with members, rename value-only, replace members; teams follow.
    sales = client.post(f"{SCIM}/Groups", json={"schemas": [GROUP], "displayName": "Sales EMEA", "members": [{"value": one, "display": "one"}]}, headers=S(token)).json()
    row = member_row(client, ws, one)
    assert row["role"] == "manager" and row["team_id"] == team
    renamed = client.patch(f"{SCIM}/Groups/{sales['id']}", json=patch({"op": "replace", "value": {"id": sales["id"], "displayName": "Sales EMEA"}}), headers=S(token))
    assert renamed.status_code == 204
    client.patch(f"{SCIM}/Groups/{sales['id']}", json=patch({"op": "replace", "path": "members", "value": [{"value": two}]}), headers=S(token))
    assert member_row(client, ws, one)["team_id"] is None and member_row(client, ws, one)["role"] == "viewer"
    assert member_row(client, ws, two)["team_id"] == team and member_row(client, ws, two)["role"] == "manager"

    # PUT replaces the group; DELETE removes it and what it gave.
    put = client.put(f"{SCIM}/Groups/{sales['id']}", json={"schemas": [GROUP], "id": sales["id"], "displayName": "Sales EMEA", "members": []}, headers=S(token))
    assert put.status_code == 200 and put.json()["members"] == []
    assert member_row(client, ws, two)["team_id"] is None
    assert client.delete(f"{SCIM}/Groups/{gid}", headers=S(token)).status_code == 204
    assert client.get(f"{SCIM}/Groups/{gid}", headers=S(token)).status_code == 404

    # Automatic teams: a group without a team mapping gets an AIVA team of the same name.
    client.put(f"{API}/identity/entra_id/mapping", json={"auto_teams": True}, headers=h)
    support = client.post(f"{SCIM}/Groups", json={"schemas": [GROUP], "displayName": "Support Tier 2", "members": [{"value": one}]}, headers=S(token)).json()
    teams = {t["name"]: t for t in client.get(f"{API}/admin/teams", headers=h).json()}
    assert "Support Tier 2" in teams and member_row(client, ws, one)["team_id"] == teams["Support Tier 2"]["id"]
    detail = client.get(f"{API}/integration-hub/entra_id", headers=h).json()["extra"]
    assert {g["name"] for g in detail["groups"]} == {"Sales EMEA", "Support Tier 2"} and detail["stats"]["groups"] == 2
    assert detail["mapping"]["auto_teams"] is True and detail["mapping"]["default_role"] == "viewer"
    assert client.delete(f"{SCIM}/Groups/{support['id']}", headers=S(token)).status_code == 204
    assert "Support Tier 2" in {t["name"] for t in client.get(f"{API}/admin/teams", headers=h).json()}  # teams are never deleted


# ---------------------------------------------------------------------------
# Discovery and errors
# ---------------------------------------------------------------------------


def test_discovery_documents_and_errors(client, idp):
    ws = signup(client, "Discovery Co")
    connect(client, ws, "okta")
    token = scim_token(client, ws, "okta")
    assert client.get(f"{SCIM}/ServiceProviderConfig").status_code == 401
    config = client.get(f"{SCIM}/ServiceProviderConfig", headers=S(token))
    assert config.status_code == 200 and config.headers["content-type"].startswith("application/scim+json")
    assert config.json()["patch"]["supported"] is True and config.json()["bulk"]["supported"] is False
    assert config.json()["filter"] == {"supported": True, "maxResults": 200}
    types = client.get(f"{SCIM}/ResourceTypes", headers=S(token)).json()
    assert {t["id"] for t in types["Resources"]} == {"User", "Group"}
    assert client.get(f"{SCIM}/ResourceTypes/User", headers=S(token)).json()["endpoint"] == "/Users"
    schemas = client.get(f"{SCIM}/Schemas", headers=S(token)).json()
    assert schemas["totalResults"] == 3 and ENTERPRISE in {s["id"] for s in schemas["Resources"]}
    assert client.get(f"{SCIM}/Schemas/{USER}", headers=S(token)).json()["name"] == "User"
    assert client.get(f"{SCIM}/Schemas/urn:nope", headers=S(token)).status_code == 404
    assert client.post(f"{SCIM}/Bulk", json={}, headers=S(token)).status_code == 501
    missing = client.get(f"{SCIM}/Users/{uuid.uuid4()}", headers=S(token))
    assert missing.status_code == 404 and missing.json()["status"] == "404" and missing.json()["schemas"] == [ERROR]
    assert client.get(f"{SCIM}/Users/not-a-uuid", headers=S(token)).status_code == 404
    no_email = client.post(f"{SCIM}/Users", json={"schemas": [USER], "userName": "plainname"}, headers=S(token))
    assert no_email.status_code == 400 and no_email.json()["scimType"] == "invalidValue"
    assert client.post(f"{SCIM}/Users", json={"schemas": [USER]}, headers=S(token)).status_code == 400
    assert client.post(f"{SCIM}/Groups", json={"schemas": [GROUP]}, headers=S(token)).status_code == 400
    assert client.get(f"{SCIM}/Groups", params={"filter": "displayName sw \"x\""}, headers=S(token)).json()["scimType"] == "invalidFilter"
    assert client.post(f"{SCIM}/Users", content=b"[]", headers=S(token)).json()["scimType"] == "invalidSyntax"


# ---------------------------------------------------------------------------
# Domain verification and require single sign-on
# ---------------------------------------------------------------------------


@pytest.fixture
def dns(monkeypatch):
    records: dict[str, list[str]] = {}

    async def lookup(name: str) -> list[str]:
        if name.startswith("slow."):
            raise TimeoutError("no answer")
        return records.get(name, [])

    monkeypatch.setattr(idp_policy, "lookup_txt", lookup)
    return records


def test_domain_verification(client, dns):
    ws = signup(client, "Domains Co")
    h = ws["headers"]
    assert client.post(f"{API}/identity/domains", json={"domain": "gmail.com"}, headers=h).status_code == 422
    assert client.post(f"{API}/identity/domains", json={"domain": "not a domain"}, headers=h).status_code == 422
    domain = f"verify-{uuid.uuid4().hex[:8]}.com"
    added = client.post(f"{API}/identity/domains", json={"domain": f"https://www.{domain}/"}, headers=h).json()
    entry = added["domains"][0]
    assert entry["domain"] == domain and entry["status"] == "pending" and entry["record_value"].startswith("aiva-verification=")

    failed = client.post(f"{API}/identity/domains/{domain}/verify", headers=h).json()
    assert failed["check"]["ok"] is False and failed["domains"][0]["status"] == "failed" and "No TXT record" in failed["domains"][0]["error"]
    dns[domain] = ["v=spf1 -all", "aiva-verification=wrong"]
    mismatch = client.post(f"{API}/identity/domains/{domain}/verify", headers=h).json()
    assert "doesn't match" in mismatch["check"]["message"]
    dns[f"_aiva-verification.{domain}"] = [entry["record_value"]]
    verified = client.post(f"{API}/identity/domains/{domain}/verify", headers=h).json()
    assert verified["check"]["ok"] is True and verified["domains"][0]["status"] == "verified" and verified["domains"][0]["verified_at"]
    # A later DNS hiccup doesn't un-verify the domain.
    dns.clear()
    again = client.post(f"{API}/identity/domains/{domain}/verify", headers=h).json()
    assert again["domains"][0]["status"] == "verified" and again["domains"][0]["error"]
    assert client.delete(f"{API}/identity/domains/{domain}", headers=h).json()["domains"] == []
    assert client.post(f"{API}/identity/domains/{domain}/verify", headers=h).status_code == 404


def _sso_sign_in(client, idp, email: str, sub: str) -> dict:
    started = client.post(f"{API}/auth/oauth/start", json={"provider": "sso", "intent": "login", "next": "/", "email": email})
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorization_url"]).query)
    assert query["client_id"] == [SSO_CLIENT]
    idp.next_claims = {"iss": ENTRA_ISSUER, "sub": sub, "email": email, "preferred_username": email, "name": "Sso Person", "nonce": query["nonce"][0]}
    finished = client.post(
        f"{API}/auth/oauth/callback",
        json={"provider": "sso", "code": "good-code", "state": query["state"][0], "state_token": started.json()["state_token"]},
    )
    assert finished.status_code == 200, finished.text
    return finished.json()


def _google_sign_in(client, idp, email: str, sub: str) -> dict:
    started = client.post(f"{API}/auth/oauth/start", json={"provider": "google", "intent": "login", "next": "/"})
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorization_url"]).query)
    idp.next_claims = {"iss": "https://accounts.google.com", "sub": sub, "email": email, "email_verified": True, "nonce": query["nonce"][0]}
    finished = client.post(
        f"{API}/auth/oauth/callback",
        json={"provider": "google", "code": "good-code", "state": query["state"][0], "state_token": started.json()["state_token"]},
    )
    assert finished.status_code == 200, finished.text
    return finished.json()


def test_require_sso_with_owner_break_glass(client, idp, dns, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", "google-client")
    monkeypatch.setattr(get_settings(), "google_client_secret", "google-secret")
    domain = f"corp-{uuid.uuid4().hex[:8]}.com"
    ws = signup(client, "Corp", email=f"owner@{domain}")
    h = ws["headers"]
    connect(client, ws)
    member_email, multi_email = f"m@{domain}", f"multi@{domain}"
    member = invite(client, ws, member_email, role="admin")
    multi = invite(client, ws, multi_email)
    other = signup(client, "Partner Co")
    invite(client, other, multi_email)
    key = client.post(f"{API}/api-keys", json={"name": "Integration"}, headers=member["headers"]).json()["key"]

    # Preconditions: a verified domain, then single sign-on that routes it.
    assert client.put(f"{API}/identity/policy", json={"require_sso": True}, headers=h).status_code == 409
    record = client.post(f"{API}/identity/domains", json={"domain": domain}, headers=h).json()["domains"][0]["record_value"]
    dns[domain] = [record]
    assert client.post(f"{API}/identity/domains/{domain}/verify", headers=h).json()["check"]["ok"] is True
    blocked = client.put(f"{API}/identity/policy", json={"require_sso": True}, headers=h)
    assert blocked.status_code == 409 and "single sign-on" in blocked.json()["detail"]
    assert client.put(f"{API}/identity/entra_id/sso", json={"client_id": SSO_CLIENT, "client_secret": "sso-secret", "domains": [domain]}, headers=h).status_code == 200

    # An admin (not owner) covered by the policy must have used SSO before turning it on.
    early = client.put(f"{API}/identity/policy", json={"require_sso": True}, headers=member["headers"])
    assert early.status_code == 409 and "Sign in once" in early.json()["detail"]
    on = client.put(f"{API}/identity/policy", json={"require_sso": True}, headers=h)
    assert on.status_code == 200, on.text
    assert on.json()["enforced"] is True and on.json()["covered_members"] == 2 and on.json()["you"]["owner"] is True

    # Password and Google-style sign-in are refused for covered members, with a clear message.
    refused = client.post(f"{API}/auth/login", json={"email": member_email, "password": PASSWORD})
    assert refused.status_code == 403 and "single sign-on" in refused.json()["detail"] and "Log In with your Organization" in refused.json()["detail"]
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": member["refresh"]}).status_code == 403
    google = _google_sign_in(client, idp, member_email, f"google-{uuid.uuid4().hex[:8]}")
    assert google["status"] == "error" and "single sign-on" in google["message"]
    # Owners keep password sign-in (break-glass); API keys keep working.
    assert client.post(f"{API}/auth/login", json={"email": ws["email"], "password": PASSWORD}).status_code == 200
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {key}", "X-Organization-ID": ws["org"]}).status_code == 200

    # Single sign-on works, and the session stays in this workspace.
    result = _sso_sign_in(client, idp, member_email, "entra-oid-m")
    assert result["status"] == "session" and result["session"]["organization_id"] == ws["org"]
    # ...also with two-step verification after the single sign-on step.
    from app.core import totp

    sso_headers = {"Authorization": f"Bearer {result['session']['access_token']}", "X-Organization-ID": ws["org"]}
    secret = client.post(f"{API}/auth/mfa/setup", headers=sso_headers).json()["secret"]
    assert client.post(f"{API}/auth/mfa/enable", json={"code": totp.current_code(secret)}, headers=sso_headers).status_code == 200
    challenge = _sso_sign_in(client, idp, member_email, "entra-oid-m")
    assert challenge["status"] == "mfa"
    verified = client.post(f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": totp.current_code(secret)})
    assert verified.status_code == 200 and verified.json()["organization_id"] == ws["org"]
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {verified.json()['access_token']}", "X-Organization-ID": ws["org"]}).status_code == 200
    refreshed_sso = client.post(f"{API}/auth/refresh", json={"refresh_token": verified.json()["refresh_token"]})
    assert refreshed_sso.status_code == 200
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {refreshed_sso.json()['access_token']}", "X-Organization-ID": ws["org"]}).status_code == 200

    # Someone who also belongs to another workspace can still use their password there, but that
    # session can't open the single sign-on workspace (not even by editing the header).
    login = client.post(f"{API}/auth/login", json={"email": multi_email, "password": PASSWORD})
    assert login.status_code == 200 and login.json()["organization_id"] == other["org"]
    assert [o["id"] for o in login.json()["organizations"]] == [other["org"]]
    sneaky = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/auth/me", headers=sneaky).status_code == 403
    assert client.post(f"{API}/auth/login", json={"email": multi_email, "password": PASSWORD, "organization_id": ws["org"]}).status_code == 403
    switched = client.post(f"{API}/auth/refresh", json={"refresh_token": multi["refresh"], "organization_id": ws["org"]})
    assert switched.status_code == 200 and switched.json()["organization_id"] == other["org"]
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {switched.json()['access_token']}", "X-Organization-ID": ws["org"]}).status_code == 403
    # ...and because this workspace verified the domain, single sign-on opens it for them (only it).
    multi_sso = _sso_sign_in(client, idp, multi_email, "entra-oid-multi")
    assert multi_sso["status"] == "session" and [o["id"] for o in multi_sso["session"]["organizations"]] == [ws["org"]]
    multi_headers = {"Authorization": f"Bearer {multi_sso['session']['access_token']}", "X-Organization-ID": ws["org"]}
    assert client.get(f"{API}/auth/me", headers=multi_headers).status_code == 200
    assert client.get(f"{API}/auth/me", headers={**multi_headers, "X-Organization-ID": other["org"]}).status_code == 403

    # New invitations for the domain are accepted by signing in with single sign-on.
    late = client.post(f"{API}/admin/users/invite", json={"email": f"late@{domain}", "full_name": "Late", "role": "member"}, headers=h).json()
    accept = client.post(f"{API}/auth/invitations/accept", json={"token": late["invite_url"].split("token=")[1], "password": PASSWORD})
    assert accept.status_code == 403 and "single sign-on" in accept.json()["detail"]

    # Can't remove the last verified domain while it's enforced; turning it off restores passwords.
    assert client.delete(f"{API}/identity/domains/{domain}", headers=h).status_code == 409
    assert client.put(f"{API}/identity/policy", json={"require_sso": False}, headers=h).json()["enforced"] is False
    assert client.post(f"{API}/auth/login", json={"email": member_email, "password": PASSWORD}).status_code == 200
    audit = client.get(f"{API}/admin/audit-logs", headers=h, params={"limit": 200}).json()
    entries = audit["items"] if isinstance(audit, dict) else audit
    assert {"require_sso_on", "require_sso_off", "domain_verified", "login_blocked"} <= {e["action"] for e in entries}


def test_require_sso_reaches_restricted_password_sessions_and_custom_roles(client, idp, dns):
    """A password session kept out of a single sign-on workspace isn't exempt from the policy
    in the workspace it was kept in, and custom roles (whatever their name) are covered."""

    domain = f"policy-{uuid.uuid4().hex[:8]}.com"
    a = signup(client, "Policy A", email=f"owner@{domain}")
    b = signup(client, "Policy B")
    connect(client, a)
    role = client.post(f"{API}/admin/roles", json={"name": "Support", "copy_from": "member"}, headers=a["headers"])
    assert role.status_code == 201 and role.json()["key"] == "support"
    email = f"pat@{domain}"
    in_a = invite(client, a, email, role="support")
    invite(client, b, email)
    dns[domain] = [client.post(f"{API}/identity/domains", json={"domain": domain}, headers=a["headers"]).json()["domains"][0]["record_value"]]
    assert client.post(f"{API}/identity/domains/{domain}/verify", headers=a["headers"]).json()["check"]["ok"] is True
    assert client.put(f"{API}/identity/entra_id/sso", json={"client_id": SSO_CLIENT, "client_secret": "sso-secret", "domains": [domain]}, headers=a["headers"]).status_code == 200
    on = client.put(f"{API}/identity/policy", json={"require_sso": True}, headers=a["headers"])
    assert on.status_code == 200 and on.json()["covered_members"] == 1
    assert client.get(f"{API}/auth/me", headers=in_a["headers"]).status_code == 403

    # A password sign-in now opens B only, in a session that stays there.
    login = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200 and login.json()["organization_id"] == b["org"]
    in_b = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Organization-ID": b["org"]}
    assert client.get(f"{API}/auth/me", headers=in_b).status_code == 200

    # The domain moves to B's single sign-on, and B requires it too: that session stops working.
    assert client.put(f"{API}/identity/policy", json={"require_sso": False}, headers=a["headers"]).status_code == 200
    assert client.delete(f"{API}/sso", headers=a["headers"]).status_code == 204
    connect(client, b)
    dns[domain] = [client.post(f"{API}/identity/domains", json={"domain": domain}, headers=b["headers"]).json()["domains"][0]["record_value"]]
    assert client.post(f"{API}/identity/domains/{domain}/verify", headers=b["headers"]).json()["check"]["ok"] is True
    assert client.put(f"{API}/identity/entra_id/sso", json={"client_id": SSO_CLIENT, "client_secret": "sso-secret", "domains": [domain]}, headers=b["headers"]).status_code == 200
    assert client.put(f"{API}/identity/policy", json={"require_sso": True}, headers=b["headers"]).status_code == 200
    assert client.get(f"{API}/auth/me", headers=in_b).status_code == 403
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": login.json()["refresh_token"]}).status_code == 403
    # A verified by the domain too: it can't take single sign-on back from B without B letting go.
    taken = client.put(f"{API}/identity/entra_id/sso", json={"client_id": SSO_CLIENT, "client_secret": "sso-secret", "domains": [domain]}, headers=a["headers"])
    assert taken.status_code == 409 and "verified by another AIVA workspace too" in taken.json()["detail"]


def test_require_sso_needs_working_sso(client, idp, dns):
    """If single sign-on stops working (turned off), passwords work again instead of locking people out."""

    domain = f"lock-{uuid.uuid4().hex[:8]}.com"
    ws = signup(client, "Lock Co", email=f"boss@{domain}")
    h = ws["headers"]
    connect(client, ws)
    invite(client, ws, f"worker@{domain}")
    record = client.post(f"{API}/identity/domains", json={"domain": domain}, headers=h).json()["domains"][0]["record_value"]
    dns[domain] = [record]
    client.post(f"{API}/identity/domains/{domain}/verify", headers=h)
    client.put(f"{API}/identity/entra_id/sso", json={"client_id": SSO_CLIENT, "client_secret": "sso-secret", "domains": [domain]}, headers=h)
    assert client.put(f"{API}/identity/policy", json={"require_sso": True}, headers=h).status_code == 200
    assert client.post(f"{API}/auth/login", json={"email": f"worker@{domain}", "password": PASSWORD}).status_code == 403
    client.put(f"{API}/identity/entra_id/sso", json={"client_id": SSO_CLIENT, "domains": [domain], "is_active": False}, headers=h)
    assert client.post(f"{API}/auth/login", json={"email": f"worker@{domain}", "password": PASSWORD}).status_code == 200
    policy = client.get(f"{API}/identity/policy", headers=h).json()
    assert policy["require_sso"] is True and policy["enforced"] is False and policy["problems"]

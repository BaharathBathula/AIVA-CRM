"""Documents, e-signature and scheduling integrations: Google Drive, OneDrive,
Dropbox, DocuSign and Calendly.

Provider APIs are never called: sign-in (token exchange, identity) and every
provider request (``app.integrations.core.request``) are replaced with fakes
that answer like the real APIs and record what AIVA sent.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1"
PASSWORD = "Integration#2026"
DRIVE = "https://www.googleapis.com/drive/v3"
GRAPH = "https://graph.microsoft.com/v1.0"
DBX = "https://api.dropboxapi.com/2"
DBX_CONTENT = "https://content.dropboxapi.com/2"
DS_BASE = "https://demo.docusign.net/restapi/v2.1/accounts/acc-1"
CAL = "https://api.calendly.com"


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client


def signup(client, company: str = "Docs Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@docs.example"
    try:
        response = client.post(f"{API}/auth/signup", json={"full_name": "Dana Owner", "email": email, "password": PASSWORD, "company_name": company})
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
        "headers": {"Authorization": f"Bearer {data['access_token']}", "X-Organization-ID": data["organization_id"]},
    }


class FakeApi:
    """Answers provider calls made through core.request, and remembers them."""

    def __init__(self):
        self.routes: list[tuple[str, str, bool, object]] = []
        self.calls: list[tuple[str, str, dict]] = []

    def on(self, method: str, url: str, answer, exact: bool = False):
        self.routes.insert(0, (method, url, exact, answer))

    def last(self, method: str, url_part: str) -> dict:
        for m, url, kwargs in reversed(self.calls):
            if m == method and url_part in url:
                return kwargs
        raise AssertionError(f"no {method} call to {url_part}")

    async def __call__(self, db, connection, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, target, exact, answer in self.routes:
            if m == method and (url == target if exact else url.startswith(target)):
                result = answer(url, kwargs) if callable(answer) else answer
                if isinstance(result, Exception):
                    raise result
                if isinstance(result, httpx.Response):
                    return result
                return httpx.Response(200, json=result, request=httpx.Request(method, url))
        raise AssertionError(f"Unexpected provider call: {method} {url}")


@pytest.fixture
def fake(monkeypatch):
    from app.integrations import core

    api = FakeApi()
    monkeypatch.setattr(core, "request", api)
    return api


def connect_oauth(client, ws, monkeypatch, key: str, family: str, who: dict) -> None:
    from app.integrations import core, identity

    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/{key}/toggle", json={"enabled": True}, headers=h).status_code == 200
    assert client.put(f"{API}/integration-hub/apps/{family}", json={"client_id": f"{family}-client", "client_secret": "client-secret-value"}, headers=h).status_code == 200
    started = client.post(f"{API}/integration-hub/{key}/connect", json={}, headers=h)
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]

    async def fake_token(family_def, app_creds, data):
        return {"access_token": f"{key}-access", "refresh_token": f"{key}-refresh", "expires_in": 3600}

    async def fake_identify(family_key, integration_key, tokens, app_creds):
        return who

    monkeypatch.setattr(core, "_token_request", fake_token)
    monkeypatch.setattr(identity, "identify", fake_identify)
    back = client.get(f"{API}/public/integrations/oauth/callback", params={"state": state, "code": "auth-code"}, follow_redirects=False)
    assert back.status_code == 302 and f"connected={key}" in back.headers["location"], back.headers["location"]


def invite_member(client, ws) -> dict:
    email = f"member-{uuid.uuid4().hex[:8]}@docs.example"
    invited = client.post(f"{API}/admin/users/invite", json={"email": email, "full_name": "Max Member", "role": "member"}, headers=ws["headers"]).json()
    joined = client.post(f"{API}/auth/invitations/accept", json={"token": invited["invite_url"].split("token=")[1], "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {joined['access_token']}", "X-Organization-ID": ws["org"]}


def make_contact(client, ws, email: str | None = None) -> dict:
    account = client.post(f"{API}/accounts", json={"name": f"Account {uuid.uuid4().hex[:6]}"}, headers=ws["headers"])
    assert account.status_code == 201, account.text
    contact = client.post(
        f"{API}/contacts",
        json={"first_name": "Cara", "last_name": "Client", "email": email or f"cara-{uuid.uuid4().hex[:6]}@client.example", "account_id": account.json()["id"]},
        headers=ws["headers"],
    )
    assert contact.status_code == 201, contact.text
    return {"account": account.json(), "contact": contact.json()}


# ===========================================================================
# Cloud files
# ===========================================================================


def test_drive_search_attach_copy_and_remove(client, monkeypatch, fake):
    ws = signup(client)
    h = ws["headers"]
    connect_oauth(client, ws, monkeypatch, "google_drive", "google", {"label": "dana@drive.example", "external_id": "g-1"})
    records = make_contact(client, ws)
    contact_id = records["contact"]["id"]

    fake.on(
        "GET",
        f"{DRIVE}/files",
        {
            "files": [
                {"id": "doc1", "name": "Proposal", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2026-10-01T10:00:00Z",
                 "webViewLink": "https://docs.google.com/document/d/doc1/edit", "owners": [{"displayName": "Dana"}]},
                {"id": "pdf1", "name": "Spec.pdf", "mimeType": "application/pdf", "size": "1234"},
                {"id": "fold1", "name": "Deal room", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "nextPageToken": "page-2",
        },
        exact=True,
    )
    found = client.get(f"{API}/cloud-files/google_drive/browse", params={"q": "Dana's proposal"}, headers=h)
    assert found.status_code == 200, found.text
    page = found.json()
    assert page["cursor"] == "page-2" and len(page["items"]) == 3
    kinds = {i["id"]: i for i in page["items"]}
    assert kinds["doc1"]["kind"] == "Google Doc" and kinds["doc1"]["copyable"] is True
    assert kinds["fold1"]["is_folder"] is True
    assert "name contains 'Dana\\'s proposal'" in fake.last("GET", f"{DRIVE}/files")["params"]["q"]

    fake.on("GET", f"{DRIVE}/files/doc1", {"id": "doc1", "name": "Proposal", "mimeType": "application/vnd.google-apps.document",
                                            "webViewLink": "https://docs.google.com/document/d/doc1/edit", "modifiedTime": "2026-10-01T10:00:00Z"}, exact=True)
    body = {"provider": "google_drive", "file_id": "doc1", "record_type": "contact", "record_id": contact_id}
    first = client.post(f"{API}/cloud-files/links", json=body, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True and first.json()["link"]["web_url"].startswith("https://docs.google.com/")
    again = client.post(f"{API}/cloud-files/links", json=body, headers=h)
    assert again.json()["created"] is False and again.json()["link"]["id"] == first.json()["link"]["id"]
    links = client.get(f"{API}/cloud-files/links", params={"record_type": "contact", "record_id": contact_id}, headers=h).json()["items"]
    assert len(links) == 1 and links[0]["added_by"] == "Dana Owner" and links[0]["can_remove"] is True

    fake.on("GET", f"{DRIVE}/files/doc1/export", httpx.Response(200, content=b"PK\x03\x04 docx bytes", request=httpx.Request("GET", "x")), exact=True)
    copied = client.post(f"{API}/cloud-files/links/{links[0]['id']}/copy", json={"format": "office"}, headers=h)
    assert copied.status_code == 200, copied.text
    assert copied.json()["document"]["name"] == "Proposal.docx"
    assert fake.last("GET", "/export")["params"]["mimeType"].endswith("wordprocessingml.document")
    documents = client.get(f"{API}/documents", params={"record_type": "contact", "record_id": contact_id}, headers=h).json()["items"]
    assert [d["name"] for d in documents] == ["Proposal.docx"]

    # Folders can be attached but not copied.
    fake.on("GET", f"{DRIVE}/files/fold1", {"id": "fold1", "name": "Deal room", "mimeType": "application/vnd.google-apps.folder"}, exact=True)
    folder = client.post(f"{API}/cloud-files/links", json={**body, "file_id": "fold1"}, headers=h).json()["link"]
    assert folder["is_folder"] and folder["web_url"] == "https://drive.google.com/drive/folders/fold1"
    assert client.post(f"{API}/cloud-files/links/{folder['id']}/copy", json={}, headers=h).status_code == 422

    # Files bigger than the Documents limit are refused before downloading.
    fake.on("GET", f"{DRIVE}/files/big1", {"id": "big1", "name": "Video.mp4", "mimeType": "video/mp4", "size": str(900 * 1024 * 1024)}, exact=True)
    big = client.post(f"{API}/cloud-files/links", json={**body, "file_id": "big1"}, headers=h).json()["link"]
    too_big = client.post(f"{API}/cloud-files/links/{big['id']}/copy", json={}, headers=h)
    assert too_big.status_code == 413 and "MB" in too_big.json()["detail"]
    assert not any(url.endswith("big1") and kwargs.get("params", {}).get("alt") == "media" for _, url, kwargs in fake.calls)

    # Bad file references never reach the provider.
    assert client.post(f"{API}/cloud-files/links", json={**body, "file_id": "../../about"}, headers=h).status_code == 422

    # Members can't remove links someone else attached.
    member = invite_member(client, ws)
    assert client.delete(f"{API}/cloud-files/links/{links[0]['id']}", headers=member).status_code == 403
    member_view = client.get(f"{API}/cloud-files/links", params={"record_type": "contact", "record_id": contact_id}, headers=member).json()["items"]
    assert all(item["can_remove"] is False for item in member_view)

    # Admins can stop copying; removing links.
    client.put(f"{API}/integration-hub/google_drive/settings", json={"config": {"allow_copy": False}}, headers=h)
    blocked = client.post(f"{API}/cloud-files/links/{links[0]['id']}/copy", json={}, headers=h)
    assert blocked.status_code == 403
    assert client.put(f"{API}/integration-hub/google_drive/settings", json={"config": {"nope": 1}}, headers=h).status_code == 422
    assert client.delete(f"{API}/cloud-files/links/{links[0]['id']}", headers=h).status_code == 200
    remaining = client.get(f"{API}/cloud-files/links", params={"record_type": "contact", "record_id": contact_id}, headers=h).json()["items"]
    assert {r["name"] for r in remaining} == {"Deal room", "Video.mp4"}

    detail = client.get(f"{API}/integration-hub/google_drive", headers=h).json()
    assert detail["extra"]["links_total"] == 2 and detail["extra"]["allow_copy"] is False
    actions = {log["action"] for log in detail["logs"]}
    assert {"file_attached", "file_copied", "file_detached"} <= actions
    assert "google_drive-access" not in json.dumps(detail)


def test_files_need_your_own_connection(client, monkeypatch, fake):
    ws = signup(client)
    h = ws["headers"]
    off = client.get(f"{API}/cloud-files/onedrive/browse", headers=h)
    assert off.status_code == 409 and "turned off" in off.json()["detail"]
    client.put(f"{API}/integration-hub/onedrive/toggle", json={"enabled": True}, headers=h)
    missing = client.get(f"{API}/cloud-files/onedrive/browse", headers=h)
    assert missing.status_code == 409 and "Connect your account" in missing.json()["detail"]
    providers = client.get(f"{API}/cloud-files/providers", headers=h).json()["items"]
    assert {p["key"]: p["status"] for p in providers} == {"google_drive": "off", "onedrive": "not_connected", "dropbox": "off"}


def test_onedrive_ids_and_page_links(client, monkeypatch, fake):
    ws = signup(client)
    h = ws["headers"]
    connect_oauth(client, ws, monkeypatch, "onedrive", "microsoft", {"label": "dana@contoso.example", "external_id": "ms-1"})
    fake.on(
        "GET",
        f"{GRAPH}/me/drive/root/children",
        {
            "value": [
                {"id": "ITEM1", "name": "b.pdf", "size": 10, "file": {"mimeType": "application/pdf"}, "webUrl": "https://contoso.sharepoint.com/b.pdf",
                 "parentReference": {"driveId": "DRIVE1", "path": "/drive/root:"}},
                {"id": "LOCAL", "name": "Shared deck.pptx", "remoteItem": {"id": "REMOTE9", "parentReference": {"driveId": "OTHER"},
                 "file": {"mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}}},
                {"id": "F1", "name": "a folder", "folder": {"childCount": 2}, "parentReference": {"driveId": "DRIVE1"}},
            ],
            "@odata.nextLink": f"{GRAPH}/me/drive/root/children?$skiptoken=abc",
        },
    )
    page = client.get(f"{API}/cloud-files/onedrive/browse", headers=h).json()
    assert [i["id"] for i in page["items"]] == ["DRIVE1:F1", "DRIVE1:ITEM1", "OTHER:REMOTE9"]
    assert page["items"][2]["kind"] == "Presentation"
    assert client.get(f"{API}/cloud-files/onedrive/browse", params={"cursor": "https://evil.example/steal"}, headers=h).status_code == 422
    assert client.get(f"{API}/cloud-files/onedrive/browse", params={"cursor": page["cursor"]}, headers=h).status_code == 200

    fake.on("GET", f"{GRAPH}/drives/OTHER/items/REMOTE9", {"id": "REMOTE9", "name": "Shared deck.pptx", "size": 2048,
                                                         "file": {"mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
                                                         "parentReference": {"driveId": "OTHER"}, "webUrl": "https://contoso.sharepoint.com/deck"}, exact=True)
    fake.on("GET", f"{GRAPH}/drives/OTHER/items/REMOTE9/content", httpx.Response(200, content=b"deck-bytes", request=httpx.Request("GET", "x")), exact=True)
    records = make_contact(client, ws)
    link = client.post(
        f"{API}/cloud-files/links",
        json={"provider": "onedrive", "file_id": "OTHER:REMOTE9", "record_type": "account", "record_id": records["account"]["id"]},
        headers=h,
    ).json()["link"]
    copied = client.post(f"{API}/cloud-files/links/{link['id']}/copy", json={}, headers=h)
    assert copied.status_code == 200, copied.text
    assert fake.last("GET", "/content")["follow_redirects"] is True


def test_dropbox_search_shared_link_and_download(client, monkeypatch, fake):
    ws = signup(client)
    h = ws["headers"]
    connect_oauth(client, ws, monkeypatch, "dropbox", "dropbox", {"label": "dana@dropbox.example", "external_id": "dbid:1"})
    fake.on(
        "POST",
        f"{DBX}/files/search_v2",
        {
            "matches": [
                {"metadata": {".tag": "metadata", "metadata": {".tag": "file", "id": "id:abc", "name": "Contract.pdf", "path_display": "/Deals/Acme/Contract.pdf",
                                                             "size": 5000, "server_modified": "2026-09-30T12:00:00Z"}}},
                {"metadata": {".tag": "metadata", "metadata": {".tag": "folder", "id": "id:fold", "name": "Acme", "path_display": "/Deals/Acme"}}},
            ],
            "has_more": True,
            "cursor": "search-cursor",
        },
    )
    page = client.get(f"{API}/cloud-files/dropbox/browse", params={"q": "contract"}, headers=h).json()
    assert page["cursor"] == "s:search-cursor"
    files = {i["id"]: i for i in page["items"]}
    assert files["id:abc"]["web_url"] == "https://www.dropbox.com/home/Deals/Acme?preview=Contract.pdf"
    assert json.loads(fake.last("POST", "search_v2")["content"])["query"] == "contract"
    fake.on("POST", f"{DBX}/files/search/continue_v2", {"matches": [], "has_more": False})
    assert client.get(f"{API}/cloud-files/dropbox/browse", params={"cursor": page["cursor"]}, headers=h).status_code == 200
    assert json.loads(fake.last("POST", "continue_v2")["content"]) == {"cursor": "search-cursor"}

    fake.on("POST", f"{DBX}/files/get_metadata", {".tag": "file", "id": "id:abc", "name": "Contract.pdf", "path_display": "/Deals/Acme/Contract.pdf", "size": 5000})
    fake.on("POST", f"{DBX}/sharing/list_shared_links", {"links": [{"url": "https://www.dropbox.com/scl/fi/abc/Contract.pdf"}]})
    records = make_contact(client, ws)
    link = client.post(
        f"{API}/cloud-files/links",
        json={"provider": "dropbox", "file_id": "id:abc", "record_type": "contact", "record_id": records["contact"]["id"]},
        headers=h,
    ).json()["link"]
    assert link["web_url"] == "https://www.dropbox.com/scl/fi/abc/Contract.pdf"

    fake.on("POST", f"{DBX_CONTENT}/files/download", httpx.Response(200, content=b"%PDF-1.7 contract", request=httpx.Request("POST", "x")))
    copied = client.post(f"{API}/cloud-files/links/{link['id']}/copy", json={}, headers=h)
    assert copied.status_code == 200, copied.text
    assert copied.json()["document"]["content_type"] == "application/pdf"
    assert json.loads(fake.last("POST", "files/download")["headers"]["Dropbox-API-Arg"]) == {"path": "id:abc"}
    assert client.post(f"{API}/cloud-files/links", json={"provider": "dropbox", "file_id": "relative/path", "record_type": "contact",
                                                         "record_id": records["contact"]["id"]}, headers=h).status_code == 422


# ===========================================================================
# DocuSign
# ===========================================================================


DOCUSIGN_WHO = {
    "label": "Docs Co (dana@docs.example)",
    "external_id": "acc-1",
    "config": {"account_id": "acc-1", "base_uri": "https://demo.docusign.net", "environment": "demo", "email": "dana@docs.example"},
}


def make_quote(client, ws, contact: dict) -> dict:
    response = client.post(
        f"{API}/quotes",
        json={
            "name": "Annual plan",
            "account_id": contact["account_id"],
            "contact_id": contact["id"],
            "terms": "Net 30",
            "line_items": [{"name": "Platform licence", "quantity": "10", "unit_price": "120.00", "discount_percent": "10"}],
        },
        headers=ws["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_docusign_quote_signed_through_connect_webhook(client, monkeypatch, fake):
    ws = signup(client)
    h = ws["headers"]
    connect_oauth(client, ws, monkeypatch, "docusign", "docusign", DOCUSIGN_WHO)
    contact = make_contact(client, ws, email="signer@client.example")["contact"]
    quote = make_quote(client, ws, contact)

    sources = client.get(f"{API}/e-signatures/sources", params={"record_type": "quote", "record_id": quote["id"]}, headers=h)
    assert sources.status_code == 200, sources.text
    assert sources.json()["quotes"][0]["id"] == quote["id"]
    assert sources.json()["recipients"][0]["email"] == "signer@client.example"

    fake.on("POST", f"{DS_BASE}/envelopes", {"envelopeId": "env-1", "status": "sent", "statusDateTime": "2026-10-23T10:00:00Z"}, exact=True)
    sent = client.post(
        f"{API}/e-signatures",
        json={
            "record_type": "quote", "record_id": quote["id"], "quote_id": quote["id"], "subject": "Please sign your quote",
            "message": "Thanks!", "recipients": [{"name": "Cara Client", "email": "signer@client.example"}, {"name": "Dana", "email": ws["email"], "role": "cc"}],
        },
        headers=h,
    )
    assert sent.status_code == 201, sent.text
    request = sent.json()
    assert request["status"] == "sent" and request["envelope_id"] == "env-1" and request["signer_count"] == 1
    envelope = fake.last("POST", "/envelopes")["json"]
    assert envelope["status"] == "sent" and envelope["emailSubject"] == "Please sign your quote"
    assert envelope["documents"][0]["fileExtension"] == "html"
    html_doc = base64.b64decode(envelope["documents"][0]["documentBase64"]).decode()
    assert "/aivasign1/" in html_doc and f"Q-{quote['number']}" in html_doc and "Platform licence" in html_doc
    assert envelope["recipients"]["signers"][0]["tabs"]["signHereTabs"][0]["anchorString"] == "/aivasign1/"
    assert envelope["recipients"]["carbonCopies"][0]["email"] == ws["email"]
    assert {"name": "aiva_request_id", "value": request["id"], "show": "false", "required": "false"} in envelope["customFields"]["textCustomFields"]
    assert "eventNotification" not in envelope  # not on a public https address in tests
    assert client.get(f"{API}/quotes/{quote['id']}", headers=h).json()["status"] == "sent"

    # Pushes must carry the Connect HMAC once a key is saved.
    hmac_key = "connect-hmac-key-0123456789abcdef"
    assert client.put(f"{API}/e-signatures/connect-key", json={"hmac_key": hmac_key}, headers=h).json() == {"hmac_configured": True}
    detail = client.get(f"{API}/integration-hub/docusign", headers=h).json()
    assert detail["extra"]["connect"]["hmac_configured"] is True and hmac_key not in json.dumps(detail)
    token = detail["connection"]["webhook_url"].rsplit("/", 1)[1]
    push = json.dumps({"event": "envelope-completed", "data": {"accountId": "acc-1", "envelopeId": "env-1"}}).encode()
    good = base64.b64encode(hmac.new(hmac_key.encode(), push, hashlib.sha256).digest()).decode()

    fake.on(
        "GET",
        f"{DS_BASE}/envelopes/env-1",
        {
            "status": "completed",
            "completedDateTime": "2026-10-23T11:00:00Z",
            "recipients": {"signers": [{"recipientId": "1", "status": "completed", "deliveredDateTime": "2026-10-23T10:30:00Z",
                                        "signedDateTime": "2026-10-23T10:59:00Z"}], "carbonCopies": [{"recipientId": "2", "status": "completed"}]},
        },
        exact=True,
    )
    fake.on("GET", f"{DS_BASE}/envelopes/env-1/documents/combined", httpx.Response(200, content=b"%PDF-1.4 signed", request=httpx.Request("GET", "x")), exact=True)
    url = f"{API}/public/integrations/docusign/webhook/{token}"
    assert client.post(url, content=push, headers={"Content-Type": "application/json"}).status_code == 401
    assert client.post(url, content=push, headers={"X-DocuSign-Signature-1": base64.b64encode(b"x" * 32).decode()}).status_code == 401
    accepted = client.post(url, content=push, headers={"Content-Type": "application/json", "X-DocuSign-Signature-1": good})
    assert accepted.status_code == 200 and accepted.json()["status"] == "completed", accepted.text
    # A retried push changes nothing (no second signed copy).
    assert client.post(url, content=push, headers={"X-DocuSign-Signature-1": good}).status_code == 200

    listed = client.get(f"{API}/e-signatures", params={"record_type": "quote", "record_id": quote["id"]}, headers=h).json()["items"]
    assert len(listed) == 1
    done = listed[0]
    assert done["status"] == "completed" and done["signed_count"] == 1 and done["signed_document_id"]
    assert [e["action"] for e in done["timeline"]][:1] == ["sent"]
    assert {"recipient_signed", "completed", "quote_accepted"} <= {e["action"] for e in done["timeline"]}
    signed_docs = client.get(f"{API}/documents", params={"record_type": "quote", "record_id": quote["id"]}, headers=h).json()["items"]
    assert [d["name"] for d in signed_docs] == [f"Quote Q-{quote['number']} · Annual plan (signed).pdf"]
    assert fake.last("GET", "/combined")["params"] == {"certificate": "true"}
    assert client.get(f"{API}/quotes/{quote['id']}", headers=h).json()["status"] == "accepted"
    assert client.post(f"{API}/e-signatures/{done['id']}/void", json={"reason": "Too late"}, headers=h).status_code == 409


def test_docusign_document_void_decline_and_polling(client, monkeypatch, fake):
    ws = signup(client)
    h = ws["headers"]
    connect_oauth(client, ws, monkeypatch, "docusign", "docusign", DOCUSIGN_WHO)
    records = make_contact(client, ws, email="buyer@client.example")
    account_id = records["account"]["id"]
    upload = client.post(
        f"{API}/documents",
        files={"file": ("MSA.pdf", b"%PDF-1.4 master services agreement", "application/pdf")},
        data={"record_type": "account", "record_id": account_id},
        headers=h,
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]
    sources = client.get(f"{API}/e-signatures/sources", params={"record_type": "account", "record_id": account_id}, headers=h).json()
    assert sources["documents"][0]["signable"] is True and sources["recipients"][0]["email"] == "buyer@client.example"

    ids = iter(["env-2", "env-3"])
    fake.on("POST", f"{DS_BASE}/envelopes", lambda url, kw: {"envelopeId": next(ids), "status": "sent"}, exact=True)
    body = {"record_type": "account", "record_id": account_id, "document_id": document_id, "recipients": [{"name": "Bo Buyer", "email": "buyer@client.example"}]}
    first = client.post(f"{API}/e-signatures", json=body, headers=h).json()
    envelope = fake.last("POST", "/envelopes")["json"]
    assert envelope["documents"][0]["fileExtension"] == "pdf" and envelope["emailSubject"] == "Please sign: MSA.pdf"
    assert base64.b64decode(envelope["documents"][0]["documentBase64"]) == b"%PDF-1.4 master services agreement"

    fake.on("PUT", f"{DS_BASE}/envelopes/env-2", {"envelopeId": "env-2"}, exact=True)
    voided = client.post(f"{API}/e-signatures/{first['id']}/void", json={"reason": "Wrong version"}, headers=h)
    assert voided.status_code == 200 and voided.json()["status"] == "voided"
    assert fake.last("PUT", "env-2")["json"] == {"status": "voided", "voidedReason": "Wrong version"}

    second = client.post(f"{API}/e-signatures", json=body, headers=h).json()
    fake.on("PUT", f"{DS_BASE}/envelopes/env-3", {"envelopeId": "env-3"}, exact=True)
    assert client.post(f"{API}/e-signatures/{second['id']}/resend", headers=h).status_code == 200
    assert fake.last("PUT", "env-3")["params"] == {"resend_envelope": "true"}

    # The scheduler picks up the decline.
    fake.on("GET", f"{DS_BASE}/envelopes/env-3", {"status": "declined", "recipients": {"signers": [{"recipientId": "1", "status": "declined", "declinedReason": "Price too high"}]}}, exact=True)

    async def poll():
        from sqlalchemy import update

        from app.db.session import AsyncSessionLocal
        from app.integrations.providers import docusign
        from app.models.integration_hub import SignatureRequest

        async with AsyncSessionLocal() as db:
            await db.execute(update(SignatureRequest).where(SignatureRequest.id == uuid.UUID(second["id"])).values(last_checked_at=None))
            await db.commit()
            return await docusign.run_due(db)

    assert asyncio.run(poll()) >= 1
    declined = client.get(f"{API}/e-signatures/{second['id']}", headers=h).json()
    assert declined["status"] == "declined" and declined["recipients"][0]["decline_reason"] == "Price too high"
    notifications = client.get(f"{API}/notifications", headers=h)
    assert notifications.status_code == 200
    assert any((n.get("title") or "").startswith("Declined: MSA.pdf") for n in notifications.json()["items"])

    member = invite_member(client, ws)
    assert client.post(f"{API}/e-signatures/{second['id']}/void", json={"reason": "Not mine"}, headers=member).status_code == 403
    assert client.put(f"{API}/e-signatures/connect-key", json={"hmac_key": "x" * 32}, headers=member).status_code == 403

    # Validation.
    bad = client.post(f"{API}/e-signatures", json={**body, "recipients": [{"name": "X", "email": "not-an-email"}]}, headers=h)
    assert bad.status_code == 422
    only_cc = client.post(f"{API}/e-signatures", json={**body, "recipients": [{"name": "X", "email": "x@y.example", "role": "cc"}]}, headers=h)
    assert only_cc.status_code == 422 and "sign" in only_cc.json()["detail"]
    both = client.post(f"{API}/e-signatures", json={**body, "quote_id": str(uuid.uuid4())}, headers=h)
    assert both.status_code == 422


def test_docusign_refuses_unexpected_account_address(client, monkeypatch, fake):
    ws = signup(client)
    h = ws["headers"]
    who = {**DOCUSIGN_WHO, "config": {**DOCUSIGN_WHO["config"], "base_uri": "https://attacker.example"}}
    connect_oauth(client, ws, monkeypatch, "docusign", "docusign", who)
    detail = client.get(f"{API}/integration-hub/docusign", headers=h).json()
    assert "valid account address" in (detail["connection"]["last_error"] or "")
    records = make_contact(client, ws)
    upload = client.post(f"{API}/documents", files={"file": ("a.pdf", b"%PDF-1.4 a", "application/pdf")}, headers=h).json()
    response = client.post(
        f"{API}/e-signatures",
        json={"record_type": "contact", "record_id": records["contact"]["id"], "document_id": upload["id"], "recipients": [{"name": "A", "email": "a@b.example"}]},
        headers=h,
    )
    assert response.status_code == 409
    assert not any("attacker" in url for _, url, _ in fake.calls)


# ===========================================================================
# Calendly
# ===========================================================================


ME = {
    "uri": f"{CAL}/users/USER1",
    "email": "owner@calendly.example",
    "current_organization": f"{CAL}/organizations/ORG1",
    "scheduling_url": "https://calendly.com/owner",
    "name": "Owner",
}


def booking(event_uri: str, invitee_uri: str, email: str, host_email: str, name: str = "Ivy Invitee", **extra) -> dict:
    return {
        "event": extra.pop("event", "invitee.created"),
        "created_at": "2026-10-23T10:00:00Z",
        "payload": {
            "uri": invitee_uri,
            "email": email,
            "name": name,
            "status": "active",
            "timezone": "Europe/London",
            "cancel_url": "https://calendly.com/cancellations/abc",
            "reschedule_url": "https://calendly.com/reschedulings/abc",
            "questions_and_answers": [{"question": "Company name", "answer": "Ivy Labs"}, {"question": "What should we cover?", "answer": "Pricing"}],
            "scheduled_event": {
                "uri": event_uri,
                "name": "Intro call",
                "status": "active",
                "start_time": "2026-11-02T15:00:00.000000Z",
                "end_time": "2026-11-02T15:30:00.000000Z",
                "event_type": f"{CAL}/event_types/ET1",
                "location": {"type": "google_conference", "join_url": "https://meet.google.com/abc-defg-hij"},
                "event_memberships": [{"user": f"{CAL}/users/USER1", "user_email": host_email, "user_name": "Dana Owner"}],
                "event_guests": [],
            },
            **extra,
        },
    }


def signed(body: bytes, key: str, at: int | None = None) -> dict:
    stamp = str(at or int(time.time()))
    signature = hmac.new(key.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Calendly-Webhook-Signature": f"t={stamp},v1={signature}", "Content-Type": "application/json"}


def test_calendly_token_bookings_and_cancellations(client, monkeypatch, fake):
    from app.core.config import get_settings
    from app.integrations.providers import calendly

    monkeypatch.setattr(get_settings(), "app_base_url", "https://crm.example")
    ws = signup(client)
    h = ws["headers"]
    assert client.put(f"{API}/integration-hub/calendly/toggle", json={"enabled": True}, headers=h).status_code == 200

    async def fake_raw_get(url, token):
        assert token == "pat-secret-123" and url == f"{CAL}/users/me"
        return {"resource": ME}

    monkeypatch.setattr(calendly, "_raw_get", fake_raw_get)
    subscriptions = []
    fake.on("GET", f"{CAL}/webhook_subscriptions", {"collection": [], "pagination": {}})

    def create_subscription(url, kwargs):
        subscriptions.append(kwargs["json"])
        return {"resource": {"uri": f"{CAL}/webhook_subscriptions/W{len(subscriptions)}", "callback_url": kwargs["json"]["url"],
                             "scope": kwargs["json"]["scope"], "state": "active"}}

    fake.on("POST", f"{CAL}/webhook_subscriptions", create_subscription, exact=True)
    fake.on("GET", f"{CAL}/organization_memberships", {"collection": []})
    fake.on("GET", f"{CAL}/event_types", {"collection": [{"uri": f"{CAL}/event_types/ET1", "name": "Intro call", "scheduling_url": "https://calendly.com/owner/intro"}]})
    empty =client.put(f"{API}/integration-hub/calendly/credentials", json={"values": {}}, headers=h)
    assert empty.status_code == 422
    connected = client.put(f"{API}/integration-hub/calendly/credentials", json={"values": {"personal_access_token": "pat-secret-123"}}, headers=h)
    assert connected.status_code == 200, connected.text
    detail = connected.json()
    assert detail["status"] == "connected" and detail["connection"]["config"]["auth"] == "token"
    subscription = subscriptions[-1]
    assert subscription["events"] == ["invitee.created", "invitee.canceled"] and subscription["scope"] == "organization"
    assert subscription["organization"] == ME["current_organization"] and subscription["url"].startswith("https://crm.example/api/aiva/public/integrations/calendly/webhook/")
    signing_key = subscription["signing_key"]
    assert signing_key not in json.dumps(detail) and "pat-secret-123" not in json.dumps(detail)
    assert detail["extra"]["webhook"]["verified"] is True and detail["extra"]["webhook"]["state"] == "active"
    assert client.get(f"{API}/calendly/status", headers=h).json() == {"enabled": True, "connected": True}

    assert client.put(f"{API}/integration-hub/calendly/settings", json={"config": {"lead_owner": "someone"}}, headers=h).status_code == 422
    assert client.put(f"{API}/integration-hub/calendly/settings", json={"config": {"create_leads": "new", "lead_owner": "host"}}, headers=h).status_code == 200

    url = subscription["url"].replace("https://crm.example/api/aiva", API)
    event_uri = f"{CAL}/scheduled_events/EV1"
    body = json.dumps(booking(event_uri, f"{event_uri}/invitees/INV1", "ivy@new.example", ws["email"])).encode()
    assert client.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    assert client.post(url, content=body, headers=signed(body, "wrong-key")).status_code == 401
    assert client.post(url, content=body, headers=signed(body, signing_key, at=int(time.time()) - 3600)).status_code == 401
    created = client.post(url, content=body, headers=signed(body, signing_key))
    assert created.status_code == 200, created.text
    result = created.json()
    assert result["lead_created"] is True and result["meeting_id"]
    again = client.post(url, content=body, headers=signed(body, signing_key)).json()
    assert again["duplicate"] is True and again["meeting_id"] == result["meeting_id"]

    meeting = client.get(f"{API}/meetings/{result['meeting_id']}", headers=h).json()
    assert meeting["status"] == "scheduled" and meeting["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert meeting["title"] == "Intro call with Ivy Invitee" and meeting["organizer_user_id"] == ws["user_id"]
    assert {a["email"] for a in meeting["attendees"]} == {"ivy@new.example", ws["email"]}
    lead = client.get(f"{API}/leads/{result['lead_id']}", headers=h).json()
    assert lead["source"] == "Calendly" and lead["company_name"] == "Ivy Labs" and lead["owner_user_id"] == ws["user_id"]

    # A booking from an existing contact links the contact instead of creating a lead.
    contact = make_contact(client, ws, email="known@client.example")["contact"]
    second_uri = f"{CAL}/scheduled_events/EV2"
    body2 = json.dumps(booking(second_uri, f"{second_uri}/invitees/INV2", "Known@Client.example", ws["email"], name="Cara Client")).encode()
    linked = client.post(url, content=body2, headers=signed(body2, signing_key)).json()
    assert linked["lead_created"] is False and linked["contact_id"] == contact["id"]

    # Cancelling cancels the AIVA meeting.
    cancel = booking(event_uri, f"{event_uri}/invitees/INV1", "ivy@new.example", ws["email"], event="invitee.canceled",
                     cancellation={"canceled_by": "Ivy Invitee", "reason": "Conflict", "canceler_type": "invitee"})
    cancel["payload"]["status"] = "canceled"
    cancel["payload"]["scheduled_event"]["status"] = "canceled"
    body3 = json.dumps(cancel).encode()
    cancelled = client.post(url, content=body3, headers=signed(body3, signing_key))
    assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
    assert client.get(f"{API}/meetings/{result['meeting_id']}", headers=h).json()["status"] == "cancelled"

    activity = client.get(f"{API}/integration-hub/calendly/logs", headers=h).json()["items"]
    assert {"subscribed", "booking", "booking_cancelled", "webhook_rejected"} <= {a["action"] for a in activity}

    # Switched off: deliveries are acknowledged but ignored.
    client.put(f"{API}/integration-hub/calendly/toggle", json={"enabled": False}, headers=h)
    body4 = json.dumps(booking(f"{CAL}/scheduled_events/EV4", f"{CAL}/scheduled_events/EV4/invitees/INV4", "late@new.example", ws["email"])).encode()
    ignored = client.post(url, content=body4, headers=signed(body4, signing_key))
    assert ignored.status_code == 200 and ignored.json().get("ignored")

    # Disconnecting removes the subscription at Calendly.
    client.put(f"{API}/integration-hub/calendly/toggle", json={"enabled": True}, headers=h)
    fake.on("DELETE", f"{CAL}/webhook_subscriptions/", {})
    assert client.delete(f"{API}/integration-hub/calendly/connection", headers=h).status_code == 200
    assert fake.calls[-1][0] == "DELETE" and fake.calls[-1][1] == f"{CAL}/webhook_subscriptions/W1"


def test_calendly_oauth_without_signing_key_reads_booking_from_api(client, monkeypatch, fake):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "app_base_url", "https://crm.example")
    monkeypatch.setattr(get_settings(), "calendly_webhook_signing_key", "")
    ws = signup(client)
    h = ws["headers"]
    fake.on("GET", f"{CAL}/webhook_subscriptions", {"collection": []})

    def only_user_scope(url, kwargs):
        if kwargs["json"]["scope"] == "organization":
            from app.integrations.core import IntegrationError

            return IntegrationError("Calendly said: Permission Denied", 400)
        return {"resource": {"uri": f"{CAL}/webhook_subscriptions/WU", "scope": "user", "state": "active"}}

    fake.on("POST", f"{CAL}/webhook_subscriptions", only_user_scope, exact=True)
    fake.on("GET", f"{CAL}/organization_memberships", {"collection": []})
    fake.on("GET", f"{CAL}/event_types", {"collection": []})
    connect_oauth(
        client, ws, monkeypatch, "calendly", "calendly",
        {"label": ME["email"], "external_id": ME["uri"], "config": {"user_uri": ME["uri"], "organization_uri": ME["current_organization"], "scheduling_url": ME["scheduling_url"]}},
    )
    detail = client.get(f"{API}/integration-hub/calendly", headers=h)
    assert detail.status_code == 200
    data = detail.json()
    assert data["connection"]["config"]["auth"] == "oauth" and data["connection"]["config"]["webhook_scope"] == "user"
    assert "signing_key" not in fake.last("POST", "webhook_subscriptions")["json"]
    assert data["extra"]["webhook"]["verified"] is False

    url = f"{API}/public/integrations/calendly/webhook/{data['connection']['webhook_url'].rsplit('/', 1)[1]}"
    event_uri = f"{CAL}/scheduled_events/EVX"
    invitee_uri = f"{event_uri}/invitees/INVX"
    # The message itself says something else; AIVA uses what the API returns.
    forged = booking(event_uri, invitee_uri, "forged@evil.example", ws["email"])
    forged["payload"]["scheduled_event"]["start_time"] = "2030-01-01T00:00:00Z"
    fake.on("GET", invitee_uri, {"resource": {"uri": invitee_uri, "email": "real@client.example", "name": "Real Person", "status": "active", "event": event_uri}}, exact=True)
    fake.on("GET", event_uri, {"resource": {**booking(event_uri, invitee_uri, "x", ws["email"])["payload"]["scheduled_event"]}}, exact=True)
    response = client.post(url, json=forged)
    assert response.status_code == 200, response.text
    meeting = client.get(f"{API}/meetings/{response.json()['meeting_id']}", headers=h).json()
    assert meeting["start_at"].startswith("2026-11-02") and {a["email"] for a in meeting["attendees"]} >= {"real@client.example"}
    assert "forged@evil.example" not in json.dumps(meeting)
    not_calendly = booking(event_uri, "https://evil.example/invitees/1", "x@y.example", ws["email"])
    assert client.post(url, json=not_calendly).status_code == 400

    # Saving the app's signing key makes signatures mandatory.
    saved = client.put(f"{API}/calendly/signing-key", json={"signing_key": "app-signing-key-0123456789"}, headers=h)
    assert saved.status_code == 200 and saved.json()["extra"]["webhook"]["verified"] is True
    assert client.post(url, json=forged).status_code == 401

    # Scheduling links, prefilled for a lead.
    lead = client.post(f"{API}/leads", json={"first_name": "Lia", "last_name": "Lead", "email": "lia@lead.example"}, headers=h).json()
    fake.on("GET", f"{CAL}/organization_memberships", {"collection": [{"user": {"uri": f"{CAL}/users/USER2", "scheduling_url": "https://calendly.com/dana"}}]})
    fake.on("GET", f"{CAL}/event_types", {"collection": [
        {"uri": f"{CAL}/event_types/ET1", "name": "Intro call", "scheduling_url": "https://calendly.com/dana/intro", "duration": 30, "kind": "solo", "secret": False},
        {"uri": f"{CAL}/event_types/ET2", "name": "Hidden", "scheduling_url": "https://calendly.com/dana/hidden", "duration": 15, "secret": True},
    ]})
    links = client.get(f"{API}/calendly/links", params={"record_type": "lead", "record_id": lead["id"]}, headers=h)
    assert links.status_code == 200, links.text
    data = links.json()
    assert data["scope"] == "mine" and [i["name"] for i in data["items"]] == ["Intro call"]
    assert data["items"][0]["share_url"] == "https://calendly.com/dana/intro?name=Lia%20Lead&email=lia%40lead.example"
    assert fake.last("GET", "organization_memberships")["params"]["email"] == ws["email"].lower()

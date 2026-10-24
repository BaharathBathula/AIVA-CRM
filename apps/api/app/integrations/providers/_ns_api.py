"""Oracle NetSuite: token-based authentication, SuiteQL and the REST record service.

Authentication
==============

NetSuite integrations use **token-based authentication (TBA)**: an integration
record gives a consumer key and secret, and an access token for a user and role
gives a token ID and secret. Every request is signed with OAuth 1.0a
(HMAC-SHA256)::

    Authorization: OAuth realm="1234567_SB1", oauth_consumer_key="…", oauth_token="…",
        oauth_signature_method="HMAC-SHA256", oauth_timestamp="…", oauth_nonce="…",
        oauth_version="1.0", oauth_signature="…"

* The **realm** is the account ID in upper case with an underscore
  (``1234567_SB1`` for sandbox 1); the **host** uses it in lower case with a
  hyphen: ``https://1234567-sb1.suitetalk.api.netsuite.com``.
* The signature base string is ``METHOD&enc(url without query)&enc(params)``,
  where params are the ``oauth_*`` values (not the realm or signature) and the
  query parameters, percent-encoded (RFC 3986) and sorted. The key is
  ``enc(consumer secret)&enc(token secret)``. JSON bodies are not signed.
* Hosts are built only from a validated account ID, so a pasted value can't
  send signed requests (or the secrets they prove) anywhere else.

The consumer key and token ID are kept encrypted with the secrets (the hub
stores them in the connection's config at first; ``take_identifiers`` moves
them). ``Credentials`` never prints its secrets.

APIs used
=========

* ``POST /services/rest/query/v1/suiteql`` (``Prefer: transient``, body
  ``{"q": …}``, ``limit``/``offset`` query parameters, at most 1,000 rows a
  page) for every read. SuiteQL has no bind parameters, so values are only put
  into statements through ``ident`` (digits) and ``text`` (quoted, quotes
  doubled, control characters removed); dates use ``TO_CHAR``/``TO_DATE`` with
  explicit formats because SuiteQL otherwise formats dates per user preference.
* ``/services/rest/record/v1/{type}`` to create (``POST`` → 204 with the new
  record's URL in ``Location``), update (``PATCH``; ``?replace=item`` replaces
  the item lines) and read by external ID (``…/{type}/eid:{externalId}``).
* Busy answers (429, ``CONCURRENCY_LIMIT_EXCEEDED``) become ``RateLimited`` in
  ``_acct_http`` so the scheduler pauses the account instead of retrying hard.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import _acct_http as http
from app.integrations.providers._acct_types import Ctx
from app.models.integration_hub import IntegrationConnection

KEY = "netsuite"
NAME = "Oracle NetSuite"
PAGE = 1000  # SuiteQL's largest page
IDENTIFIERS = ("consumer_key", "token_id")  # moved from config into encrypted secrets
SECRETS = ("consumer_secret", "token_secret")

# Account IDs: production "1234567", sandboxes "1234567_SB1", release previews
# "1234567_RP", test drives "TSTDRV1234567".
_ACCOUNT = re.compile(r"^[A-Z0-9]{1,30}(?:_(?:SB|RP)[0-9]{0,3})?$")
_HOST_ACCOUNT = re.compile(r"^([a-z0-9]{1,30}(?:-(?:sb|rp)[0-9]{0,3})?)\.(?:suitetalk\.api|app|restlets\.api|extforms)\.netsuite\.com$")
_KEYISH = re.compile(r"^[A-Za-z0-9]{16,128}$")
_ID = re.compile(r"^[0-9]{1,18}$")
_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_RECORD_TYPE = re.compile(r"^[a-zA-Z]{2,60}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


# ---------------------------------------------------------------------------
# Account IDs and hosts
# ---------------------------------------------------------------------------


def normalize_account(value: Any) -> str:
    """``1234567_SB1`` from ``1234567_sb1``, ``1234567-sb1`` or a pasted NetSuite URL. Raises if it isn't one."""

    text = str(value or "").strip()
    if "://" in text or ".netsuite.com" in text.lower():
        host = (urlsplit(text if "://" in text else f"https://{text}").hostname or "").lower()
        found = _HOST_ACCOUNT.match(host)
        text = found.group(1) if found else ""
    text = text.upper().replace("-", "_")
    if not _ACCOUNT.match(text):
        raise IntegrationError(
            "That isn't a NetSuite account ID. Find it in Setup → Company → Company Information (for example 1234567, or 1234567_SB1 for a sandbox).", 422
        )
    return text


def host_label(account: str) -> str:
    return normalize_account(account).lower().replace("_", "-")


def rest_base(account: str) -> str:
    return f"https://{host_label(account)}.suitetalk.api.netsuite.com/services/rest"


def app_base(account: str) -> str:
    return f"https://{host_label(account)}.app.netsuite.com"


def is_sandbox(account: str) -> bool:
    return "_SB" in normalize_account(account)


_UI = {
    "customer": "/app/common/entity/custjob.nl?id=",
    "contact": "/app/common/entity/contact.nl?id=",
    "invoice": "/app/accounting/transactions/custinvc.nl?id=",
    "salesOrder": "/app/accounting/transactions/salesord.nl?id=",
    "item": "/app/common/item/item.nl?id=",
}


def ui_url(account: str | None, kind: str, record_id: str | None) -> str | None:
    if not account or not record_id or not _ID.match(str(record_id)) or kind not in _UI:
        return None
    try:
        return f"{app_base(account)}{_UI[kind]}{record_id}"
    except IntegrationError:
        return None


# ---------------------------------------------------------------------------
# Token-based authentication (OAuth 1.0a, HMAC-SHA256)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Credentials:
    account: str
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str

    def __repr__(self) -> str:  # never print secrets
        return f"Credentials(account={self.account!r}, consumer_key='…{self.consumer_key[-4:]}', token_id='…{self.token_id[-4:]}')"

    __str__ = __repr__


def _enc(value: Any) -> str:
    """RFC 3986 percent-encoding, as OAuth 1.0a requires (only A-Z a-z 0-9 - . _ ~ stay)."""

    return quote(str(value), safe="-._~")


def base_string(method: str, url: str, oauth_params: dict[str, str]) -> str:
    """The OAuth 1.0a signature base string for ``method`` on ``url`` (its query included in the params)."""

    parsed = httpx.URL(url)
    scheme = parsed.scheme.lower()
    host = (parsed.host or "").lower()
    port = parsed.port
    default = (scheme == "https" and port in (None, 443)) or (scheme == "http" and port in (None, 80))
    path = parsed.raw_path.decode("ascii").split("?", 1)[0] or "/"
    normalized_url = f"{scheme}://{host}{'' if default else f':{port}'}{path}"
    pairs = [(_enc(k), _enc(v)) for k, v in parsed.params.multi_items()]
    pairs += [(_enc(k), _enc(v)) for k, v in oauth_params.items()]
    param_string = "&".join(f"{k}={v}" for k, v in sorted(pairs))
    return "&".join((method.upper(), _enc(normalized_url), _enc(param_string)))


def authorization(creds: Credentials, method: str, url: str, *, timestamp: int | None = None, nonce: str | None = None) -> str:
    """The ``Authorization`` header for one request."""

    oauth = {
        "oauth_consumer_key": creds.consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(int(timestamp if timestamp is not None else time.time())),
        "oauth_token": creds.token_id,
        "oauth_version": "1.0",
    }
    key = f"{_enc(creds.consumer_secret)}&{_enc(creds.token_secret)}".encode()
    digest = hmac.new(key, base_string(method, url, oauth).encode(), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    parts = [f'realm="{normalize_account(creds.account)}"'] + [f'{k}="{_enc(v)}"' for k, v in oauth.items()] + [f'oauth_signature="{_enc(signature)}"']
    return "OAuth " + ", ".join(parts)


def credentials_from(values: dict[str, Any]) -> Credentials:
    """Credentials from the connect form, checked for shape (not yet with NetSuite)."""

    account = normalize_account(values.get("account_id"))
    found = {name: str(values.get(name) or "").strip() for name in (*IDENTIFIERS, *SECRETS)}
    labels = {"consumer_key": "consumer key", "consumer_secret": "consumer secret", "token_id": "token ID", "token_secret": "token secret"}
    for name, value in found.items():
        if not _KEYISH.match(value):
            raise IntegrationError(f"That {labels[name]} doesn't look right. Copy it again from NetSuite (it's a long string of letters and numbers).", 422)
    if found["consumer_key"] == found["token_id"]:
        raise IntegrationError("The consumer key and token ID are the same. The token ID comes from Setup → Users/Roles → Access Tokens.", 422)
    return Credentials(account=account, **found)


def credentials_of(connection: IntegrationConnection) -> Credentials:
    config = connection.config or {}
    values = {"account_id": config.get("account_id")}
    for name in IDENTIFIERS:
        values[name] = core.secret(connection, name) or config.get(name)
    for name in SECRETS:
        values[name] = core.secret(connection, name)
    if not all(values.get(name) for name in (*IDENTIFIERS, *SECRETS)) or not values["account_id"]:
        raise IntegrationError(f"{NAME} isn't fully connected. An admin can paste the account ID, consumer and token credentials again.", 409)
    try:
        return credentials_from(values)
    except IntegrationError as exc:
        raise IntegrationError(f"{NAME}'s saved credentials aren't valid any more: {exc}", 409) from exc


def take_identifiers(connection: IntegrationConnection) -> None:
    """Move the consumer key and token ID into encrypted secrets; keep a short hint for the page."""

    config = dict(connection.config or {})
    changed = False
    for name in IDENTIFIERS:
        value = config.get(name)
        if isinstance(value, str) and value and not value.startswith("…"):
            core.set_secret(connection, name, value.strip())
            config[name] = None
            changed = True
        stored = core.secret(connection, name)  # the connect form may already have stored it encrypted
        if stored and config.get(f"{name}_hint") != f"…{stored.strip()[-4:]}":
            config[f"{name}_hint"] = f"…{stored.strip()[-4:]}"
            changed = True
    if config.get("account_id"):
        try:
            config["account_id"] = normalize_account(config["account_id"])
        except IntegrationError:
            pass
    if changed or config != (connection.config or {}):
        connection.config = config


def _sign(connection: IntegrationConnection, method: str, url: str) -> str:
    creds = credentials_of(connection)
    allowed = f"https://{host_label(creds.account)}.suitetalk.api.netsuite.com/"
    if not url.startswith(allowed):
        raise IntegrationError(f"AIVA only sends signed requests to your {NAME} account's own address.", 500)
    return authorization(creds, method, url)


http.SIGNERS[KEY] = _sign


async def probe(creds: Credentials) -> dict[str, Any]:
    """Check pasted credentials with one SuiteQL call before saving them (no connection yet)."""

    url = str(httpx.URL(f"{rest_base(creds.account)}/query/v1/suiteql").copy_merge_params({"limit": "5", "offset": "0"}))
    statement = "SELECT s.id, s.name FROM subsidiary s WHERE s.isinactive = 'F' ORDER BY s.id"
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Prefer": "transient",
               "Authorization": authorization(creds, "POST", url)}
    try:
        async with http.client(20) as client:
            response = await client.post(url, headers=headers, json={"q": statement})
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Couldn't reach {NAME} for account {creds.account}. Check the account ID.", 502) from exc
    if response.status_code == 401:
        raise IntegrationError(
            f"{NAME} refused these credentials. Check the account ID, that the integration has token-based authentication on, "
            "and that the token belongs to that integration and an active role.", 422,
        )
    if response.status_code == 403:
        raise IntegrationError(f"{NAME} accepted the token but its role can't use REST web services or SuiteQL: {http.provider_message(response)}", 422)
    if response.status_code == 429 or http.error_codes(response) & http.BUSY_CODES:
        raise IntegrationError(f"{NAME} is busy right now. Try again in a minute.", 429)
    if response.status_code >= 400:
        raise IntegrationError(f"{NAME} said: {http.provider_message(response)}", 422)
    data = response.json() if response.content else {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# SuiteQL
# ---------------------------------------------------------------------------


def ident(value: Any) -> str:
    """A NetSuite internal ID, safe to put into SuiteQL. Raises for anything else."""

    text = str(value).strip() if value is not None else ""
    if not _ID.match(text):
        raise IntegrationError(f"“{text[:40]}” isn't a {NAME} internal ID.", 422)
    return text


def ids(values: Any) -> str:
    """``1, 2, 3`` for an ``IN (…)`` list (only internal IDs)."""

    found = sorted({ident(v) for v in values}, key=int)
    if not found:
        raise IntegrationError("Nothing to look up.", 422)
    return ", ".join(found)


def text(value: Any, limit: int = 300) -> str:
    """A quoted SuiteQL string literal: quotes doubled, control characters dropped."""

    cleaned = "".join(ch for ch in str(value or "") if ch >= " " and ch != "\x7f")[:limit]
    return "'" + cleaned.replace("'", "''") + "'"


def like(value: Any, limit: int = 100) -> str:
    """``'%term%'`` for a contains search (wildcards in the term itself are dropped)."""

    term = str(value or "").replace("%", "").replace("_", " ").strip()[:limit]
    return text(f"%{term}%", limit + 2)


def stamp(value: datetime) -> str:
    """``TO_DATE('2026-10-24 13:05:00', 'YYYY-MM-DD HH24:MI:SS')`` for NetSuite-local time ``value``."""

    return f"TO_DATE('{value.strftime('%Y-%m-%d %H:%M:%S')}', 'YYYY-MM-DD HH24:MI:SS')"


def stamp_text(value: str) -> str:
    """The same for a timestamp NetSuite returned (``YYYY-MM-DD HH24:MI:SS``), validated."""

    try:
        parsed = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 - NetSuite-local wall time
    except ValueError as exc:
        raise IntegrationError("NetSuite sent a timestamp AIVA doesn't understand.", 502) from exc
    return stamp(parsed)


def day(value: date | None) -> str | None:
    return value.isoformat() if value else None


async def suiteql(ctx: Ctx, statement: str, *, limit: int = PAGE, offset: int = 0) -> list[dict[str, Any]]:
    """One page of SuiteQL rows (``links`` removed; column names in lower case)."""

    account = credentials_of(ctx.connection).account
    response = await ctx.call(
        "POST", f"{rest_base(account)}/query/v1/suiteql", params={"limit": str(max(1, min(limit, PAGE))), "offset": str(max(0, offset))},
        json={"q": statement}, headers={"Content-Type": "application/json", "Prefer": "transient"},
    )
    data = response.json() if response.content else {}
    rows = data.get("items") if isinstance(data, dict) else None
    out = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append({str(k).lower(): v for k, v in row.items() if k != "links"})
    return out


async def suiteql_all(ctx: Ctx, statement: str, *, cap: int = 5000) -> list[dict[str, Any]]:
    """Every row (up to ``cap``), a page at a time."""

    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < cap:
        page = await suiteql(ctx, statement, limit=min(PAGE, cap - len(rows)), offset=offset)
        rows.extend(page)
        if len(page) < min(PAGE, cap - len(rows) + len(page)):
            break
        offset += len(page)
    return rows[:cap]


async def optional(ctx: Ctx, statement: str, *, limit: int = PAGE) -> list[dict[str, Any]]:
    """Rows from a query that fails harmlessly when a feature is off in the account (departments, classes...)."""

    try:
        return await suiteql(ctx, statement, limit=limit)
    except http.RateLimited:
        raise
    except IntegrationError:
        return []


def flag(value: Any) -> bool:
    return str(value or "").upper() in {"T", "TRUE", "Y", "YES", "1"}


# ---------------------------------------------------------------------------
# REST records
# ---------------------------------------------------------------------------


def _record_ref(record_type: str, record_id: str) -> str:
    if not _RECORD_TYPE.match(record_type):
        raise IntegrationError("Unknown NetSuite record type.", 500)
    value = str(record_id)
    if _ID.match(value):
        return f"{record_type}/{value}"
    if value.startswith("eid:") and _EXTERNAL_ID.match(value[4:]):
        return f"{record_type}/{value}"
    raise IntegrationError(f"“{value[:40]}” isn't a {NAME} record ID.", 422)


def record_url(ctx: Ctx, record_type: str, record_id: str | None = None) -> str:
    account = credentials_of(ctx.connection).account
    path = _record_ref(record_type, record_id) if record_id is not None else record_type
    if record_id is None and not _RECORD_TYPE.match(record_type):
        raise IntegrationError("Unknown NetSuite record type.", 500)
    return f"{rest_base(account)}/record/v1/{path}"


def external_id(prefix: str, value: Any) -> str:
    found = f"{prefix}-{value}"
    if not _EXTERNAL_ID.match(found):
        raise IntegrationError("That can't be used as a NetSuite external ID.", 500)
    return found


async def get_record(ctx: Ctx, record_type: str, record_id: str, *, fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    params = {"fields": ",".join(f for f in fields if _RECORD_TYPE.match(f))} if fields else None
    response = await ctx.call("GET", record_url(ctx, record_type, record_id), ok_statuses=(404,), **({"params": params} if params else {}))
    if response.status_code == 404:
        return None
    data = response.json() if response.content else {}
    return data if isinstance(data, dict) else None


def _created_id(response: httpx.Response, record_type: str) -> str:
    location = response.headers.get("location") or ""
    found = re.search(rf"/{re.escape(record_type)}/([0-9]+)$", location.split("?")[0])
    if not found:
        raise IntegrationError(f"{NAME} didn't say which {record_type} it created.", 502)
    return found.group(1)


async def create_record(ctx: Ctx, record_type: str, body: dict[str, Any]) -> str:
    """POST a new record; returns its internal ID (from ``Location``)."""

    response = await ctx.call("POST", record_url(ctx, record_type), json=body, headers={"Content-Type": "application/json"})
    return _created_id(response, record_type)


async def update_record(ctx: Ctx, record_type: str, record_id: str, body: dict[str, Any], *, replace: tuple[str, ...] = (), missing_ok: bool = False) -> bool:
    """PATCH a record (``replace`` names sublists to replace rather than merge). False when it no longer exists and ``missing_ok``."""

    params = {"replace": ",".join(replace)} if replace else None
    response = await ctx.call("PATCH", record_url(ctx, record_type, record_id), json=body, headers={"Content-Type": "application/json"},
                              ok_statuses=(404,) if missing_ok else (), **({"params": params} if params else {}))
    return response.status_code != 404


async def transform(ctx: Ctx, record_type: str, record_id: str, to_type: str, body: dict[str, Any]) -> str | None:
    """``POST /{type}/{id}/!transform/{toType}`` (an invoice into a credit memo...): 204 when done.
    Returns the new record's ID when NetSuite gives its URL in ``Location``."""

    if not _RECORD_TYPE.match(to_type):
        raise IntegrationError("Unknown NetSuite record type.", 500)
    response = await ctx.call("POST", f"{record_url(ctx, record_type, record_id)}/!transform/{to_type}", json=body,
                              headers={"Content-Type": "application/json"})
    try:
        return _created_id(response, to_type)
    except IntegrationError:
        return None


async def find_by_external_id(ctx: Ctx, record_type: str, value: str) -> str | None:
    found = await get_record(ctx, record_type, f"eid:{value}", fields=("id",)) if _EXTERNAL_ID.match(value) else None
    return str(found["id"]) if found and found.get("id") and _ID.match(str(found["id"])) else None


def duplicate_external_id(exc: IntegrationError) -> bool:
    message = str(exc).lower()
    return "external id" in message or "externalid" in message or "already exists" in message or "dup_" in message


def needs_entity_id(exc: IntegrationError) -> bool:
    message = str(exc).lower()
    return "entityid" in message or "customer id" in message or "contact id" in message or ("please enter value" in message and "name" in message)


def ref(value: Any) -> dict[str, str] | None:
    """``{"id": "12"}`` for a reference field, or None when unset."""

    if value in (None, ""):
        return None
    return {"id": ident(value)}


def clock(info: dict[str, Any]) -> timedelta | None:
    """NetSuite-local time minus UTC, measured when the account was last checked."""

    minutes = info.get("clock_offset")
    return timedelta(minutes=int(minutes)) if isinstance(minutes, int) else None


def local_time(info: dict[str, Any], value: datetime) -> datetime:
    """``value`` (aware) as NetSuite-local wall time, erring early when the offset isn't known."""

    offset = clock(info)
    base = value.astimezone(timezone.utc).replace(tzinfo=None)
    return base + offset - timedelta(minutes=10) if offset is not None else base - timedelta(hours=14)

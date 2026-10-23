"""Sign in with Google, Microsoft, Apple and workspace single sign-on.

All four use the OpenID Connect authorization-code flow:

1. ``start`` builds the provider's sign-in URL and a signed, short-lived
   state token. The web app keeps the token in an httpOnly cookie, which ties
   the provider's redirect back to the browser that started it.
2. ``finish`` checks the state, exchanges the code for tokens at the
   provider and verifies the ID token's signature (provider JWKS), issuer,
   audience, expiry and nonce before reading who the person is.

Google, Microsoft and workspace connections also use PKCE.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from app.core.config import get_settings
from app.core.crypto import decrypt

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
APPLE_ISSUER = "https://appleid.apple.com"
# Microsoft personal (consumer) accounts all sign in through this tenant.
MICROSOFT_PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"

STATE_MINUTES = 10
CACHE_SECONDS = 3600

# Free email providers: never a company's single sign-on domain.
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
        "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
        "gmx.com", "gmx.net", "zoho.com", "yandex.com", "mail.com", "fastmail.com", "hey.com",
        "privaterelay.appleid.com",
    }
)

LABELS = {"google": "Google", "microsoft": "Microsoft", "apple": "Apple", "sso": "your organization"}


class OAuthError(Exception):
    """Something the person should see, phrased for them."""


@dataclass
class Provider:
    key: str  # google | microsoft | apple | sso:<connection id>
    label: str
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    issuer: str  # exact issuer, or a template for Microsoft
    scopes: str = "openid email profile"
    pkce: bool = True
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def path(self) -> str:
        """The provider segment in the web app's callback URL."""
        return "sso" if self.key.startswith("sso:") else self.key


@dataclass
class Profile:
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def configured() -> dict[str, bool]:
    settings = get_settings()
    return {
        "google": bool(settings.google_client_id and settings.google_client_secret),
        "microsoft": bool(settings.microsoft_client_id and settings.microsoft_client_secret),
        "apple": bool(settings.apple_client_id and settings.apple_team_id and settings.apple_key_id and settings.apple_private_key),
    }


def redirect_uri(path: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/api/session/oauth/{path}/callback"


_cache: dict[str, tuple[float, Any]] = {}


async def fetch_json(url: str) -> Any:
    """GET a JSON document (discovery or keys), cached for an hour."""
    hit = _cache.get(url)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthError(f"Couldn't reach {urlparse(url).netloc}. Try again in a moment.") from exc
    if response.status_code != 200:
        raise OAuthError(f"{urlparse(url).netloc} answered {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise OAuthError(f"{urlparse(url).netloc} didn't return valid JSON.") from exc
    _cache[url] = (time.monotonic() + CACHE_SECONDS, data)
    return data


def check_issuer_url(issuer: str) -> str:
    """Workspace connections must use https (plain http only for local development)."""
    issuer = issuer.strip().rstrip("/")
    parsed = urlparse(issuer)
    local = parsed.hostname in {"localhost", "127.0.0.1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local and get_settings().environment in {"development", "test"}):
        raise OAuthError("The issuer URL must start with https://.")
    if not parsed.netloc:
        raise OAuthError("Enter the issuer URL from your identity provider, such as https://acme.okta.com.")
    return issuer


async def discover(issuer: str) -> dict[str, Any]:
    doc = await fetch_json(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if not doc.get(key):
            raise OAuthError(f"The identity provider's configuration is missing {key}.")
    return doc


async def provider(key: str, connection: Any = None) -> Provider:
    settings = get_settings()
    if key == "google":
        if not configured()["google"]:
            raise OAuthError("Google sign-in isn't set up on this server yet.")
        doc = await discover("https://accounts.google.com")
        return Provider(
            "google", "Google", settings.google_client_id, settings.google_client_secret,
            doc["authorization_endpoint"], doc["token_endpoint"], doc["jwks_uri"], doc["issuer"],
            extra={"prompt": "select_account"},
        )
    if key == "microsoft":
        if not configured()["microsoft"]:
            raise OAuthError("Microsoft sign-in isn't set up on this server yet.")
        tenant = settings.microsoft_tenant_id or "common"
        doc = await discover(f"https://login.microsoftonline.com/{tenant}/v2.0")
        return Provider(
            "microsoft", "Microsoft", settings.microsoft_client_id, settings.microsoft_client_secret,
            doc["authorization_endpoint"], doc["token_endpoint"], doc["jwks_uri"], "https://login.microsoftonline.com/{tenantid}/v2.0",
            extra={"prompt": "select_account"},
        )
    if key == "apple":
        if not configured()["apple"]:
            raise OAuthError("Sign in with Apple isn't set up on this server yet.")
        return Provider(
            "apple", "Apple", settings.apple_client_id, "",
            f"{APPLE_ISSUER}/auth/authorize", f"{APPLE_ISSUER}/auth/token", f"{APPLE_ISSUER}/auth/keys", APPLE_ISSUER,
            scopes="name email", pkce=False, extra={"response_mode": "form_post"},
        )
    if key.startswith("sso:") and connection is not None:
        doc = await discover(connection.issuer)
        return Provider(
            key, connection.name, connection.client_id, decrypt(connection.client_secret) or "",
            doc["authorization_endpoint"], doc["token_endpoint"], doc["jwks_uri"], doc["issuer"],
        )
    raise OAuthError("Unknown sign-in method.")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_state(
    provider_key: str,
    intent: str,
    next_path: str,
    *,
    link_user_id: uuid.UUID | None = None,
) -> tuple[dict[str, str], str]:
    """Random values for one sign-in attempt, and the signed token that carries them."""
    values = {
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
        "verifier": secrets.token_urlsafe(48),
    }
    now = datetime.now(timezone.utc)
    claims = {
        "typ": "oauth_state",
        "provider": provider_key,
        "intent": intent,
        "next": next_path,
        "link": str(link_user_id) if link_user_id else None,
        "iat": now,
        "exp": now + timedelta(minutes=STATE_MINUTES),
        **values,
    }
    return values, jwt.encode(claims, get_settings().jwt_secret_key, algorithm="HS256")


def read_state(token: str | None, state: str | None, provider_path: str) -> dict[str, Any]:
    if not token or not state:
        raise OAuthError("Your sign-in took too long or was started in another browser. Try again.")
    try:
        claims = jwt.decode(token, get_settings().jwt_secret_key, algorithms=["HS256"], options={"require": ["exp", "typ"]})
    except jwt.InvalidTokenError as exc:
        raise OAuthError("Your sign-in took too long. Try again.") from exc
    if claims.get("typ") != "oauth_state" or not secrets.compare_digest(str(claims.get("state")), state):
        raise OAuthError("That sign-in response doesn't match this browser. Try again.")
    provider_key = str(claims.get("provider"))
    if (provider_key if not provider_key.startswith("sso:") else "sso") != provider_path:
        raise OAuthError("That sign-in response came from a different provider. Try again.")
    return claims


def authorization_url(p: Provider, values: dict[str, str], login_hint: str | None = None) -> str:
    params = {
        "client_id": p.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri(p.path),
        "scope": p.scopes,
        "state": values["state"],
        "nonce": values["nonce"],
        **p.extra,
    }
    if p.pkce:
        params["code_challenge"] = _b64(hashlib.sha256(values["verifier"].encode()).digest())
        params["code_challenge_method"] = "S256"
    if login_hint and p.key != "apple":
        params["login_hint"] = login_hint
    separator = "&" if "?" in p.authorization_endpoint else "?"
    return f"{p.authorization_endpoint}{separator}{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token exchange and ID token checks
# ---------------------------------------------------------------------------


def apple_client_secret() -> str:
    """Apple wants a short-lived ES256 JWT signed with the team's private key."""
    settings = get_settings()
    key = settings.apple_private_key.replace("\\n", "\n").strip()
    now = int(time.time())
    return jwt.encode(
        {"iss": settings.apple_team_id, "iat": now, "exp": now + 300, "aud": APPLE_ISSUER, "sub": settings.apple_client_id},
        key,
        algorithm="ES256",
        headers={"kid": settings.apple_key_id},
    )


async def exchange_code(p: Provider, code: str, verifier: str) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(p.path),
        "client_id": p.client_id,
        "client_secret": apple_client_secret() if p.key == "apple" else p.client_secret,
    }
    if p.pkce:
        form["code_verifier"] = verifier
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(p.token_endpoint, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthError(f"Couldn't reach {p.label}. Try again in a moment.") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200 or not data.get("id_token"):
        reason = data.get("error_description") or data.get("error") or f"status {response.status_code}"
        raise OAuthError(f"{p.label} didn't confirm the sign-in ({reason}). Try again.")
    return data


async def verify_id_token(p: Provider, id_token: str, nonce: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.InvalidTokenError as exc:
        raise OAuthError(f"{p.label} returned an unreadable sign-in token.") from exc
    keys = (await fetch_json(p.jwks_uri)).get("keys") or []
    match = next((k for k in keys if k.get("kid") == header.get("kid")), None)
    if match is None:
        # Keys rotate; fetch once more without the cache.
        _cache.pop(p.jwks_uri, None)
        keys = (await fetch_json(p.jwks_uri)).get("keys") or []
        match = next((k for k in keys if k.get("kid") == header.get("kid")), None)
    if match is None:
        raise OAuthError(f"Couldn't verify the sign-in token from {p.label}.")
    algorithm = header.get("alg") or match.get("alg") or "RS256"
    if algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "PS256"}:
        raise OAuthError(f"{p.label} signed the sign-in token with an unsupported method.")
    try:
        claims = jwt.decode(
            id_token,
            jwt.PyJWK(match, algorithm=algorithm).key,
            algorithms=[algorithm],
            audience=p.client_id,
            leeway=60,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.InvalidTokenError as exc:
        raise OAuthError(f"The sign-in token from {p.label} isn't valid ({exc}).") from exc

    issuer = str(claims.get("iss"))
    if p.key == "microsoft":
        expected = p.issuer.replace("{tenantid}", str(claims.get("tid", "")))
        tenant = get_settings().microsoft_tenant_id or "common"
        if tenant not in {"common", "organizations", "consumers"} and claims.get("tid") != tenant:
            raise OAuthError("That Microsoft account belongs to a different organization.")
    elif p.key == "google":
        expected = issuer if issuer in {"https://accounts.google.com", "accounts.google.com"} else p.issuer
    else:
        expected = p.issuer
    if issuer.rstrip("/") != expected.rstrip("/"):
        raise OAuthError(f"The sign-in token didn't come from {p.label}.")
    if claims.get("nonce") != nonce:
        raise OAuthError("That sign-in response was already used or doesn't belong to this attempt. Try again.")
    return claims


def _truthy(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def profile(p: Provider, claims: dict[str, Any], apple_user: str | None = None) -> Profile:
    """Who signed in, with an email only when the provider vouches for it."""
    subject = str(claims["sub"])
    name = claims.get("name") or " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x) or None
    if p.key == "microsoft":
        # Personal accounts: the email is verified. Work accounts: the sign-in name
        # (UPN) uses a domain the organization verified; the "email" claim is
        # editable by tenant admins, so it isn't trusted.
        if claims.get("tid") == MICROSOFT_PERSONAL_TENANT:
            email, verified = claims.get("email") or claims.get("preferred_username"), True
        else:
            upn = str(claims.get("preferred_username") or "")
            email, verified = (upn, True) if "@" in upn and "#EXT#" not in upn.upper() else (claims.get("email"), False)
        # Microsoft subjects are per app; oid + tid identify the person.
        if claims.get("oid") and claims.get("tid"):
            subject = f"{claims['tid']}:{claims['oid']}"
    elif p.key == "apple":
        email, verified = claims.get("email"), _truthy(claims.get("email_verified"))
        if apple_user and not name:
            try:
                info = json.loads(apple_user).get("name") or {}
                name = " ".join(x for x in (info.get("firstName"), info.get("lastName")) if x) or None
            except (ValueError, AttributeError):
                pass
    elif p.key.startswith("sso:"):
        # A workspace's own identity provider vouches for its people.
        email = claims.get("email") or claims.get("preferred_username")
        verified = "email_verified" not in claims or _truthy(claims.get("email_verified"))
    else:
        email, verified = claims.get("email"), _truthy(claims.get("email_verified"))
    email = str(email).strip().lower() if email and "@" in str(email) else None
    return Profile(subject=subject, email=email, email_verified=bool(email) and verified, name=name)


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower()


def workspace_name(email: str, name: str | None) -> str:
    domain = email_domain(email)
    if domain not in PUBLIC_EMAIL_DOMAINS:
        label = domain.split(".")[0].replace("-", " ").strip()
        if label:
            return label.title()
    first = (name or email.split("@")[0]).split(" ")[0].strip() or "My"
    return f"{first}'s workspace"

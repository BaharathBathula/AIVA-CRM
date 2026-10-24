"""WordPress Forms: form submissions from a WordPress site become AIVA leads.

The site posts to the integration's webhook URL, either through the small AIVA
plugin (downloadable from the integration page, already configured) or from any
form plugin's own webhook feature. Accepted bodies:

* the AIVA plugin's JSON: ``{"plugin", "form_id", "form_name", "fields": [{"key",
  "label", "type", "value"}], "page_url", ...}``;
* flat JSON or form-encoded key/value pairs (Contact Form 7 add-ons, WPForms,
  Gravity Forms and Ninja Forms webhooks, Fluent Forms, Zapier-style senders);
* Elementor Pro's webhook format (``fields[<id>][value]`` / ``form[name]``).

Requests may be signed: ``X-AIVA-Signature: t=<unix>,v1=<hex HMAC-SHA256 of
"<t>.<raw body>">`` (what the plugin sends) or carry the shared secret as
``X-AIVA-Secret`` (for form tools that can only add a fixed header). A request
with a bad signature is always refused; when "Require a signature" is on,
unsigned requests are refused too.
"""

from __future__ import annotations

import hmac
import io
import json
import re
import time
import uuid
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import lead_capture
from app.integrations.providers.automation import verify_signature
from app.integrations.providers.lead_capture import Answer, Capture
from app.models.integration_hub import IntegrationConnection, IntegrationLog, IntegrationToggle

KEY = "wordpress_forms"
MAX_BODY = 256 * 1024
RATE_PER_MINUTE = 120
SUBMISSION_ACTIONS = ("lead_created", "lead_updated", "submission_skipped", "submission_duplicate", "submission_ignored", "submission_rejected", "test_received")
META_KEYS = {
    "form_id", "formid", "form_name", "form_title", "formname", "form", "source", "plugin", "entry_id", "entryid", "page_url",
    "page", "referrer", "referer", "site", "site_url", "site_name", "submitted_at", "date", "time", "user_agent", "ip", "remote_ip",
    "aiva_test", "test", "form_key",
}
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid")
PLUGINS = {
    "contact-form-7": "Contact Form 7",
    "wpforms": "WPForms",
    "gravity-forms": "Gravity Forms",
    "elementor": "Elementor",
    "ninja-forms": "Ninja Forms",
    "fluent-forms": "Fluent Forms",
    "formidable": "Formidable Forms",
}

_RATE: dict[str, deque] = defaultdict(deque)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _rate_limited(token: str) -> bool:
    now = time.monotonic()
    bucket = _RATE[token]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_PER_MINUTE:
        return True
    bucket.append(now)
    if len(_RATE) > 10000:
        _RATE.clear()
    return False


# ===========================================================================
# Parsing
# ===========================================================================


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(v) for v in value if _text(v))
    if isinstance(value, dict):
        # Name/address sub-fields: join the parts in order.
        return " ".join(_text(v) for v in value.values() if _text(v))
    return str(value).strip()[:5000]


def _flatten(data: dict[str, Any]) -> list[tuple[str, Any]]:
    """One level of nesting becomes dotted keys (Fluent Forms "names.first_name")."""

    out: list[tuple[str, Any]] = []
    for key, value in data.items():
        if isinstance(value, dict) and value:
            out.extend((f"{key}.{inner}", inner_value) for inner, inner_value in value.items())
        else:
            out.append((str(key), value))
    return out


def _bracket_form(pairs: list[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, dict[str, str]], list[tuple[str, str]]]:
    """Split Elementor-style ``fields[x][value]`` / ``form[name]`` pairs from flat ones."""

    meta: dict[str, Any] = {}
    structured: dict[str, dict[str, str]] = {}
    flat: list[tuple[str, str]] = []
    for key, value in pairs:
        match = re.fullmatch(r"fields\[([^\]]+)\]\[(value|title|type|id|raw_value)\]", key)
        if match:
            structured.setdefault(match.group(1), {})[match.group(2)] = value
            continue
        match = re.fullmatch(r"form\[(id|name)\]", key)
        if match:
            meta[f"form_{match.group(1)}"] = value
            continue
        match = re.fullmatch(r"form_fields\[([^\]]+)\]", key)
        if match:
            flat.append((match.group(1), value))
            continue
        match = re.fullmatch(r"([^\[\]]+)\[\]", key)
        if match:
            flat.append((match.group(1), value))
            continue
        flat.append((key, value))
    return meta, structured, flat


def parse_submission(body: bytes, content_type: str, form_pairs: list[tuple[str, str]] | None = None) -> tuple[dict[str, Any], list[Answer]]:
    """Returns (meta, answers). Raises IntegrationError when the body can't be read."""

    meta: dict[str, Any] = {}
    answers: list[Answer] = []
    seen: dict[str, int] = {}

    def add(key: str, label: str, value: Any, kind: str = "") -> None:
        key = str(key).strip()[:200]
        if not key or lead_capture.is_noise(key):
            return
        text = _text(value)
        # Keep keys unique (checkbox groups, repeated names).
        if key in seen:
            seen[key] += 1
            existing = next((a for a in answers if a.key == key), None)
            if existing is not None and text:
                existing.value = f"{existing.value}, {text}" if existing.value else text
            return
        seen[key] = 1
        answers.append(Answer(key=key, label=(str(label or key).strip() or key)[:200], value=text, type=str(kind or "")[:40]))

    def take_meta(key: str, value: Any) -> bool:
        normal = key.lower()
        if normal in META_KEYS:
            meta[normal] = _text(value)[:500]
            return True
        if normal in UTM_KEYS:
            meta.setdefault("utm", {})[normal] = _text(value)[:500]
            return True
        return False

    kind = (content_type or "").split(";")[0].strip().lower()
    if kind in {"application/json", "text/json"} or (not kind and body.strip().startswith(b"{")):
        try:
            data = json.loads(body or b"{}")
        except ValueError as exc:
            raise IntegrationError("The body isn't valid JSON.", 400) from exc
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        if not isinstance(data, dict):
            raise IntegrationError("Send the form's answers as a JSON object.", 422)
        fields = data.get("fields")
        for key, value in data.items():
            if key == "fields" and isinstance(fields, (list, dict)):
                continue
            if key == "utm" and isinstance(value, dict):
                for k, v in value.items():
                    take_meta(str(k), v)
                continue
            if key == "form" and isinstance(value, dict):
                meta["form_id"] = _text(value.get("id"))[:200]
                meta["form_name"] = _text(value.get("name") or value.get("title"))[:200]
                continue
            if take_meta(str(key), value):
                continue
            for name, inner in _flatten({key: value}):
                add(name, name.replace(".", " "), inner)
        if isinstance(fields, list):
            for item in fields:
                if not isinstance(item, dict):
                    continue
                key = item.get("key") or item.get("name") or item.get("id") or item.get("label") or item.get("title")
                add(str(key or ""), item.get("label") or item.get("title") or str(key or ""), item.get("value", item.get("raw_value")), item.get("type") or "")
        elif isinstance(fields, dict):
            for key, item in fields.items():
                if isinstance(item, dict):
                    add(str(item.get("id") or key), item.get("title") or item.get("label") or str(key), item.get("value", item.get("raw_value")), item.get("type") or "")
                else:
                    add(str(key), str(key), item)
    elif kind in {"application/x-www-form-urlencoded", "multipart/form-data"} or form_pairs is not None:
        pairs = form_pairs if form_pairs is not None else parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
        form_meta, structured, flat = _bracket_form(pairs)
        meta.update({k: str(v)[:200] for k, v in form_meta.items()})
        for field_id, item in structured.items():
            add(item.get("id") or field_id, item.get("title") or field_id, item.get("value", item.get("raw_value", "")), item.get("type", ""))
        for key, value in flat:
            if take_meta(key, value):
                continue
            add(key, key, value)
    else:
        raise IntegrationError("Send JSON or a form-encoded body.", 415)

    if not any(a.value for a in answers) and not meta.get("aiva_test") and not meta.get("test"):
        raise IntegrationError("The submission didn't contain any answers.", 422)
    return meta, answers


def form_key(meta: dict[str, Any]) -> str:
    if meta.get("form_key"):
        return str(meta["form_key"]).strip()[:120] or "default"
    plugin = lead_capture.normalise(meta.get("plugin") or meta.get("source") or "form") or "form"
    ident = meta.get("form_id") or meta.get("formid") or lead_capture.normalise(meta.get("form_name") or meta.get("form_title") or meta.get("formname") or meta.get("form") or "")
    return f"{plugin}:{str(ident)[:60]}" if ident else plugin


def form_name(meta: dict[str, Any]) -> str:
    name = meta.get("form_name") or meta.get("form_title") or meta.get("formname") or meta.get("form")
    plugin = PLUGINS.get(str(meta.get("plugin") or meta.get("source") or ""), meta.get("plugin") or meta.get("source"))
    if name:
        return str(name)[:200]
    if meta.get("form_id"):
        return f"{plugin or 'Form'} #{meta['form_id']}"
    return str(plugin or "Website form")[:200]


# ===========================================================================
# Settings
# ===========================================================================


def defaults(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("defaults") or {}
    return {
        "lead_source": raw.get("lead_source") or "wordpress",
        "owner_user_id": raw.get("owner_user_id"),
        "on_duplicate": raw.get("on_duplicate") if raw.get("on_duplicate") in lead_capture.DUPLICATE_MODES else "update",
        "tags": list(raw.get("tags") or []),
    }


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    from app.services.custom_fields import fields_for

    org = principal.organization_id
    custom_keys = {f.key for f in await fields_for(db, org, "lead")}
    cleaned: dict[str, Any] = {}
    if "defaults" in config:
        raw = config.get("defaults") or {}
        mode = raw.get("on_duplicate") or "update"
        if mode not in lead_capture.DUPLICATE_MODES:
            raise IntegrationError("Choose what happens to duplicates: update, skip or create.", 422)
        owner = await lead_capture.valid_owner(db, org, raw.get("owner_user_id"))
        cleaned["defaults"] = {
            "lead_source": (str(raw.get("lead_source") or "wordpress").strip()[:80]) or "wordpress",
            "owner_user_id": str(owner) if owner else None,
            "on_duplicate": mode,
            "tags": lead_capture.clean_tags(raw.get("tags")),
        }
    if "require_signature" in config:
        cleaned["require_signature"] = bool(config.get("require_signature"))
    if "forms" in config:
        forms_in = config.get("forms") or {}
        if not isinstance(forms_in, dict):
            raise IntegrationError("Form settings must be an object.", 422)
        forms: dict[str, Any] = {}
        for raw_key, raw in list(forms_in.items())[:200]:
            if not isinstance(raw, dict):
                continue
            owner = await lead_capture.valid_owner(db, org, raw.get("owner_user_id"))
            forms[str(raw_key)[:120]] = {
                "enabled": raw.get("enabled", True) is not False,
                "lead_source": (str(raw.get("lead_source") or "").strip()[:80]) or None,
                "owner_user_id": str(owner) if owner else None,
                "tags": lead_capture.clean_tags(raw.get("tags")),
                "mapping": lead_capture.clean_mapping(raw.get("mapping"), custom_keys),
            }
        cleaned["forms"] = forms
    return cleaned


# ===========================================================================
# Receiving
# ===========================================================================


async def _toggle(db: AsyncSession, organization_id: uuid.UUID) -> IntegrationToggle | None:
    return await core.toggle_row(db, organization_id, KEY)


def _remember_form(toggle: IntegrationToggle | None, key: str, name: str, meta: dict[str, Any], answers: list[Answer]) -> None:
    """Keep the forms and field keys seen (no answers), for the mapping editor."""

    if toggle is None:
        return
    config = dict(toggle.config or {})
    seen = dict(config.get("seen_forms") or {})
    entry = dict(seen.get(key) or {})
    fields = {f["key"]: f for f in entry.get("fields") or []}
    changed = not entry
    for answer in answers:
        if answer.key not in fields and len(fields) < 100 and not lead_capture.is_sensitive(answer):
            fields[answer.key] = {"key": answer.key, "label": answer.label, "type": answer.type}
            changed = True
    last = entry.get("last_at")
    stale = True
    if last:
        try:
            stale = utcnow() - datetime.fromisoformat(last) > timedelta(minutes=10)
        except ValueError:
            stale = True
    if not changed and not stale:
        return  # nothing new; saved at most every ten minutes per form
    entry.update(
        {
            "key": key,
            "name": name,
            "plugin": meta.get("plugin") or meta.get("source"),
            "site": meta.get("site_url") or meta.get("site"),
            "fields": list(fields.values()),
            "count": int(entry.get("count") or 0) + 1,
            "first_at": entry.get("first_at") or utcnow().isoformat(),
            "last_at": utcnow().isoformat(),
        }
    )
    seen[key] = entry
    if len(seen) > 100:
        for old in sorted(seen, key=lambda k: seen[k].get("last_at") or "")[: len(seen) - 100]:
            seen.pop(old, None)
    config["seen_forms"] = seen
    toggle.config = config


async def process(
    db: AsyncSession,
    connection: IntegrationConnection,
    meta: dict[str, Any],
    answers: list[Answer],
    *,
    dry_run: bool = False,
    origin_note: str | None = None,
) -> dict[str, Any]:
    org = connection.organization_id
    toggle = await _toggle(db, org)
    config = dict(toggle.config or {}) if toggle else {}
    base = defaults(config)
    key = form_key(meta)
    name = form_name(meta)
    form = (config.get("forms") or {}).get(key) or {}
    if not dry_run:
        _remember_form(toggle, key, name, meta, answers)
    if form.get("enabled") is False:
        core.log(db, org, KEY, "submission_ignored", f"Ignored a submission from {name} (that form is switched off in the mapping).", connection=connection,
                 detail={"form": key, "form_name": name})
        return {"ok": True, "result": "ignored"}
    owner = form.get("owner_user_id") or base["owner_user_id"]
    site = meta.get("site_url") or meta.get("site")
    page = meta.get("page_url") or meta.get("page") or meta.get("referrer") or meta.get("referer")
    capture = Capture(
        provider=KEY,
        lead_source=form.get("lead_source") or base["lead_source"],
        answers=answers,
        mapping=dict(form.get("mapping") or {}),
        owner_user_id=uuid.UUID(owner) if owner else connection.connected_by_user_id,
        on_duplicate=base["on_duplicate"],
        tags=lead_capture.clean_tags([*(base.get("tags") or []), *(form.get("tags") or [])]),
        attribution={
            "form": name,
            "form_key": key,
            "integration": KEY,
            "plugin": meta.get("plugin") or meta.get("source"),
            "site": (urlsplit(site).hostname if site and "://" in str(site) else site),
            "page": page,
            "wp_entry_id": meta.get("entry_id") or meta.get("entryid"),
            **(meta.get("utm") or {}),
        },
        origin=origin_note or f"{name}{(' on ' + urlsplit(str(site)).hostname) if site and '://' in str(site) else ''}",
    )
    if not dry_run:
        await lead_capture.prepare(db, org)
    outcome = await lead_capture.save(db, org, capture, dry_run=dry_run)
    lead = outcome.lead
    label = f"{lead.first_name} {lead.last_name}".strip() if lead is not None else None
    if dry_run:
        return {"ok": True, "result": "preview", "mapped": outcome.mapped, "ignored": outcome.ignored, "form": key, "form_name": name}
    messages = {
        "created": ("lead_created", "success", f"New lead {label} from {name}."),
        "updated": ("lead_updated", "info", f"{name}: {label} was already a lead, so the lead was updated."),
        "skipped": ("submission_skipped", "info", f"{name}: skipped a duplicate of {label}."),
        "duplicate": ("submission_duplicate", "info", f"{name}: this entry was already imported."),
    }
    action, level, message = messages[outcome.result]
    core.log(
        db, org, KEY, action, message, level=level, connection=connection,
        detail={"form": key, "form_name": name, "result": outcome.result, "lead_id": str(lead.id) if lead else None, "fields": sorted(outcome.mapped)},
    )
    connection.last_used_at = utcnow()
    return {"ok": True, "result": outcome.result, "lead_id": str(lead.id) if lead is not None else None, "mapped": outcome.mapped}


def _reject(db: AsyncSession, connection: IntegrationConnection, status_code: int, message: str) -> JSONResponse:
    core.log(db, connection.organization_id, KEY, "submission_rejected", message, level="warning", connection=connection)
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method == "GET":
        return JSONResponse({"ok": True, "message": "AIVA is ready to receive WordPress form submissions at this address (POST)."})
    if _rate_limited(connection.webhook_token or str(connection.id)):
        return JSONResponse({"ok": False, "error": "Too many submissions. Slow down and try again in a minute."}, status_code=429)
    declared = request.headers.get("content-length")
    if declared and declared.isascii() and declared.isdigit() and int(declared) > MAX_BODY:
        return _reject(db, connection, 413, "A submission was refused because it was larger than 256 KB.")
    body = await request.body()
    if len(body) > MAX_BODY:
        return _reject(db, connection, 413, "A submission was refused because it was larger than 256 KB.")

    secret = core.secret(connection, "signing_secret")
    toggle = await _toggle(db, connection.organization_id)
    require = bool((toggle.config or {}).get("require_signature")) if toggle else False
    signature = request.headers.get("x-aiva-signature")
    shared = request.headers.get("x-aiva-secret")
    if signature:
        if not secret or not verify_signature(secret, body, signature):
            return _reject(db, connection, 401, "A submission was refused: its signature didn't match (check the secret in the AIVA plugin's settings, and the site's clock).")
    elif shared:
        if not secret or not hmac.compare_digest(shared.strip().encode(), secret.encode()):
            return _reject(db, connection, 401, "A submission was refused: the X-AIVA-Secret header didn't match.")
    elif require:
        return _reject(db, connection, 401, "A submission was refused because it wasn't signed and this workspace requires signed submissions.")

    content_type = request.headers.get("content-type", "")
    pairs: list[tuple[str, str]] | None = None
    if content_type.lower().startswith("multipart/form-data"):
        form = await request.form()
        pairs = [(k, v) for k, v in form.multi_items() if isinstance(v, str)]
    try:
        meta, answers = parse_submission(body, content_type, pairs)
    except IntegrationError as exc:
        return _reject(db, connection, exc.status_code, f"A submission was refused: {exc}")

    if str(meta.get("aiva_test") or "").lower() in {"1", "true", "yes"}:
        site = meta.get("site_url") or meta.get("site") or "your site"
        core.log(db, connection.organization_id, KEY, "test_received", f"Test from {site} received. The connection works.", level="success", connection=connection)
        connection.last_used_at = utcnow()
        return JSONResponse({"ok": True, "result": "test", "message": "Connected to AIVA."})
    honeypot = next((a for a in answers if a.key.lower() in {"_hp", "hp", "honeypot", "website_url_hp"} and a.value), None)
    if honeypot is not None:
        return JSONResponse({"ok": True, "result": "ignored"})

    try:
        result = await process(db, connection, meta, answers)
    except IntegrationError as exc:
        return _reject(db, connection, exc.status_code, f"A submission from {form_name(meta)} was refused: {exc}")
    return JSONResponse({"ok": True, "result": result["result"]})


# ===========================================================================
# Secret and plugin
# ===========================================================================


async def ensure_secret(db: AsyncSession, connection: IntegrationConnection) -> str:
    secret = core.secret(connection, "signing_secret")
    if not secret:
        secret = "wpsec_" + uuid.uuid4().hex + uuid.uuid4().hex[:16]
        core.set_secret(connection, "signing_secret", secret)
    return secret


async def rotate_secret(db: AsyncSession, connection: IntegrationConnection, user_id: uuid.UUID) -> str:
    secret = "wpsec_" + uuid.uuid4().hex + uuid.uuid4().hex[:16]
    core.set_secret(connection, "signing_secret", secret)
    core.log(db, connection.organization_id, KEY, "secret_rotated", "A new signing secret was created. Update it in the AIVA plugin's settings (or download the plugin again).",
             level="warning", connection=connection, user_id=user_id)
    return secret


def _php_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


PLUGIN_VERSION = "1.0.0"


def plugin_zip(webhook_url: str, secret: str, workspace: str) -> bytes:
    from app.integrations.providers.wordpress_plugin import MAIN_PHP, README_TXT, UNINSTALL_PHP

    config = (
        "<?php\n"
        "/**\n * Settings filled in by AIVA when this copy of the plugin was downloaded.\n"
        " * They're copied into the plugin's settings when it is activated; change them later\n"
        " * under Settings → AIVA CRM.\n */\n"
        "if ( ! defined( 'ABSPATH' ) ) { exit; }\n"
        f"return array(\n    'webhook_url' => {_php_string(webhook_url)},\n    'secret'      => {_php_string(secret)},\n    'workspace'   => {_php_string(workspace)},\n);\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        stamp = time.localtime()[:6]
        for name, content in (
            ("aiva-crm-forms/aiva-crm-forms.php", MAIN_PHP.replace("{{VERSION}}", PLUGIN_VERSION)),
            ("aiva-crm-forms/aiva-config.php", config),
            ("aiva-crm-forms/uninstall.php", UNINSTALL_PHP),
            ("aiva-crm-forms/readme.txt", README_TXT.replace("{{VERSION}}", PLUGIN_VERSION)),
            ("aiva-crm-forms/index.php", "<?php\n// Silence is golden.\n"),
        ):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()


# ===========================================================================
# Hub hooks
# ===========================================================================


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    if not enabled:
        return
    connection = await core.connection_for(db, organization_id, KEY)
    if connection is not None:
        await ensure_secret(db, connection)


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    from app.services.custom_fields import fields_for

    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    toggle = await _toggle(db, org)
    config = dict(toggle.config or {}) if toggle else {}
    seen = sorted((config.get("seen_forms") or {}).values(), key=lambda f: f.get("last_at") or "", reverse=True)
    logs = (
        await db.execute(
            select(IntegrationLog)
            .where(IntegrationLog.organization_id == org, IntegrationLog.provider == KEY, IntegrationLog.action.in_(SUBMISSION_ACTIONS))
            .order_by(IntegrationLog.created_at.desc())
            .limit(25)
        )
    ).scalars().all()
    since = utcnow() - timedelta(days=7)
    week = [log for log in logs if log.created_at and log.created_at >= since]
    return {
        "webhook_url": core.webhook_url(KEY, connection.webhook_token) if connection else None,
        "has_secret": bool(connection and core.secret(connection, "signing_secret")),
        "require_signature": bool(config.get("require_signature")),
        "defaults": defaults(config),
        "forms": config.get("forms") or {},
        "seen_forms": seen,
        "targets": [{"key": k, "label": v} for k, v in lead_capture.TARGETS.items()],
        "custom_fields": [{"key": f"cf.{f.key}", "label": f.label} for f in await fields_for(db, org, "lead")],
        "plugins": [{"key": k, "name": v} for k, v in PLUGINS.items()],
        "recent": [
            {
                "id": str(log.id), "at": log.created_at, "action": log.action, "level": log.level, "message": log.message,
                "form_name": (log.detail or {}).get("form_name"), "result": (log.detail or {}).get("result"), "lead_id": (log.detail or {}).get("lead_id"),
            }
            for log in logs
        ],
        "stats_7d": {
            "created": sum(1 for log in week if log.action == "lead_created"),
            "updated": sum(1 for log in week if log.action == "lead_updated"),
            "rejected": sum(1 for log in week if log.action == "submission_rejected"),
        },
        "plugin_version": PLUGIN_VERSION,
    }

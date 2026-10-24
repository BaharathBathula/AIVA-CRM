"""LinkedIn Lead Gen Forms: lead-form responses from your ad accounts become AIVA leads.

* Connecting: an admin of the ad account signs in with LinkedIn (workspace
  OAuth, ``r_marketing_leadgen_automation`` + ``r_ads``). AIVA lists the ad
  accounts the person can see; the admin picks which ones to sync, and AIVA
  loads their lead forms (questions, standard fields such as first name,
  email, company) so answers map onto lead fields.
* Syncing: every 10 minutes AIVA asks LinkedIn's Lead Sync API for responses
  submitted since the last check on each chosen account (with a five-minute
  overlap, and three days back the first time), at most a few pages per run.
  Each response becomes a lead through the same capture path as the WordPress
  and Facebook integrations (field mapping, duplicates, owner, tags), and is
  imported once (by LinkedIn response id). Campaign and form names are kept on
  the lead's attribution.
* Every call uses LinkedIn's versioned API (``LinkedIn-Version`` and Rest.li
  2.0 headers).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote as urlquote

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import lead_capture
from app.integrations.providers.lead_capture import Answer, Capture
from app.models.integration_hub import IntegrationConnection

logger = logging.getLogger("aiva.integrations.linkedin")

KEY = "linkedin_lead_gen"
REST = "https://api.linkedin.com/rest"
# LinkedIn's monthly API version (YYYYMM); each stays supported for about a year.
VERSION = os.getenv("LINKEDIN_API_VERSION", "202608")
SYNC_EVERY = timedelta(minutes=10)
FIRST_WINDOW = timedelta(days=3)
OVERLAP = timedelta(minutes=5)
MAX_PAGES = 5
WINDOW = timedelta(hours=6)
WINDOWS_PER_RUN = 16
PAGE_SIZE = 100
MAX_ACCOUNTS = 25
DEFAULT_SOURCE = "linkedin_ads"
PREDEFINED = {
    "FIRST_NAME": "first_name", "LAST_NAME": "last_name", "EMAIL": "email", "WORK_EMAIL": "work_email",
    "PHONE_NUMBER": "phone", "WORK_PHONE_NUMBER": "work_phone", "MOBILE_PHONE_NUMBER": "mobile_phone",
    "COMPANY_NAME": "company_name", "JOB_TITLE": "job_title", "CITY": "city", "STATE": "state", "COUNTRY": "country",
    "ZIP_CODE": "zip_code", "POSTAL_CODE": "zip_code", "LINKEDIN_PROFILE_LINK": "linkedin", "SENIORITY": "seniority",
    "INDUSTRY": "industry", "COMPANY_SIZE": "company_size", "DEGREE": "degree", "FIELD_OF_STUDY": "field_of_study",
    "SCHOOL": "school", "GENDER": "gender",
}
STANDARD_LABELS = {
    "first_name": "First name", "last_name": "Last name", "email": "Email", "work_email": "Work email", "phone": "Phone",
    "work_phone": "Work phone", "mobile_phone": "Mobile phone", "company_name": "Company", "job_title": "Job title",
    "city": "City", "state": "State", "country": "Country", "zip_code": "ZIP code", "linkedin": "LinkedIn profile",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _headers() -> dict[str, str]:
    return {"LinkedIn-Version": VERSION, "X-Restli-Protocol-Version": "2.0.0", "Accept": "application/json"}


async def _get(db: AsyncSession, connection: IntegrationConnection, path_and_query: str) -> dict[str, Any]:
    """GET a Rest.li URL (built by hand: Rest.li wants its parentheses and colons unencoded)."""

    return (await core.request(db, connection, "GET", f"{REST}{path_and_query}", headers=_headers())).json()


def account_urn(account_id: str) -> str:
    return urlquote(f"urn:li:sponsoredAccount:{account_id}", safe="")


def _set_config(connection: IntegrationConnection, **values: Any) -> None:
    connection.config = {**(connection.config or {}), **values}


def _accounts(connection: IntegrationConnection | None) -> list[dict[str, Any]]:
    return list(((connection.config or {}).get("ad_accounts") or [])) if connection else []


# ---------------------------------------------------------------------------
# Ad accounts and forms
# ---------------------------------------------------------------------------


async def refresh_accounts(db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
    data = await _get(db, connection, "/adAccounts?q=search&search=(status:(values:List(ACTIVE,DRAFT,CANCELED)))&count=100")
    previous = {a["id"]: a for a in _accounts(connection)}
    accounts = []
    for element in (data.get("elements") or [])[:MAX_ACCOUNTS * 4]:
        account_id = str(element.get("id") or "")
        if not account_id.isdigit():
            continue
        accounts.append(
            {
                "id": account_id,
                "name": str(element.get("name") or f"Ad account {account_id}")[:200],
                "status": element.get("status"),
                "currency": element.get("currency"),
                "sync": bool((previous.get(account_id) or {}).get("sync")),
            }
        )
    _set_config(connection, ad_accounts=accounts, accounts_synced_at=utcnow().isoformat())
    return accounts


def _localized(value: Any) -> str:
    if isinstance(value, dict):
        localized = value.get("localized") if isinstance(value.get("localized"), dict) else value
        for text in localized.values():
            if text:
                return str(text)
    return str(value or "")


async def load_forms(db: AsyncSession, connection: IntegrationConnection, account_id: str) -> list[dict[str, Any]]:
    forms: dict[str, Any] = dict((connection.config or {}).get("lead_forms") or {})
    loaded = []
    start = 0
    for _ in range(5):
        data = await _get(db, connection, f"/leadForms?q=owner&owner=(sponsoredAccount:{account_urn(account_id)})&count=100&start={start}")
        elements = data.get("elements") or []
        for form in elements:
            form_id = str(form.get("id") or "")
            if not form_id:
                continue
            questions = []
            for question in ((form.get("content") or {}).get("questions") or [])[:100]:
                details = question.get("questionDetails") or {}
                choice = details.get("multipleChoiceQuestionDetails") or {}
                questions.append(
                    {
                        "id": str(question.get("questionId")),
                        "name": str(question.get("name") or "")[:100],
                        "label": _localized(question.get("question"))[:200],
                        "predefined": question.get("predefinedField"),
                        "options": {str(o.get("id")): _localized(o.get("text"))[:200] for o in choice.get("options") or []},
                    }
                )
            info = {"id": form_id, "name": str(form.get("name") or f"Form {form_id}")[:200], "state": form.get("state"), "account_id": account_id, "questions": questions}
            forms[form_id] = info
            loaded.append(info)
        if len(elements) < 100:
            break
        start += 100
    _set_config(connection, lead_forms=forms)
    return loaded


async def set_account_sync(db: AsyncSession, connection: IntegrationConnection, account_id: str, sync: bool, user_id: uuid.UUID | None) -> dict[str, Any]:
    accounts = _accounts(connection)
    account = next((a for a in accounts if a["id"] == account_id), None)
    if account is None:
        raise IntegrationError("That ad account isn't available to the connected LinkedIn member. Refresh the list.", 404)
    if sync and sum(1 for a in accounts if a.get("sync")) >= MAX_ACCOUNTS and not account.get("sync"):
        raise IntegrationError(f"Sync up to {MAX_ACCOUNTS} ad accounts.", 409)
    if sync:
        forms = await load_forms(db, connection, account_id)
        account["forms"] = len(forms)
    account["sync"] = sync
    _set_config(connection, ad_accounts=accounts)
    core.log(db, connection.organization_id, KEY, "account_sync_on" if sync else "account_sync_off",
             f"{'Syncing' if sync else 'Stopped syncing'} leads from {account['name']}.", connection=connection, user_id=user_id)
    return account


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


def question_key(question: dict[str, Any]) -> str:
    """The answer key a question's value is stored under (and that field mapping uses)."""

    return PREDEFINED.get(str(question.get("predefined") or "")) or str(question.get("name") or "") or f"question_{question.get('id')}"


def answers_from(response: dict[str, Any], form: dict[str, Any] | None) -> list[Answer]:
    questions = {q["id"]: q for q in (form or {}).get("questions") or []}
    answers: list[Answer] = []
    for item in ((response.get("formResponse") or {}).get("answers") or [])[:100]:
        question = questions.get(str(item.get("questionId"))) or {}
        details = item.get("answerDetails") or {}
        if "textQuestionAnswer" in details:
            value = str((details.get("textQuestionAnswer") or {}).get("answer") or "")
        elif "multipleChoiceAnswer" in details:
            options = question.get("options") or {}
            value = ", ".join(options.get(str(o), str(o)) for o in (details.get("multipleChoiceAnswer") or {}).get("options") or [])
        else:
            value = ""
        predefined = PREDEFINED.get(str(question.get("predefined") or ""))
        key = question_key(question) if question else f"question_{item.get('questionId')}"
        label = STANDARD_LABELS.get(predefined or "") or question.get("label") or str(key).replace("_", " ").capitalize()
        answers.append(Answer(key=str(key)[:200], label=str(label)[:200], value=value[:5000]))
    return answers


def _form_id(response: dict[str, Any]) -> str:
    urn = str(response.get("versionedLeadGenFormUrn") or "")
    # urn:li:versionedLeadGenForm:(urn:li:leadGenForm:3162,1)
    marker = "urn:li:leadGenForm:"
    if marker in urn:
        return urn.split(marker, 1)[1].split(",", 1)[0].rstrip(")")
    return ""


async def import_response(db: AsyncSession, connection: IntegrationConnection, account: dict[str, Any], response: dict[str, Any]) -> str:
    org = connection.organization_id
    response_id = str(response.get("id") or "")
    form_id = _form_id(response)
    forms = (connection.config or {}).get("lead_forms") or {}
    form = forms.get(form_id)
    config = await core.workspace_config(db, org, KEY)
    base = config.get("defaults") or {}
    settings = (config.get("forms") or {}).get(form_id) or {}
    form_name = (form or {}).get("name") or (f"Form {form_id}" if form_id else "LinkedIn lead form")
    if settings.get("enabled") is False:
        return "ignored"
    owner = settings.get("owner_user_id") or base.get("owner_user_id")
    campaign = str(((response.get("leadMetadata") or {}).get("sponsoredLeadMetadata") or {}).get("campaign") or "")
    campaign_id = campaign.rsplit(":", 1)[-1] if campaign else None
    submitted = response.get("submittedAt")
    capture = Capture(
        provider=KEY,
        lead_source=settings.get("lead_source") or base.get("lead_source") or DEFAULT_SOURCE,
        answers=answers_from(response, form),
        mapping=dict(settings.get("mapping") or {}),
        owner_user_id=uuid.UUID(owner) if owner else connection.connected_by_user_id,
        on_duplicate=base.get("on_duplicate") if base.get("on_duplicate") in lead_capture.DUPLICATE_MODES else "update",
        tags=lead_capture.clean_tags([*(base.get("tags") or []), *(settings.get("tags") or [])]),
        attribution={
            "integration": KEY,
            "ad_account": account.get("name"),
            "ad_account_id": account.get("id"),
            "form": form_name,
            "form_id": form_id,
            "campaign_id": campaign_id,
            "utm_source": "linkedin",
            "utm_medium": "paid_social",
            "lead_created_at": datetime.fromtimestamp(submitted / 1000, tz=timezone.utc).isoformat() if isinstance(submitted, (int, float)) else None,
        },
        external_key="linkedin_lead_id",
        external_id=response_id or None,
        origin=f"LinkedIn lead form {form_name}",
    )
    await lead_capture.prepare(db, org)
    outcome = await lead_capture.save(db, org, capture)
    record = outcome.lead
    label = f"{record.first_name} {record.last_name}".strip() if record is not None else "a lead"
    if outcome.result != "duplicate":
        messages = {
            "created": ("lead_created", "success", f"New lead {label} from {form_name} ({account.get('name')})."),
            "updated": ("lead_updated", "info", f"{form_name}: {label} was already a lead, so the lead was updated."),
            "skipped": ("lead_skipped", "info", f"{form_name}: skipped a duplicate of {label}."),
        }
        action, level, message = messages[outcome.result]
        core.log(db, org, KEY, action, message, level=level, connection=connection,
                 detail={"form_id": form_id, "account_id": account.get("id"), "lead_id": str(record.id) if record else None, "result": outcome.result})
    return outcome.result


async def sync(db: AsyncSession, connection: IntegrationConnection) -> int:
    """Import new responses from every chosen ad account. Commits after each account.

    Responses are read in six-hour windows from the account's cursor; the cursor
    only moves past a window once it has been read to the end, so a backlog is
    worked through over several runs and nothing is skipped.
    """

    now = utcnow()
    marks: dict[str, str] = dict((connection.config or {}).get("cursors") or {})
    imported = 0
    for account in [a for a in _accounts(connection) if a.get("sync")][:MAX_ACCOUNTS]:
        account_id = account["id"]
        try:
            since = datetime.fromisoformat(marks[account_id]) - OVERLAP if marks.get(account_id) else now - FIRST_WINDOW
        except ValueError:
            since = now - FIRST_WINDOW
        window_start = since
        for _ in range(WINDOWS_PER_RUN):
            if window_start >= now:
                break
            window_end = min(window_start + WINDOW, now)
            start_ms, end_ms = int(window_start.timestamp() * 1000), int(window_end.timestamp() * 1000)
            start = 0
            for page in range(MAX_PAGES):
                data = await _get(
                    db, connection,
                    f"/leadFormResponses?q=owner&owner=(sponsoredAccount:{account_urn(account_id)})&leadType=(leadType:SPONSORED)"
                    f"&limitedToTestLeads=false&submittedAtTimeRange=(start:{start_ms},end:{end_ms})&count={PAGE_SIZE}&start={start}",
                )
                elements = data.get("elements") or []
                for response in elements:
                    if response.get("testLead"):
                        continue
                    if await import_response(db, connection, account, response) == "created":
                        imported += 1
                if len(elements) < PAGE_SIZE:
                    break
                start += PAGE_SIZE
                if page == MAX_PAGES - 1:
                    core.log(db, connection.organization_id, KEY, "sync_capped",
                             f"More than {MAX_PAGES * PAGE_SIZE} LinkedIn leads arrived within six hours on {account['name']}; export the rest from Campaign Manager.",
                             level="warning", connection=connection)
            window_start = window_end
            marks[account_id] = (window_end + OVERLAP if window_end < now else window_end).isoformat()
            _set_config(connection, cursors=dict(marks))
            await db.commit()
    _set_config(connection, cursors=marks, last_sync_at=now.isoformat())
    connection.last_sync_at = now
    if imported:
        core.log(db, connection.organization_id, KEY, "synced", f"Imported {imported} new lead{'s' if imported != 1 else ''} from LinkedIn.", level="success", connection=connection)
    return imported


async def run_due(db: AsyncSession) -> int:
    info = db.sync_session.info
    saved = {name: info.get(name) for name in ("automation_disabled", "automation", "features")}
    handled = 0
    try:
        now = utcnow()
        rows = (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.provider == KEY, IntegrationConnection.status == "connected",
                    or_(IntegrationConnection.last_sync_at.is_(None), IntegrationConnection.last_sync_at < now - SYNC_EVERY),
                )
                # Least recently looked at first (skipped ones included), so none waits forever.
                .order_by(core.scheduler_order()).limit(60)
            )
        ).scalars().all()
        due = [(row.id, row.organization_id) for row in rows if any(a.get("sync") for a in _accounts(row))]
        examined: list[uuid.UUID] = [row.id for row in rows if row.id not in {d[0] for d in due}]
        try:
            for connection_id, organization_id in due[:20]:
                examined.append(connection_id)
                if not await core.is_enabled(db, organization_id, KEY):
                    continue
                connection = await db.get(IntegrationConnection, connection_id)
                if connection is None:
                    continue
                try:
                    handled += await sync(db, connection)
                    await db.commit()
                except Exception as exc:  # noqa: BLE001 - one connection's problem mustn't stop the others
                    await db.rollback()
                    if not isinstance(exc, IntegrationError):
                        logger.exception("LinkedIn lead sync failed for %s", connection_id)
                    fresh = await db.get(IntegrationConnection, connection_id, populate_existing=True)
                    if fresh is not None:
                        fresh.last_error = (str(exc) if isinstance(exc, IntegrationError) else "Checking for new leads failed unexpectedly; it will be retried.")[:1000]
                        fresh.last_sync_at = now
                        core.log(db, fresh.organization_id, KEY, "sync_failed", f"Couldn't check LinkedIn for new leads: {fresh.last_error}", level="error", connection=fresh)
                        await db.commit()
        finally:
            await db.rollback()
            await core.mark_seen_ids(db, examined)
    finally:
        for name, value in saved.items():
            if value is None:
                info.pop(name, None)
            else:
                info[name] = value
    return handled


# ---------------------------------------------------------------------------
# Hub hooks
# ---------------------------------------------------------------------------


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    accounts = await refresh_accounts(db, connection)
    for account in accounts:
        if account.get("sync"):
            await load_forms(db, connection, account["id"])
    core.log(db, connection.organization_id, KEY, "accounts_loaded",
             f"Found {len(accounts)} ad account{'s' if len(accounts) != 1 else ''}. Choose which ones send leads to AIVA.", connection=connection)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    accounts = await refresh_accounts(db, connection)
    syncing = sum(1 for a in accounts if a.get("sync"))
    if not accounts:
        return "Connected, but this LinkedIn member can't see any ad accounts."
    return f"Connected. {len(accounts)} ad account{'s' if len(accounts) != 1 else ''} found; {syncing} sending leads to AIVA."


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    from app.services.custom_fields import fields_for

    if personal:
        raise IntegrationError("LinkedIn Lead Gen Forms is set up for the whole workspace.", 422)
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
            "lead_source": (str(raw.get("lead_source") or DEFAULT_SOURCE).strip()[:80]) or DEFAULT_SOURCE,
            "owner_user_id": str(owner) if owner else None,
            "on_duplicate": mode,
            "tags": lead_capture.clean_tags(raw.get("tags")),
        }
    if "forms" in config:
        forms_in = config.get("forms") or {}
        if not isinstance(forms_in, dict):
            raise IntegrationError("Form settings must be an object.", 422)
        forms: dict[str, Any] = {}
        for form_id, raw in list(forms_in.items())[:300]:
            if not isinstance(raw, dict) or not str(form_id).isdigit():
                continue
            owner = await lead_capture.valid_owner(db, org, raw.get("owner_user_id"))
            forms[str(form_id)] = {
                "enabled": raw.get("enabled", True) is not False,
                "lead_source": (str(raw.get("lead_source") or "").strip()[:80]) or None,
                "owner_user_id": str(owner) if owner else None,
                "tags": lead_capture.clean_tags(raw.get("tags")),
                "mapping": lead_capture.clean_mapping(raw.get("mapping"), custom_keys),
            }
        cleaned["forms"] = forms
    unknown = set(config) - {"defaults", "forms"}
    if unknown:
        raise IntegrationError(f"Unknown setting “{sorted(unknown)[0]}”.", 422)
    return cleaned


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    from app.services.custom_fields import fields_for

    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    config = await core.workspace_config(db, org, KEY)
    connection_config = (connection.config or {}) if connection else {}
    return {
        "accounts": _accounts(connection),
        "lead_forms": {
            form_id: {**form, "questions": [{**q, "key": question_key(q)} for q in form.get("questions") or []]}
            for form_id, form in (connection_config.get("lead_forms") or {}).items()
            if isinstance(form, dict)
        },
        "defaults": {
            "lead_source": (config.get("defaults") or {}).get("lead_source") or DEFAULT_SOURCE,
            "owner_user_id": (config.get("defaults") or {}).get("owner_user_id"),
            "on_duplicate": (config.get("defaults") or {}).get("on_duplicate") or "update",
            "tags": (config.get("defaults") or {}).get("tags") or [],
        },
        "forms": config.get("forms") or {},
        "targets": [{"key": k, "label": v} for k, v in lead_capture.TARGETS.items()],
        "custom_fields": [{"key": f"cf.{f.key}", "label": f.label} for f in await fields_for(db, org, "lead")],
        "last_sync_at": connection_config.get("last_sync_at"),
        "accounts_synced_at": connection_config.get("accounts_synced_at"),
        "sync_every_minutes": int(SYNC_EVERY.total_seconds() // 60),
        "api_version": VERSION,
    }

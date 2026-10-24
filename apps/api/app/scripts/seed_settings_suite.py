"""Demo content for the workspace settings suite.

Contact stages, territories, personas, buying-intent topics and signals,
snippets, sequences (with sent history and waiting call tasks), goals, a
global picklist, data requests, a removal request, conversation trackers,
scorecards, field prompts and two analysed call transcripts, plus the AI
Context Center.

Like ``seed_workspace``, records are created through the REST API in-process
so they behave like hand-made ones. Sequence history is written directly so
the demo shows sent, opened and replied emails without sending any mail, and
the people still active in a sequence are parked on a call step (a task), so
the scheduler never emails the demo addresses on its own.

Runs once per workspace (``demo_settings_suite_seeded`` in organization
settings). Every section is independent; one that fails is reported and the
rest still run.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.outbound import Sequence, SequenceEnrollment, SequenceMessage, SequenceStep
from app.models.pipeline_stage import PipelineStage
from app.scripts.seed_workspace import Client, now

MARKER = "demo_settings_suite_seeded"


async def seed_settings_suite(users: dict[str, uuid.UUID], organization_id: uuid.UUID) -> bool:
    """Adds demo content for the settings suite once. Returns True if it ran."""

    org = organization_id
    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, org)
        if organization is None or (organization.settings or {}).get(MARKER):
            return False
        organization.settings = {**(organization.settings or {}), MARKER: now().isoformat()}
        await db.commit()
        contacts = (
            await db.execute(select(Contact).where(Contact.organization_id == org, Contact.email.is_not(None)).order_by(Contact.created_at, Contact.last_name))
        ).scalars().all()
        leads = (
            await db.execute(select(Lead).where(Lead.organization_id == org, Lead.email.is_not(None), Lead.status != "converted").order_by(Lead.created_at))
        ).scalars().all()
        stages = {s.id: s.category for s in (await db.execute(select(PipelineStage))).scalars()}
        deals = (await db.execute(select(Opportunity).where(Opportunity.organization_id == org).order_by(Opportunity.amount.desc().nulls_last()))).scalars().all()
        accounts = (await db.execute(select(Account).where(Account.organization_id == org).order_by(Account.name))).scalars().all()
        snapshot = {
            "contacts": [(c.id, c.first_name, c.last_name, c.email, c.account_id, c.job_title) for c in contacts],
            "leads": [(l.id, l.first_name, l.last_name, l.email, l.company_name) for l in leads],
            "open": [(d.id, d.name, d.account_id, d.primary_contact_id, d.owner_user_id) for d in deals if stages.get(d.stage_id) == "open"],
            "accounts": [(a.id, a.name) for a in accounts],
        }

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://seed.internal", timeout=60) as http:
        api = Client(http, org, users)
        context: dict[str, Any] = {"org": org, "users": users, **snapshot}
        for name, section in (
            ("AI context", _ai_context),
            ("contact stages", _contact_stages),
            ("territories", _territories),
            ("personas, intent and signals", _icp),
            ("snippets", _snippets),
            ("sequences", _sequences),
            ("goals and picklists", _goals),
            ("data requests", _data_requests),
            ("privacy requests", _privacy),
            ("conversation intelligence", _conversations),
            ("AI word usage", _ai_usage),
        ):
            try:
                await section(api, context)
            except Exception as exc:  # noqa: BLE001 - demo content must never stop startup
                print(f"Demo content for {name} skipped: {exc}")
    return True


# ---------------------------------------------------------------------------
# AI Context Center
# ---------------------------------------------------------------------------


async def _ai_context(api: Client, context: dict) -> None:
    await api.put(
        "/workspace-settings/ai_context",
        {
            "data": {
                "company_overview": "AIVA builds a CRM for mid-market companies that sell complex services: sales, service and marketing in one workspace.",
                "products": "AIVA CRM (Starter, Pro, Enterprise) and add-ons for conversation intelligence, credits and advanced data.",
                "value_props": "One system instead of five; setup in days, not months; AI that drafts, summarizes and keeps the CRM up to date.",
                "ideal_customer": "Operations and revenue leaders at 200–5,000 person companies in logistics, healthcare, energy and financial services.",
                "competitors": [
                    {"name": "Salesforce", "differentiator": "Live in days without consultants, at a fraction of the cost."},
                    {"name": "HubSpot", "differentiator": "Service desk, CPQ and inventory included, not separate hubs."},
                ],
                "tone": "Friendly, plain and confident. Short sentences. No buzzwords.",
                "words_to_avoid": ["synergy", "leverage", "best-in-class"],
                "signature": "",
                "faqs": [
                    {"question": "How long does setup take?", "answer": "Most teams are live within two weeks, including data import."},
                    {"question": "Can we keep our Gmail or Outlook?", "answer": "Yes. Mailboxes and calendars sync both ways."},
                ],
                "use_for": ["drafts", "summaries", "agents", "ask", "conversations"],
            }
        },
    )


# ---------------------------------------------------------------------------
# Contact stages
# ---------------------------------------------------------------------------


async def _contact_stages(api: Client, context: dict) -> None:
    plan = ["interested", "replied", "approaching", "approaching", "unresponsive", "not_interested", "changed_job", "cold"]
    for index, contact in enumerate(context["contacts"][:24]):
        stage = plan[index % len(plan)]
        if stage != "cold":
            await api.patch(f"/contacts/{contact[0]}", {"stage": stage})


# ---------------------------------------------------------------------------
# Territories
# ---------------------------------------------------------------------------


async def _territories(api: Client, context: dict) -> None:
    users = context["users"]
    daniel, priya, maya = (str(users[n]) for n in ("Daniel Ruiz", "Priya Raman", "Maya Chen"))
    for payload in (
        {
            "name": "East", "color": "blue", "priority": 10, "description": "Northeast and Southeast accounts.",
            "rules": [{"field": "state", "operator": "in", "values": ["MA", "NY", "PA", "FL", "NJ", "GA"]}],
            "member_user_ids": [daniel, priya], "assignment": "round_robin", "applies_to": ["account", "lead"],
        },
        {
            "name": "West & Central", "color": "green", "priority": 20, "description": "Everything west of the Mississippi plus Illinois.",
            "rules": [{"field": "state", "operator": "in", "values": ["CA", "WA", "OR", "CO", "AZ", "TX", "IL"]}],
            "member_user_ids": [maya, daniel], "assignment": "round_robin", "applies_to": ["account", "lead"],
        },
        {
            "name": "Canada", "color": "red", "priority": 30,
            "rules": [{"field": "country", "operator": "in", "values": ["Canada", "CA-CAN"]}],
            "member_user_ids": [priya], "assignment": "territory_only", "applies_to": ["account"],
        },
    ):
        await api.post("/territories", payload)
    # Tag existing records without moving anyone's accounts.
    await api.post("/territories/apply", {"only_untagged": True, "reassign_owners": False})


# ---------------------------------------------------------------------------
# Personas, buying intent and signals
# ---------------------------------------------------------------------------


async def _icp(api: Client, context: dict) -> None:
    ops = await api.post(
        "/personas",
        {"name": "Operations leaders", "color": "blue", "description": "Own the processes AIVA replaces; usually the champion.",
         "title_keywords": ["operations", "COO", "chief operating"], "exclude_keywords": ["assistant", "intern"]},
    )
    await api.post(
        "/personas",
        {"name": "Finance decision makers", "color": "green", "description": "Sign the contract; care about cost and consolidation.",
         "title_keywords": ["CFO", "finance", "controller", "procurement"], "exclude_keywords": ["assistant"]},
    )
    await api.post(
        "/personas",
        {"name": "IT and security", "color": "purple", "description": "Review security, SSO and integrations.",
         "title_keywords": ["IT", "CTO", "technology", "security", "information"], "exclude_keywords": ["intern"]},
    )
    for topic in (
        {"name": "Pricing", "url_patterns": ["/pricing", "/plans"], "keywords": ["pricing", "cost"], "weight": 3},
        {"name": "Integrations", "url_patterns": ["/integrations", "/api"], "keywords": ["integration", "api"], "weight": 2},
        {"name": "Security & compliance", "url_patterns": ["/security", "/trust"], "keywords": ["security", "hipaa", "soc 2"], "weight": 2},
    ):
        await api.post("/intent/topics", topic)
    rules = [
        await api.post("/signals/rules", {"name": "Pricing page visit", "kind": "page_visit", "config": {"paths": ["/pricing"], "min_views": 1, "days": 14}, "notify_owner": True}),
        await api.post("/signals/rules", {"name": "Deal has gone quiet", "kind": "deal_stalled", "config": {"days": 10}, "create_task": True}),
        await api.post("/signals/rules", {"name": "New operations leader", "kind": "persona_match", "config": {"persona_id": ops["id"], "days": 30}}),
        await api.post("/signals/rules", {"name": "Champion changed jobs", "kind": "job_change", "config": {"days": 30}, "create_task": True}),
    ]
    for rule in rules:
        await api.post(f"/signals/rules/{rule['id']}/run")


# ---------------------------------------------------------------------------
# Snippets
# ---------------------------------------------------------------------------


async def _snippets(api: Client, context: dict) -> None:
    for snippet in (
        {"name": "Pricing overview", "shortcut": "pricing", "category": "Sales",
         "body": "Hi {{first_name}}, our plans start at $19 per user a month (Starter), $29 (Pro) and $59 (Enterprise), billed monthly with no long contract."},
        {"name": "Book a call", "shortcut": "meet", "category": "Sales",
         "body": "Would a 20-minute call next week work? Pick any time that suits you and I'll send an invite."},
        {"name": "Case study: Northwind", "shortcut": "northwind", "category": "Proof",
         "body": "Northwind Logistics moved 120 people from three tools to AIVA in two weeks and cut quote turnaround from 3 days to 4 hours."},
        {"name": "Security answer", "shortcut": "security", "category": "Objections",
         "body": "Data is encrypted in transit and at rest, single sign-on and two-step verification are built in, and every change is in the audit log."},
    ):
        await api.post("/snippets", snippet, as_user="Daniel Ruiz")


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


async def _sequences(api: Client, context: dict) -> None:
    users = context["users"]
    inbound = await api.post(
        "/sequences",
        {"name": "Inbound demo request follow-up", "description": "For people who asked for a demo but haven't booked one."},
        as_user="Daniel Ruiz",
    )
    steps = [
        {"kind": "auto_email", "subject": "Your AIVA demo, {{first_name}}",
         "body": "Hi {{first_name}},\n\nThanks for asking about AIVA. Most teams like {{company}} start with a 20-minute walkthrough of pipeline and quotes.\n\nWould Tuesday or Wednesday work?\n\n{{sender_name}}"},
        {"kind": "call", "delay_days": 2, "note": "Call to book the demo. Mention the Northwind case study.", "priority": "high"},
        {"kind": "linkedin", "delay_days": 1, "note": "Connect with a short note referencing the demo request."},
        {"kind": "auto_email", "delay_days": 3, "thread_reply": True,
         "body": "Hi {{first_name}}, just bringing this back to the top of your inbox. Happy to send a 5-minute video instead if that's easier."},
    ]
    for step in steps:
        await api.post(f"/sequences/{inbound['id']}/steps", step, as_user="Daniel Ruiz")
    await api.post(f"/sequences/{inbound['id']}/status", {"status": "active"}, as_user="Daniel Ruiz")

    draft = await api.post("/sequences", {"name": "Closed-lost win-back", "description": "Reopen conversations six months after a loss."}, as_user="Maya Chen")
    await api.post(
        f"/sequences/{draft['id']}/steps",
        {"kind": "auto_email", "subject": "A lot has changed at AIVA", "body": "Hi {{first_name}}, since we last spoke we've added CPQ, conversation intelligence and a service desk. Worth a fresh look?"},
        as_user="Maya Chen",
    )

    people = [c for c in context["contacts"] if c[3] and "@" in c[3]][24:34] or context["contacts"][:10]
    if not people:
        return
    result = await api.post(f"/sequences/{inbound['id']}/enroll", {"record_type": "contact", "record_ids": [str(p[0]) for p in people]}, as_user="Daniel Ruiz")
    enrolled = {e["id"] for e in result["enrolled"]}

    # Write the history directly: first email sent a few days ago, some opened, one replied, one finished.
    from app.services import outbound, workspace_settings

    rng = random.Random(7)
    async with AsyncSessionLocal() as db:
        db.sync_session.info["automation_disabled"] = True
        sequence = await db.get(Sequence, uuid.UUID(inbound["id"]))
        step_rows = (await db.execute(select(SequenceStep).where(SequenceStep.sequence_id == sequence.id).order_by(SequenceStep.position))).scalars().all()
        rows = (await db.execute(select(SequenceEnrollment).where(SequenceEnrollment.sequence_id == sequence.id))).scalars().all()
        protection = await workspace_settings.get(db, context["org"], "sending")
        for index, enrollment in enumerate(rows):
            if str(enrollment.record_id) not in enrolled:
                continue
            sent_at = now() - timedelta(days=4, hours=rng.randint(0, 20))
            enrollment.created_at = sent_at - timedelta(minutes=5)
            opened = index % 3 != 2
            message = SequenceMessage(
                id=uuid.uuid4(), organization_id=enrollment.organization_id, enrollment_id=enrollment.id, sequence_id=sequence.id, step_id=step_rows[0].id,
                kind="auto_email", to_email=enrollment.email, subject=f"Your AIVA demo, {enrollment.name.split(' ')[0]}",
                body="Hi,\n\nThanks for asking about AIVA…", status="sent", token=uuid.uuid4().hex + uuid.uuid4().hex[:16], delivery="outbox",
                sender_user_id=enrollment.sender_user_id, sent_at=sent_at, message_id_header=f"<seed-{uuid.uuid4().hex[:12]}@aiva.local>",
                open_count=rng.randint(1, 4) if opened else 0, opened_at=sent_at + timedelta(hours=2) if opened else None,
                click_count=1 if index % 4 == 0 else 0, clicked_at=sent_at + timedelta(hours=3) if index % 4 == 0 else None,
            )
            db.add(message)
            enrollment.thread_message_id = message.message_id_header
            enrollment.thread_subject = message.subject
            if index == 1:
                message.replied_at = sent_at + timedelta(hours=6)
                await outbound.finish(db, enrollment, "replied", "Replied")
                continue
            if index == 3:
                await outbound.finish(db, enrollment, "meeting_booked", "Meeting booked")
                continue
            # Everyone else is on the call step: the call task is created now and waits for the rep.
            enrollment.current_position = 1
            enrollment.next_run_at = now()
            await outbound.process_enrollment(db, enrollment, sequence, list(step_rows), protection, {})
        await db.commit()


# ---------------------------------------------------------------------------
# Goals and global picklists
# ---------------------------------------------------------------------------


async def _goals(api: Client, context: dict) -> None:
    users = context["users"]
    reps = [str(users[n]) for n in ("Daniel Ruiz", "Priya Raman")]
    await api.post("/goals", {"name": "Quarterly bookings", "metric": "deals_won_amount", "period": "quarter", "target": 250000, "assignee_type": "workspace"})
    await api.post("/goals", {"name": "New pipeline per rep", "metric": "pipeline_created", "period": "month", "target": 60000, "assignee_type": "user", "assignee_ids": reps})
    await api.post("/goals", {"name": "Calls logged per rep", "metric": "calls_logged", "period": "week", "target": 25, "assignee_type": "user", "assignee_ids": reps})
    await api.post("/goals", {"name": "Meetings held", "metric": "meetings_held", "period": "month", "target": 20, "assignee_type": "workspace"})
    picklist = await api.post(
        "/picklists",
        {"name": "Sales regions", "description": "Shared by account and lead region fields.",
         "values": [{"value": "Northeast", "color": "blue"}, {"value": "Southeast", "color": "teal"}, {"value": "Central", "color": "amber"}, {"value": "West", "color": "green"}, {"value": "Canada", "color": "red"}]},
    )
    for object_type in ("account", "lead"):
        try:
            await api.post("/custom-fields", {"object_type": object_type, "label": "Sales region", "field_type": "picklist", "picklist_id": picklist["id"]})
        except Exception as exc:  # noqa: BLE001 - the field may already exist
            print(f"Demo region field on {object_type} skipped: {exc}")


# ---------------------------------------------------------------------------
# Data requests
# ---------------------------------------------------------------------------


async def _data_requests(api: Client, context: dict) -> None:
    contacts = context["contacts"]
    if len(contacts) < 3:
        return
    first = await api.post("/data-requests", {"record_type": "contact", "record_id": str(contacts[0][0]), "fields": ["mobile"], "note": "Need a direct line before Thursday's call."}, as_user="Daniel Ruiz")
    await api.post("/data-requests", {"record_type": "contact", "record_id": str(contacts[1][0]), "fields": ["linkedin", "phone"]}, as_user="Priya Raman")
    if context["leads"]:
        await api.post("/data-requests", {"record_type": "lead", "record_id": str(context["leads"][0][0]), "fields": ["job_title", "phone"]}, as_user="Daniel Ruiz")
    await api.post(f"/data-requests/{first['id']}/fulfil", {"values": {"mobile": "+1 (312) 555-0148"}, "note": "Confirmed on their website's team page."})


# ---------------------------------------------------------------------------
# Privacy: a pending removal request and the do-not-contact list
# ---------------------------------------------------------------------------


async def _privacy(api: Client, context: dict) -> None:
    await api.post(
        "/removal-requests",
        {"email": "j.doe@formerprospect.example", "full_name": "Jordan Doe", "regulation": "gdpr", "source": "email",
         "notes": "Asked by email to delete all personal data and stop contact."},
    )
    await api.post("/suppression", {"values": ["competitor.example", "noreply@juniperretail.example"], "note": "Don't prospect competitors or system inboxes."})


# ---------------------------------------------------------------------------
# Conversation intelligence
# ---------------------------------------------------------------------------


DISCOVERY = """WEBVTT

00:00:02.000 --> 00:00:09.000
<v Daniel Ruiz>Thanks for making time. What's prompting the search for a new CRM right now?

00:00:09.500 --> 00:00:24.000
<v {name}>Honestly our quoting takes three days. Sales lives in spreadsheets and support is in a different tool, so nobody sees the whole customer.

00:00:24.500 --> 00:00:33.000
<v Daniel Ruiz>That's common. Who else is involved in choosing, and is there a budget set aside?

00:00:33.500 --> 00:00:47.000
<v {name}>Our CFO signs off. The budget is about $60,000 for the first year and we're also looking at Salesforce and HubSpot.

00:00:47.500 --> 00:00:58.000
<v Daniel Ruiz>Helpful. What would make this a success six months from now?

00:00:58.500 --> 00:01:12.000
<v {name}>Quotes out the same day, and one place to see open cases before a renewal call. Security review will want SSO and an audit log.

00:01:12.500 --> 00:01:21.000
<v Daniel Ruiz>Both are built in. Next step: I'll send a proposal by Friday and set up a security review with your IT lead.
"""

CHECKIN = """WEBVTT

00:00:01.000 --> 00:00:08.000
<v Priya Raman>Quick check-in on the rollout. How is the team finding the pipeline view?

00:00:08.500 --> 00:00:20.000
<v {name}>Adoption is good. The only concern is pricing for the extra seats next quarter, it came in higher than we expected.

00:00:20.500 --> 00:00:30.000
<v Priya Raman>Understood. I can look at an annual price for the 40 seats. Any timeline for the decision?

00:00:30.500 --> 00:00:38.000
<v {name}>We'd like to decide by the end of next month.

00:00:38.500 --> 00:00:45.000
<v Priya Raman>Great, I'll send options this week.
"""


async def _conversations(api: Client, context: dict) -> None:
    for tracker in (
        {"name": "Competitors", "category": "competition", "keywords": ["salesforce", "hubspot", "pipedrive", "zoho"], "color": "red"},
        {"name": "Pricing", "category": "pricing", "keywords": ["price", "pricing", "budget", "discount", "cost"], "color": "amber"},
        {"name": "Security", "category": "objection", "keywords": ["security", "sso", "audit log", "compliance"], "color": "purple"},
        {"name": "Next steps", "category": "process", "keywords": ["next step", "proposal", "send options", "follow up"], "color": "green"},
    ):
        await api.post("/conversation-trackers", tracker)
    scorecard = await api.post(
        "/scorecards",
        {"name": "Discovery call", "description": "The basics of a strong first call.",
         "questions": [
             {"text": "Uncovered the business problem", "kind": "rating", "weight": 2},
             {"text": "Identified who decides and the budget", "kind": "yes_no", "weight": 2},
             {"text": "Asked how success will be measured", "kind": "yes_no", "weight": 1},
             {"text": "Agreed a concrete next step with a date", "kind": "yes_no", "weight": 2},
         ]},
    )
    await api.post("/field-prompts", {"name": "Budget", "object_type": "opportunity", "field_key": "amount", "prompt": "What budget did they mention for the first year?", "keywords": ["budget"]})
    await api.post("/field-prompts", {"name": "Competitors mentioned", "object_type": "opportunity", "field_key": "competitor", "prompt": "Which competitors are they evaluating?", "keywords": ["salesforce", "hubspot", "looking at"]})
    await api.post("/field-prompts", {"name": "Next step", "object_type": "opportunity", "field_key": "next_step", "prompt": "What next step was agreed?", "keywords": ["next step"]})

    deals = context["open"]
    contacts = {c[0]: c for c in context["contacts"]}
    created = []
    for index, (template, owner) in enumerate(((DISCOVERY, "Daniel Ruiz"), (CHECKIN, "Priya Raman"))):
        deal = deals[index] if len(deals) > index else None
        person = contacts.get(deal[3]) if deal else None
        name = f"{person[1]} {person[2]}" if person else "Jordan Avery"
        start = now() - timedelta(days=2 + index * 3, hours=3)
        payload = {
            "title": f"{'Discovery' if index == 0 else 'Rollout check-in'} · {deal[1] if deal else 'Prospect'}",
            "occurred_at": start.isoformat(),
            "transcript_text": template.replace("{name}", name),
            "opportunity_id": str(deal[0]) if deal else None,
            "account_id": str(deal[2]) if deal and deal[2] else None,
            "contact_id": str(person[0]) if person else None,
            "force": True,
        }
        created.append(await api.post("/conversations", payload, as_user=owner))
    if created:
        questions = scorecard["questions"]
        answers = {questions[0]["id"]: 4, questions[1]["id"]: True, questions[2]["id"]: True, questions[3]["id"]: True}
        await api.post(f"/conversations/{created[0]['id']}/reviews", {"scorecard_id": scorecard["id"], "answers": answers, "comment": "Great discovery. Next time, confirm the decision date."}, as_user="Maya Chen")


# ---------------------------------------------------------------------------
# AI word usage (rules-based drafts and summaries, so no AI key is needed)
# ---------------------------------------------------------------------------


async def _ai_usage(api: Client, context: dict) -> None:
    for index, contact in enumerate(context["contacts"][:6]):
        user = ("Daniel Ruiz", "Priya Raman", "Maya Chen")[index % 3]
        await api.post("/ai/draft", {"object_type": "contact", "record_id": str(contact[0]), "intent": "follow up on the demo"}, as_user=user)
    for deal in context["open"][:3]:
        await api.get(f"/ai/summary/opportunity/{deal[0]}", as_user="Maya Chen")

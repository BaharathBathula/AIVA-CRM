"""Demo content for the Starter, Pro and Enterprise features.

Adds templates, a sent campaign, web forms, a journey, a custom module with
records, stage playbooks, an approval rule, custom buttons, target accounts,
a customer portal, inventory, an invoice, a reporting hierarchy and AI
suggestions to the demo workspace. Runs once: it does nothing if the demo
templates already exist, so it's safe on every start.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import (
    ApprovalRule,
    CampaignRecipient,
    CustomButton,
    CustomField,
    CustomModule,
    CustomRecord,
    EmailCampaign,
    EmailTemplate,
    Journey,
    Portal,
    PortalUser,
    SalesInvoice,
    StageRule,
    StockMovement,
    TargetAccount,
    TeamMember,
    WebForm,
)
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.platform import Product, Quote, Team
from app.services import ai_agents
from app.services.forms import default_fields, new_slug
from app.services.merge import new_send_token


def now() -> datetime:
    return datetime.now(timezone.utc)


TEMPLATES = [
    ("Renewal check-in", "Renewals", "Your renewal is coming up, {{first_name}}",
     "Hi {{first_name|there}},\n\nYour coverage with us renews soon. I'd love 15 minutes to review your policies and make sure {{company}} is still getting the best value.\n\nDoes Thursday or Friday work?\n\nBest,\n{{sender_name}}"),
    ("Thanks for the meeting", "Follow-ups", "Great speaking with you, {{first_name}}",
     "Hi {{first_name}},\n\nThanks for your time today. As promised, here's a quick recap and the next steps we agreed on.\n\nTalk soon,\n{{sender_name}}"),
    ("Quote follow-up", "Sales", "Any questions about your quote?",
     "Hi {{first_name}},\n\nI wanted to check whether you had any questions about the quote I sent over. Happy to walk through it together.\n\nBest,\n{{sender_name}}"),
    ("Welcome aboard", "Onboarding", "Welcome to {{organization_name}}, {{first_name}}!",
     "Hi {{first_name}},\n\nWelcome! I'm {{sender_name}} and I'll be your main point of contact. Reply to this email any time you need something.\n\nCheers,\n{{sender_name}}"),
]


async def seed_editions(db, users: dict[str, uuid.UUID], organization_id: uuid.UUID) -> bool:
    org = organization_id
    exists = (
        await db.execute(select(EmailTemplate).where(EmailTemplate.organization_id == org, EmailTemplate.name == TEMPLATES[0][0]))
    ).scalar_one_or_none()
    if exists is not None:
        return False

    info = db.sync_session.info
    info["automation_disabled"] = True
    admin = users["AIVA Administrator"]
    maya, daniel, priya, tom, aisha = (users[n] for n in ("Maya Chen", "Daniel Ruiz", "Priya Raman", "Tom Becker", "Aisha Okafor"))

    # Reporting hierarchy and teams
    memberships = {
        m.user_id: m
        for m in (await db.execute(select(OrganizationMembership).where(OrganizationMembership.organization_id == org))).scalars()
    }
    for user_id, manager in ((maya, admin), (daniel, maya), (priya, maya), (tom, admin), (aisha, tom)):
        if user_id in memberships:
            memberships[user_id].reports_to_user_id = manager
    teams = {t.name: t for t in (await db.execute(select(Team).where(Team.organization_id == org))).scalars()}
    if "Enterprise Sales" in teams and "Mid-Market Sales" in teams:
        sales = Team(id=uuid.uuid4(), organization_id=org, name="Sales", description="All sales teams.", manager_user_id=maya)
        db.add(sales)
        await db.flush()
        teams["Enterprise Sales"].parent_team_id = sales.id
        teams["Mid-Market Sales"].parent_team_id = sales.id
        db.add(TeamMember(id=uuid.uuid4(), organization_id=org, team_id=teams["Mid-Market Sales"].id, user_id=maya, role="lead"))

    # Email templates and a sent campaign
    templates = []
    for name, category, subject, body in TEMPLATES:
        template = EmailTemplate(
            id=uuid.uuid4(), organization_id=org, name=name, category=category, subject=subject, body=body,
            owner_user_id=maya, is_shared=True, usage_count=0,
        )
        templates.append(template)
    db.add_all(templates)
    await db.flush()

    contacts = (await db.execute(select(Contact).where(Contact.organization_id == org).order_by(Contact.created_at).limit(12))).scalars().all()
    campaign = EmailCampaign(
        id=uuid.uuid4(), organization_id=org, name="Q4 renewal reminders", subject=templates[0].subject, body=templates[0].body,
        template_id=templates[0].id, audience={"object_type": "contact", "filters": [], "abm_tier": None}, status="sent",
        started_at=now() - timedelta(days=6), completed_at=now() - timedelta(days=6), created_by_user_id=maya, stats={},
    )
    db.add(campaign)
    await db.flush()
    opened = 0
    for index, contact in enumerate(contacts):
        if not contact.email:
            continue
        open_time = now() - timedelta(days=6, hours=-index) if index % 3 != 2 else None
        opened += 1 if open_time else 0
        db.add(
            CampaignRecipient(
                id=uuid.uuid4(), organization_id=org, campaign_id=campaign.id, record_type="contact", record_id=contact.id,
                email=contact.email, name=f"{contact.first_name} {contact.last_name}", token=new_send_token(), status="sent",
                delivery="outbox", subject=f"Your renewal is coming up, {contact.first_name}",
                body=templates[0].body.replace("{{first_name|there}}", contact.first_name).replace("{{company}}", "your team").replace("{{sender_name}}", "Maya Chen"),
                sent_at=now() - timedelta(days=6), opened_at=open_time, open_count=1 if open_time else 0,
            )
        )
    sent = len([c for c in contacts if c.email])
    campaign.stats = {"recipients": sent, "sent": sent, "failed": 0, "queued": 0, "opened": opened, "unsubscribed": 0, "outbox": sent, "open_rate": round(opened / sent * 100, 1) if sent else 0}
    templates[0].usage_count = 1

    # Web forms
    lead_form = WebForm(
        id=uuid.uuid4(), organization_id=org, name="Website: request a quote", slug=new_slug(), target="lead",
        fields=default_fields("lead") + [{"key": "interest", "label": "What do you need?", "type": "select", "required": False, "maps_to": "", "options": ["Auto", "Home", "Business", "Life"], "placeholder": "", "help": "", "step": 1}],
        settings={"title": "Get a free quote", "description": "Tell us a little about you and we'll reply within one business day.", "submit_label": "Request my quote", "owner_user_id": str(daniel), "lead_source": "website"},
        is_active=True, submission_count=0, created_by_user_id=maya,
    )
    case_form = WebForm(
        id=uuid.uuid4(), organization_id=org, name="Support request", slug=new_slug(), target="case",
        fields=default_fields("case"), settings={"title": "Contact support", "submit_label": "Send to support"},
        is_active=True, submission_count=0, created_by_user_id=tom,
    )
    db.add_all([lead_form, case_form])

    # Journey
    db.add(
        Journey(
            id=uuid.uuid4(), organization_id=org, name="Website lead nurture", description="Welcome new website leads and get them a call within two days.",
            object_type="lead", trigger="created", conditions=[{"field": "source", "operator": "equals", "value": "website"}],
            steps=[
                {"type": "send_email", "params": {"template_id": str(templates[3].id)}},
                {"type": "create_task", "params": {"title": "Call {{first_name}} {{last_name}}", "due_in_days": 1, "task_type": "call"}},
                {"type": "wait", "params": {"days": 3}},
                {"type": "branch", "params": {"conditions": [{"field": "status", "operator": "in", "value": "new,contacted"}], "else": "exit"}},
                {"type": "send_email", "params": {"template_id": str(templates[1].id)}},
                {"type": "notify", "params": {"title": "{{full_name}} finished the nurture journey"}},
            ],
            status="active", created_by_user_id=maya,
        )
    )

    # Custom module: insured vehicles
    module = CustomModule(id=uuid.uuid4(), organization_id=org, key="m_vehicles", name="Vehicles", singular="Vehicle", description="Insured vehicles on commercial and personal auto policies.", icon="car", is_active=True)
    db.add(module)
    fields = [
        ("vin", "VIN", "text", True, [], {}, 1),
        ("year", "Year", "number", False, [], {}, 1),
        ("usage", "Usage", "picklist", True, ["Personal", "Business", "Fleet"], {}, 1),
        ("value", "Stated value", "currency", False, [], {}, 2),
        ("premium", "Annual premium", "currency", False, [], {}, 2),
        ("monthly", "Monthly premium", "formula", False, [], {"expression": "ROUND(premium / 12, 2)"}, 2),
        ("unit", "Unit number", "auto_number", False, [], {"prefix": "VEH-", "start": 1001, "next": 1005}, 1),
        ("owner_account", "Owner account", "lookup", False, [], {"target": "account"}, 1),
    ]
    for position, (key, label, kind, required, options, config, step) in enumerate(fields):
        db.add(CustomField(id=uuid.uuid4(), organization_id=org, object_type="m_vehicles", key=key, label=label, field_type=kind, required=required, options=options, config=config, position=position, step=step, is_active=True, is_encrypted=False))
    db.add(CustomField(id=uuid.uuid4(), organization_id=org, object_type="contact", key="drivers_license", label="Driver's license number", field_type="text", required=False, options=[], config={}, position=0, step=1, is_active=True, is_encrypted=True))
    db.add(CustomField(id=uuid.uuid4(), organization_id=org, object_type="account", key="fleet_size", label="Fleet size", field_type="number", required=False, options=[], config={}, position=0, step=1, is_active=True, is_encrypted=False))
    accounts = (await db.execute(select(Account).where(Account.organization_id == org).order_by(Account.created_at).limit(4))).scalars().all()
    await db.flush()
    for index, (name, vin, year, usage, value, premium) in enumerate(
        [("Ford Transit 250", "1FTBR1C82LKA12345", 2022, "Fleet", 42000, 2880), ("Tesla Model Y", "7SAYGDEE5PF654321", 2023, "Personal", 51000, 2160),
         ("RAM 3500", "3C63R3HL0NG112233", 2021, "Business", 58500, 3420), ("Toyota Camry", "4T1G11AK0NU445566", 2022, "Personal", 27000, 1380)]
    ):
        account = accounts[index % len(accounts)] if accounts else None
        db.add(
            CustomRecord(
                id=uuid.uuid4(), organization_id=org, module_id=module.id, name=name, owner_user_id=[daniel, priya][index % 2], created_by_user_id=admin,
                data={"vin": vin, "year": year, "usage": usage, "value": value, "premium": premium, "unit": f"VEH-{1001 + index}", **({"owner_account": str(account.id)} if account else {})},
            )
        )

    # Stage playbooks and an approval rule
    stages = (await db.execute(select(PipelineStage).where(PipelineStage.organization_id == org).order_by(PipelineStage.position))).scalars().all()
    by_name = {s.name: s for s in stages}
    if "Discovery" in by_name:
        db.add(StageRule(id=uuid.uuid4(), organization_id=org, stage_id=by_name["Discovery"].id, required_fields=[], checklist=["Confirm budget", "Identify the decision maker", "Agree on timeline"], exit_requires_checklist=False, guidance="Use discovery to qualify budget, authority, need and timeline before demoing."))
    if "Proposal" in by_name:
        db.add(StageRule(id=uuid.uuid4(), organization_id=org, stage_id=by_name["Proposal"].id, required_fields=["amount", "expected_close_date"], checklist=["Proposal reviewed by manager", "Pricing approved"], exit_requires_checklist=False, guidance="A deal needs an amount and a close date before it can move to Proposal."))
    won = next((s for s in stages if s.category == "won"), None)
    if won is not None:
        db.add(ApprovalRule(id=uuid.uuid4(), organization_id=org, name="Deals over $150K need VP sign-off", object_type="opportunity", trigger="stage_change", target_stage_id=won.id, conditions=[{"field": "amount", "operator": "greater_than", "value": "150000"}], approver_type="user", approver_value=str(maya), lock_record=True, is_active=True))
    db.add(ApprovalRule(id=uuid.uuid4(), organization_id=org, name="Quote discounts over 20%", object_type="quote", trigger="quote_send", conditions=[{"field": "discount_percent", "operator": "greater_than", "value": "20"}], approver_type="role", approver_value="manager", lock_record=True, is_active=True))

    # Custom buttons
    db.add_all([
        CustomButton(id=uuid.uuid4(), organization_id=org, object_type="account", label="Google Maps", description="Open the account's city in Google Maps.", action_type="open_url", config={"url": "https://www.google.com/maps/search/{{name}}+{{billing_city}}", "new_tab": True}, is_active=True, position=0, run_count=0),
        CustomButton(id=uuid.uuid4(), organization_id=org, object_type="opportunity", label="Request legal review", description="Creates a legal review task and notifies the owner.", action_type="function", config={"steps": [{"type": "create_task", "title": "Legal review: {{name}}", "due_in_days": 2}, {"type": "notify", "message": "Legal review requested"}], "confirm": True}, is_active=True, position=0, run_count=0),
    ])

    # Account based marketing targets
    for index, account in enumerate(accounts):
        db.add(TargetAccount(id=uuid.uuid4(), organization_id=org, account_id=account.id, tier=("tier_1", "tier_1", "tier_2", "tier_3")[index], goal=("Expand to all regions", "Win the renewal plus upsell", "Land a pilot", "Re-engage after churn")[index], owner_user_id=account.owner_user_id))

    # Customer portal with the first contact invited
    organization = await db.get(Organization, org)
    portal = Portal(id=uuid.uuid4(), organization_id=org, name="Customer Help Center", slug=f"{organization.slug}-help", audience="customer", settings={"modules": ["cases", "knowledge", "quotes", "invoices"], "accent": "#2563eb", "welcome": "Track your requests, read how-to guides and review quotes.", "logo_text": organization.name, "support_email": "support@aiva.local"}, is_active=True)
    db.add(portal)
    await db.flush()
    if contacts:
        first = contacts[0]
        db.add(PortalUser(id=uuid.uuid4(), organization_id=org, portal_id=portal.id, contact_id=first.id, account_id=first.account_id, email=first.email, full_name=f"{first.first_name} {first.last_name}", status="active"))

    # Inventory
    products = (await db.execute(select(Product).where(Product.organization_id == org).order_by(Product.name))).scalars().all()
    for index, product in enumerate(products[:4]):
        product.track_inventory = True
        product.unit_cost = (Decimal(product.unit_price) * Decimal("0.55")).quantize(Decimal("0.01"))
        product.reorder_level = Decimal(10)
        stock = Decimal((60, 14, 7, 32)[index])
        product.stock_on_hand = stock
        db.add(StockMovement(id=uuid.uuid4(), organization_id=org, product_id=product.id, kind="receive", quantity=stock, balance_after=stock, reference="PO-1001", note="Opening stock", created_by_user_id=admin))

    # Invoice from an accepted quote
    quote = (await db.execute(select(Quote).where(Quote.organization_id == org, Quote.status == "accepted").limit(1))).scalar_one_or_none()
    if quote is not None:
        db.add(
            SalesInvoice(
                id=uuid.uuid4(), organization_id=org, number=1001, quote_id=quote.id, account_id=quote.account_id, contact_id=quote.contact_id,
                opportunity_id=quote.opportunity_id, owner_user_id=quote.owner_user_id, status="sent", issue_date=date.today() - timedelta(days=10),
                due_date=date.today() + timedelta(days=20), currency=quote.currency, subtotal=quote.subtotal, discount_total=quote.discount_total,
                tax_total=quote.tax_total, total=quote.total, amount_paid=Decimal("0"), terms="Net 30",
                line_items=[{"name": l.name, "description": l.description, "quantity": float(l.quantity), "unit_price": float(l.unit_price), "discount_percent": float(l.discount_percent), "tax_percent": float(l.tax_percent), "line_total": float(l.line_total)} for l in quote.line_items],
            )
        )

    # A few calls for the dialer history
    for index, contact in enumerate(contacts[:5]):
        db.add(
            Activity(
                id=uuid.uuid4(), organization_id=org, contact_id=contact.id, account_id=contact.account_id, created_by_user_id=[daniel, priya, maya][index % 3],
                activity_type="call", subject=f"Call with {contact.first_name} {contact.last_name} · {('connected', 'left voicemail', 'connected', 'no answer', 'connected')[index]}",
                body=("Walked through renewal options; wants a bundled quote.", None, "Confirmed decision timeline for next month.", None, "Discussed adding two vehicles.")[index],
                direction="outbound", occurred_at=now() - timedelta(days=index, hours=2),
                activity_metadata={"outcome": ("connected", "left_voicemail", "connected", "no_answer", "connected")[index], "duration_seconds": (412, 31, 655, 0, 289)[index], "source": "dialer", "phone": contact.phone},
            )
        )

    organization.settings = {**(organization.settings or {}), "record_visibility": "all"}
    await db.flush()

    # Let the agents leave their first suggestions.
    for agent in ai_agents.AGENTS:
        try:
            await ai_agents.run_agent(db, organization, agent)
        except Exception:  # noqa: BLE001 - demo extras shouldn't block startup
            pass
    info["automation_disabled"] = False
    return True

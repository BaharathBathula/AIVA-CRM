"""Development bootstrap: workspace, users and a realistic demo dataset.

Safe to run repeatedly: the workspace, users and memberships are upserted,
and demo records are only created when the workspace has no accounts yet.

Environment variables:
    AIVA_DEV_PASSWORD   password for the seeded users (default: AivaDemo#2026)
    AIVA_SEED_DEMO      set to "false" to skip demo records
"""

import asyncio
import os
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.platform import (
    Case,
    CaseComment,
    ForecastQuota,
    KnowledgeArticle,
    LeadScoringRule,
    Product,
    Quote,
    QuoteLineItem,
    SlaPolicy,
    Team,
    WorkflowRule,
)
from app.models.task import Task
from app.models.user import User
from app.services.automation import score_lead
from app.models.editions import Subscription
from app.scripts.seed_editions import seed_editions
from app.scripts.seed_settings_suite import seed_settings_suite
from app.scripts.seed_workspace import seed_workspace
from app.services.workspace import provision_workspace

DEV_ORGANIZATION_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
DEV_USER_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
DEV_MEMBERSHIP_ID = uuid.UUID("30000000-0000-0000-0000-000000000001")
DEV_PIPELINE_ID = uuid.UUID("40000000-0000-0000-0000-000000000001")
DEV_STAGE_IDS = [uuid.UUID(f"41000000-0000-0000-0000-00000000000{i}") for i in range(1, 8)]

DEV_PASSWORD = os.getenv("AIVA_DEV_PASSWORD", "AivaDemo#2026")
SEED_DEMO = os.getenv("AIVA_SEED_DEMO", "true").lower() not in {"0", "false", "no"}

# (id suffix, email, name, title, role, team)
TEAM_USERS = [
    (2, "maya.chen@aiva.local", "Maya Chen", "VP of Sales", "manager", "Enterprise Sales"),
    (3, "daniel.ruiz@aiva.local", "Daniel Ruiz", "Account Executive", "member", "Enterprise Sales"),
    (4, "priya.raman@aiva.local", "Priya Raman", "Account Executive", "member", "Mid-Market Sales"),
    (5, "tom.becker@aiva.local", "Tom Becker", "Support Lead", "support_agent", "Customer Support"),
    (6, "aisha.okafor@aiva.local", "Aisha Okafor", "Support Specialist", "support_agent", "Customer Support"),
]

ACCOUNTS = [
    ("Northwind Logistics", "northwind.example", "Logistics", "customer", 820, 145_000_000, "Chicago", "IL", "USA"),
    ("Brightline Health", "brightlinehealth.example", "Healthcare", "customer", 2400, 610_000_000, "Boston", "MA", "USA"),
    ("Cobalt Robotics", "cobaltrobotics.example", "Manufacturing", "prospect", 310, 52_000_000, "Austin", "TX", "USA"),
    ("Fernbrook Financial", "fernbrook.example", "Financial Services", "customer", 1500, 420_000_000, "New York", "NY", "USA"),
    ("Helios Energy", "heliosenergy.example", "Energy", "prospect", 950, 230_000_000, "Denver", "CO", "USA"),
    ("Juniper Retail Group", "juniperretail.example", "Retail", "customer", 4100, 980_000_000, "Seattle", "WA", "USA"),
    ("Keystone Education", "keystone-edu.example", "Education", "lead", 600, 38_000_000, "Philadelphia", "PA", "USA"),
    ("Lumen Media", "lumenmedia.example", "Media", "partner", 220, 31_000_000, "Los Angeles", "CA", "USA"),
    ("Meridian Hotels", "meridianhotels.example", "Hospitality", "prospect", 3300, 510_000_000, "Miami", "FL", "USA"),
    ("Orchid Biotech", "orchidbio.example", "Biotechnology", "customer", 410, 88_000_000, "San Diego", "CA", "USA"),
    ("Pinecrest Construction", "pinecrest.example", "Construction", "churned", 700, 95_000_000, "Phoenix", "AZ", "USA"),
    ("Quanta Software", "quantasoft.example", "Software", "customer", 180, 24_000_000, "Toronto", "ON", "Canada"),
]

FIRST = ["Olivia", "Liam", "Emma", "Noah", "Sophia", "James", "Ava", "Lucas", "Mia", "Ethan", "Isabella", "Mason", "Zoe", "Logan", "Chloe", "Elijah", "Grace", "Henry"]
LAST = ["Johnson", "Patel", "Nguyen", "Garcia", "Kim", "Brown", "Martinez", "Lee", "Walker", "Hughes", "Rossi", "Fischer", "Silva", "Cohen", "Murphy", "Ito", "Novak", "Diaz"]
TITLES = ["CEO", "CFO", "VP of Operations", "Director of IT", "Head of Procurement", "Operations Manager", "IT Manager", "Chief Technology Officer", "Director of Finance"]

PRODUCTS = [
    ("AIVA Sales Cloud", "AIVA-SALES", "Subscriptions", 120, "monthly", "Per user. Pipeline, forecasting and quotes."),
    ("AIVA Service Desk", "AIVA-SERVICE", "Subscriptions", 95, "monthly", "Per agent. Cases, SLAs and knowledge base."),
    ("AIVA Analytics Pro", "AIVA-ANALYTICS", "Subscriptions", 45, "monthly", "Per user. Advanced reports and dashboards."),
    ("AIVA Automation Pack", "AIVA-AUTO", "Add-ons", 1500, "annual", "Unlimited workflow rules and runs."),
    ("Premier Support", "SUP-PREMIER", "Services", 12000, "annual", "24/7 support with a named engineer."),
    ("Implementation (standard)", "SVC-IMPL-STD", "Services", 15000, "one_time", "Six-week guided rollout."),
    ("Implementation (enterprise)", "SVC-IMPL-ENT", "Services", 45000, "one_time", "Twelve-week rollout with data migration."),
    ("Admin Training Workshop", "SVC-TRAIN", "Services", 3500, "one_time", "Two-day admin certification workshop."),
    ("Data Migration", "SVC-DATA", "Services", 8000, "one_time", "Import from Salesforce, HubSpot or spreadsheets."),
    ("Sandbox Environment", "AIVA-SANDBOX", "Add-ons", 6000, "annual", "Full-copy sandbox for testing."),
]

ARTICLES = [
    ("Reset your password", "Account & sign-in", "How to reset a forgotten password and unlock your account.",
     "If you forgot your password, choose **Forgot password** on the sign-in page and enter your work email.\n\nYou'll receive a link that works for one hour. After five failed sign-in attempts your account locks for 15 minutes; resetting your password unlocks it immediately.\n\n## Still stuck?\nAsk your workspace administrator to send you a reset link from **Settings → Users**."),
    ("Turn on two-step verification", "Account & sign-in", "Protect your account with an authenticator app.",
     "Go to **Settings → Security** and choose **Set up two-step verification**.\n\n1. Scan the QR code with Google Authenticator, Microsoft Authenticator or 1Password.\n2. Enter the 6-digit code to confirm.\n\nFrom then on you'll enter a code after your password each time you sign in."),
    ("Import contacts from a spreadsheet", "Data management", "Bring your existing contacts into AIVA.",
     "Prepare a CSV with columns for first name, last name, email and company.\n\nOpen **Contacts**, choose **Import**, map your columns and review duplicates before importing. Records with an existing email are updated instead of duplicated."),
    ("Set up SLA policies", "Service desk", "Define response and resolution targets by priority.",
     "Open **Settings → Service levels**. Each priority (urgent, high, medium, low) has a first-response and a resolution target.\n\nCases get due times when they're created and when their priority changes. The case list shows cases at risk and breached in red."),
    ("Build your first report", "Reporting", "Answer a question with the report builder in two minutes.",
     "Open **Reports → New report**, pick the records (for example Opportunities), choose columns, then add filters such as *Close date is this quarter*.\n\nGroup by a field (Stage, Owner, Month) to get a chart. Save the report and add it to a dashboard."),
    ("Automate follow-ups with workflows", "Automation", "Create tasks and notifications automatically.",
     "Workflows watch records and act when conditions match. Example: when an opportunity over $50,000 is created, create a task for the owner to book an executive sponsor call and notify their manager.\n\nOpen **Workflows → New workflow**, choose the record type, the trigger, conditions and actions."),
    ("Integration sync is delayed", "Troubleshooting", "What to check when email or calendar sync falls behind.",
     "Sync usually completes within five minutes. If it's delayed:\n\n- Check the connection status under **Settings → Integrations**.\n- Reconnect the account if the token expired.\n- Large mailboxes can take up to an hour on the first sync."),
    ("Export data for audits", "Data management", "Download records and the audit log.",
     "Administrators can review every change under **Settings → Audit log** and filter by user, record type or date.\n\nList views offer **Export CSV** for the records you can see."),
]

CASES = [
    ("Cannot sign in after password change", "urgent", "phone", "Account & sign-in", "open", 0),
    ("Dashboard charts not loading", "high", "web", "Reporting", "open", 1),
    ("Request to add 25 seats", "medium", "email", "Billing", "pending", 2),
    ("Email sync delayed by several hours", "high", "email", "Integrations", "new", 3),
    ("How do I build a pipeline report?", "low", "chat", "Reporting", "resolved", 4),
    ("Workflow created duplicate tasks", "medium", "portal", "Automation", "open", 5),
    ("SSO configuration question", "medium", "email", "Account & sign-in", "new", 6),
    ("Invoice shows the wrong billing address", "low", "email", "Billing", "closed", 7),
    ("Data import skipped 40 contacts", "high", "web", "Data management", "on_hold", 8),
    ("Mobile app crashes on case view", "urgent", "phone", "Mobile", "new", 9),
    ("Need audit log export for compliance", "medium", "email", "Data management", "resolved", 10),
    ("Quote PDF missing discount line", "high", "portal", "Quotes", "open", 11),
]


def now() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_users(db) -> dict[str, uuid.UUID]:
    organization = await db.get(Organization, DEV_ORGANIZATION_ID)
    if organization is None:
        organization = Organization(id=DEV_ORGANIZATION_ID, name="AIVA Development", slug="aiva-development", is_active=True, settings={})
        db.add(organization)
    organization.is_active = True
    await db.flush()

    teams: dict[str, Team] = {}
    for name, description in (
        ("Enterprise Sales", "Accounts over 1,000 employees."),
        ("Mid-Market Sales", "Accounts with 100–1,000 employees."),
        ("Customer Support", "Service desk queue."),
    ):
        team = (await db.execute(select(Team).where(Team.organization_id == DEV_ORGANIZATION_ID, Team.name == name))).scalar_one_or_none()
        if team is None:
            team = Team(id=uuid.uuid4(), organization_id=DEV_ORGANIZATION_ID, name=name, description=description)
            db.add(team)
        teams[name] = team
    await db.flush()

    users = [(DEV_USER_ID, "admin@aiva.local", "AIVA Administrator", "Workspace administrator", "owner", None)] + [
        (uuid.UUID(f"20000000-0000-0000-0000-00000000000{suffix}"), email, name, title, role, team)
        for suffix, email, name, title, role, team in TEAM_USERS
    ]
    ids: dict[str, uuid.UUID] = {}
    for user_id, email, name, title, role, team_name in users:
        user = await db.get(User, user_id)
        if user is None:
            user = User(id=user_id, email=email, full_name=name, is_active=True)
            db.add(user)
        user.is_active = True
        user.job_title = user.job_title or title
        if not user.password_hash:
            user.password_hash = hash_password(DEV_PASSWORD)
        await db.flush()

        membership = (
            await db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == DEV_ORGANIZATION_ID,
                    OrganizationMembership.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            membership = OrganizationMembership(
                id=DEV_MEMBERSHIP_ID if user_id == DEV_USER_ID else uuid.uuid4(),
                organization_id=DEV_ORGANIZATION_ID,
                user_id=user_id,
                role=role,
            )
            db.add(membership)
        membership.role = role
        membership.status = "active"
        membership.team_id = teams[team_name].id if team_name else None
        ids[name] = user_id

    teams["Enterprise Sales"].manager_user_id = ids["Maya Chen"]
    teams["Customer Support"].manager_user_id = ids["Tom Becker"]
    await provision_workspace(db, organization, DEV_USER_ID, pipeline_id=DEV_PIPELINE_ID, stage_ids=DEV_STAGE_IDS)

    # The demo workspace runs on Enterprise so every feature can be tried.
    subscription = (
        await db.execute(select(Subscription).where(Subscription.organization_id == DEV_ORGANIZATION_ID))
    ).scalar_one_or_none()
    if subscription is None:
        db.add(
            Subscription(
                id=uuid.uuid4(),
                organization_id=DEV_ORGANIZATION_ID,
                plan="enterprise",
                status="active",
                seats=len(users),
                provider="demo",
                billing_email="admin@aiva.local",
                current_period_start=now(),
                current_period_end=now() + timedelta(days=30),
            )
        )
    return ids


async def seed_demo(db, users: dict[str, uuid.UUID]) -> None:
    rng = random.Random(2026)
    org = DEV_ORGANIZATION_ID
    reps = [users["Daniel Ruiz"], users["Priya Raman"], users["Maya Chen"]]
    agents = [users["Tom Becker"], users["Aisha Okafor"]]
    admin = users["AIVA Administrator"]
    db.sync_session.info["automation_disabled"] = True
    today = date.today()

    stages = (
        await db.execute(select(PipelineStage).where(PipelineStage.pipeline_id == DEV_PIPELINE_ID).order_by(PipelineStage.position))
    ).scalars().all()
    open_stages = [s for s in stages if s.category == "open"]
    won = next(s for s in stages if s.category == "won")
    lost = next(s for s in stages if s.category == "lost")

    # Accounts and contacts
    accounts = []
    for index, (name, domain, industry, stage, employees, revenue, city, state, country) in enumerate(ACCOUNTS):
        account = Account(
            id=uuid.uuid4(), organization_id=org, name=name, domain=domain, website=f"https://{domain}",
            industry=industry, lifecycle_stage=stage, employee_count=employees, annual_revenue=Decimal(revenue),
            billing_city=city, billing_state=state, billing_country=country, owner_user_id=reps[index % len(reps)],
            phone=f"(555) 01{index:02d}-{1000 + index * 37}",
            description=f"{industry} company headquartered in {city}.",
        )
        accounts.append(account)
    db.add_all(accounts)
    await db.flush()

    contacts = []
    for index in range(18):
        account = accounts[index % len(accounts)]
        first, last = FIRST[index], LAST[index]
        contacts.append(
            Contact(
                id=uuid.uuid4(), organization_id=org, account_id=account.id, owner_user_id=account.owner_user_id,
                first_name=first, last_name=last, email=f"{first.lower()}.{last.lower()}@{account.domain}",
                phone=f"(555) 02{index:02d}-{2000 + index * 41}", job_title=TITLES[index % len(TITLES)],
                is_primary=index < len(accounts), segment="enterprise" if account.employee_count > 1000 else "mid_market",
                tags=["decision maker"] if "Chief" in TITLES[index % len(TITLES)] or TITLES[index % len(TITLES)] in {"CEO", "CFO"} else [],
            )
        )
    db.add_all(contacts)
    await db.flush()

    # Opportunities across stages, including closed deals for trend reports
    opportunities = []
    names = ["Platform expansion", "Service desk rollout", "Analytics upgrade", "Renewal 2027", "New region launch", "Support tier upgrade", "Automation pilot", "Data migration project"]
    for index in range(18):
        account = accounts[index % len(accounts)]
        amount = Decimal(rng.choice([18_000, 24_500, 36_000, 48_000, 62_000, 75_000, 96_000, 120_000, 185_000]))
        if index < 11:
            stage = open_stages[index % len(open_stages)]
            closed_at, close_date = None, today + timedelta(days=rng.randint(5, 110))
            forecast = "commit" if stage.position >= 4 else "best_case" if stage.position == 3 else "pipeline"
        elif index < 16:
            stage = won
            closed_at = now() - timedelta(days=(4, 11, 48, 95, 150)[index - 11])
            close_date = closed_at.date()
            forecast = "closed"
        else:
            stage = lost
            closed_at = now() - timedelta(days=rng.randint(10, 120))
            close_date = closed_at.date()
            forecast = "omitted"
        opportunities.append(
            Opportunity(
                id=uuid.uuid4(), organization_id=org, name=f"{account.name.split()[0]} — {names[index % len(names)]}",
                account_id=account.id, primary_contact_id=contacts[index % len(contacts)].id, pipeline_id=DEV_PIPELINE_ID,
                stage_id=stage.id, owner_user_id=account.owner_user_id, amount=amount, currency="USD",
                probability=stage.probability, expected_close_date=close_date, closed_at=closed_at,
                opportunity_type=rng.choice(["new_business", "upsell", "renewal", "expansion"]),
                lead_source=rng.choice(["Website", "Referral", "Event", "Outbound"]),
                priority=rng.choice(["medium", "high", "high", "critical"]) if amount > 60_000 else "medium",
                forecast_category=forecast,
                next_step="Schedule technical review" if stage.category == "open" else None,
                loss_reason="Chose a lower-priced competitor" if stage.category == "lost" else None,
                competitor="Salesforce" if index % 3 == 0 else "HubSpot" if index % 3 == 1 else None,
                stage_entered_at=now() - timedelta(days=rng.randint(1, 40)),
            )
        )
    db.add_all(opportunities)
    await db.flush()

    # Leads, scored with the workspace rules
    rules = [
        {"name": r.name, "field": r.field, "operator": r.operator, "value": r.value, "points": r.points}
        for r in (await db.execute(select(LeadScoringRule).where(LeadScoringRule.organization_id == org))).scalars()
    ]
    lead_rows = [
        ("Harper", "Lin", "Director of Operations", "Atlas Freight", "referral", "qualified"),
        ("Mateo", "Alvarez", "VP Sales", "Summit Outdoor", "website", "contacted"),
        ("Nora", "Schmidt", "Chief Revenue Officer", "Vantage Labs", "event", "qualified"),
        ("Owen", "Clarke", "Sales Manager", "Redwood Dental", "website", "new"),
        ("Leah", "Morgan", "IT Director", "Granite Bank", "referral", "contacted"),
        ("Samuel", "Osei", "Operations Lead", "Cedar Logistics", "outbound", "new"),
        ("Isla", "Ford", "Chief Operating Officer", "Harbor Insurance", "website", "qualified"),
        ("Felix", "Wagner", "Analyst", "Bluebird Travel", "social", "new"),
        ("Ruby", "Tanaka", "VP Customer Success", "Spark Fitness", "event", "contacted"),
        ("Jonah", "Price", "Office Manager", "Maple Clinics", "website", "nurture"),
        ("Clara", "Weiss", "Director of Finance", "Oakline Capital", "referral", "new"),
        ("Andre", "Mensah", "Procurement Specialist", "Ironclad Security", "outbound", "unqualified"),
        ("Priya", "Shah", "Head of Support", "Nimbus Cloud", "website", "contacted"),
        ("Diego", "Fuentes", "CTO", "Pixel Forge", "referral", "new"),
        ("Hana", "Yamamoto", "Marketing Coordinator", "Lotus Spa Group", "social", "new"),
    ]
    for index, (first, last, title, company, source, lead_status) in enumerate(lead_rows):
        lead = Lead(
            id=uuid.uuid4(), organization_id=org, first_name=first, last_name=last, job_title=title,
            company_name=company, source=source, status=lead_status,
            email=f"{first.lower()}@{company.split()[0].lower()}.example" if index % 5 != 4 else None,
            phone=f"(555) 03{index:02d}-{3000 + index * 13}" if index % 3 != 2 else None,
            owner_user_id=reps[index % len(reps)],
        )
        lead.created_at = now() - timedelta(days=rng.randint(0, 45))
        lead.score, _ = score_lead(lead, rules)
        db.add(lead)

    # Tasks and activities
    task_titles = ["Send proposal", "Book executive sponsor call", "Follow up on pricing questions", "Prepare demo environment",
                   "Confirm legal review timeline", "Share security questionnaire", "Check in after onboarding", "Review renewal terms"]
    for index in range(16):
        opportunity = opportunities[index % 11]
        due = now() + timedelta(days=rng.randint(-4, 12), hours=rng.randint(0, 8))
        done = index % 6 == 5
        db.add(
            Task(
                id=uuid.uuid4(), organization_id=org, title=task_titles[index % len(task_titles)],
                description=f"Related to {opportunity.name}.", task_type=rng.choice(["call", "email", "follow_up", "proposal", "demo"]),
                status="completed" if done else rng.choice(["open", "open", "in_progress"]),
                priority=rng.choice(["medium", "high", "urgent", "low"]), source="manual",
                due_at=due, completed_at=due if done else None, opportunity_id=opportunity.id, account_id=opportunity.account_id,
                assigned_to_user_id=admin if index % 4 == 0 else opportunity.owner_user_id, created_by_user_id=admin,
                is_recurring=False, is_ai_generated=False, tags=[], task_metadata={},
            )
        )

    subjects = [("call", "Discovery call"), ("email", "Sent pricing overview"), ("meeting", "On-site workshop"),
                ("note", "Budget approved for Q1"), ("email", "Shared case study"), ("call", "Negotiation call")]
    for index in range(24):
        opportunity = opportunities[index % len(opportunities)]
        kind, subject = subjects[index % len(subjects)]
        db.add(
            Activity(
                id=uuid.uuid4(), organization_id=org, account_id=opportunity.account_id, opportunity_id=opportunity.id,
                contact_id=opportunity.primary_contact_id, created_by_user_id=opportunity.owner_user_id,
                activity_type=kind, subject=subject, body=f"{subject} with {accounts[index % len(accounts)].name}.",
                direction="outbound" if kind in {"call", "email"} else None,
                occurred_at=now() - timedelta(days=rng.randint(0, 30), hours=rng.randint(0, 10)),
            )
        )

    # Products and quotes
    products = []
    for name, sku, family, price, billing, description in PRODUCTS:
        products.append(
            Product(id=uuid.uuid4(), organization_id=org, name=name, sku=sku, family=family, unit_price=Decimal(price),
                    billing_frequency=billing, description=description, currency="USD", tax_rate=Decimal(0), is_active=True)
        )
    db.add_all(products)
    await db.flush()

    for number, (opportunity, status, lines) in enumerate(
        (
            (opportunities[3], "sent", [(0, 40, 10), (4, 1, 0), (5, 1, 0)]),
            (opportunities[4], "draft", [(1, 15, 0), (7, 2, 0)]),
            (opportunities[11], "accepted", [(0, 60, 15), (2, 60, 15), (6, 1, 5)]),
            (opportunities[1], "sent", [(1, 25, 5), (3, 1, 0), (8, 1, 0)]),
        ),
        start=1001,
    ):
        quote = Quote(
            id=uuid.uuid4(), organization_id=org, number=number, name=f"{opportunity.name} proposal",
            status=status, opportunity_id=opportunity.id, account_id=opportunity.account_id,
            contact_id=opportunity.primary_contact_id, owner_user_id=opportunity.owner_user_id,
            valid_until=today + timedelta(days=30), currency="USD",
            terms="Net 30. Prices in USD. Subscription fees are billed annually in advance.",
            sent_at=now() - timedelta(days=3) if status in {"sent", "accepted"} else None,
            accepted_at=now() - timedelta(days=1) if status == "accepted" else None,
        )
        quote.created_at = now() - timedelta(days=5)
        quote.updated_at = quote.accepted_at or quote.sent_at or quote.created_at
        quote.line_items = []
        subtotal = discount_total = Decimal(0)
        for position, (product_index, quantity, discount) in enumerate(lines):
            product = products[product_index]
            months = 12 if product.billing_frequency == "monthly" else 1
            unit = product.unit_price * months
            gross = unit * quantity
            line_discount = (gross * discount / 100).quantize(Decimal("0.01"))
            subtotal += gross
            discount_total += line_discount
            quote.line_items.append(
                QuoteLineItem(
                    id=uuid.uuid4(), organization_id=org, product_id=product.id, name=product.name,
                    description=f"{product.description} {'Annual (12 months).' if months == 12 else ''}".strip(),
                    quantity=Decimal(quantity), unit_price=unit, discount_percent=Decimal(discount), tax_percent=Decimal(0),
                    position=position, line_total=gross - line_discount,
                )
            )
        quote.subtotal, quote.discount_total, quote.tax_total = subtotal, discount_total, Decimal(0)
        quote.total = subtotal - discount_total
        db.add(quote)

    # Knowledge base
    articles = []
    for title, category, summary, body in ARTICLES:
        article = KnowledgeArticle(
            id=uuid.uuid4(), organization_id=org, title=title, slug=title.lower().replace(" ", "-").replace("?", ""),
            summary=summary, body=body, category=category, status="published", tags=[],
            author_user_id=users["Tom Becker"], view_count=rng.randint(20, 400), helpful_count=rng.randint(5, 60),
            not_helpful_count=rng.randint(0, 6), published_at=now() - timedelta(days=rng.randint(5, 120)),
        )
        articles.append(article)
    db.add_all(articles)

    # Cases with SLA due dates and conversations
    policies = {
        p.priority: p
        for p in (await db.execute(select(SlaPolicy).where(SlaPolicy.organization_id == org))).scalars()
    }
    support_team = (await db.execute(select(Team).where(Team.organization_id == org, Team.name == "Customer Support"))).scalar_one()
    for offset, (subject, priority, channel, category, status, index) in enumerate(CASES):
        created = now() - timedelta(hours=rng.choice([1, 3, 6, 20, 30, 50, 75, 120]) + offset)
        policy = policies.get(priority)
        contact = contacts[index % len(contacts)]
        responded = status not in {"new"}
        case = Case(
            id=uuid.uuid4(), organization_id=org, number=1001 + offset, subject=subject,
            description=f"{contact.first_name} reported: {subject.lower()}. Impacting their team today.",
            status=status, priority=priority, channel=channel, category=category, account_id=contact.account_id,
            contact_id=contact.id, assigned_to_user_id=None if status == "new" and offset % 2 else agents[offset % 2],
            team_id=support_team.id, sla_policy_id=policy.id if policy else None, created_by_user_id=agents[0],
            first_response_due_at=created + timedelta(minutes=policy.first_response_minutes) if policy else None,
            resolution_due_at=created + timedelta(minutes=policy.resolution_minutes) if policy else None,
            first_responded_at=created + timedelta(minutes=rng.randint(10, 90)) if responded else None,
            resolved_at=created + timedelta(hours=rng.randint(2, 30)) if status in {"resolved", "closed"} else None,
            closed_at=created + timedelta(hours=36) if status == "closed" else None,
            satisfaction_rating=rng.choice([4, 5, 5, 3]) if status in {"resolved", "closed"} else None,
            tags=[], linked_article_ids=[],
        )
        case.created_at = created
        case.updated_at = case.resolved_at or case.first_responded_at or created
        db.add(case)
        await db.flush()
        if responded:
            reply = CaseComment(id=uuid.uuid4(), organization_id=org, case_id=case.id, author_user_id=agents[offset % 2],
                                body=f"Thanks for reaching out, {contact.first_name}. I'm looking into this now and will update you shortly.",
                                is_internal=False)
            reply.created_at = case.first_responded_at
            note = CaseComment(id=uuid.uuid4(), organization_id=org, case_id=case.id, author_user_id=agents[offset % 2],
                               body="Reproduced in staging. Checking recent deploys before escalating to engineering.",
                               is_internal=True)
            note.created_at = case.first_responded_at + timedelta(minutes=12)
            db.add_all([reply, note])

    # Workflows
    db.add_all([
        WorkflowRule(
            id=uuid.uuid4(), organization_id=org, name="Large deal: book an executive sponsor call",
            description="When an opportunity over $50,000 is created, the owner gets a task and Maya is notified.",
            object_type="opportunity", trigger_event="created",
            conditions=[{"field": "amount", "operator": "greater_than", "value": "50000"}],
            actions=[
                {"type": "create_task", "params": {"title": "Book executive sponsor call for {{name}}", "due_in_days": 2, "priority": "high", "assign_to": "owner"}},
                {"type": "notify", "params": {"recipient": str(users["Maya Chen"]), "title": "New large deal: {{name}}", "message": "A new opportunity worth ${{amount}} was created."}},
            ],
            is_active=True, created_by_user_id=admin,
        ),
        WorkflowRule(
            id=uuid.uuid4(), organization_id=org, name="Urgent case: alert the support lead",
            description="Every new urgent case notifies Tom immediately.",
            object_type="case", trigger_event="created_or_updated",
            conditions=[{"field": "priority", "operator": "equals", "value": "urgent"}, {"field": "status", "operator": "in", "value": "new,open"}],
            actions=[{"type": "notify", "params": {"recipient": str(users["Tom Becker"]), "title": "Urgent case: {{record}}", "message": "Check the case and confirm someone is on it."}}],
            is_active=True, created_by_user_id=admin,
        ),
        WorkflowRule(
            id=uuid.uuid4(), organization_id=org, name="Qualified lead: schedule discovery",
            description="When a lead becomes qualified, its owner gets a discovery call task.",
            object_type="lead", trigger_event="field_changed", trigger_field="status",
            conditions=[{"field": "status", "operator": "equals", "value": "qualified"}],
            actions=[{"type": "create_task", "params": {"title": "Discovery call with {{first_name}} {{last_name}}", "due_in_days": 1, "priority": "high", "assign_to": "owner", "task_type": "call"}}],
            is_active=True, created_by_user_id=admin,
        ),
    ])

    # Quotas for this quarter
    quarter = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    for user_id, amount in ((users["Daniel Ruiz"], 250_000), (users["Priya Raman"], 200_000), (users["Maya Chen"], 300_000)):
        db.add(ForecastQuota(id=uuid.uuid4(), organization_id=org, user_id=user_id, period=quarter, amount=Decimal(amount)))

    db.sync_session.info["automation_disabled"] = False


def known_users() -> dict[str, uuid.UUID]:
    users = {"AIVA Administrator": DEV_USER_ID}
    for suffix, _, name, *_ in TEAM_USERS:
        users[name] = uuid.UUID(f"20000000-0000-0000-0000-00000000000{suffix}")
    return users


async def add_edition_demo(db) -> None:
    """Demo content for plan features; runs once, also on existing demo workspaces."""

    if not SEED_DEMO:
        return
    users = known_users()
    present = {
        user_id
        for user_id in (
            await db.execute(select(OrganizationMembership.user_id).where(OrganizationMembership.organization_id == DEV_ORGANIZATION_ID))
        ).scalars()
    }
    if not set(users.values()) <= present:
        return
    if await seed_editions(db, users, DEV_ORGANIZATION_ID):
        await db.commit()
        print("Demo content for Starter, Pro and Enterprise features created.")
    try:
        if await seed_workspace(users, DEV_ORGANIZATION_ID):
            print("Demo content for orders, purchasing, meetings, documents, services, projects, visits, email and chat created.")
    except Exception as exc:  # noqa: BLE001 - demo extras must never stop startup
        print(f"Demo content for the workspace modules skipped: {exc}")
    try:
        if await seed_settings_suite(users, DEV_ORGANIZATION_ID):
            print("Demo content for sequences, territories, personas, signals, goals, data requests and conversations created.")
    except Exception as exc:  # noqa: BLE001 - demo extras must never stop startup
        print(f"Demo content for the settings suite skipped: {exc}")


async def bootstrap(if_new: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        if if_new and await db.get(Organization, DEV_ORGANIZATION_ID) is not None:
            # Hosted demos call this on every start; leave existing data alone,
            # apart from adding demo content for features added since.
            subscription = (
                await db.execute(select(Subscription).where(Subscription.organization_id == DEV_ORGANIZATION_ID))
            ).scalar_one_or_none()
            if subscription is None:
                db.add(
                    Subscription(
                        id=uuid.uuid4(), organization_id=DEV_ORGANIZATION_ID, plan="enterprise", status="active",
                        seats=len(known_users()), provider="demo", billing_email="admin@aiva.local",
                        current_period_start=now(), current_period_end=now() + timedelta(days=30),
                    )
                )
                await db.commit()
            await add_edition_demo(db)
            print("Demo workspace already exists; skipping seed.")
            return
        users = await upsert_users(db)
        await db.commit()

        has_data = (await db.execute(select(func.count()).select_from(Account).where(Account.organization_id == DEV_ORGANIZATION_ID))).scalar_one()
        if SEED_DEMO and not has_data:
            await seed_demo(db, users)
            await db.commit()
            print("Demo data created.")
        await add_edition_demo(db)

    print("AIVA development workspace ready.")
    print(f"Organization ID: {DEV_ORGANIZATION_ID}")
    print(f"Pipeline ID: {DEV_PIPELINE_ID}")
    print(f"Sign in at {os.getenv('APP_BASE_URL', 'http://localhost:3000')}/login")
    print("  admin@aiva.local (owner), maya.chen@aiva.local (sales manager),")
    print("  daniel.ruiz@aiva.local (sales rep), tom.becker@aiva.local (support)")
    if os.getenv("AIVA_DEV_PASSWORD"):
        print("  Password for all demo users: the value of AIVA_DEV_PASSWORD")
    else:
        print(f"  Password for all demo users: {DEV_PASSWORD}  (set AIVA_DEV_PASSWORD to change it)")


if __name__ == "__main__":
    asyncio.run(bootstrap(if_new="--if-new" in sys.argv))

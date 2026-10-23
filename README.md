# AIVA CRM

**Sales, service, analytics and automation in one CRM workspace.**

AIVA CRM is a multi-tenant CRM platform modeled on the feature set of the leading CRMs (Salesforce, HubSpot, Microsoft Dynamics 365, ServiceNow, Pipedrive, Zoho): secure sign-in with roles and two-step verification, a full sales cloud (leads to quotes, invoices and forecast), marketing (mass email, web forms, journeys, ABM), a service desk with SLAs, a knowledge base and customer portals, AI agents and predictions, a report builder with dashboards, and a no-code workflow engine — sold in three plans with a free trial.

**Project status:** Active development. Everything listed as *Available* below is implemented end-to-end (database, API, permissions, UI) and covered by tests.

## Plans and pricing

Prices are per user per month. Every new workspace starts a **7-day free trial with everything in Enterprise** — no card needed. When the trial ends the workspace becomes read-only until someone picks a plan; nothing is deleted.

| | Starter — $19 | Pro — $29 | Enterprise — $59 |
|---|---|---|---|
| Core CRM | Leads, contacts, accounts, deals, tasks, meetings and calls, follow-up reminders, standard reports, data import/export, mobile app (PWA), REST API | Everything in Starter | Everything in Pro |
| Sales | Multiple pipelines, sales forecasting, lead scoring, data enrichment | CPQ (quotes, products, invoices), inventory, predictive intelligence, unlimited reports | Approvals, QuickBooks export |
| Marketing | Mass email, email templates, web forms, calendar sync | Customer journeys, Google Ads integration | Account-based marketing (ABM) |
| Service | Service desk, mailbox sync (Gmail, Outlook, SMTP/IMAP) | Customer portal, web-to-case, custom (partner) portals | — |
| Automation and AI | Workflow automation | AI agents, process management (stage playbooks) | Extended AI (summaries, reply drafts, Ask AIVA) |
| Customization | Custom modules, Slack/Teams/Zoom, HIPAA mode | — | Extended field types, sequential (multi-step) forms, custom buttons and functions, field-level encryption, sandbox, multiple teams, reporting hierarchy, single sign-on |
| Limits | 5 pipelines, 3 custom modules, 25 saved reports, 5 dashboards, 5 web forms | 20 custom modules; everything else unlimited | Unlimited |

Plan changes are made in **Settings → Billing and plan** (owners and admins). Without Stripe keys the server runs *demo billing*: switching plans applies instantly and records an invoice with no charge. With `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` set, upgrades go through Stripe Checkout and the Stripe customer portal. The public price list is at `/pricing`.

Features outside a workspace's plan show a lock in the navigation and an upgrade card instead of the page; the API returns `402 Payment Required` for them.

## Feature overview

### Sign-in and security

| Feature | What it does |
|---|---|
| Sign in / sign up | Email + password sign-in, "keep me signed in", self-serve workspace creation |
| Google, Microsoft and Apple | "Log In with Google / Microsoft / Apple" and "Sign up with …": OpenID Connect with PKCE, verified emails only, linked to the existing account when the email matches, pending invitations accepted automatically |
| Log In with your Organization | Workspace single sign-on (Enterprise) with Okta, Microsoft Entra ID, Google Workspace or any OpenID Connect provider: people enter their work email and are sent to their company's sign-in; optional automatic accounts with a default role; those sessions only open that workspace |
| Two-step verification (MFA) | TOTP authenticator apps (Google/Microsoft Authenticator, 1Password) with QR setup |
| Password policy | 10+ characters, 3 of 4 character types, can't contain the email name |
| Account lockout | 5 failed attempts lock the account for 15 minutes; admins can unlock |
| Password reset | Expiring single-use reset links; all other sessions are signed out |
| Invitations | Admins invite by email with a role and team; invitees set their own password |
| Session management | httpOnly cookie sessions, rotating refresh tokens, "where you're signed in" list with remote sign-out, workspace-configurable session length |
| Multiple workspaces | One login can belong to several organizations and switch between them |

### Administration

| Feature | What it does |
|---|---|
| Users | Invite, change role/team, deactivate, reactivate/unlock, send password resets, remove |
| Roles & permissions | Six built-in roles (Owner, Administrator, Sales manager, Sales representative, Support agent, Read only) plus custom roles from a 34-permission catalog |
| Teams | Teams with managers; used for case queues and reporting |
| Audit log | Every sign-in (and failed sign-in), permission change and record create/update/delete with before → after values, IP and device; filter and export |
| Organization settings | Name, currency, time zone, fiscal year start, session length |
| Billing and plan | 7-day trial, Starter / Pro / Enterprise, seats, invoices, cancel and resume, Stripe or demo billing |
| Fields and modules | Custom fields on leads, contacts, accounts, deals and cases (16 types incl. currency, lookup, formula, auto-number); custom modules with their own records, multi-step forms and encrypted fields |
| Hierarchy and visibility | Reporting lines, nested teams, people on several teams; record visibility by everyone / team / reporting hierarchy |
| Sales processes and approvals | Stage playbooks (required fields, checklists, guidance); approval rules for stage changes and quotes with an approvals inbox |
| Custom buttons | Record-page buttons that open a link, call a signed webhook, or run a function (set fields, create tasks) |
| API keys | Personal read or read/write keys for the REST API (`Authorization: Bearer aiva_sk_…`) |
| Import and export | CSV import with column mapping and duplicate handling; CSV export of every record type and custom module |
| Compliance and sandbox | HIPAA mode (MFA required, shorter sessions, view logging); sandboxes with deploy-to-production |

### Sales

| Module | Capabilities | Status |
|---|---|---|
| Leads | Capture, qualify, convert; automatic lead scoring (A–D grades) from configurable rules | Available |
| Accounts & Contacts | Company and people records with ownership, hierarchy, duplicate checks | Available |
| Opportunities & Pipeline | Multiple pipelines and stages, board view, bulk actions, saved views, forecast categories | Available |
| Products & price book | Catalog with SKUs, families, billing frequency, default tax | Available |
| Price books | Named price lists per customer segment or currency with list prices and volume tiers (e.g. 10–49 units at 8% off); pick one on a quote or sales order and every line is priced from it | Pro |
| Quotes (CPQ) | Quote builder with catalog lines, per-line discount and tax, totals, send/accept/decline with approval permission, duplicate, print/PDF; accepting updates the deal amount | Available |
| Sales orders | Create from an accepted quote or from scratch; confirm, fulfil (books stock out), cancel; past-due tracking; one click to invoice the order | Pro |
| Vendors & purchase orders | Vendor records with contacts, terms and spend; purchase orders that are issued to the vendor, received in part or in full (stock goes back on the shelf and the product cost updates) or cancelled; reorder low-stock items in one click | Pro |
| Invoices and inventory | Invoices from accepted quotes, payments, overdue tracking, QuickBooks Online CSV export; stock levels, receipts and adjustments, automatic stock-out on accepted quotes, low-stock alerts | Available |
| Forecasting and predictions | Fiscal-quarter forecast by rep with quotas; win probability and predicted close date for every open deal, learned from your closed deals | Available |
| Calls | Click-to-call with a call timer, outcomes, notes and follow-up tasks; call log with connect rate and talk time | Available |
| Meetings | Week calendar and agenda; invite colleagues and customers (email invites carry an `.ics` calendar file), RSVPs, email reminders, video-call links, notes and outcomes logged on the lead, contact, account or deal | Available |

### Service

| Module | Capabilities | Status |
|---|---|---|
| Cases | Queues (my open, unassigned, past SLA…), priorities, channels, assignment, customer replies and internal notes, CSAT | Available |
| SLA policies | First-response and resolution targets by priority with live countdowns and breach tracking | Available |
| Knowledge base | Markdown articles with drafts, publishing, categories, view counts, helpful votes; suggested articles on every case | Available |
| Services & appointments | Catalog of the services you deliver (duration, price, buffer, who can deliver it); booking calendar that checks staff availability (including buffer time) and tracks completed, cancelled and no-show appointments | Pro |
| Projects | Delivery projects started from won deals, with milestones, tasks, a team, progress and overdue tracking | Pro |
| Portals | Customer portal (open and follow cases, help articles, accept quotes, see invoices) and partner portal (deal registration), with passwordless email sign-in | Available |

### Marketing

| Module | Capabilities | Status |
|---|---|---|
| Email templates and campaigns | Templates with merge fields, audiences from record filters, test sends, scheduling, open tracking and one-click unsubscribe | Available |
| Web forms | Hosted or embeddable lead and case forms with multi-step layouts, spam protection and UTM/gclid capture | Available |
| Customer journeys | Multi-step nurture: send email, wait, create task, update field, notify, branch | Available |
| Account-based marketing | Target accounts by tier with engagement scores and buying-committee coverage | Available |
| Google Ads | Lead-form webhook and offline-conversion export for closed deals | Available |
| Social | Connect LinkedIn, X, Facebook and Instagram; compose once, publish now or schedule, see per-network results and retry failures | Pro |
| Website visits | A small tracking script for your website: visitors, sessions, pages read, time on page, referrers and UTM sources; visitors who submit a form are matched to their lead or contact, and you can create a lead from any known visitor | Pro |

### Insights and automation

| Module | Capabilities | Status |
|---|---|---|
| Home | Live KPIs, items needing attention, pipeline by stage, deals closing soon, my tasks, hottest leads, recent activity | Available |
| Reports | Report builder over 9 record types: filters (incl. relative dates like "this quarter"), grouping by field or date, sum/avg/min/max, bar/line/donut/number charts, CSV export, folders, sharing | Available |
| Dashboards | Boards of saved reports with resizable, reorderable widgets | Available |
| Workflows | "When a record is created/updated/field changes, and conditions match, then…" create a task, update a field, change owner or send a notification; run history | Available |
| Global search & notifications | ⌘K search across records, cases, quotes, articles and products; in-app notification bell | Available |
| Workspace shell | Floating top navigation with section tabs, an icon rail for Home, Sales, Service, Insights and Settings, and light and dark themes (follows your system until you pick one) | Available |
| Engagement | Activities, tasks with reminders, email threads, mailbox sync, calendar feed (iCal), Slack/Teams alerts, Zoom links | Available |
| SalesInbox | Email sorted into Deals, Contacts, Leads and Others by who it's with, so customer conversations come first | Available |
| Documents | Folders, uploads up to 25 MB, versions with notes, in-browser preview, private or shared, linked to any account, contact, deal, order or case | Starter |
| Team chat & people | Channels and direct messages with @mentions and record links; a people directory with roles, teams and reporting lines | Starter |
| My Pins | Pin any record and reach it from the top bar | Available |
| AI agents | Lead qualifier, deal coach, case triage, follow-up writer and data hygiene agents leave suggestions you accept or dismiss | Available |
| Extended AI | Record summaries, email and case-reply drafts, and "Ask AIVA" questions about your CRM — Claude when `ANTHROPIC_API_KEY` is set, built-in rules otherwise | Available |
| Mobile app | Installable on iPhone, Android and desktop (PWA) with offline fallback | Available |

## Technology stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Manrope (self-hosted) |
| Backend | Python, FastAPI, Pydantic v2 |
| Database | PostgreSQL with SQLAlchemy 2 (async) and Alembic migrations |
| Cache | Redis |
| Auth | Argon2 password hashing, short-lived JWT access tokens, rotating opaque refresh tokens, TOTP MFA |
| Infrastructure | Docker Compose |

## Architecture

```text
Browser ──► Next.js (pages + /api/session, /api/aiva proxy)
                │   httpOnly cookies hold the session; the proxy
                │   attaches credentials and refreshes tokens
                ▼
           FastAPI  /api/v1
                │   permission checks per route (roles → permissions)
                │   flush hook: audit log, record owner, lead scoring, workflows
                ▼
       PostgreSQL (multi-tenant by organization)      Redis
```

## Repository structure

```text
AIVA-CRM/
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   ├── api/routes/      auth, admin, sales, service, analytics, billing, marketing,
│   │   │   │                    customization, data, integrations, intelligence, operations,
│   │   │   │                    enterprise, portals, public + CRM records
│   │   │   ├── core/            config, security, permissions, plans, encryption, TOTP
│   │   │   ├── models/          SQLAlchemy models
│   │   │   ├── services/        automation, reporting, billing, mailer, AI agents, predictions,
│   │   │   │                    journeys, approvals, sandbox, background scheduler
│   │   │   └── scripts/         bootstrap_dev (demo workspace)
│   │   ├── alembic/             migrations
│   │   └── tests/               unit + PostgreSQL integration tests
│   └── web/
│       └── src/
│           ├── app/             pages (login, home, cases, quotes, reports, settings, …)
│           ├── components/      app shell, UI kit, charts, builders
│           └── lib/             API client, session, formatting
├── docker-compose.yml
├── .env.example
└── README.md
```

## Getting started

### Prerequisites

- Docker and Docker Compose (or Python 3.13+, Node 20.9+, PostgreSQL 16+ and Redis 7+ for a manual setup)

### 1. Configure

```bash
git clone https://github.com/BaharathBathula/AIVA-CRM.git
cd AIVA-CRM
cp .env.example .env
# Set JWT_SECRET_KEY to a long random value, e.g. `openssl rand -hex 32`
```

Never commit `.env` or production credentials.

### 2. Start a fresh development environment

```bash
export JWT_SECRET_KEY=$(openssl rand -hex 32)
docker compose up -d postgres redis
docker compose run --rm migrate
docker compose run --rm bootstrap
docker compose up -d --no-deps api web
```

The bootstrap step seeds a demo workspace and is meant for a fresh database.

- App: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/api/v1/health>

### 3. Sign in with the demo workspace

All demo users share the password `AivaDemo#2026` (override with `AIVA_DEV_PASSWORD` when bootstrapping).

| Email | Role | Good for trying |
|---|---|---|
| `admin@aiva.local` | Owner | Everything, including users, roles and the audit log |
| `maya.chen@aiva.local` | Sales manager | Forecast quotas, quote approval, reports |
| `priya.raman@aiva.local` / `daniel.ruiz@aiva.local` | Sales representative | Leads, deals, quotes |
| `tom.becker@aiva.local` / `aisha.okafor@aiva.local` | Support agent | Cases, SLAs, knowledge base |

In development, invitation and password-reset links are shown on screen because no email service is configured.

The demo workspace is on the Enterprise plan with sample data for every feature: a custom module, campaigns, a web form, a journey, target accounts, invoices, tracked inventory, approval rules and a customer portal (Olivia Johnson at Northwind Logistics has access). New sign-ups start a 7-day trial.

### Optional configuration

| Variable | Purpose |
|---|---|
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Real card billing through Stripe Checkout (otherwise demo billing). Point the Stripe webhook at `/api/aiva/public/stripe/webhook`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_SECURITY` | Server email for invitations, reminders, campaigns and portal sign-in links. People can also connect their own mailbox in Settings → Integrations. |
| `ANTHROPIC_API_KEY`, `AI_MODEL` | Claude for summaries, drafts and Ask AIVA. Without it, AI features use built-in rules. |
| `FIELD_ENCRYPTION_KEY` | Key for encrypted fields and stored secrets. Defaults to one derived from `JWT_SECRET_KEY`; set it explicitly in production and keep it safe — changing it makes encrypted values unreadable. |
| `APP_BASE_URL` | Public URL used in emails, portal links, calendar feeds and webhooks (Render sets it automatically). |
| `SCHEDULER_ENABLED` | Background jobs for reminders, campaigns, journeys and mailbox sync (default on). |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Turns on "Log In with Google". |
| `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID` | Turns on "Log In with Microsoft" (tenant `common` allows work and personal accounts). |
| `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` | Turns on "Sign in with Apple" (Services ID, team ID, key ID and the `.p8` key contents). |

### Tracking website visits

In **Marketing → Visits**, add your website and copy its tracking code into every page, just before `</body>`:

```html
<script async src="https://YOUR-AIVA-ADDRESS/aiva-track.js" data-site="ws_..."></script>
```

The script uses first-party storage only and honours the browser's Do Not Track setting. When a visitor submits a form with an email address they're matched to their lead or contact; your site can also call `window.aiva.identify(email, name)` after sign-in.

### Publishing to social networks

In **Marketing → Social**, connect each account with an access token from that network's developer portal. Tokens are stored encrypted and checked when you connect.

| Network | What to paste | Token permissions |
|---|---|---|
| LinkedIn | Organization or member ID (the number in `urn:li:organization:…`) | `w_organization_social` (company pages) or `w_member_social` |
| X | Optional handle | OAuth 2.0 user token with `tweet.write`, `tweet.read`, `users.read` |
| Facebook | Page ID | Page access token with `pages_manage_posts` |
| Instagram | Instagram business account ID | Token with `instagram_content_publish`; every post needs a public image URL |

Scheduled posts go out within a minute of their time while the background scheduler is on.

### Turning on Google, Microsoft and Apple sign-in

Each button shows on the sign-in page and explains itself until its credentials are set. Register this redirect URI with each provider, using your site's address:

```text
https://<your-site>/api/session/oauth/google/callback
https://<your-site>/api/session/oauth/microsoft/callback
https://<your-site>/api/session/oauth/apple/callback
```

- **Google:** Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID (Web application). Add the redirect URI, then set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
- **Microsoft:** Microsoft Entra admin center → App registrations → New registration. Supported accounts: "Any organizational directory and personal Microsoft accounts"; platform Web with the redirect URI. Create a client secret under Certificates & secrets, then set `MICROSOFT_CLIENT_ID` (Application ID) and `MICROSOFT_CLIENT_SECRET`.
- **Apple:** Apple Developer → Identifiers → Services ID with Sign in with Apple, your domain and the redirect URI; Keys → a key with Sign in with Apple. Set `APPLE_CLIENT_ID` (the Services ID), `APPLE_TEAM_ID`, `APPLE_KEY_ID` and `APPLE_PRIVATE_KEY` (the `.p8` contents; `\n` for line breaks is fine). Apple requires https.

Workspace single sign-on needs no server settings: an owner or admin fills in **Settings → Single sign-on** (issuer URL, client ID and secret, email domains). The redirect URI to register with the identity provider is shown on that page (`https://<your-site>/api/session/oauth/sso/callback`).

### Manual setup (without Docker)

```bash
# API
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://aiva:aiva_local_dev@localhost:5432/aiva_crm
export REDIS_URL=redis://localhost:6379/0
export JWT_SECRET_KEY=$(openssl rand -hex 32) ENVIRONMENT=development
alembic upgrade head
python -m app.scripts.bootstrap_dev
uvicorn app.main:app --reload --port 8000

# Web (new terminal)
cd apps/web
npm install
AIVA_INTERNAL_API_URL=http://localhost:8000/api/v1 npm run dev
```

## Deploy online (free, on Render)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/BaharathBathula/AIVA-CRM)

`render.yaml` deploys AIVA as one free web service (web app and API in one container, built from `deploy/Dockerfile`) plus a free PostgreSQL database.

1. Click the button above, or in Render choose **New → Blueprint** and pick this repository.
2. When asked for `AIVA_DEV_PASSWORD`, enter the password you want for the demo sign-ins (use a strong one; the site is public).
3. Click **Deploy Blueprint**. The first build takes several minutes.
4. Open the URL Render shows for the `aiva-crm` service (`https://aiva-crm-….onrender.com`) and sign in as `admin@aiva.local` with that password.

Every push to `main` redeploys automatically. On first start the container applies migrations and creates the demo workspace; later restarts keep your data.

**Already have a free Render database?** Render allows one per workspace. Either delete the unused one and click **Manual sync** on the Blueprint, or use a free database from [Neon](https://neon.tech) (it doesn't expire): create a Blueprint with **Blueprint path** `render-external-db.yaml` and paste the Neon connection string as `DATABASE_URL`.

Free-plan limits: the service sleeps after 15 minutes without visitors (the next visit takes about a minute), and Render's free database expires 30 days after creation unless you upgrade it. Invitation and password-reset links are shown on screen because no email service is connected (`SHOW_LINKS_WITHOUT_EMAIL`).

## Testing

```bash
cd apps/api
pytest            # unit tests + integration tests (integration tests need PostgreSQL)

cd apps/web
npx tsc --noEmit  # type check
npm run build     # production build
```

The integration suite signs up a brand-new workspace per run and exercises sign-in, MFA, lockout, invitations, permissions, tenant isolation, quotes, forecasting, cases and SLAs, workflows, reports and dashboards — plus plans and trial expiry, custom modules and fields, campaigns, forms, imports, calls, API keys, HIPAA mode, AI agents and predictions, inventory and invoices, approvals, journeys, portals, custom buttons, record visibility, ABM, Google Ads and sandboxes.

## Security notes

- Passwords are hashed with Argon2; refresh, reset, API-key and portal tokens are stored only as SHA-256 hashes.
- Mailbox passwords, webhook URLs and encrypted fields are encrypted at rest (Fernet).
- Browser sessions use httpOnly, SameSite cookies; the frontend never sees tokens.
- Every API route checks the caller's permissions, and every query is scoped to the caller's organization.
- Production deployments still need: a real email provider for invitations and resets, HTTPS everywhere, secret management, and rate limiting at the edge.

## Roadmap

- OAuth mailbox connection for Gmail and Microsoft 365 and two-way calendar sync (today: app passwords and an iCal feed)
- Domain verification (DNS) for single sign-on domains and an option to require single sign-on
- Native QuickBooks and Google Ads API connections (today: CSV export and lead-form webhook)
- SAML single sign-on and SCIM provisioning (OpenID Connect single sign-on is available)

## License

No license file is currently included in the repository. All rights remain reserved unless an explicit license is added.

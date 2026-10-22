# AIVA CRM

**Sales, service, analytics and automation in one CRM workspace.**

AIVA CRM is a multi-tenant CRM platform modeled on the feature set of the leading CRMs (Salesforce, HubSpot, Microsoft Dynamics 365, ServiceNow, Pipedrive): secure sign-in with roles and two-step verification, a full sales cloud (leads to quotes to forecast), a service desk with SLAs and a knowledge base, a report builder with dashboards, and a no-code workflow engine.

**Project status:** Active development. Everything listed as *Available* below is implemented end-to-end (database, API, permissions, UI) and covered by tests.

## Feature overview

### Sign-in and security

| Feature | What it does |
|---|---|
| Sign in / sign up | Email + password sign-in, "keep me signed in", self-serve workspace creation |
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
| Roles & permissions | Six built-in roles (Owner, Administrator, Sales manager, Sales representative, Support agent, Read only) plus custom roles from a 23-permission catalog |
| Teams | Teams with managers; used for case queues and reporting |
| Audit log | Every sign-in (and failed sign-in), permission change and record create/update/delete with before → after values, IP and device; filter and export |
| Organization settings | Name, currency, time zone, fiscal year start, session length |

### Sales

| Module | Capabilities | Status |
|---|---|---|
| Leads | Capture, qualify, convert; automatic lead scoring (A–D grades) from configurable rules | Available |
| Accounts & Contacts | Company and people records with ownership, hierarchy, duplicate checks | Available |
| Opportunities & Pipeline | Multiple pipelines and stages, board view, bulk actions, saved views, forecast categories | Available |
| Products & price book | Catalog with SKUs, families, billing frequency, default tax | Available |
| Quotes (CPQ) | Quote builder with catalog lines, per-line discount and tax, totals, send/accept/decline with approval permission, duplicate, print/PDF; accepting updates the deal amount | Available |
| Forecasting | Fiscal-quarter forecast by rep: closed won, commit, best case, pipeline, quotas and attainment | Available |

### Service

| Module | Capabilities | Status |
|---|---|---|
| Cases | Queues (my open, unassigned, past SLA…), priorities, channels, assignment, customer replies and internal notes, CSAT | Available |
| SLA policies | First-response and resolution targets by priority with live countdowns and breach tracking | Available |
| Knowledge base | Markdown articles with drafts, publishing, categories, view counts, helpful votes; suggested articles on every case | Available |

### Insights and automation

| Module | Capabilities | Status |
|---|---|---|
| Home | Live KPIs, items needing attention, pipeline by stage, deals closing soon, my tasks, hottest leads, recent activity | Available |
| Reports | Report builder over 9 record types: filters (incl. relative dates like "this quarter"), grouping by field or date, sum/avg/min/max, bar/line/donut/number charts, CSV export, folders, sharing | Available |
| Dashboards | Boards of saved reports with resizable, reorderable widgets | Available |
| Workflows | "When a record is created/updated/field changes, and conditions match, then…" create a task, update a field, change owner or send a notification; run history | Available |
| Global search & notifications | ⌘K search across records, cases, quotes, articles and products; in-app notification bell | Available |
| Engagement | Activities, tasks and email threads | Available (foundation) |
| AI capabilities | AI-assisted prioritization, summaries and recommendations | Planned |

## Technology stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript |
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
│   │   │   ├── api/routes/      auth, admin, sales, service, analytics, platform + CRM records
│   │   │   ├── core/            config, security, permissions, TOTP
│   │   │   ├── models/          SQLAlchemy models
│   │   │   ├── services/        auth sessions, automation engine, reporting engine, catalog
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

The integration suite signs up a brand-new workspace per run and exercises sign-in, MFA, lockout, invitations, permissions, tenant isolation, quotes, forecasting, cases and SLAs, workflows, reports and dashboards.

## Security notes

- Passwords are hashed with Argon2; refresh and reset tokens are stored only as SHA-256 hashes.
- Browser sessions use httpOnly, SameSite cookies; the frontend never sees tokens.
- Every API route checks the caller's permissions, and every query is scoped to the caller's organization.
- Production deployments still need: a real email provider for invitations and resets, HTTPS everywhere, secret management, and rate limiting at the edge.

## Roadmap

- Email and calendar sync (Gmail, Microsoft 365) and meeting scheduling
- Customer self-service portal for cases and knowledge
- Marketing: campaigns, lists, forms
- AI: lead and deal insights, case summaries, reply drafting, next-best-action
- SSO (SAML/OIDC) and SCIM provisioning

## License

No license file is currently included in the repository. All rights remain reserved unless an explicit license is added.

# AIVA CRM

**Sales, service, analytics and automation in one CRM workspace.**

AIVA CRM is a multi-tenant CRM platform modeled on the feature set of the leading CRMs (Salesforce, HubSpot, Microsoft Dynamics 365, ServiceNow, Pipedrive, Zoho): secure sign-in with roles and two-step verification, a full sales cloud (leads to quotes, invoices and forecast), marketing (mass email, web forms, journeys, ABM), a service desk with SLAs, a knowledge base and customer portals, AI agents and predictions, a report builder with dashboards, and a no-code workflow engine — sold in three plans with a free trial.

**Project status:** Active development. Everything listed as *Available* below is implemented end-to-end (database, API, permissions, UI) and covered by tests.

## Plans and pricing

Prices are per user per month. Every new workspace starts a **7-day free trial with everything in Enterprise** — no card needed. When the trial ends the workspace becomes read-only until someone picks a plan; nothing is deleted.

| | Starter — $19 | Pro — $29 | Enterprise — $59 |
|---|---|---|---|
| Core CRM | Leads, contacts, accounts, deals, tasks, meetings and calls, follow-up reminders, standard reports, data import/export, mobile app (PWA), REST API | Everything in Starter | Everything in Pro |
| Sales | Multiple pipelines, sales forecasting, lead scoring, data enrichment, sales sequences, goals | CPQ (quotes, products, invoices), inventory, predictive intelligence, unlimited reports, territories, personas, buying intent and signals, waterfall enrichment | Approvals, QuickBooks export, conversation intelligence |
| Marketing | Mass email, email templates, web forms, calendar sync | Customer journeys, Google Ads integration | Account-based marketing (ABM) |
| Service | Service desk, mailbox sync (Gmail, Outlook, SMTP/IMAP) | Customer portal, web-to-case, custom (partner) portals | — |
| Automation and AI | Workflow automation | AI agents, process management (stage playbooks) | Extended AI (summaries, reply drafts, Ask AIVA) |
| Customization | Custom modules, Slack/Teams/Zoom, HIPAA mode | — | Extended field types, sequential (multi-step) forms, custom buttons and functions, field-level encryption, sandbox, multiple teams, reporting hierarchy, single sign-on |
| Limits | 5 pipelines, 3 custom modules, 25 saved reports, 5 dashboards, 5 web forms | 20 custom modules; everything else unlimited | Unlimited |

Every user also adds credits (for waterfall enrichment and data requests) and AI words (for drafts, summaries, call notes and agents) to a shared monthly pool: 500 credits and 2,500 words on Starter, 1,500 and 10,000 on Pro, 5,000 and 25,000 on Enterprise. **Product add-ons** top these up or add single features without changing plan: extra credits ($49 for 5,000), AI words ($19 for 100,000), conversation intelligence ($15 per user) and advanced data ($12 per user: waterfall enrichment, personas, intent and signals).

Plan changes are made in **Settings → Plan & billing** (owners and admins). Without Stripe keys the server runs *demo billing*: switching plans applies instantly and records an invoice with no charge. With `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` set, upgrades go through Stripe Checkout and the Stripe customer portal. The public price list is at `/pricing`.

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
| Workspace shell | Floating top navigation with section tabs, an icon rail for Home, Sales, Service, Insights, Integrations and Settings, and light and dark themes (follows your system until you pick one) | Available |
| Engagement | Activities, tasks with reminders, email threads, mailbox sync, calendar feed (iCal), and the integrations below | Available |
| SalesInbox | Email sorted into Deals, Contacts, Leads and Others by who it's with, so customer conversations come first | Available |
| Documents | Folders, uploads up to 25 MB, versions with notes, in-browser preview, private or shared, linked to any account, contact, deal, order or case | Starter |
| Team chat & people | Channels and direct messages with @mentions and record links; a people directory with roles, teams and reporting lines | Starter |
| My Pins | Pin any record and reach it from the top bar | Available |
| AI agents | Lead qualifier, deal coach, case triage, follow-up writer and data hygiene agents leave suggestions you accept or dismiss | Available |
| Extended AI | Record summaries, email and case-reply drafts, and "Ask AIVA" questions about your CRM — Claude when `ANTHROPIC_API_KEY` is set, built-in rules otherwise | Available |
| Mobile app | Installable on iPhone, Android and desktop (PWA) with offline fallback | Available |

### Settings suite

Settings open with a collapsible menu on the left, grouped the way Apollo groups them; every page is also reachable from **Settings → All settings** and the menu has its own search.

| Group | Pages |
|---|---|
| Personal | Profile, password and two-step verification, mobile app |
| Plan & billing | Plan overview, product add-ons (buy, change packs, cancel — runs to the end of the paid month), billing (invoices, payment method, billing email) |
| Credits & activity | Credit usage by person and feature with a six-month trend and CSV export; data requests (ask for a missing email, phone or title; the data team fills it in or runs waterfall enrichment, and only found data is charged); AI word usage by day, feature and person |
| Users & teams | Users, teams, permission profiles, **territories** (rules on country, state, city, industry, size, revenue or custom fields; round-robin routing of new accounts and leads; apply to existing records), **license settings** (seat rules, default role for invites, per-person credit and AI word limits), workspace details, **team sharing & defaults** (shared or private by default for templates, sequences, snippets, reports and documents; default meeting length and task due date; route imports by territory), system activity log, **workspace security** (require two-step verification, IP allowlist, allowed invite domains, minimum password length, turn API keys off), single sign-on, hierarchy, HIPAA, **support access** (time-limited read-only or full access for AIVA support, every page they open is logged) |
| Integrations | All integrations (opens the Integrations section), other connections (app-password mailboxes, calendar feed, personal Zoom link), **MCP** (connect Claude and other MCP clients over Streamable HTTP with an API key; read tools always, write tools only when allowed and with a read & write key; per-tool switches and a call log), API keys, sandbox |
| Outbound | Overview, sending **domains** (SPF, DKIM, DMARC, MX and tracking-domain checks with fixes), **mailboxes** (daily limits, health, bounce rate), **diagnose**, **sending protection** (sending days and hours, time zone, holidays, daily/hourly caps, spacing, bounce pause, skip shared inboxes), **sequence rules**, **tracking** (opens, clicks, unsubscribe link, tracking domain), **dialer** (your own call outcomes, required notes, automatic follow-ups, Twilio calling), **prospecting configuration** (duplicate rules, the contact stages people move through), **snippets** |
| Data management | **Waterfall enrichment** (built-in, website, Hunter.io and People Data Labs in your order, paid with credits), contact, account and deal fields & stages (your own contact and account stages), fields & modules, **global picklists** shared by fields, **goals** with live progress and pace, imports & exports, **removal requests** (GDPR and CCPA erasure with the 30- or 45-day deadline, shows everything stored about the person, erases it and adds them to the do-not-contact list) |
| Automation | Workflows, stage playbooks, approval rules, custom buttons, SLA policies |
| Ideal customer profile | **Personas** (matched by job title), **buying intent** topics and account intent scores, website visitor settings (exclusions, Do Not Track, retention), **signals** (pricing-page visits, intent surges, repeated opens, form fills, stalled deals, job changes, sequence replies, new persona matches) with notifications and tasks, scoring for leads, contacts and accounts, the **AI Context Center** (company facts, tone, words to avoid, competitors and FAQs used by every AI feature) |
| Meetings & conversations | Recording configuration (what's captured, consent notice on invites, Deepgram or AssemblyAI transcription, retention, number redaction), team permissions, keyword **trackers**, coaching **scorecards**, **custom field prompts** (AI answers a question from each call and suggests or applies the CRM update), team meetings |

### Sequences, conversations and signals

| Module | Capabilities | Status |
|---|---|---|
| Sequences | Multi-step outreach: automatic emails (sent inside the sending window with per-mailbox caps, threaded follow-ups, open/click tracking and one-click unsubscribe), plus call, LinkedIn and task steps that land on the rep's task list. People leave automatically when they reply, book a meeting, bounce or unsubscribe; per-step stats; add people from a record or many at once from the contact and lead lists | Starter |
| Conversations | Paste or upload call transcripts (.vtt, .srt, text) or recordings; speaker talk time, tracker hits, a summary with next steps, suggested CRM updates you apply in one click, and scorecard reviews | Enterprise or add-on |
| Signals | A feed of moments worth acting on, from the signal rules, with links to the record and done/dismiss | Pro |

### Integrations

**Integrations** has its own icon in the left rail. Each of the 20 integrations has an on/off switch for the whole workspace (owners and admins), a page with its connection, settings and activity log, and a plan badge when the plan doesn't include it. Personal integrations (mail, calendars, Zoom, cloud files) are connected by each person for their own account; admins see who has connected. Turning an integration off stops everything it does straight away: no sending, no syncing, and its inbound webhooks are ignored. The section also has a **Messages** inbox (SMS and WhatsApp), **Webhooks** (outgoing events for Zapier, Make and n8n) and **Activity** (every connection, sync, send and webhook, with filters).

| Category | Integration | What it does | Plan |
|---|---|---|---|
| Email | Gmail, Microsoft Outlook Mail | Each person signs in with Google or Microsoft; one-off emails, campaigns, sequence steps and meeting invites go out through the Gmail API or Microsoft Graph, and the inbox is read every 10 minutes so customer replies land on the right timeline and stop sequences | Starter |
| Calendar | Google Calendar, Microsoft Outlook Calendar | Two-way sync: meetings scheduled in AIVA are created, moved and cancelled in the person's calendar (which sends the invitations); customer meetings booked in the calendar come into AIVA linked to the contact, account or lead; choose which calendar and which events | Starter |
| Communication | Microsoft Teams | Deal, lead and case alerts in a Teams channel (channel webhook); Teams meeting links on AIVA meetings through each person's Outlook Calendar connection | Starter |
| Communication | Slack | Sign in once for the workspace; alerts for new leads, won deals, urgent cases and more, with a default channel and a channel per alert; **Share to Slack** on leads, contacts, accounts, deals and quotes; an incoming webhook works too | Starter |
| Communication | WhatsApp Business Platform | Send messages and approved templates (24-hour window respected) from the record or the Messages inbox; replies and delivery status come back on a signed webhook | Starter |
| Communication | Twilio SMS | Two-way SMS with delivery status; STOP, START and HELP handled, and opted-out numbers can't be texted (they join the do-not-contact list) | Starter |
| Meetings | Zoom, Google Meet | Pick Zoom, Google Meet or Teams as the video call when scheduling; AIVA creates the meeting and the join link, and updates or deletes it with the AIVA meeting | Starter |
| Scheduling | Calendly | Bookings become AIVA meetings linked to the contact or lead (a new lead when nobody matches); cancellations cancel the meeting; scheduling links with the person's name and email filled in, from lead and contact pages | Starter |
| Documents | Google Drive, Microsoft OneDrive, Dropbox | Search your files and attach them to accounts, contacts, leads, deals, quotes, cases and projects; copy a file into AIVA Documents (Google Docs, Sheets and Slides as PDF or Office) | Starter |
| E-signature | DocuSign | Send a quote or a document for signature with signers in order and copies; status on the record; the signed PDF is saved to Documents and the quote marked accepted; remind, resend and void | Pro |
| Lead capture | WordPress Forms | A downloadable plugin (Contact Form 7, WPForms, Gravity Forms, Elementor, Ninja Forms, Fluent Forms, Formidable) or any form's webhook; field mapping per form, signed submissions, duplicate handling | Starter |
| Lead capture | Facebook Lead Ads | Pick your Facebook pages; Facebook and Instagram lead-form leads arrive within seconds, mapped per form, with a catch-up every 15 minutes | Starter |
| Automation | Zapier, Make, n8n | Signed outgoing webhooks for 25 events (new lead, deal won, task completed…), retried for a day, with a delivery log and resend; REST hooks for Zapier apps; actions through the REST API with an API key | Starter |

Record pages show **Messages**, **Cloud files** and **Signatures** panels, a **Share to Slack** button and **Scheduling links**, each only when that integration is on and connected.

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

The demo workspace is on the Enterprise plan with sample data for every feature: a custom module, campaigns, a web form, a journey, target accounts, invoices, tracked inventory, approval rules, a customer portal (Olivia Johnson at Northwind Logistics has access), vendors and purchase orders, price books, sales orders, meetings, documents, services and bookings, delivery projects, website visits, SalesInbox email and team chat. Existing demo workspaces pick up content for newly added features on their next start, once. New sign-ups start a 7-day trial.

### Optional configuration

| Variable | Purpose |
|---|---|
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Real card billing through Stripe Checkout (otherwise demo billing). Point the Stripe webhook at `/api/aiva/public/stripe/webhook`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_SECURITY` | Server email for invitations, reminders, campaigns and portal sign-in links. People can also connect their own mailbox (Integrations → Gmail or Outlook Mail, or an app password under Other connections). |
| `ANTHROPIC_API_KEY`, `AI_MODEL` | Claude for summaries, drafts, Ask AIVA, call notes and field prompts. Without it, AI features use built-in rules. |
| Workspace keys (entered in Settings or on an integration's page, stored encrypted) | Hunter.io and People Data Labs for waterfall enrichment; Twilio for dialer calls and SMS; WhatsApp Cloud API; Deepgram or AssemblyAI to transcribe call recordings. Sequences send through each rep's connected mailbox (Gmail or Outlook sign-in, an app password, or the server SMTP settings). |
| `FIELD_ENCRYPTION_KEY` | Key for encrypted fields and stored secrets. Defaults to one derived from `JWT_SECRET_KEY`; set it explicitly in production and keep it safe — changing it makes encrypted values unreadable. |
| `APP_BASE_URL` | Public URL used in emails, portal links, calendar feeds, sign-in redirects and webhooks (Render sets it automatically). Integrations that call AIVA back (Twilio, WhatsApp, Calendly, DocuSign, Facebook) need it to be the public https address. |
| `SCHEDULER_ENABLED` | Background jobs for reminders, campaigns, journeys and mailbox sync (default on). |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Turns on "Log In with Google". |
| `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID` | Turns on "Log In with Microsoft" (tenant `common` allows work and personal accounts). |
| `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` | Turns on "Sign in with Apple" (Services ID, team ID, key ID and the `.p8` key contents). |
| `GOOGLE_CLIENT_ID`/`SECRET`, `MICROSOFT_CLIENT_ID`/`SECRET` (again) | The same apps also power Gmail, Google Calendar and Meet, Google Drive, Outlook Mail and Calendar, Teams meeting links and OneDrive (see below). |
| `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | The Slack app people sign in with. |
| `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` | The Zoom app people sign in with. |
| `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET` | The Dropbox app. |
| `DOCUSIGN_INTEGRATION_KEY`, `DOCUSIGN_SECRET_KEY`, `DOCUSIGN_ENVIRONMENT` | The DocuSign app; `demo` (developer sandbox, the default) or `production`. |
| `CALENDLY_CLIENT_ID`, `CALENDLY_CLIENT_SECRET`, `CALENDLY_WEBHOOK_SIGNING_KEY` | The Calendly app and its webhook signing key. |
| `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`, `FACEBOOK_WEBHOOK_VERIFY_TOKEN` | The Meta app for Facebook Lead Ads; the verify token is optional (derived from the app secret when unset). |

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

### Setting up integrations

Turn an integration on in **Integrations**, then connect it. Integrations that sign in with the provider need an app registered with that provider; set the server-wide credentials above, or let each workspace use its own app (**Use your own app** on the integration's page, where the client secret is stored encrypted). Register this redirect URI with every provider app:

```text
https://<your-site>/api/aiva/public/integrations/oauth/callback
```

| Provider app | Where | Turn on / scopes |
|---|---|---|
| Google (Gmail, Calendar, Meet, Drive) | Google Cloud Console → APIs & Services → Credentials → OAuth client ID (Web application) | Enable the Gmail, Google Calendar and Google Drive APIs. Scopes: `gmail.send`, `gmail.readonly`, `calendar.events`, `calendar.calendarlist.readonly`, `drive.readonly`. These are sensitive scopes, so Google's app verification is needed before people outside your organization can connect. Meet must be allowed in the Workspace admin console. |
| Microsoft (Outlook Mail and Calendar, Teams links, OneDrive) | Microsoft Entra admin center → App registrations | Delegated `Mail.Send`, `Mail.Read`, `Calendars.ReadWrite`, `OnlineMeetings.ReadWrite`, `Files.Read.All`, `offline_access`, `User.Read`. Some tenants need admin consent. Teams links need a work or school account. |
| Slack | api.slack.com → Your Apps → Create New App | Bot scopes `chat:write`, `chat:write.public`, `channels:read`, `groups:read`, `team:read`. For private channels, invite the app (`/invite @AIVA`). |
| Zoom | Zoom App Marketplace → Develop → General app (OAuth) | Meeting read/write/delete and user read (classic `meeting:write`, `user:read`, or the granular equivalents); AIVA uses whatever scopes the app has. |
| Dropbox | dropbox.com/developers → App console (scoped access, full Dropbox) | `account_info.read`, `files.metadata.read`, `files.content.read`, `sharing.read` |
| DocuSign | DocuSign Admin → Apps and Keys → Add app and integration key (Authorization Code Grant) with a secret key | `signature`, `extended`. Optional: Admin → Connect → Connect Keys, then paste the HMAC key on the DocuSign page so status pushes are verified (otherwise AIVA checks every 15 minutes). |
| Calendly | developer.calendly.com → Create an OAuth app | Set `CALENDLY_WEBHOOK_SIGNING_KEY` to the app's webhook signing key. Workspaces can also connect with a personal access token instead. Bookings need `APP_BASE_URL` to be public https. |
| Facebook Lead Ads | developers.facebook.com → Create app (Business) | Permissions `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`, `leads_retrieval`, `business_management` (App Review for other businesses). Webhooks → Page → callback `https://<your-site>/api/aiva/public/integrations/facebook_lead_ads/webhook/app` with the verify token shown on the integration's page; subscribe to `leadgen`. |

Integrations connected with keys show what to paste and where on their page:

- **Twilio SMS:** account SID and auth token, plus a phone number or messaging service. Point the number's "A message comes in" webhook at the URL shown (or click **Point at AIVA**). `APP_BASE_URL` must match the address Twilio calls exactly, or its signatures won't verify. US numbers need A2P 10DLC registration.
- **WhatsApp:** phone number ID, WhatsApp Business account ID, a permanent system-user token (`whatsapp_business_messaging`, `whatsapp_business_management`) and the Meta app secret. Set the callback URL and verify token shown on the page in the Meta app (or click **Set it up for me**) and subscribe to `messages`.
- **Microsoft Teams:** in the channel, Workflows → "Post to a channel when a webhook request is received", then paste the URL.
- **WordPress Forms:** download the plugin from the integration's page (it already has your address and secret) and activate it, or paste the webhook URL into any form plugin.
- **Zapier, Make, n8n:** add a webhook on the integration's page or under **Integrations → Webhooks** with the URL from the Zap trigger, Make's custom webhook or n8n's Webhook node. Requests carry `X-AIVA-Signature: t=<time>,v1=<HMAC-SHA256 of "<time>.<body>">` with the webhook's secret. To create or update records from those tools, call the REST API (`https://<your-site>/api/aiva/...`) with an API key; Zapier apps can subscribe through the REST hooks at `/api/aiva/hooks`.

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

The integration suite signs up a brand-new workspace per run and exercises sign-in, MFA, lockout, invitations, permissions, tenant isolation, quotes, forecasting, cases and SLAs, workflows, reports and dashboards — plus plans and trial expiry, custom modules and fields, campaigns, forms, imports, calls, API keys, HIPAA mode, AI agents and predictions, inventory and invoices, approvals, journeys, portals, custom buttons, record visibility, ABM, Google Ads and sandboxes — and the settings suite: workspace security (IP allowlist, required MFA, API-key switch), add-ons and credits, data requests, territories and round-robin routing, support access, the MCP server, sending domains, snippets, sequences end to end (sending, click tracking, replies, bounces, meetings, unsubscribes), picklists, goals, removal requests, personas, signals and conversation intelligence — and the integrations: on/off switches and dependencies, sign-in with PKCE and single-use state, every provider's connect, send, sync and webhook paths with the provider APIs mocked (signature checks both accepted and refused), opt-outs, envelope completion, form and lead-ad capture, and outgoing webhook signing, retries and URL safety.

## Security notes

- Passwords are hashed with Argon2; refresh, reset, API-key and portal tokens are stored only as SHA-256 hashes.
- Mailbox passwords, webhook URLs, encrypted fields and workspace integration keys (enrichment, Twilio, transcription) are encrypted at rest (Fernet) and never sent back to the browser.
- Workspaces can require two-step verification, restrict access to an IP allowlist and switch API keys off; AIVA support can only enter a workspace with a time-limited grant from an admin, and every page they open is logged.
- Sequence click links are HMAC-signed, so the tracking redirect can't be used as an open redirect.
- Integration tokens, keys and webhook secrets are encrypted at rest and never returned to the browser (an outgoing webhook's signing secret is shown once). Provider sign-in uses PKCE where supported and single-use, 15-minute state; inbound webhooks verify each provider's signature (Twilio, Meta, Calendly, DocuSign, the WordPress plugin) in constant time and ignore repeats; in production, outgoing webhooks only go to public https addresses, checked when saved and again, pinned, on every delivery.
- Browser sessions use httpOnly, SameSite cookies; the frontend never sees tokens.
- Every API route checks the caller's permissions, and every query is scoped to the caller's organization.
- Production deployments still need: a real email provider for invitations and resets, HTTPS everywhere, secret management, and rate limiting at the edge.

## Roadmap

- Push notifications from Gmail, Google Calendar and Microsoft Graph (today: inbox and calendar sync every 10 minutes)
- Sending media (MMS and WhatsApp images and documents), Slack slash commands and interactive buttons
- Domain verification (DNS) for single sign-on domains and an option to require single sign-on
- Native QuickBooks and Google Ads API connections (today: CSV export and lead-form webhook)
- SAML single sign-on and SCIM provisioning (OpenID Connect single sign-on is available)

## License

No license file is currently included in the repository. All rights remain reserved unless an explicit license is added.

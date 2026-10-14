# AIVA CRM

**AI-Native Customer Relationship Management Platform**

AIVA CRM is a modular, AI-native CRM platform designed to streamline sales operations, customer engagement, and enterprise workflows through a unified application.

**Project Status:** Active Development

> AIVA CRM is an evolving software platform. The repository contains implemented CRM foundations, while advanced automation, integrations, and enterprise capabilities remain under development.

## Vision

Build an intelligent CRM platform that combines traditional customer relationship management with AI-assisted operations, workflow automation, and enterprise-grade administration.

The long-term product direction includes capabilities comparable in scope to established CRM platforms such as Salesforce, HubSpot, Zoho CRM, and ServiceNow, with an emphasis on AI-native workflows.

## Core Modules

| Module | Capabilities | Development Status |
|---|---|---|
| Leads | Lead creation, management, conversion, and tracking | Implemented foundation |
| Accounts | Customer account management and related workflows | Implemented foundation |
| Contacts | Contact management, ownership, and segmentation | Implemented foundation |
| Opportunities | Opportunity tracking, bulk actions, pipeline integration, and saved views | Implemented foundation |
| Sales Pipelines | Pipeline and stage management | Implemented foundation |
| Activities | Customer interaction logging and activity timelines | Enhancement planned |
| Tasks | Task creation, editing, assignments, and tracking | Enhancement planned |
| Email | Email data models, APIs, and compose/reply components | Integration in progress |
| Meetings | Meeting data model | Scheduling functionality planned |
| Administration | Organization and user membership foundations | In development |
| General Settings | Workspace configuration | In development |
| AI Capabilities | AI-assisted sales and enterprise automation | Planned |

## Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, React, TypeScript, Tailwind CSS |
| Backend | Python, FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Database Migrations | Alembic |
| Caching | Redis |
| Authentication | JWT-based authentication |
| Infrastructure | Docker, Docker Compose |
| Development | GitHub, GitHub Codespaces |

## System Architecture

```text
                  AIVA CRM
                      |
                Next.js Frontend
                      |
                API Proxy Layer
                      |
                 FastAPI Backend
                      |
             Business Service Layer
                      |
          +-----------+-----------+
          |                       |
      PostgreSQL                Redis
          |
    CRM Business Data
```

The application separates frontend presentation, backend API routes, business services, database models, and infrastructure concerns.

## Repository Structure

```text
AIVA-CRM/
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   ├── api/routes/
│   │   │   ├── core/
│   │   │   ├── db/
│   │   │   ├── models/
│   │   │   ├── schemas/
│   │   │   └── services/
│   │   └── alembic/
│   └── web/
│       └── src/
│           ├── app/
│           ├── components/
│           ├── lib/
│           └── types/
├── docker-compose.yml
├── .env.example
└── README.md
```

## Development Setup

### Prerequisites

- Git
- Docker and Docker Compose
- GitHub Codespaces or a compatible development environment

### Clone the repository

```bash
git clone https://github.com/BaharathBathula/AIVA-CRM.git
cd AIVA-CRM
```

### Configure environment

Create a local `.env` file based on `.env.example`.

Configure the database connection and a unique JWT signing secret. Do not commit `.env` or production credentials to GitHub.

Review the networking configuration in `docker-compose.yml` before starting the services.

### Start a fresh development environment

```bash
docker compose up -d postgres redis
docker compose run --rm migrate
docker compose run --rm bootstrap
docker compose up -d --no-deps api web
```

The bootstrap step is intended for a fresh database. Do not rerun it against an existing seeded database without verifying that it is safe.

### Application URLs

- Frontend: `http://localhost:3000`
- API health endpoint: `http://localhost:8000/api/v1/health`

When using GitHub Codespaces, access the frontend through its forwarded port.

## Development Roadmap

### Phase 1 — CRM Foundation

Lead, Account, Contact, Opportunity, and Sales Pipeline management.

**Status:** Implemented foundations; continued validation and refinement.

### Phase 2 — Engagement Module

Development sequence:

1. Activities
2. Tasks
3. Email
4. Meetings

Each module will undergo frontend, API, database, authorization, and functional validation.

### Phase 3 — Administration

- General Settings
- Users & Roles
- Sales Configuration
- Integrations
- Security
- AI Settings

**Status:** In development.

### Phase 4 — AI-Native CRM

Planned capabilities include AI-assisted lead prioritization, opportunity insights, customer interaction summaries, intelligent task recommendations, and workflow automation.

These are roadmap capabilities, not claims of existing autonomous functionality.

### Phase 5 — Enterprise Readiness

- Expanded automated testing
- Security hardening
- Auditability and observability
- Reliable deployment workflows
- Performance optimization
- Production-grade authentication and authorization

## Security and Data Management

AIVA CRM is designed around organization-aware data access, API-level authorization, and controlled database changes.

Production readiness requires additional security review, particularly around session persistence, secret management, database configuration, and deployment isolation.

## Current Development Checkpoint

**October 2026**

The current development focus is completing General Settings authentication and persistence, followed by the Engagement Module.

Active work in GitHub Codespaces may be ahead of the committed repository state.

## License

No license file is currently included in the repository. All rights remain reserved unless an explicit license is added.

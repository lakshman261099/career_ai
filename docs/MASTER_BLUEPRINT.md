# 🚀 CareerAI --- Master Production Blueprint (v1.0)

## 0. Snapshot

**Product:** CareerAI --- AI-powered career platform for students\
**Tech:** Flask, SQLAlchemy, Tailwind, RQ + Redis, OpenAI (GPT-4o /
4o-mini), Stripe\
**Hosting:** DigitalOcean (India-focused)\
**Database:** DigitalOcean Managed Postgres (Bangalore)\
**Queue:** Redis + RQ workers\
**Login:** Email OTP + Google OAuth\
**Credits:** Silver 🪙 (Free), Gold ⭐ (Pro)\
**Tenancy:** Multi-tenant: `university.careerai.app`\
**Core Features:** Portfolio Builder, Internship Finder, Referral
Trainer, Job Pack, Skill Mapper + Pro-only Profile Portal

------------------------------------------------------------------------

## 1. Product & Plans

### 1.1 Core Features Overview

**Portfolio Builder**\
- Free: 1 simple project idea\
- Pro: 3 detailed project ideas\
- Publishing: Pro-only, uses Profile Portal data

**Internship Finder**\
- Input: pasted JD\
- Free: 1 daily run\
- Pro: unlimited

**Referral Trainer**\
- Free templates\
- Pro tone-control **coming soon**

**Job Pack**\
- Free: basic analysis\
- Pro: deep structured report

**Skill Mapper**\
- Free: suggests 3 roles\
- Pro: uses Profile Portal + resume → roles + trends + gaps +
micro-projects

**Profile Portal (Pro)**\
- Identity, links, education, experience, projects, certifications\
- Resume upload\
- Required for Portfolio Publish

------------------------------------------------------------------------

## 2. Architecture & Infra

### 2.1 Stack

-   Flask\
-   SQLAlchemy + Alembic\
-   DigitalOcean Managed Postgres\
-   Redis + RQ workers\
-   OpenAI GPT-4o / 4o-mini\
-   Tailwind\
-   WeasyPrint/Chromium for PDFs

### 2.2 Multi-Tenancy

-   Subdomain routing: `university.careerai.app`\
-   Middleware loads current tenant\
-   Queries filtered by `university_id`

### 2.3 AI Flows

-   Job Pack → RQ → OpenAI → JSON → DB\
-   Skill Mapper → RQ → OpenAI → JSON → DB\
-   Internship Finder → sync OpenAI\
-   Referral Trainer → templates\
-   Portfolio Builder → optional AI assist

------------------------------------------------------------------------

## 3. Auth & Verification

### 3.1 Methods

-   Email OTP\
-   Google OAuth\
    Both verify email automatically.

### 3.2 Access Rules

-   Unverified → browse only\
-   Verified → can run features

------------------------------------------------------------------------

## 4. Credits & Billing

### 4.1 Balances

-   Silver 🪙 (Free) --- signup bonus + daily refill\
-   Gold ⭐ (Pro) --- monthly fixed + top-ups (future)

### 4.2 Plan Structure

-   Pro Basic → moderate monthly Gold\
-   Pro Advanced → higher monthly Gold

### 4.3 Flow

1.  Deduct credits before enqueue\
2.  Retry/repair on AI errors\
3.  Refund on failure\
4.  Log via CreditTransaction

------------------------------------------------------------------------

## 5. Admin & Tenancy

### 5.1 Roles

-   Super Admin\
-   University Admin\
-   Students

### 5.2 Admin Features

-   Manage universities\
-   Manage students\
-   Credit allocation\
-   Usage charts\
-   Billing history\
-   CSV export\
-   RQ logs

------------------------------------------------------------------------

## 6. Stability & Safety

### 6.1 JSON Reliability

-   3 retries\
-   Auto-repair\
-   Fallback summary + refund

### 6.2 Monitoring

-   Sentry\
-   Worker health\
-   Cost logging

------------------------------------------------------------------------

## 7. Deployment --- DigitalOcean (India)

### 7.1 Components

-   DO App Platform (Web)\
-   DO Worker\
-   DO Managed Postgres (Bangalore)\
-   DO Managed Redis\
-   Wildcard: `*.careerai.app`

### 7.2 Commands

**Web:**\
gunicorn wsgi:app --bind 0.0.0.0:\$PORT --workers 2 --threads 4

**Worker:**\
rq worker careerai_queue

**Migrations:**\
alembic -x dburl="\${DATABASE_URL}?sslmode=require" upgrade head

### 7.3 Required Env Vars

DATABASE_URL\
REDIS_URL\
OPENAI_API_KEY\
STRIPE_SECRET_KEY\
SIGNUP_SILVER_BONUS\
DAILY_SILVER_REFILL\
PRO_BASIC_GOLD\
PRO_ADVANCED_GOLD\
MOCK\
SECRET_KEY

------------------------------------------------------------------------

# End of Master Blueprint

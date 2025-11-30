# 🧠 CareerAI Backend Blueprint (Flask + SQLAlchemy)

## 1. Stack

-   Flask\
-   SQLAlchemy + Alembic\
-   DigitalOcean Managed Postgres (Bangalore)\
-   Redis + RQ workers\
-   OpenAI GPT-4o / mini\
-   Tailwind

------------------------------------------------------------------------

## 2. Models

### 2.1 User

-   id\
-   name\
-   email\
-   email_verified\
-   google_id\
-   university_id\
-   role\
-   is_pro\
-   created_at

### 2.2 University

-   id\
-   name\
-   subdomain\
-   allowed_domains (JSON)\
-   default_silver\
-   default_gold\
-   created_at

### 2.3 CreditWallet

-   user_id (1--1)\
-   silver_credits\
-   gold_credits\
-   updated_at

### 2.4 CreditTransaction

-   id\
-   user_id\
-   university_id\
-   feature\
-   currency (silver/gold)\
-   amount\
-   type (debit/refund/topup/system)\
-   run_id\
-   status\
-   metadata\
-   timestamp

### 2.5 JobPackRequest

### 2.6 SkillMapperRun

------------------------------------------------------------------------

## 3. Auth System

### Email OTP

-   OTPRequest table\
-   Flow: request → send → verify → mark email_verified

### Google OAuth

-   Callback sets email_verified and creates/links account

### Access Rules

-   Not verified → cannot run tools

------------------------------------------------------------------------

## 4. Credits

### Deduction Flow

1.  Validate user\
2.  Determine cost\
3.  Deduct credits\
4.  Enqueue RQ job\
5.  Worker: success → complete; fail → refund

### Feature Costs

Config-driven.

------------------------------------------------------------------------

## 5. AI Workflows

### Job Pack

-   RQ job\
-   GPT-4o-mini (Free) / GPT-4o (Pro)\
-   JSON schema\
-   PDF via WeasyPrint

### Skill Mapper

-   RQ job\
-   GPT-4o\
-   Profile Portal + resume

### Internship Finder

-   Sync mini model

### Referral Trainer

-   Template

------------------------------------------------------------------------

## 6. Tenancy

### Routing

Subdomain → University lookup

### Query Scoping

Filter models by `university_id`

------------------------------------------------------------------------

## 7. Workers & Queues

-   `rq worker careerai_queue`\
-   Retry + repair JSON\
-   Fallback + refund

------------------------------------------------------------------------

## 8. Dev & Testing

-   MOCK=1 → sample JSON\
-   SQLite OK locally

------------------------------------------------------------------------

# End

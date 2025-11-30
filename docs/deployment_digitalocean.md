# ☁️ CareerAI Deployment Guide --- DigitalOcean (India Region)

## 1. Infra

-   DO App Platform (Flask API)\
-   DO App Platform Worker (RQ)\
-   DO Managed Postgres (Bangalore)\
-   DO Managed Redis\
-   Wildcard DNS for tenants\
-   Domain: `careerai.app`

------------------------------------------------------------------------

## 2. Create Resources

### 2.1 Postgres

-   Region: Bangalore\
-   Smallest tier to start

### 2.2 Redis

-   Region: Bangalore\
-   Key-value mode

### 2.3 App Platform --- Web

Build:

    pip install -r requirements.txt

Run:

    gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4

### 2.4 App Platform --- Worker

    rq worker careerai_queue

------------------------------------------------------------------------

## 3. Env Vars

-   DATABASE_URL\
-   REDIS_URL\
-   OPENAI_API_KEY\
-   STRIPE_SECRET_KEY\
-   FLASK_ENV=production\
-   SECRET_KEY\
-   MOCK\
-   SIGNUP_SILVER_BONUS\
-   DAILY_SILVER_REFILL\
-   PRO_BASIC_GOLD\
-   PRO_ADVANCED_GOLD

------------------------------------------------------------------------

## 4. DNS

### Main:

careerai.app → Web App

### Tenants:

\*.careerai.app → Web App

------------------------------------------------------------------------

## 5. Migrations

    alembic -x dburl="${DATABASE_URL}?sslmode=require" upgrade head

------------------------------------------------------------------------

## 6. Monitoring

-   DO logs\
-   Sentry\
-   Worker heartbeat job

------------------------------------------------------------------------

# End

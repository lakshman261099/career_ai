#!/usr/bin/env python3
"""
CareerAI Background Worker (Fixed for RQ 1.15+)

This worker processes background jobs from the Redis queue.
Jobs include: Job Pack analysis, Skill Mapper, and other long-running AI tasks.

Usage:
    python worker.py

Environment Variables Required:
    REDIS_URL - Redis connection URL (e.g., redis://localhost:6379/0)
    DATABASE_URL - Postgres connection URL (or SQLite for dev)
    OPENAI_API_KEY - OpenAI API key
"""

import os
import sys
import logging
from pathlib import Path

from redis import Redis
from rq import Worker, Queue
from dotenv import load_dotenv

# ✅ Upgrade: Always load .env from the same directory as this worker.py
# This avoids failures when running from IDEs or different working directories.
BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BASE_DIR / ".env"
load_dotenv(DOTENV_PATH, override=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

# ✅ Upgrade: helpful diagnostics in dev (safe - does NOT print the key)
logger.info("Worker CWD: %s", Path().resolve())
logger.info("Worker dotenv path: %s (exists=%s)", DOTENV_PATH, DOTENV_PATH.exists())
logger.info("OPENAI_API_KEY present: %s", bool(os.getenv("OPENAI_API_KEY")))

# Get Redis URL from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Validate required environment variables
REQUIRED_ENV_VARS = ["OPENAI_API_KEY"]
missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    logger.error("Please set them in your .env file or environment")
    sys.exit(1)

# Queue names
QUEUE_NAMES = [
    "careerai_queue",  # Default queue for Job Pack, Skill Mapper, etc.
    "careerai_priority",  # High-priority jobs (future use)
]


def main():
    """Start the RQ worker."""
    logger.info("=" * 60)
    logger.info("CareerAI Background Worker Starting")
    logger.info("=" * 60)
    logger.info(f"Redis URL: {REDIS_URL}")
    logger.info(f"Queues: {', '.join(QUEUE_NAMES)}")
    logger.info("=" * 60)

    # Connect to Redis
    try:
        redis_conn = Redis.from_url(REDIS_URL)
        redis_conn.ping()  # Test connection
        logger.info("✓ Redis connection successful")
    except Exception as e:
        logger.error(f"✗ Failed to connect to Redis: {e}")
        logger.error("Make sure Redis is running: brew services start redis")
        sys.exit(1)

    # Create queues
    queues = [Queue(name, connection=redis_conn) for name in QUEUE_NAMES]

    # Create worker
    try:
        worker = Worker(
            queues,
            connection=redis_conn,
            name=f"careerai-worker-{os.getpid()}",
        )

        logger.info("=" * 60)
        logger.info("Worker ready! Waiting for jobs...")
        logger.info("=" * 60)

        # Start processing jobs (blocking)
        worker.work(with_scheduler=False)

    except KeyboardInterrupt:
        logger.info("\n" + "=" * 60)
        logger.info("Worker stopped by user (Ctrl+C)")
        logger.info("=" * 60)
        sys.exit(0)
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

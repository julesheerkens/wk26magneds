"""
Background scheduler: syncs match results every 5 minutes.
Only active when FDORG_API_KEY is set.

Avoids double-start when Flask's debug reloader is active by checking
WERKZEUG_RUN_MAIN (only the reloaded child process starts the scheduler).
"""

import logging
import os

logger = logging.getLogger(__name__)
_scheduler = None


def start(app):
    global _scheduler

    # In Flask debug mode, the reloader forks the process.
    # Only start the scheduler in the actual worker process.
    if os.environ.get("FLASK_DEBUG") and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    if _scheduler is not None:
        return

    if not os.environ.get("FDORG_API_KEY", "").strip():
        logger.info(
            "FDORG_API_KEY not set — auto-sync disabled. "
            "Set it in .env to enable automatic result fetching."
        )
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from fetcher import sync_results
    except ImportError:
        logger.warning("apscheduler not installed — auto-sync disabled")
        return

    def _job():
        try:
            summary = sync_results(app)
            if not summary["skipped"]:
                logger.info(
                    "Sync done: %d results updated, %d teams updated",
                    summary["updated_results"],
                    summary["updated_teams"],
                )
        except Exception:
            logger.exception("Sync job raised an exception")

    _scheduler = BackgroundScheduler(daemon=True)
    # Run immediately on start, then every 5 minutes
    _scheduler.add_job(_job, "interval", minutes=5, id="sync_results")
    _scheduler.start()
    logger.info("Result auto-sync started (every 5 min via football-data.org)")

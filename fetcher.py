"""
Fetch WK 2026 match results and knockout team assignments from football-data.org.

Free tier: https://www.football-data.org/client/register  (no credit card needed)
Set FDORG_API_KEY in .env after registering.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

FDORG_BASE = "https://api.football-data.org/v4"
WC_CODE = "WC"

# Our team names → football-data.org names (where they differ)
_TO_FDORG = {
    "USA":                   "United States",
    "Ivory Coast":           "Côte d'Ivoire",
    "Bosnia & Herzegovina":  "Bosnia and Herzegovina",
    "DR Congo":              "Congo DR",
    "Korea Republic":        "Korea Republic",
    "Türkiye":               "Türkiye",
}
# Reverse: fdorg name → our name
_FROM_FDORG = {v: k for k, v in _TO_FDORG.items()}


def _headers():
    key = os.environ.get("FDORG_API_KEY", "")
    return {"X-Auth-Token": key} if key else {}


def _to_fdorg(name: str) -> str:
    return _TO_FDORG.get(name, name)


def _from_fdorg(name: str) -> str:
    return _FROM_FDORG.get(name, name)


def _api_enabled() -> bool:
    return bool(os.environ.get("FDORG_API_KEY", "").strip())


def fetch_wc_fixtures() -> list | None:
    """Return raw fixture list from football-data.org or None on error/no key."""
    if not _api_enabled():
        logger.debug("FDORG_API_KEY not configured — skipping fetch")
        return None
    try:
        resp = requests.get(
            f"{FDORG_BASE}/competitions/{WC_CODE}/matches",
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning("football-data.org rate limit hit — will retry later")
            return None
        resp.raise_for_status()
        return resp.json().get("matches", [])
    except requests.RequestException as exc:
        logger.error("football-data.org request failed: %s", exc)
        return None


def sync_results(app) -> dict:
    """
    Main sync job — call from scheduler or admin route.

    For each returned fixture:
      - If FINISHED and our match has no result → store result + recalculate points
      - If knockout match has teams now known in fdorg but still placeholder → assign teams

    Returns a summary dict: {"updated_results": N, "updated_teams": N, "skipped": bool}
    """
    from scoring import calculate_match_points
    from models import db, Match, Team

    fixtures = fetch_wc_fixtures()
    if fixtures is None:
        return {"updated_results": 0, "updated_teams": 0, "skipped": True}

    updated_results = 0
    updated_teams = 0

    with app.app_context():
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        # Build fast lookups
        team_by_name: dict[str, Team] = {t.name: t for t in Team.query.all()}
        # Also index by fdorg name
        team_by_fdorg: dict[str, Team] = {
            _to_fdorg(name): t for name, t in team_by_name.items()
        }

        for fx in fixtures:
            status = fx.get("status", "")
            home_fdorg = fx.get("homeTeam", {}).get("name", "")
            away_fdorg = fx.get("awayTeam", {}).get("name", "")
            home_team = team_by_fdorg.get(home_fdorg) or team_by_name.get(_from_fdorg(home_fdorg))
            away_team = team_by_fdorg.get(away_fdorg) or team_by_name.get(_from_fdorg(away_fdorg))

            # Parse kickoff time from fixture
            fx_dt = None
            utc_str = fx.get("utcDate", "")
            if utc_str:
                try:
                    fx_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    pass

            # --- Find our matching Match row ---
            match: Match | None = None

            if home_team and away_team:
                # Group stage or knockout where teams already assigned
                match = Match.query.filter_by(
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                ).first()

            if match is None and fx_dt and (home_team or away_team):
                # Knockout match: teams just became known → find by date proximity
                # and at least one team matching
                candidates = Match.query.filter(
                    Match.stage != "group",
                    Match.kickoff_utc >= fx_dt - timedelta(hours=3),
                    Match.kickoff_utc <= fx_dt + timedelta(hours=3),
                ).all()
                for c in candidates:
                    # Accept if at least one team matches and teams were placeholders
                    if c.home_team_id is None or c.away_team_id is None:
                        if (home_team and c.home_team_id is None) or (
                            away_team and c.away_team_id is None
                        ):
                            match = c
                            break

            if match is None:
                continue

            # --- Assign knockout teams if now known ---
            teams_changed = False
            if home_team and match.home_team_id is None:
                match.home_team_id = home_team.id
                teams_changed = True
            if away_team and match.away_team_id is None:
                match.away_team_id = away_team.id
                teams_changed = True
            if teams_changed:
                db.session.commit()
                updated_teams += 1
                logger.info(
                    "Teams set for %s: %s vs %s",
                    match.match_code,
                    match.home_name,
                    match.away_name,
                )

            # --- Store result if match finished and result not yet stored ---
            if status == "FINISHED" and match.actual_home is None:
                score = fx.get("score", {})
                ft = score.get("fullTime", {})  # always 90-min score
                home_goals = ft.get("home")
                away_goals = ft.get("away")

                if home_goals is not None and away_goals is not None:
                    match.actual_home = int(home_goals)
                    match.actual_away = int(away_goals)
                    match.result_entered_at = now_utc
                    db.session.commit()

                    # Recalculate points for every prediction on this match
                    for pred in match.predictions:
                        pred.points = calculate_match_points(
                            pred.pred_home, pred.pred_away,
                            match.actual_home, match.actual_away,
                            match.stage,
                        )
                    db.session.commit()
                    updated_results += 1
                    logger.info(
                        "Result synced: %s  %d–%d  %s",
                        match.home_name, home_goals, away_goals, match.away_name,
                    )

    return {
        "updated_results": updated_results,
        "updated_teams": updated_teams,
        "skipped": False,
    }

"""Run once to populate the database with teams and all 104 matches.

Usage:
    python seed.py
"""

import json
import os
from datetime import datetime, timezone

from app import app
from models import db, Team, Match

GROUPS = {
    "A": ["Mexico", "South Africa", "Korea Republic", "Czechia"],
    "B": ["Canada", "Bosnia & Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["USA", "Paraguay", "Australia", "Türkiye"],
    "E": ["Germany", "Ivory Coast", "Ecuador", "Curaçao"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "DR Congo", "Uzbekistan", "Colombia"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}

# ISO 3166-1 alpha-2 flag codes (lowercase)
FLAG_CODES = {
    "Mexico": "mx", "South Africa": "za", "Korea Republic": "kr", "Czechia": "cz",
    "Canada": "ca", "Bosnia & Herzegovina": "ba", "Qatar": "qa", "Switzerland": "ch",
    "Brazil": "br", "Morocco": "ma", "Haiti": "ht", "Scotland": "gb-sct",
    "USA": "us", "Paraguay": "py", "Australia": "au", "Türkiye": "tr",
    "Germany": "de", "Ivory Coast": "ci", "Ecuador": "ec", "Curaçao": "cw",
    "Netherlands": "nl", "Japan": "jp", "Sweden": "se", "Tunisia": "tn",
    "Belgium": "be", "Egypt": "eg", "Iran": "ir", "New Zealand": "nz",
    "Spain": "es", "Cape Verde": "cv", "Saudi Arabia": "sa", "Uruguay": "uy",
    "France": "fr", "Senegal": "sn", "Iraq": "iq", "Norway": "no",
    "Argentina": "ar", "Algeria": "dz", "Austria": "at", "Jordan": "jo",
    "Portugal": "pt", "DR Congo": "cd", "Uzbekistan": "uz", "Colombia": "co",
    "England": "gb-eng", "Croatia": "hr", "Ghana": "gh", "Panama": "pa",
}


def seed():
    with app.app_context():
        db.create_all()

        # Skip if already seeded (safe for redeployments)
        if Team.query.count() > 0:
            print("Database already seeded — skipping.")
            return

        # Teams
        team_map = {}
        for group_letter, team_names in GROUPS.items():
            for name in team_names:
                team = Team(
                    name=name,
                    group_letter=group_letter,
                    flag_code=FLAG_CODES.get(name, ""),
                )
                db.session.add(team)
                db.session.flush()
                team_map[name] = team

        # Matches
        matches_path = os.path.join(os.path.dirname(__file__), "data", "matches.json")
        with open(matches_path, encoding="utf-8") as f:
            match_data = json.load(f)

        for m in match_data:
            ko_str = m["kickoff_utc"].replace("Z", "+00:00")
            kickoff = datetime.fromisoformat(ko_str).replace(tzinfo=timezone.utc)

            match = Match(
                match_code=m["code"],
                stage=m["stage"],
                group_letter=m.get("group"),
                home_team_id=team_map[m["home"]].id if m.get("home") else None,
                away_team_id=team_map[m["away"]].id if m.get("away") else None,
                home_placeholder=m.get("home_placeholder"),
                away_placeholder=m.get("away_placeholder"),
                kickoff_utc=kickoff.replace(tzinfo=None),  # store naive UTC
                venue=m.get("venue"),
                is_final_day=m.get("final_day", False),
            )
            db.session.add(match)

        db.session.commit()
        print(f"Seeded {len(team_map)} teams and {len(match_data)} matches.")


if __name__ == "__main__":
    seed()

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    pin_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    predictions = db.relationship("Prediction", backref="user", lazy=True)
    tourn_pred = db.relationship(
        "TournamentPrediction", backref="user", uselist=False, lazy=True
    )


class Team(db.Model):
    __tablename__ = "teams"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    group_letter = db.Column(db.String(1), nullable=False)
    flag_code = db.Column(db.String(10))  # ISO 3166-1 alpha-2, lowercase


class Match(db.Model):
    __tablename__ = "matches"
    id = db.Column(db.Integer, primary_key=True)
    match_code = db.Column(db.String(20), unique=True, nullable=False)  # e.g. "A1", "R32_01"
    stage = db.Column(db.String(30), nullable=False)  # group / round_of_32 / round_of_16 / quarter_final / semi_final / third_place / final
    group_letter = db.Column(db.String(1))  # only for group stage
    home_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"))
    away_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"))
    home_placeholder = db.Column(db.String(30))  # e.g. "Winner Group A"
    away_placeholder = db.Column(db.String(30))
    kickoff_utc = db.Column(db.DateTime, nullable=False)
    venue = db.Column(db.String(200))
    actual_home = db.Column(db.Integer)
    actual_away = db.Column(db.Integer)
    result_entered_at = db.Column(db.DateTime)
    is_final_day = db.Column(db.Boolean, default=False)  # both group matches same time

    home_team = db.relationship("Team", foreign_keys=[home_team_id], lazy="joined")
    away_team = db.relationship("Team", foreign_keys=[away_team_id], lazy="joined")
    predictions = db.relationship("Prediction", backref="match", lazy=True)

    @property
    def is_locked(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        ko = self.kickoff_utc
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
        return now >= ko - timedelta(minutes=30)

    @property
    def has_result(self):
        return self.actual_home is not None and self.actual_away is not None

    @property
    def home_name(self):
        if self.home_team:
            return self.home_team.name
        return self.home_placeholder or "TBD"

    @property
    def away_name(self):
        if self.away_team:
            return self.away_team.name
        return self.away_placeholder or "TBD"


class Prediction(db.Model):
    __tablename__ = "predictions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)
    pred_home = db.Column(db.Integer, nullable=False)
    pred_away = db.Column(db.Integer, nullable=False)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    points = db.Column(db.Integer)  # null until result entered

    __table_args__ = (db.UniqueConstraint("user_id", "match_id"),)


class TournamentPrediction(db.Model):
    __tablename__ = "tourn_preds"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    winner_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"))
    topscorer_goals = db.Column(db.Integer)
    red_cards = db.Column(db.Integer)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    pts_winner = db.Column(db.Integer)
    pts_topscorer = db.Column(db.Integer)
    pts_redcards = db.Column(db.Integer)

    winner_team = db.relationship("Team", lazy="joined")

    @property
    def total_special_points(self):
        pts = [self.pts_winner, self.pts_topscorer, self.pts_redcards]
        return sum(p for p in pts if p is not None)


# Tournament-level results stored in a single-row settings table
class TournamentResult(db.Model):
    __tablename__ = "tourn_result"
    id = db.Column(db.Integer, primary_key=True)
    winner_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"))
    topscorer_goals = db.Column(db.Integer)
    red_cards_total = db.Column(db.Integer)
    entered_at = db.Column(db.DateTime)

    winner_team = db.relationship("Team")

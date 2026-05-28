import os
from datetime import datetime, timezone, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

from config import Config
from models import Match, Prediction, Team, TournamentPrediction, TournamentResult, User, db
from scoring import (
    STAGE_LABELS,
    STAGE_MULTIPLIERS,
    calculate_match_points,
    calculate_special_points,
)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tournament_locked():
    lock = Config.TOURNAMENT_LOCK_UTC.replace(tzinfo=None)
    return _now_utc() >= lock


def _get_user():
    uid = session.get("user_id")
    if uid:
        return db.session.get(User, uid)
    return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _recalc_match(match):
    """Recalculate points for all predictions of a match that has a result."""
    if not match.has_result:
        return
    for pred in match.predictions:
        pred.points = calculate_match_points(
            pred.pred_home, pred.pred_away,
            match.actual_home, match.actual_away,
            match.stage,
        )
    db.session.commit()


def _recalc_special():
    """Recalculate special prediction points based on TournamentResult."""
    result = TournamentResult.query.first()
    if not result:
        return
    for tp in TournamentPrediction.query.all():
        pts_w, pts_t, pts_r = calculate_special_points(
            tp.winner_team_id, result.winner_team_id,
            tp.topscorer_goals, result.topscorer_goals,
            tp.red_cards, result.red_cards_total,
        )
        tp.pts_winner = pts_w
        tp.pts_topscorer = pts_t
        tp.pts_redcards = pts_r
    db.session.commit()


def _leaderboard_data():
    users = User.query.all()
    rows = []
    for user in users:
        match_pts = sum(p.points for p in user.predictions if p.points is not None)
        tp = user.tourn_pred
        special_pts = tp.total_special_points if tp else 0
        rows.append({
            "user": user,
            "match_points": match_pts,
            "special_points": special_pts,
            "total": match_pts + special_pts,
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    # Assign ranks (ties share rank)
    rank = 1
    for i, row in enumerate(rows):
        if i > 0 and row["total"] < rows[i - 1]["total"]:
            rank = i + 1
        row["rank"] = rank
    return rows


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("home"))
    if session.get("is_admin"):
        return redirect(url_for("admin"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pin = request.form.get("pin", "").strip()

        # Admin check
        if pin == Config.ADMIN_PIN:
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin"))

        # User lookup — try each user
        matched = None
        for user in User.query.all():
            if check_password_hash(user.pin_hash, pin):
                matched = user
                break

        if matched:
            session.clear()
            session["user_id"] = matched.id
            return redirect(url_for("home"))

        flash("Ongeldige code. Probeer opnieuw.")
    return render_template("login.html", kiosk_timeout=Config.KIOSK_TIMEOUT_SECONDS)


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# User routes
# ---------------------------------------------------------------------------

@app.route("/home")
@login_required
def home():
    user = _get_user()
    tourn_pred = user.tourn_pred
    tourn_locked = _tournament_locked()
    # Count filled match predictions
    filled = Prediction.query.filter_by(user_id=user.id).count()
    total_matches = Match.query.count()
    return render_template(
        "home.html",
        user=user,
        tourn_pred=tourn_pred,
        tourn_locked=tourn_locked,
        filled=filled,
        total_matches=total_matches,
        kiosk_timeout=Config.KIOSK_TIMEOUT_SECONDS,
    )


@app.route("/special", methods=["GET", "POST"])
@login_required
def special():
    user = _get_user()
    tourn_locked = _tournament_locked()
    teams = Team.query.order_by(Team.group_letter, Team.name).all()

    if request.method == "POST":
        if tourn_locked:
            flash("Voorspellingen zijn gesloten — het toernooi is begonnen.")
            return redirect(url_for("special"))

        winner_id = request.form.get("winner_team_id", type=int)
        topscorer = request.form.get("topscorer_goals", type=int)
        redcards = request.form.get("red_cards", type=int)

        tp = user.tourn_pred
        if tp is None:
            tp = TournamentPrediction(user_id=user.id)
            db.session.add(tp)

        tp.winner_team_id = winner_id
        tp.topscorer_goals = topscorer
        tp.red_cards = redcards
        tp.submitted_at = _now_utc()
        db.session.commit()
        flash("Voorspellingen opgeslagen!")
        return redirect(url_for("home"))

    return render_template(
        "special.html",
        user=user,
        teams=teams,
        tourn_pred=user.tourn_pred,
        tourn_locked=tourn_locked,
        kiosk_timeout=Config.KIOSK_TIMEOUT_SECONDS,
    )


@app.route("/matches")
@login_required
def matches():
    user = _get_user()
    all_matches = Match.query.order_by(Match.kickoff_utc).all()

    # Group by stage then date
    user_preds = {p.match_id: p for p in Prediction.query.filter_by(user_id=user.id).all()}

    stages_order = [
        "group", "round_of_32", "round_of_16",
        "quarter_final", "semi_final", "third_place", "final",
    ]
    grouped = {s: [] for s in stages_order}
    for m in all_matches:
        grouped[m.stage].append({
            "match": m,
            "prediction": user_preds.get(m.id),
            "locked": m.is_locked,
        })

    return render_template(
        "matches.html",
        user=user,
        grouped=grouped,
        stage_labels=STAGE_LABELS,
        stages_order=stages_order,
        kiosk_timeout=Config.KIOSK_TIMEOUT_SECONDS,
    )


@app.route("/match/<match_code>", methods=["GET", "POST"])
@login_required
def predict(match_code):
    user = _get_user()
    match = Match.query.filter_by(match_code=match_code).first_or_404()

    existing = Prediction.query.filter_by(user_id=user.id, match_id=match.id).first()

    if request.method == "POST":
        if match.is_locked:
            flash("Deze wedstrijd is gesloten voor voorspellingen.")
            return redirect(url_for("predict", match_code=match_code))

        pred_home = request.form.get("pred_home", type=int)
        pred_away = request.form.get("pred_away", type=int)

        if pred_home is None or pred_away is None or pred_home < 0 or pred_away < 0:
            flash("Voer geldige scores in (geheel getal ≥ 0).")
            return redirect(url_for("predict", match_code=match_code))

        if existing:
            existing.pred_home = pred_home
            existing.pred_away = pred_away
            existing.submitted_at = _now_utc()
            existing.points = None  # reset until result re-entered
        else:
            pred = Prediction(
                user_id=user.id,
                match_id=match.id,
                pred_home=pred_home,
                pred_away=pred_away,
            )
            db.session.add(pred)

        db.session.commit()
        flash("Voorspelling opgeslagen!")
        return redirect(url_for("matches"))

    multiplier = STAGE_MULTIPLIERS.get(match.stage, 1)
    return render_template(
        "predict.html",
        user=user,
        match=match,
        prediction=existing,
        stage_label=STAGE_LABELS.get(match.stage, match.stage),
        multiplier=multiplier,
        kiosk_timeout=Config.KIOSK_TIMEOUT_SECONDS,
    )


@app.route("/leaderboard")
def leaderboard():
    rows = _leaderboard_data()
    result = TournamentResult.query.first()
    return render_template(
        "leaderboard.html",
        rows=rows,
        result=result,
        kiosk_timeout=Config.KIOSK_TIMEOUT_SECONDS,
        is_admin=session.get("is_admin", False),
        current_user_id=session.get("user_id"),
    )


@app.route("/api/leaderboard")
def api_leaderboard():
    rows = _leaderboard_data()
    return jsonify([
        {
            "rank": r["rank"],
            "name": r["user"].name,
            "match_points": r["match_points"],
            "special_points": r["special_points"],
            "total": r["total"],
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    matches_with_results = Match.query.order_by(Match.kickoff_utc).all()
    result = TournamentResult.query.first()
    teams = Team.query.order_by(Team.group_letter, Team.name).all()
    users = User.query.order_by(User.name).all()
    return render_template(
        "admin.html",
        matches=matches_with_results,
        result=result,
        teams=teams,
        users=users,
        stage_labels=STAGE_LABELS,
    )


@app.route("/admin/result/<match_code>", methods=["POST"])
@admin_required
def admin_result(match_code):
    match = Match.query.filter_by(match_code=match_code).first_or_404()
    home = request.form.get("actual_home", type=int)
    away = request.form.get("actual_away", type=int)

    if home is None or away is None or home < 0 or away < 0:
        flash("Ongeldige score ingevoerd.")
        return redirect(url_for("admin"))

    match.actual_home = home
    match.actual_away = away
    match.result_entered_at = _now_utc()
    db.session.commit()
    _recalc_match(match)
    flash(f"Resultaat opgeslagen: {match.home_name} {home}–{away} {match.away_name}")
    return redirect(url_for("admin"))


@app.route("/admin/set-teams/<match_code>", methods=["POST"])
@admin_required
def admin_set_teams(match_code):
    match = Match.query.filter_by(match_code=match_code).first_or_404()
    home_id = request.form.get("home_team_id", type=int)
    away_id = request.form.get("away_team_id", type=int)
    if home_id:
        match.home_team_id = home_id
    if away_id:
        match.away_team_id = away_id
    db.session.commit()
    flash("Teams bijgewerkt.")
    return redirect(url_for("admin"))


@app.route("/admin/special-result", methods=["POST"])
@admin_required
def admin_special_result():
    winner_id = request.form.get("winner_team_id", type=int)
    topscorer = request.form.get("topscorer_goals", type=int)
    redcards = request.form.get("red_cards_total", type=int)

    result = TournamentResult.query.first()
    if result is None:
        result = TournamentResult()
        db.session.add(result)

    result.winner_team_id = winner_id
    result.topscorer_goals = topscorer
    result.red_cards_total = redcards
    result.entered_at = _now_utc()
    db.session.commit()
    _recalc_special()
    flash("Toernooiresultaten opgeslagen en punten herberekend.")
    return redirect(url_for("admin"))


@app.route("/admin/users", methods=["POST"])
@admin_required
def admin_add_user():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Naam is verplicht.")
        return redirect(url_for("admin"))

    # Generate a unique 4-digit PIN
    import random
    existing_hashes = [u.pin_hash for u in User.query.all()]
    for _ in range(1000):
        pin = str(random.randint(1000, 9999))
        # Check that this PIN doesn't collide
        clash = any(check_password_hash(h, pin) for h in existing_hashes)
        if not clash:
            break
    else:
        flash("Kon geen unieke PIN genereren.")
        return redirect(url_for("admin"))

    user = User(name=name, pin_hash=generate_password_hash(pin))
    db.session.add(user)
    db.session.commit()
    flash(f"Gebruiker '{name}' aangemaakt met PIN: {pin}  (noteer deze nu!)")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    user = db.session.get(User, user_id)
    if user:
        Prediction.query.filter_by(user_id=user_id).delete()
        TournamentPrediction.query.filter_by(user_id=user_id).delete()
        db.session.delete(user)
        db.session.commit()
        flash(f"Gebruiker '{user.name}' verwijderd.")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

@app.template_filter("cest")
def to_cest(dt):
    """Convert naive UTC datetime to CEST (UTC+2) string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    cest = dt + timedelta(hours=2)
    return cest.strftime("%a %d %b %H:%M")


@app.template_filter("cest_date")
def to_cest_date(dt):
    if dt is None:
        return ""
    cest = dt + timedelta(hours=2)
    return cest.strftime("%d %b %Y")


@app.template_filter("cest_time")
def to_cest_time(dt):
    if dt is None:
        return ""
    cest = dt + timedelta(hours=2)
    return cest.strftime("%H:%M")


@app.route("/admin/sync", methods=["POST"])
@admin_required
def admin_sync():
    """Trigger an immediate result sync from football-data.org."""
    from fetcher import sync_results
    summary = sync_results(app)
    if summary["skipped"]:
        flash("Sync overgeslagen — FDORG_API_KEY niet ingesteld.")
    else:
        flash(
            f"Sync klaar: {summary['updated_results']} resultaten bijgewerkt, "
            f"{summary['updated_teams']} teams ingevuld."
        )
    return redirect(url_for("admin"))


if __name__ == "__main__":
    import scheduler
    scheduler.start(app)
    app.run(debug=True, host="0.0.0.0", port=5000)

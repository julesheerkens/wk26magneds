import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date as date_type
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
# Avatar colours (deterministic per user id)
# ---------------------------------------------------------------------------

_AVATAR_COLORS = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
    "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#82E0AA",
    "#F8C471", "#85C1E9", "#F1948A", "#A9CCE3", "#A2D9CE",
]


@app.template_filter("avatar_color")
def avatar_color(user_id):
    return _AVATAR_COLORS[int(user_id) % len(_AVATAR_COLORS)]


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

        if pin == Config.ADMIN_PIN:
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin"))

        matched = None
        for user in User.query.all():
            if check_password_hash(user.pin_hash, pin):
                matched = user
                break

        if matched:
            session.clear()
            session["user_id"] = matched.id
            return redirect(url_for("home"))

        flash("Ongeldige code — probeer opnieuw.")
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
    """Smart redirect: first-time users go to special predictions, else to matches."""
    user = _get_user()
    if user.tourn_pred is None and not _tournament_locked():
        return redirect(url_for("special"))
    return redirect(url_for("matches"))


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
        flash("Voorspellingen opgeslagen! 🎉")
        return redirect(url_for("matches"))

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

    # Group by CEST date (UTC+2)
    days = defaultdict(list)
    for m in all_matches:
        cest_date = (m.kickoff_utc + timedelta(hours=2)).date()
        days[cest_date].append(m)

    sorted_dates = sorted(days.keys())

    # Selected date from query param
    selected_date = None
    selected_str = request.args.get("date")
    if selected_str:
        try:
            selected_date = date_type.fromisoformat(selected_str)
        except (ValueError, TypeError):
            pass

    if selected_date not in days:
        today = _now_utc().date()
        upcoming = [d for d in sorted_dates if d >= today]
        selected_date = upcoming[0] if upcoming else (sorted_dates[-1] if sorted_dates else today)

    day_matches = days.get(selected_date, [])

    user_preds = {p.match_id: p for p in Prediction.query.filter_by(user_id=user.id).all()}

    # Prev / next dates
    if sorted_dates and selected_date in sorted_dates:
        idx = sorted_dates.index(selected_date)
        prev_date = sorted_dates[idx - 1] if idx > 0 else None
        next_date = sorted_dates[idx + 1] if idx < len(sorted_dates) - 1 else None
    else:
        prev_date = next_date = None

    total_day = len(day_matches)
    predicted_day = sum(1 for m in day_matches if m.id in user_preds)

    return render_template(
        "matches.html",
        user=user,
        day_matches=day_matches,
        user_preds=user_preds,
        selected_date=selected_date,
        prev_date=prev_date,
        next_date=next_date,
        total_day=total_day,
        predicted_day=predicted_day,
        kiosk_timeout=Config.KIOSK_TIMEOUT_SECONDS,
    )


@app.route("/matches/save", methods=["POST"])
@login_required
def matches_save():
    """Bulk save all predictions submitted from the day view."""
    user = _get_user()
    redirect_date = request.form.get("date", "")
    saved = 0

    for key in request.form:
        if not key.startswith("pred_home_"):
            continue
        try:
            match_id = int(key[len("pred_home_"):])
        except ValueError:
            continue

        home_str = request.form.get(f"pred_home_{match_id}", "").strip()
        away_str = request.form.get(f"pred_away_{match_id}", "").strip()
        if home_str == "" or away_str == "":
            continue

        try:
            pred_home = int(home_str)
            pred_away = int(away_str)
        except ValueError:
            continue

        if pred_home < 0 or pred_away < 0:
            continue

        match = db.session.get(Match, match_id)
        if not match or match.is_locked:
            continue

        existing = Prediction.query.filter_by(user_id=user.id, match_id=match_id).first()
        if existing:
            existing.pred_home = pred_home
            existing.pred_away = pred_away
            existing.submitted_at = _now_utc()
            existing.points = None
        else:
            db.session.add(Prediction(
                user_id=user.id,
                match_id=match_id,
                pred_home=pred_home,
                pred_away=pred_away,
            ))
        saved += 1

    db.session.commit()

    if saved:
        flash(f"{saved} voorspelling{'en' if saved != 1 else ''} opgeslagen! 🎉")
    else:
        flash("Niets opgeslagen — vul beide scores in.")

    url = url_for("matches")
    if redirect_date:
        url += f"?date={redirect_date}"
    return redirect(url)


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
            existing.points = None
        else:
            db.session.add(Prediction(
                user_id=user.id, match_id=match.id,
                pred_home=pred_home, pred_away=pred_away,
            ))

        db.session.commit()
        flash("Voorspelling opgeslagen! 🎉")
        return redirect(url_for("matches"))

    multiplier = STAGE_MULTIPLIERS.get(match.stage, 1)
    return render_template(
        "predict.html",
        user=user, match=match, prediction=existing,
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
        rows=rows, result=result,
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
    matches_all = Match.query.order_by(Match.kickoff_utc).all()
    result = TournamentResult.query.first()
    teams = Team.query.order_by(Team.group_letter, Team.name).all()
    users = User.query.order_by(User.name).all()
    return render_template(
        "admin.html",
        matches=matches_all, result=result,
        teams=teams, users=users,
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
    flash(f"Resultaat: {match.home_name} {home}–{away} {match.away_name}")
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
    import random
    name = request.form.get("name", "").strip()
    if not name:
        flash("Naam is verplicht.")
        return redirect(url_for("admin"))

    existing_hashes = [u.pin_hash for u in User.query.all()]
    for _ in range(1000):
        pin = str(random.randint(1000, 9999))
        if not any(check_password_hash(h, pin) for h in existing_hashes):
            break
    else:
        flash("Kon geen unieke PIN genereren.")
        return redirect(url_for("admin"))

    db.session.add(User(name=name, pin_hash=generate_password_hash(pin)))
    db.session.commit()
    flash(f"'{name}' toegevoegd — PIN: {pin}  (noteer dit!)")
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


@app.route("/admin/sync", methods=["POST"])
@admin_required
def admin_sync():
    from fetcher import sync_results
    summary = sync_results(app)
    if summary["skipped"]:
        flash("Sync overgeslagen — FDORG_API_KEY niet ingesteld.")
    else:
        flash(
            f"Sync klaar: {summary['updated_results']} resultaten, "
            f"{summary['updated_teams']} teams bijgewerkt."
        )
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

@app.template_filter("cest")
def to_cest(dt):
    if dt is None:
        return ""
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


if __name__ == "__main__":
    import scheduler
    scheduler.start(app)
    app.run(debug=True, host="0.0.0.0", port=5000)

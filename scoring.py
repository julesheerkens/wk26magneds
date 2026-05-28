import math

STAGE_MULTIPLIERS = {
    "group": 1,
    "round_of_32": 1.5,
    "round_of_16": 2,
    "quarter_final": 3,
    "semi_final": 4,
    "third_place": 3,
    "final": 5,
}

STAGE_LABELS = {
    "group": "Groepsfase",
    "round_of_32": "Ronde van 32",
    "round_of_16": "Ronde van 16",
    "quarter_final": "Kwartfinale",
    "semi_final": "Halve finale",
    "third_place": "3e plaats",
    "final": "Finale",
}


def _outcome(home, away):
    if home > away:
        return "H"
    if home < away:
        return "A"
    return "D"


def calculate_match_points(pred_home, pred_away, actual_home, actual_away, stage):
    """Return points for a single match prediction.

    Scoring:
        Exact score:                    4 pts
        Correct outcome + correct GD:   2 pts
        Correct outcome only:           1 pt
        Wrong outcome:                  0 pts

    Multiplied by stage factor, rounded up.
    """
    p_out = _outcome(pred_home, pred_away)
    a_out = _outcome(actual_home, actual_away)

    if p_out != a_out:
        base = 0
    elif pred_home == actual_home and pred_away == actual_away:
        base = 4
    elif (pred_home - pred_away) == (actual_home - actual_away):
        base = 2
    else:
        base = 1

    multiplier = STAGE_MULTIPLIERS.get(stage, 1)
    return math.ceil(base * multiplier)


def calculate_special_points(
    pred_winner_id,
    actual_winner_id,
    pred_topscorer,
    actual_topscorer,
    pred_redcards,
    actual_redcards,
):
    """Return (pts_winner, pts_topscorer, pts_redcards).

    Tournament winner:  15 pts exact
    Top scorer goals:   10 pts exact | 5 pts within ±2
    Red cards total:    8 pts exact  | 4 pts within ±5 | 2 pts within ±10
    """
    pts_winner = 15 if (pred_winner_id and pred_winner_id == actual_winner_id) else 0

    if actual_topscorer is None or pred_topscorer is None:
        pts_topscorer = None
    elif pred_topscorer == actual_topscorer:
        pts_topscorer = 10
    elif abs(pred_topscorer - actual_topscorer) <= 2:
        pts_topscorer = 5
    else:
        pts_topscorer = 0

    if actual_redcards is None or pred_redcards is None:
        pts_redcards = None
    elif pred_redcards == actual_redcards:
        pts_redcards = 8
    elif abs(pred_redcards - actual_redcards) <= 5:
        pts_redcards = 4
    elif abs(pred_redcards - actual_redcards) <= 10:
        pts_redcards = 2
    else:
        pts_redcards = 0

    return pts_winner, pts_topscorer, pts_redcards

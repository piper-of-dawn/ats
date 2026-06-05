from ats.fundamentals.combined_score import METRIC_WEIGHTS


def test_combined_score_weights_prioritize_momentum():
    assert METRIC_WEIGHTS == {
        "stm": 0.45,
        "ltm": 0.25,
        "analyst_rating": 0.10,
        "analyst_price_target_deviation": 0.10,
        "beta": 0.10,
    }
    assert METRIC_WEIGHTS["stm"] > METRIC_WEIGHTS["ltm"]

    other_weights = {
        metric: weight
        for metric, weight in METRIC_WEIGHTS.items()
        if metric not in {"stm", "ltm"}
    }
    assert len(set(other_weights.values())) == 1
    assert all(METRIC_WEIGHTS["ltm"] > weight for weight in other_weights.values())

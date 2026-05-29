def _get_price_target_value(price_targets: dict, *candidate_keys: str) -> float:
    for key in candidate_keys:
        if key in price_targets and price_targets[key] is not None:
            return float(price_targets[key])
    raise ValueError(f"Missing price target keys: {candidate_keys}")


def median_centered_score(price_targets: dict) -> float:
    current_price = _get_price_target_value(price_targets, "current", "currentPrice")
    high_price = _get_price_target_value(price_targets, "high", "targetHighPrice")
    low_price = _get_price_target_value(price_targets, "low", "targetLowPrice")
    median_price = _get_price_target_value(price_targets, "median", "targetMedianPrice")

    scale = max(high_price - median_price, median_price - low_price)
    if scale == 0:
        return 0.0
    return (current_price - median_price) / scale

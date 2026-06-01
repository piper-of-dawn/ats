import argparse

import polars as pl

from ats.dataIO.supabase_integration import (
    batch_insert_polars_df,
    fetch_recent_dates,
    fetch_rows_for_date,
)


METRIC_WEIGHTS = {
    "stm": 0.45,
    "ltm": 0.25,
    "analyst_rating": 0.10,
    "analyst_price_target_deviation": 0.10,
    "beta": 0.10,
}

METRIC_DIRECTIONS = {
    "stm": 1,
    "ltm": 1,
    "analyst_rating": 1,
    "analyst_price_target_deviation": -1,
    "beta": -1,
}

RATING_COLUMN_CANDIDATES = ("analyst_rating", "rating")
DERIVED_METRIC_COLUMNS = {}


def _metric_quantile_name(metric: str) -> str:
    return f"{metric}_quantile"


def _resolve_metric_columns(df: pl.DataFrame) -> dict[str, str]:
    metric_columns = {
        metric: DERIVED_METRIC_COLUMNS.get(metric, metric)
        for metric in METRIC_WEIGHTS
        if DERIVED_METRIC_COLUMNS.get(metric, metric) in df.columns
    }
    if "analyst_rating" not in metric_columns:
        for candidate in RATING_COLUMN_CANDIDATES:
            if candidate in df.columns:
                metric_columns["analyst_rating"] = candidate
                break
    return metric_columns


def add_derived_metric_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df


def add_metric_quantiles(
    df: pl.DataFrame,
    metric_columns: dict[str, str],
) -> pl.DataFrame:
    quantile_exprs = []
    for metric, source_column in metric_columns.items():
        rank_pct = pl.col(source_column).rank().over("as_of_date") / pl.col(
            source_column
        ).count().over("as_of_date").cast(pl.Float64)
        if METRIC_DIRECTIONS[metric] < 0:
            rank_pct = 1 - rank_pct
        quantile_exprs.append(rank_pct.round(4).alias(_metric_quantile_name(metric)))
    return df.with_columns(quantile_exprs)


def add_weighted_combined_score(
    df: pl.DataFrame,
    metric_columns: dict[str, str],
) -> pl.DataFrame:
    """
    combined_score_i = sum(w_j * q_ij) / sum(w_j)
    where q_ij is ticker i's quantile for metric j and null metrics are skipped.
    """
    weighted_terms = []
    available_weights = []

    for metric in metric_columns:
        quantile_column = _metric_quantile_name(metric)
        weight = METRIC_WEIGHTS[metric]
        weighted_terms.append(
            pl.when(pl.col(quantile_column).is_not_null())
            .then(pl.col(quantile_column) * weight)
            .otherwise(0.0)
        )
        available_weights.append(
            pl.when(pl.col(quantile_column).is_not_null())
            .then(weight)
            .otherwise(0.0)
        )

    weighted_sum = sum(weighted_terms)
    weight_sum = sum(available_weights)

    return df.with_columns(
        pl.when(weight_sum > 0)
        .then(weighted_sum / weight_sum)
        .otherwise(None)
        .round(4)
        .alias("combined_score")
    )


def compute_combined_score(df: pl.DataFrame) -> pl.DataFrame:
    required_key_columns = {"ticker", "as_of_date"}
    missing_key_columns = required_key_columns - set(df.columns)
    if missing_key_columns:
        raise ValueError(f"Missing required columns: {sorted(missing_key_columns)}")

    df = add_derived_metric_columns(df)
    metric_columns = _resolve_metric_columns(df)
    missing_metrics = sorted(set(METRIC_WEIGHTS) - set(metric_columns))
    if missing_metrics:
        raise ValueError(f"Missing metric columns: {missing_metrics}")

    return (
        df.filter(pl.col("ticker").is_not_null() & pl.col("as_of_date").is_not_null())
        .pipe(add_metric_quantiles, metric_columns)
        .pipe(add_weighted_combined_score, metric_columns)
        .select(
            "ticker",
            "as_of_date",
            "combined_score",
            *[_metric_quantile_name(metric) for metric in metric_columns],
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compute a weighted combined score from metric quantiles"
    )
    parser.add_argument("table", nargs="?", help="Table name (positional or via --table)")
    parser.add_argument("--table", dest="table_named", help="Table name (named argument)")
    args = parser.parse_args()

    table_name = args.table_named or args.table
    metrics_table = f"{table_name}_metrics"
    latest_dates = fetch_recent_dates(metrics_table, limit=1)
    if not latest_dates:
        raise ValueError(f"No as_of_date rows found in {metrics_table}")

    df = compute_combined_score(fetch_rows_for_date(metrics_table, latest_dates[0]))

    batch_insert_polars_df(
        df,
        ["ticker", "as_of_date", "combined_score"],
        metrics_table,
        overwrite_conflicts=True,
        conflict_columns=["ticker", "as_of_date"],
    )
    return 0


if __name__ == "__main__":
    main()

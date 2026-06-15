import argparse
import re
from datetime import date

import polars as pl
from dagster import DynamicOut, DynamicOutput, job, multiprocess_executor, op

from ats.dataIO.supabase_integration import batch_insert_polars_df, fetch_table
from ats.fundamentals.combined_score import compute_combined_score
from ats.ticker import EquityTicker

LOG_CONFIG = {"loggers": {"console": {"config": {"log_level": "INFO"}}}}


def source_ticker_symbols_from_database(source_table: str, limit: int | None = None):
    source_ticker_series = fetch_table(source_table)[
        "yahoo_finance_ticker"
    ].drop_nulls()
    tickers = [
        str(ticker).strip() for ticker in source_ticker_series if str(ticker).strip()
    ]
    return tickers[:limit] if limit else tickers


def optional_float(value):
    return None if value is None else float(value)


def empty_equity_factor_metric_row(equity_ticker_symbol: str):
    return {
        "ticker": equity_ticker_symbol,
        "ltm": None,
        "stm": None,
        "beta": None,
        "cbs": None,
        "analyst_price_target_deviation": None,
    }


def compute_equity_factor_metric_row(equity_ticker_symbol: str, market_index: str):
    equity_ticker = (
        EquityTicker(equity_ticker_symbol, EquityTicker(market_index))
        .get_long_term_momentum_signal()
        .get_short_term_momentum_signal()
        .getCombinedRating()
        .getAnalystPriceTargetDeviation()
    )
    return {
        "ticker": equity_ticker_symbol,
        "ltm": float(equity_ticker.ltm),
        "stm": float(equity_ticker.stm),
        "beta": float(equity_ticker.beta),
        "cbs": float(equity_ticker.combined_rating),
        "analyst_price_target_deviation": optional_float(
            equity_ticker.analyst_price_target_deviation
        ),
    }


def build_factor_matrix(equity_factor_metric_rows: list[dict]):
    factor_matrix_without_combined_score = pl.DataFrame(
        equity_factor_metric_rows
    ).with_columns(
        (pl.col("cbs").rank() / pl.col("cbs").count().cast(pl.Float64))
        .round(2)
        .alias("analyst_rating"),
        (
            pl.col("analyst_price_target_deviation").rank()
            / pl.col("analyst_price_target_deviation").count().cast(pl.Float64)
        ).round(2),
        pl.lit(date.today()).alias("as_of_date"),
    )
    combined_scores = compute_combined_score(factor_matrix_without_combined_score)
    return (
        factor_matrix_without_combined_score.join(
            combined_scores.select("ticker", "as_of_date", "combined_score"),
            on=["ticker", "as_of_date"],
            how="left",
        )
        .with_columns(
            pl.col(
                "ltm",
                "stm",
                "beta",
                "analyst_price_target_deviation",
                "analyst_rating",
                "combined_score",
            ).round(2)
        )
        .select(
            "ticker",
            "ltm",
            "stm",
            "beta",
            "as_of_date",
            "analyst_price_target_deviation",
            "analyst_rating",
            "combined_score",
        )
        .sort("ticker")
    )


@op(out=DynamicOut())
def source_tickers_from_database(context):
    for ticker_index, equity_ticker_symbol in enumerate(
        source_ticker_symbols_from_database(context.op_config["source_table"])
    ):
        dynamic_mapping_key = re.sub(
            r"[^A-Za-z0-9_]", "_", f"{ticker_index}_{equity_ticker_symbol}"
        )
        context.log.info(
            "source_tickers_from_database -> compute_equity_factor_metrics[%s]",
            equity_ticker_symbol,
        )
        yield DynamicOutput(equity_ticker_symbol, mapping_key=dynamic_mapping_key)


@op
def compute_equity_factor_metrics(context, equity_ticker_symbol: str):
    try:
        equity_factor_metric_row = compute_equity_factor_metric_row(
            equity_ticker_symbol, context.op_config["market_index"]
        )
    except Exception as exc:
        context.log.warning(
            "compute_equity_factor_metrics[%s] failed: %s",
            equity_ticker_symbol,
            exc,
        )
        equity_factor_metric_row = empty_equity_factor_metric_row(equity_ticker_symbol)
    context.log.info(
        "compute_equity_factor_metrics[%s] -> %s",
        equity_ticker_symbol,
        equity_factor_metric_row,
    )
    return equity_factor_metric_row


@op
def write_factor_metrics_to_database(context, equity_factor_metric_rows: list[dict]):
    factor_matrix = build_factor_matrix(equity_factor_metric_rows)
    target_table_name = context.op_config["target_table"]
    batch_insert_polars_df(
        factor_matrix,
        factor_matrix.columns,
        target_table_name,
        conflict_columns=["ticker", "as_of_date"],
        overwrite_conflicts=True,
    )
    context.log.info(
        "write_factor_matrix_to_database -> %s: inserted %s rows",
        target_table_name,
        factor_matrix.height,
    )


@job(executor_def=multiprocess_executor)
def factor_metrics_job():
    (
        write_factor_metrics_to_database(
            source_tickers_from_database().map(compute_equity_factor_metrics).collect()
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_table")
    parser.add_argument("target_table")
    parser.add_argument("market_index")
    args = parser.parse_args()
    factor_metrics_job.execute_in_process(
        run_config={
            **LOG_CONFIG,
            "ops": {
                "source_tickers_from_database": {
                    "config": {"source_table": args.source_table}
                },
                "compute_equity_factor_metrics": {
                    "config": {"market_index": args.market_index}
                },
                "write_factor_metrics_to_database": {
                    "config": {"target_table": args.target_table}
                },
            },
        }
    )


if __name__ == "__main__":
    main()

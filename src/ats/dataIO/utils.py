from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from functools import wraps

import polars as pl
from tqdm.auto import tqdm


def with_parallel_runner(
    *,
    item_name="ticker",
    result_name=None,
    max_workers=20,
    desc=None,
    unit="item",
):
    def decorator(func):
        @wraps(func)
        def parallel(items, *args, max_workers=max_workers, **kwargs):
            results = []
            errors = []

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_item = {
                    executor.submit(func, item, *args, **kwargs): item
                    for item in items
                }

                for future in tqdm(
                    as_completed(future_to_item),
                    total=len(future_to_item),
                    desc=desc or f"Running {func.__name__}",
                    unit=unit,
                ):
                    item = future_to_item[future]
                    try:
                        value = future.result()
                        result = {item_name: item}
                        if isinstance(value, dict):
                            result.update(value)
                        else:
                            result[result_name or func.__name__.lower()] = value
                        results.append(result)
                    except Exception as exc:
                        errors.append({item_name: item, "error": str(exc)})

            return pl.DataFrame(results), pl.DataFrame(errors)

        func.parallel = parallel
        return func

    return decorator


def build_metric_pivot_frame(
    df: pl.DataFrame,
    metric_columns: list[str],
    created_at: date | None = None,
) -> pl.DataFrame:
    created_at = created_at or date.today()
    pivot_frames = []

    for metric in metric_columns:
        if metric not in df.columns:
            continue
        pivot_frames.append(
            df.select(
                pl.lit(created_at).alias("created_at"),
                pl.col("ticker"),
                pl.lit(metric).alias("metric"),
                pl.col(metric).cast(pl.Float64, strict=False).alias("value"),
            ).filter(pl.col("value").is_not_null())
        )

    if not pivot_frames:
        return pl.DataFrame(
            schema={
                "created_at": pl.Date,
                "ticker": pl.Utf8,
                "metric": pl.Utf8,
                "value": pl.Float64,
            }
        )

    return pl.concat(pivot_frames, how="vertical")

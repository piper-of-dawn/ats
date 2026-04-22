from concurrent.futures import ThreadPoolExecutor, as_completed
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

"""Standalone CLI: collect-stocks AAPL MSFT, or --tickers-file universe.txt."""

import argparse
import json
import logging
import re
import sys
from contextlib import nullcontext
from pathlib import Path

from dotenv import load_dotenv

from .checkpoints import Checkpoints
from .collector import DEFAULT_LOG_PATH, MASSIVE_DATASETS, _checkpoint_window, _datasets, _logger, _symbol, iter_tickers
from .massive import resolve_api_key
from .option_metrics import OptionSettings


def _read_tickers(path: Path) -> list[str]:
    text = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())
    return [symbol for symbol in re.split(r"[,\s]+", text.strip()) if symbol]


def main(argv=None, *, backfill=False) -> int:
    parser = argparse.ArgumentParser(description="Collect free Yahoo and Massive inputs as JSON lines")
    parser.add_argument("tickers", nargs="*", help="Selected stock symbols")
    parser.add_argument("--tickers-file", type=Path, help="Ticker list separated by newlines, commas or spaces")
    parser.add_argument("--start", required=backfill, help="Inclusive historical start date, YYYY-MM-DD")
    parser.add_argument("--end", required=backfill, help="Inclusive historical end date, YYYY-MM-DD")
    parser.add_argument("--datasets", nargs="+", default=["prices", "options"] if backfill else None, help="Only collect these named feeds")
    parser.add_argument("--options-history-days", type=int, default=-1 if backfill else 0,
                        help="0: latest session; -1: full date range (backfill); positive: trailing calendar days")
    parser.add_argument("--option-target-days", type=int, default=30, help="Minimum option tenor in calendar days")
    parser.add_argument("--rv-window", type=int, default=21, help="Trailing daily returns for realized volatility")
    parser.add_argument("--risk-free-rate", type=float, default=0.04, help="Annual continuously compounded rate as a decimal")
    parser.add_argument("--dividend-yield", type=float, default=0.0, help="Annual continuous dividend yield as a decimal")
    parser.add_argument("--env-file", type=Path, help="Explicit .env file containing MASSIVE_API_KEY or POLYGON_IO")
    parser.add_argument("--api-key-file", type=Path, help="Plain-text Massive key file; overrides environment keys")
    parser.add_argument("--price-mode", choices=("ticker", "bulk"), default="ticker" if backfill else "bulk")
    parser.add_argument("--market-cache", type=Path, help="Persistent shared cache for market-wide daily price responses")
    parser.add_argument("--output", type=Path, required=backfill, help="JSONL output, rebuilt from checkpoints on restart")
    parser.add_argument("--checkpoint", type=Path, help="SQLite checkpoints; defaults to OUTPUT.checkpoint.sqlite")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_PATH, help="Rotating collection log")
    parser.add_argument("--log-to-stderr", action="store_true", help="Also stream progress to the console")
    args = parser.parse_args(argv)
    if args.env_file:
        load_dotenv(args.env_file)
    checkpoint_path = args.checkpoint
    if checkpoint_path is None and args.output:
        checkpoint_path = Path(str(args.output) + ".checkpoint.sqlite")
    paths = [p.resolve() for p in (args.output, checkpoint_path, args.log_file, args.market_cache) if p is not None]
    if len(paths) != len(set(paths)):
        parser.error("Output, checkpoint, market cache and log must use different files")
    log = _logger(args.log_file)
    if args.output and args.output.exists() and not (checkpoint_path and checkpoint_path.exists()):
        log.error("Existing output has no checkpoint; choose a new output path")
        parser.error("Output exists without a checkpoint; choose a new output path to preserve that file")
    failed = False
    console = None
    if args.log_to_stderr:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(log.handlers[0].formatter)
        log.addHandler(console)
    try:
        # Validate everything before rebuilding an existing JSONL output.
        tickers = args.tickers + (_read_tickers(args.tickers_file) if args.tickers_file else [])
        symbols = list(dict.fromkeys(_symbol(symbol) for symbol in tickers))
        if not symbols:
            raise ValueError("Supply ticker symbols or --tickers-file")
        names = [name for group in args.datasets for name in re.split(r"[,\s]+", group) if name] if args.datasets else None
        selected = _datasets(names)
        settings = OptionSettings(args.options_history_days, args.option_target_days, args.rv_window,
                                  args.risk_free_rate, args.dividend_yield).validate()
        if backfill and (settings.history_days != -1 or set(selected) != {"prices", "options"}):
            raise ValueError("Backfill collects prices and options for the full explicit date range")
        api_key = args.api_key_file.read_text().strip() if args.api_key_file else None
        if args.api_key_file and not api_key:
            raise ValueError("API key file is empty")
        if set(selected) & MASSIVE_DATASETS and not resolve_api_key(api_key):
            log.error("Missing Massive key; supply MASSIVE_API_KEY, POLYGON_IO or --api-key-file")
            return 1
        with Checkpoints(checkpoint_path) if checkpoint_path else nullcontext(None) as checkpoint:
            start, end = _checkpoint_window(args.start, args.end, checkpoint)
            if checkpoint and "options" in selected:
                checkpoint.bind_options(settings.dictionary())
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
        log.info("RUN tickers=%s feeds=%s window=%s..%s checkpoint=%s",
                 len(symbols), len(selected), start, end, bool(checkpoint_path))
        # SQLite is authoritative: rebuilding the JSONL removes torn lines and
        # avoids duplicate ticker rows after a crash between saving and export.
        with args.output.open("w", encoding="utf-8") if args.output else nullcontext(sys.stdout) as stream:
            for result in iter_tickers(
                symbols, start_date=start, end_date=end, datasets=selected,
                checkpoint_path=checkpoint_path, log_path=args.log_file,
                api_key=api_key,
                option_settings=settings,
                price_mode=args.price_mode, market_cache_path=args.market_cache,
            ):
                stream.write(json.dumps(result, allow_nan=False) + "\n")
                stream.flush()
                failed |= any(feed["status"] in ("error", "partial") for feed in result["datasets"].values())
        log.info("RUN finished failed_feeds=%s", failed)
    except KeyboardInterrupt:
        log.warning("RUN interrupted; rerun the same command to resume committed feeds")
        return 130
    except Exception as exc:
        log.error("RUN stopped type=%s; checkpoints retained", type(exc).__name__)
        print(f"Collection stopped ({type(exc).__name__}); see {args.log_file}", file=sys.stderr)
        return 1
    finally:
        if console:
            log.removeHandler(console)
            console.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

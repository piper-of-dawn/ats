"""Independent, resumable historical prices/option-metrics process."""

from .__main__ import main as collect_main


def main():
    return collect_main(backfill=True)


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for every reproducible step of the project.

    python -m solaris.cli fetch-grid       # download the Grid-India workbooks
    python -m solaris.cli fetch-weather    # download archived weather forecasts
    python -m solaris.cli prepare          # parse, join, quality-check
    python -m solaris.cli train            # fit and evaluate every model
    python -m solaris.cli report           # regenerate figures and tables
    python -m solaris.cli all              # prepare + train + report

Every number published by this project comes from these commands.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def cmd_fetch_grid(args: argparse.Namespace) -> int:
    from solaris.data import grid

    paths = grid.download_all(force=args.force)
    print(f"Downloaded/verified {len(paths)} workbooks.")
    return 0


def cmd_fetch_weather(args: argparse.Namespace) -> int:
    from solaris.data import weather

    directory = weather.download_all(force=args.force)
    print(f"Weather cached under {directory}")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    """Parse raw sources into the processed dataset and write the quality report."""
    from solaris import config
    from solaris.data import build, grid, weather

    config.ensure_dirs()

    if args.reparse_grid or not config.GRID_HOURLY_PARQUET.exists():
        print("Parsing Grid-India workbooks (this takes several minutes)...")
        grid_hourly, infos = grid.build_hourly()
        grid.save_hourly(grid_hourly, infos)
    else:
        import pandas as pd

        grid_hourly = pd.read_parquet(config.GRID_HOURLY_PARQUET)
        infos = _load_workbook_infos()
        print(f"Reusing cached hourly grid data ({len(grid_hourly)} hours).")

    print("Building national weather index...")
    weather_hourly = weather.build_national_index()
    weather.save_national_index(weather_hourly)

    print("Joining and validating...")
    dataset = build.build_dataset(grid_hourly, weather_hourly)
    dataset = build.finalise(dataset)
    build.save_dataset(dataset)

    report = build.quality_report(dataset, infos)
    build.save_quality_report(report)

    coverage = report["coverage"]
    print(
        f"\nProcessed dataset: {coverage['hours_present']} hours, "
        f"{coverage['first_hour_ist']} -> {coverage['last_hour_ist']} "
        f"({coverage['pct_complete']}% of the hourly grid complete)"
    )
    print(f"Valid modelling hours: {report['validity_flags']['n_valid']}")
    print(f"Quality report: {config.DATA_QUALITY_JSON}")
    return 0


def _load_workbook_infos() -> list:
    from solaris import config
    from solaris.data.grid import WorkbookInfo

    path = config.INTERIM_DIR / "grid_workbooks.json"
    if not path.exists():
        return []
    return [WorkbookInfo(**d) for d in json.loads(path.read_text(encoding="utf-8"))]


def cmd_train(args: argparse.Namespace) -> int:
    from solaris import config, experiment

    print("Fitting models...")
    result = experiment.run(weighting=args.weighting, fit_quantiles=not args.no_quantiles)

    extras: dict = {}
    if not args.skip_sensitivity:
        print("Running hot-threshold sensitivity...")
        extras["hot_threshold_sensitivity"] = experiment.hot_threshold_sensitivity(result)
        print("Running weather-weighting sensitivity...")
        extras["weighting_sensitivity"] = experiment.weighting_sensitivity()
    if args.rolling:
        print("Running rolling-origin backtest (slow)...")
        extras["rolling_backtest"] = experiment.rolling_backtest(args.weighting)

    path = experiment.save(result, extras)
    _print_summary(result.metrics)
    print(f"\nFull metrics written to {path}")
    return 0


def _print_summary(metrics: dict) -> None:
    print("\n" + "=" * 72)
    split = metrics["split"]
    print(f"Train : {split['train']['start']}  ->  {split['train']['end']}"
          f"  ({split['train']['hours']} h)")
    print(f"Valid : {split['valid']['start']}  ->  {split['valid']['end']}"
          f"  ({split['valid']['hours']} h)")
    print(f"Test  : {split['test']['start']}  ->  {split['test']['end']}"
          f"  ({split['test']['hours']} h)")
    print("=" * 72)
    print(f"{'model':<28}{'MAE (MW)':>12}{'RMSE (MW)':>12}{'MAPE %':>10}{'vs naive24 %':>14}")
    print("-" * 72)
    for name, entry in metrics["models"].items():
        overall = entry["test"]["overall"]
        if overall["mae_mw"] is None:
            continue
        skill = entry.get("skill_vs_naive_24h_pct")
        print(
            f"{name:<28}{overall['mae_mw']:>12,.0f}{overall['rmse_mw']:>12,.0f}"
            f"{overall['mape_pct']:>10.2f}{(skill if skill is not None else 0):>14.1f}"
        )
    print("-" * 72)
    contribution = metrics["weather_contribution"]
    reduction = contribution.get("mae_reduction_pct")
    print(
        "Weather features vs calendar+history: "
        f"MAE {contribution['mae_mw_without_weather']:,.0f} -> "
        f"{contribution['mae_mw_with_weather']:,.0f} MW "
        f"({reduction:+.2f}%)" if reduction is not None else "n/a"
    )
    significance = contribution.get("significance", {})
    if significance.get("paired_t_pvalue") is not None:
        print(
            f"Paired test on daily MAE: improvement "
            f"{significance['mean_daily_mae_improvement_mw']:,.0f} MW, "
            f"p={significance['paired_t_pvalue']:.3g} "
            f"({significance['days_improved_pct']:.0f}% of days improved)"
        )


def cmd_report(args: argparse.Namespace) -> int:
    from solaris import report

    paths = report.generate_all()
    print(f"Wrote {len(paths)} report artefacts.")
    for path in paths:
        print(f"  {path}")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    for step in (cmd_prepare, cmd_train, cmd_report):
        code = step(args)
        if code != 0:
            return code
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solaris", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_grid = sub.add_parser("fetch-grid", help="download Grid-India workbooks")
    fetch_grid.add_argument("--force", action="store_true")
    fetch_grid.set_defaults(func=cmd_fetch_grid)

    fetch_weather = sub.add_parser("fetch-weather", help="download archived weather forecasts")
    fetch_weather.add_argument("--force", action="store_true")
    fetch_weather.set_defaults(func=cmd_fetch_weather)

    prepare = sub.add_parser("prepare", help="build the processed dataset")
    prepare.add_argument("--reparse-grid", action="store_true",
                         help="re-parse the xlsx workbooks instead of reusing the cache")
    prepare.set_defaults(func=cmd_prepare)

    train = sub.add_parser("train", help="fit and evaluate every model")
    train.add_argument("--weighting", default="pop", choices=("pop", "equal", "region"))
    train.add_argument("--no-quantiles", action="store_true")
    train.add_argument("--skip-sensitivity", action="store_true")
    train.add_argument("--rolling", action="store_true",
                       help="also run the monthly rolling-origin backtest (slow)")
    train.set_defaults(func=cmd_train)

    report_cmd = sub.add_parser("report", help="regenerate figures and tables")
    report_cmd.set_defaults(func=cmd_report)

    all_cmd = sub.add_parser("all", help="prepare + train + report")
    all_cmd.add_argument("--weighting", default="pop", choices=("pop", "equal", "region"))
    all_cmd.add_argument("--no-quantiles", action="store_true")
    all_cmd.add_argument("--skip-sensitivity", action="store_true")
    all_cmd.add_argument("--rolling", action="store_true")
    all_cmd.add_argument("--reparse-grid", action="store_true")
    all_cmd.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

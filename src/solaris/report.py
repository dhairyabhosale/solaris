"""Generate every figure and table the project publishes, from saved results.

Figures are written twice: as standalone interactive HTML for reading, and as
compact JSON that the web app loads directly, so the dashboard and the report
can never disagree about what the model produced.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from solaris import config, evaluate
from solaris.data import build as build_mod

logger = logging.getLogger(__name__)

# A single palette used by every figure so the report reads as one document.
PALETTE = {
    "actual": "#0f172a",
    "weather": "#f97316",
    "baseline": "#0ea5e9",
    "naive": "#94a3b8",
    "band": "rgba(249, 115, 22, 0.18)",
    "solar": "#fbbf24",
    "wind": "#34d399",
    "residual": "#7c3aed",
    "hot": "#dc2626",
}

LAYOUT = {
    "template": "plotly_white",
    "font": {"family": "Inter, system-ui, sans-serif", "size": 13},
    "margin": {"l": 70, "r": 30, "t": 60, "b": 60},
    "hovermode": "x unified",
}


def _load() -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Load the evaluation metrics, the test predictions and the full dataset."""
    metrics_path = config.REPORTS_DIR / "evaluation.json"
    if not metrics_path.exists():
        raise FileNotFoundError(
            "No evaluation.json. Run `python -m solaris.cli train` first."
        )
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    predictions = pd.read_parquet(config.PROCESSED_DIR / "test_predictions.parquet")
    dataset = build_mod.load_dataset()
    return metrics, predictions, dataset


def _write_figure(figure, name: str, paths: list[Path]) -> None:
    """Save a Plotly figure as interactive HTML and as app-ready JSON."""
    figure.update_layout(**LAYOUT)
    html_path = config.FIGURES_DIR / f"{name}.html"
    figure.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)
    json_path = config.FIGURES_DIR / f"{name}.json"
    json_path.write_text(figure.to_json(), encoding="utf-8")
    paths.extend([html_path, json_path])


def _write_table(frame: pd.DataFrame, name: str, paths: list[Path]) -> None:
    path = config.TABLES_DIR / f"{name}.csv"
    frame.to_csv(path)
    paths.append(path)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


def model_comparison_table(metrics: dict) -> pd.DataFrame:
    """The headline model-comparison table, in MW, on the held-out test block."""
    rows = []
    for name, entry in metrics["models"].items():
        overall = entry["test"]["overall"]
        peak = entry.get("test_peak", {})
        hot = entry["test"].get("hot_period", {})
        evening = entry["test"].get("evening_window", {})
        rows.append(
            {
                "model": name,
                "MAE_MW": overall["mae_mw"],
                "RMSE_MW": overall["rmse_mw"],
                "MAPE_pct": overall["mape_pct"],
                "bias_MW": overall["bias_mw"],
                "R2": overall["r2"],
                "skill_vs_naive24h_pct": entry.get("skill_vs_naive_24h_pct"),
                "peak_MAE_MW": peak.get("peak_mae_mw"),
                "peak_hour_MAE_h": peak.get("peak_hour_mae_h"),
                "MAE_hot_MW": hot.get("mae_mw") if hot.get("sufficient") else None,
                "MAE_evening_MW": evening.get("mae_mw") if evening.get("sufficient") else None,
                "n_test_hours": overall["n"],
            }
        )
    table = pd.DataFrame(rows).set_index("model")
    return table.sort_values("MAE_MW")


def hourly_error_table(metrics: dict) -> pd.DataFrame:
    """MAE by hour of day for each model."""
    data = {}
    for name, entry in metrics["models"].items():
        by_hour = entry["test"].get("by_hour", {})
        data[name] = {int(h): v["mae_mw"] for h, v in by_hour.items()}
    table = pd.DataFrame(data).sort_index()
    table.index.name = "hour_ist"
    return table


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------


def figure_actual_vs_predicted(predictions: pd.DataFrame, days: int = 14):
    """A two-week slice of actual against the two headline models."""
    import plotly.graph_objects as go

    end = predictions.index.max()
    window = predictions.loc[predictions.index > end - pd.Timedelta(days=days)]

    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=window.index, y=window[config.TARGET], name="Actual demand",
        line={"color": PALETTE["actual"], "width": 2.5},
    ))
    if "q10" in window and "q90" in window:
        figure.add_trace(go.Scatter(
            x=window.index, y=window["q90"], name="P90",
            line={"color": PALETTE["weather"], "width": 0}, showlegend=False,
        ))
        figure.add_trace(go.Scatter(
            x=window.index, y=window["q10"], name="P10-P90 band",
            line={"color": PALETTE["weather"], "width": 0},
            fill="tonexty", fillcolor=PALETTE["band"],
        ))
    figure.add_trace(go.Scatter(
        x=window.index, y=window["lgbm_weather"], name="Weather-enhanced model",
        line={"color": PALETTE["weather"], "width": 2},
    ))
    figure.add_trace(go.Scatter(
        x=window.index, y=window["lgbm_calendar_history"],
        name="Calendar + history model",
        line={"color": PALETTE["baseline"], "width": 1.6, "dash": "dot"},
    ))
    figure.update_layout(
        title=f"Day-ahead demand forecast, last {days} days of the held-out test period",
        yaxis_title="All-India demand (MW)",
        xaxis_title="Time (IST)",
    )
    return figure


def figure_model_comparison(metrics: dict):
    """Bar chart of test MAE by model, overall and on the hot-period slice."""
    import plotly.graph_objects as go

    table = model_comparison_table(metrics)
    figure = go.Figure()
    figure.add_trace(go.Bar(
        x=table.index, y=table["MAE_MW"], name="All test hours",
        marker_color=PALETTE["baseline"],
        text=[f"{v:,.0f}" for v in table["MAE_MW"]], textposition="outside",
    ))
    if table["MAE_hot_MW"].notna().any():
        figure.add_trace(go.Bar(
            x=table.index, y=table["MAE_hot_MW"], name="Research-defined hot hours",
            marker_color=PALETTE["hot"],
        ))
    figure.update_layout(
        title="Mean absolute error on the held-out test period",
        yaxis_title="MAE (MW)", xaxis_title="", barmode="group",
    )
    return figure


def figure_error_by_hour(metrics: dict):
    import plotly.graph_objects as go

    table = hourly_error_table(metrics)
    figure = go.Figure()
    for name, colour in (
        ("naive_lag_24h", PALETTE["naive"]),
        ("lgbm_calendar_history", PALETTE["baseline"]),
        ("lgbm_weather", PALETTE["weather"]),
    ):
        if name in table:
            figure.add_trace(go.Scatter(
                x=table.index, y=table[name], name=name, mode="lines+markers",
                line={"color": colour, "width": 2},
            ))
    start, end = config.EVENING_WINDOW
    figure.add_vrect(
        x0=start - 0.5, x1=end + 0.5, fillcolor=PALETTE["hot"], opacity=0.07,
        line_width=0, annotation_text="evening window", annotation_position="top left",
    )
    figure.update_layout(
        title="Forecast error by hour of day (test period)",
        xaxis_title="Hour of day (IST)", yaxis_title="MAE (MW)",
    )
    return figure


def figure_daily_profile(dataset: pd.DataFrame):
    """Mean demand, solar, wind and residual load by hour of day."""
    import plotly.graph_objects as go

    table = evaluate.ramp_table(dataset)
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=table.index, y=table["demand_mw"], name="Demand",
        line={"color": PALETTE["actual"], "width": 2.5},
    ))
    figure.add_trace(go.Scatter(
        x=table.index, y=table["residual_load_mw"], name="Research residual load",
        line={"color": PALETTE["residual"], "width": 2.5},
    ))
    figure.add_trace(go.Scatter(
        x=table.index, y=table["solar_mw"], name="Solar",
        line={"color": PALETTE["solar"], "width": 2}, fill="tozeroy",
        fillcolor="rgba(251,191,36,0.25)",
    ))
    figure.add_trace(go.Scatter(
        x=table.index, y=table["wind_mw"], name="Wind",
        line={"color": PALETTE["wind"], "width": 2},
    ))
    figure.update_layout(
        title="Average day: demand, variable renewables and residual load",
        xaxis_title="Hour of day (IST)", yaxis_title="MW",
    )
    return figure


def figure_temperature_vs_demand(dataset: pd.DataFrame):
    """Daily peak demand against daily maximum forecast temperature."""
    import plotly.graph_objects as go

    daily = pd.DataFrame({
        "peak_demand_mw": dataset[config.TARGET].resample("1D").max(),
        "max_temp_c": dataset["temperature_2m_fc_pop"].resample("1D").max(),
        "mean_temp_c": dataset["temperature_2m_fc_pop"].resample("1D").mean(),
    }).dropna()
    daily["month"] = daily.index.month

    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=daily["max_temp_c"], y=daily["peak_demand_mw"], mode="markers",
        marker={
            "size": 6, "opacity": 0.55, "color": daily["month"],
            "colorscale": "Turbo", "showscale": True,
            "colorbar": {"title": "Month"},
        },
        text=[d.strftime("%Y-%m-%d") for d in daily.index],
        hovertemplate="%{text}<br>Max temp %{x:.1f} C<br>Peak %{y:,.0f} MW<extra></extra>",
        name="Day",
    ))

    # A LOWESS-style trend via a rolling median over sorted temperature, which
    # shows the shape of the relationship without asserting a functional form.
    ordered = daily.sort_values("max_temp_c")
    trend = ordered["peak_demand_mw"].rolling(45, center=True, min_periods=15).median()
    figure.add_trace(go.Scatter(
        x=ordered["max_temp_c"], y=trend, name="Rolling median",
        line={"color": PALETTE["hot"], "width": 3},
    ))
    figure.update_layout(
        title="Daily peak demand vs daily maximum population-weighted forecast temperature",
        xaxis_title="Daily max national forecast temperature (C)",
        yaxis_title="Daily peak all-India demand (MW)",
    )
    return figure


def figure_evening_ramp(dataset: pd.DataFrame):
    """Distribution of the hour at which the residual-load ramp peaks."""
    import plotly.graph_objects as go

    stats = evaluate.evening_ramp_stats(dataset)
    distribution = stats.get("ramp_hour_distribution", {})
    if not distribution:
        return None

    hours = sorted(distribution)
    figure = go.Figure(go.Bar(
        x=hours, y=[distribution[h] for h in hours],
        marker_color=[
            PALETTE["hot"] if config.EVENING_WINDOW[0] <= h <= config.EVENING_WINDOW[1]
            else PALETTE["naive"] for h in hours
        ],
    ))
    figure.update_layout(
        title="Hour of the steepest hourly residual-load ramp, by day",
        xaxis_title="Hour of day (IST)", yaxis_title="Number of days",
    )
    return figure


def figure_residuals(predictions: pd.DataFrame):
    """Residual distribution and residual-vs-fitted for the headline model."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    residual = predictions["lgbm_weather"] - predictions[config.TARGET]
    figure = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Residual distribution", "Residual vs actual demand"),
    )
    figure.add_trace(
        go.Histogram(x=residual, nbinsx=70, marker_color=PALETTE["weather"],
                     name="Residual"),
        row=1, col=1,
    )
    figure.add_trace(
        go.Scattergl(
            x=predictions[config.TARGET], y=residual, mode="markers",
            marker={"size": 4, "opacity": 0.35, "color": PALETTE["baseline"]},
            name="Hour",
        ),
        row=1, col=2,
    )
    figure.add_hline(y=0, line={"color": PALETTE["actual"], "dash": "dash"}, row=1, col=2)
    figure.update_xaxes(title_text="Predicted - actual (MW)", row=1, col=1)
    figure.update_xaxes(title_text="Actual demand (MW)", row=1, col=2)
    figure.update_yaxes(title_text="Residual (MW)", row=1, col=2)
    figure.update_layout(
        title="Weather-enhanced model residuals on the held-out test period",
        showlegend=False,
    )
    return figure


def figure_feature_importance(metrics: dict):
    import plotly.graph_objects as go

    importance = metrics.get("feature_importance_gain", {})
    if not importance:
        return None
    items = sorted(importance.items(), key=lambda kv: kv[1])[-18:]
    figure = go.Figure(go.Bar(
        x=[v for _, v in items], y=[k for k, _ in items], orientation="h",
        marker_color=PALETTE["weather"],
    ))
    figure.update_layout(
        title="Feature importance (share of total split gain), weather model",
        xaxis_title="Share of total gain", yaxis_title="",
        height=560,
    )
    return figure


def figure_peak_scatter(predictions: pd.DataFrame):
    """Predicted vs actual daily peak demand."""
    import plotly.graph_objects as go

    table = evaluate.peak_table(predictions[config.TARGET], predictions["lgbm_weather"])
    if table.empty:
        return None

    low = float(min(table["actual_peak_mw"].min(), table["predicted_peak_mw"].min()))
    high = float(max(table["actual_peak_mw"].max(), table["predicted_peak_mw"].max()))

    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=[low, high], y=[low, high], mode="lines", name="Perfect forecast",
        line={"color": PALETTE["naive"], "dash": "dash"},
    ))
    figure.add_trace(go.Scatter(
        x=table["actual_peak_mw"], y=table["predicted_peak_mw"], mode="markers",
        marker={"size": 8, "opacity": 0.7, "color": PALETTE["weather"]},
        text=[d.strftime("%Y-%m-%d") for d in table.index],
        hovertemplate="%{text}<br>Actual %{x:,.0f} MW<br>Predicted %{y:,.0f} MW<extra></extra>",
        name="Day",
    ))
    figure.update_layout(
        title="Daily peak demand: predicted vs actual (test period)",
        xaxis_title="Actual daily peak (MW)", yaxis_title="Predicted daily peak (MW)",
    )
    return figure


def figure_quantile_calibration(metrics: dict):
    """Nominal vs empirical coverage for the fitted quantiles."""
    import plotly.graph_objects as go

    quantile = metrics.get("quantile", {}).get("quantiles", {})
    if not quantile:
        return None
    nominal, empirical, labels = [], [], []
    for name, entry in sorted(quantile.items()):
        if entry.get("nominal_coverage") is None:
            continue
        nominal.append(entry["nominal_coverage"])
        empirical.append(entry["empirical_coverage"])
        labels.append(name.upper())

    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=[0, 1], y=[0, 1], mode="lines", name="Perfect calibration",
        line={"color": PALETTE["naive"], "dash": "dash"},
    ))
    figure.add_trace(go.Scatter(
        x=nominal, y=empirical, mode="markers+text", text=labels,
        textposition="top center", name="Fitted quantile",
        marker={"size": 14, "color": PALETTE["weather"]},
    ))
    figure.update_layout(
        title="Quantile calibration on the held-out test period",
        xaxis_title="Nominal coverage", yaxis_title="Empirical coverage",
        xaxis_range=[0, 1], yaxis_range=[0, 1],
    )
    return figure


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def generate_all() -> list[Path]:
    """Regenerate every table and figure from the saved evaluation run."""
    config.ensure_dirs()
    metrics, predictions, dataset = _load()
    paths: list[Path] = []

    # Tables
    _write_table(model_comparison_table(metrics), "model_comparison", paths)
    _write_table(hourly_error_table(metrics), "error_by_hour", paths)
    _write_table(evaluate.ramp_table(dataset), "daily_profile", paths)
    peaks = evaluate.peak_table(predictions[config.TARGET], predictions["lgbm_weather"])
    if not peaks.empty:
        _write_table(peaks, "daily_peaks_test", paths)
        worst = peaks.reindex(peaks["peak_error_mw"].abs().sort_values(ascending=False).index)
        _write_table(worst.head(20), "worst_peak_days", paths)
        _write_table(
            peaks.reindex(peaks["peak_error_mw"].abs().sort_values().index).head(20),
            "best_peak_days", paths,
        )

    # Figures
    for name, figure in (
        ("actual_vs_predicted", figure_actual_vs_predicted(predictions)),
        ("model_comparison", figure_model_comparison(metrics)),
        ("error_by_hour", figure_error_by_hour(metrics)),
        ("daily_profile", figure_daily_profile(dataset)),
        ("temperature_vs_demand", figure_temperature_vs_demand(dataset)),
        ("evening_ramp", figure_evening_ramp(dataset)),
        ("residuals", figure_residuals(predictions)),
        ("feature_importance", figure_feature_importance(metrics)),
        ("peak_scatter", figure_peak_scatter(predictions)),
        ("quantile_calibration", figure_quantile_calibration(metrics)),
    ):
        if figure is not None:
            _write_figure(figure, name, paths)

    paths.append(_write_markdown_summary(metrics))

    # Export the static JSON bundle the web app reads, so the dashboard can
    # never drift from the figures and tables written above.
    from solaris import webexport

    paths.extend(webexport.export_all())
    return paths


def _write_markdown_summary(metrics: dict) -> Path:
    """Write reports/EVALUATION.md -- a human-readable rendering of evaluation.json."""
    table = model_comparison_table(metrics)
    split = metrics["split"]
    contribution = metrics["weather_contribution"]
    significance = contribution.get("significance", {})

    lines = [
        "# Solaris Grid Intelligence - evaluation report",
        "",
        "> Generated by `python -m solaris.cli report`. Every number on this page "
        "was computed by `solaris.evaluate` from held-out predictions. Do not edit "
        "by hand.",
        "",
        "## Forecast task",
        "",
        metrics["forecast_convention"],
        "",
        f"- Target: `{metrics['dataset']['target']}` (all-India metered demand), "
        f"unit **{metrics['dataset']['unit']}**",
        f"- Timezone: {metrics['dataset']['timezone']}",
        f"- Weather aggregation: {metrics['dataset']['weighting_scheme']}-weighted "
        "national index over 18 load-centre cities",
        "",
        "## Chronological split",
        "",
        "| Block | Start (IST) | End (IST) | Hours |",
        "| --- | --- | --- | --- |",
    ]
    for part in ("train", "valid", "test"):
        block = split[part]
        lines.append(
            f"| {part} | {block['start']} | {block['end']} | {block['hours']:,} |"
        )

    lines += [
        "",
        "No random splitting is used anywhere. The test block was never seen during "
        "fitting, early stopping or threshold selection.",
        "",
        "## Model comparison (held-out test period)",
        "",
        "| Model | MAE (MW) | RMSE (MW) | MAPE (%) | Bias (MW) | R2 | vs naive-24h (%) | "
        "Peak MAE (MW) | Peak-hour MAE (h) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, row in table.iterrows():
        lines.append(
            f"| `{name}` | {_fmt(row['MAE_MW'])} | {_fmt(row['RMSE_MW'])} | "
            f"{_fmt(row['MAPE_pct'], 2)} | {_fmt(row['bias_MW'])} | "
            f"{_fmt(row['R2'], 3)} | {_fmt(row['skill_vs_naive24h_pct'], 1)} | "
            f"{_fmt(row['peak_MAE_MW'])} | {_fmt(row['peak_hour_MAE_h'], 2)} |"
        )

    reduction = contribution.get("mae_reduction_pct")
    lines += [
        "",
        "## Does weather add information?",
        "",
        "`lgbm_weather` and `lgbm_calendar_history` are the same algorithm with the "
        "same hyperparameters. The only difference is that one may see the archived "
        "day-ahead temperature forecast. The gap between them is therefore the "
        "contribution of weather information.",
        "",
        f"- MAE without weather: **{_fmt(contribution['mae_mw_without_weather'])} MW**",
        f"- MAE with weather: **{_fmt(contribution['mae_mw_with_weather'])} MW**",
        f"- Reduction: **{_fmt(reduction, 2)}%**",
    ]
    if significance.get("paired_t_pvalue") is not None:
        lines += [
            f"- Paired t-test on daily MAE: t = {_fmt(significance['paired_t_statistic'], 2)}, "
            f"p = {significance['paired_t_pvalue']:.3g}, n = {significance['n_days']} days",
            f"- Wilcoxon signed-rank p = {significance['wilcoxon_pvalue']:.3g}",
            f"- Mean daily MAE improvement: {_fmt(significance['mean_daily_mae_improvement_mw'])} MW "
            f"(95% CI {_fmt(significance['ci95_mw'][0])} to {_fmt(significance['ci95_mw'][1])} MW)",
            f"- Days improved: {_fmt(significance['days_improved_pct'], 1)}%",
        ]

    if "hot_period" in metrics:
        hot = metrics["hot_period"]
        lines += [
            "",
            "## Hot periods",
            "",
            f"**Definition.** {hot['definition']}",
            "",
            f"- Threshold: {_fmt(hot.get('threshold_c'), 2)} C "
            f"(p{int((hot.get('percentile') or 0) * 100)} of training-period daily maxima)",
            f"- Test hours labelled hot: {hot['test_hours_hot']:,} of "
            f"{hot['test_hours_total']:,} ({hot['test_pct_hot']}%)",
        ]

    if metrics.get("quantile"):
        lines += ["", "## Probabilistic forecast calibration", "",
                  "| Quantile | Nominal | Empirical | Calibration error | Pinball loss (MW) |",
                  "| --- | ---: | ---: | ---: | ---: |"]
        for name, entry in sorted(metrics["quantile"].get("quantiles", {}).items()):
            if entry.get("nominal_coverage") is None:
                continue
            lines.append(
                f"| {name.upper()} | {entry['nominal_coverage']:.2f} | "
                f"{entry['empirical_coverage']:.3f} | "
                f"{entry['calibration_error']:+.3f} | "
                f"{_fmt(entry['pinball_loss_mw'])} |"
            )
        interval = metrics["quantile"].get("interval_p10_p90")
        if interval:
            lines += [
                "",
                f"P10-P90 interval: nominal {interval['nominal_coverage']:.0%}, "
                f"empirical {interval['empirical_coverage']:.1%}, "
                f"mean width {_fmt(interval['mean_width_mw'])} MW.",
            ]

    ramp = metrics.get("evening_ramp_full_period") or {}
    if ramp:
        lines += [
            "", "## Evening residual-load ramp", "",
            f"**Definition.** {ramp['definition']}", "",
            f"- Days analysed: {ramp['n_days']:,}",
            f"- Mean steepest hourly ramp: {_fmt(ramp['mean_max_hourly_ramp_mw'])} MW/h",
            f"- 95th percentile steepest hourly ramp: {_fmt(ramp['p95_max_hourly_ramp_mw'])} MW/h",
            f"- Largest single-hour ramp observed: {_fmt(ramp['max_observed_hourly_ramp_mw'])} MW/h",
            f"- Mean steepest 3-hour ramp: {_fmt(ramp['mean_max_3h_ramp_mw'])} MW",
            f"- Most common hour of steepest ramp: {ramp['modal_ramp_hour_ist']:02d}:00 IST",
            f"- Most common evening residual-load peak hour: "
            f"{ramp['modal_evening_peak_hour_ist']:02d}:00 IST",
        ]

    if metrics.get("weighting_sensitivity"):
        lines += ["", "## Sensitivity: weather aggregation weighting", "",
                  "| Scheme | MAE (MW) | MAE reduction vs no-weather (%) |",
                  "| --- | ---: | ---: |"]
        for scheme, entry in metrics["weighting_sensitivity"].items():
            lines.append(
                f"| {scheme} | {_fmt(entry['mae_mw'])} | "
                f"{_fmt(entry['mae_reduction_vs_no_weather_pct'], 2)} |"
            )

    if metrics.get("hot_threshold_sensitivity"):
        lines += ["", "## Sensitivity: hot-period threshold", "",
                  "| Percentile | Threshold (C) | Hot test hours | Weather MAE (MW) | "
                  "Calendar+history MAE (MW) |", "| --- | ---: | ---: | ---: | ---: |"]
        for name, entry in metrics["hot_threshold_sensitivity"].items():
            if not entry.get("sufficient"):
                lines.append(
                    f"| {name} | {_fmt(entry['threshold_c'], 2)} | "
                    f"{entry['test_hours_hot']} | insufficient sample | insufficient sample |"
                )
                continue
            lines.append(
                f"| {name} | {_fmt(entry['threshold_c'], 2)} | {entry['test_hours_hot']} | "
                f"{_fmt(entry.get('lgbm_weather'))} | {_fmt(entry.get('lgbm_calendar_history'))} |"
            )

    if metrics.get("rolling_backtest"):
        backtest = metrics["rolling_backtest"]
        lines += ["", "## Rolling-origin backtest", "",
                  f"{backtest['scheme']}; {backtest['n_folds']} folds.", ""]
        if backtest.get("mean_mae_mw"):
            lines += ["| Model | Mean fold MAE (MW) |", "| --- | ---: |"]
            for name, value in backtest["mean_mae_mw"].items():
                lines.append(f"| `{name}` | {_fmt(value)} |")
        per_fold = backtest.get("weather_mae_reduction_pct_per_fold")
        if per_fold:
            lines += [
                "",
                f"Weather MAE reduction per fold: mean {_fmt(per_fold['mean'], 2)}%, "
                f"range {_fmt(per_fold['min'], 2)}% to {_fmt(per_fold['max'], 2)}%; "
                f"{per_fold['folds_improved']} of {backtest['n_folds']} folds improved.",
            ]

    lines += [
        "", "## Limitations", "",
        "- Only `temperature_2m` is available from Open-Meteo's Previous Runs API at a "
        "genuine day-ahead lead over 2021-2023. Humidity, radiation, cloud cover and "
        "wind forecasts are not, so they are excluded from the operational model "
        "rather than substituted with observations.",
        "- The national weather index is population-weighted, which is a proxy for, "
        "not a measurement of, each region's share of electricity demand.",
        "- The hot-period label is research-defined and percentile-based. It is not an "
        "India Meteorological Department heatwave declaration.",
        "- Residual load here is metered demand minus SCADA solar and wind. It excludes "
        "behind-the-meter rooftop solar, interchange and storage, so it is an indicator "
        "of the evening ramp rather than an operator's net-load figure.",
        "- Associations between temperature and demand reported here are predictive, "
        "not causal. No causal identification strategy is used.",
        "",
    ]

    path = config.REPORTS_DIR / "EVALUATION.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _fmt(value, decimals: int = 0) -> str:
    """Format a number for a Markdown table, or 'n/a' when absent."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    if decimals == 0:
        return f"{value:,.0f}"
    return f"{value:,.{decimals}f}"

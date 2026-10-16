"""Tests for dataset assembly, quality flagging and the web API.

The API tests stub the artefact loaders, so they pass with no pipeline output
and no network access.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from solaris import config
from solaris.data import build


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _grid_frame(hours: int = 24 * 40) -> pd.DataFrame:
    index = pd.date_range("2022-01-01", periods=hours, freq="1h", tz=config.IST)
    hour = index.hour.to_numpy()
    solar = np.clip(20_000 * np.sin((hour - 6) / 12 * np.pi), 0, None)
    frame = pd.DataFrame(
        {
            "thermal_mw": 120_000.0,
            "hydro_mw": 20_000.0,
            "gas_mw": 4_000.0,
            "nuclear_mw": 5_000.0,
            "wind_mw": 8_000.0,
            "solar_raw_mw": np.where(solar > 0, solar, -20.0),
            "demand_mw": 170_000 + 20_000 * np.sin((hour - 6) / 24 * 2 * np.pi),
            "n_samples": 12,
        },
        index=index,
    )
    frame.index.name = "timestamp_ist"
    from solaris.data import grid

    return grid.derive_columns(frame)


def _weather_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    hour = index.hour.to_numpy()
    temperature = 28 + 9 * np.sin((hour - 9) / 24 * 2 * np.pi)
    frame = pd.DataFrame(
        {
            "temperature_2m_fc_pop": temperature,
            "temperature_2m_fc_equal": temperature - 0.4,
            "temperature_2m_fc_region": temperature - 0.2,
            "temperature_2m_fc_max": temperature + 6,
            "temperature_2m_fc_spread": 10.0,
            "temperature_2m_obs": temperature + 1,
        },
        index=index,
    )
    frame.index.name = "timestamp_ist"
    return frame


# --------------------------------------------------------------------------
# Dataset assembly
# --------------------------------------------------------------------------


def test_build_dataset_joins_on_the_hourly_index():
    grid_frame = _grid_frame()
    weather = _weather_frame(grid_frame.index)
    joined = build.build_dataset(grid_frame, weather)

    assert len(joined) == len(grid_frame)
    assert "demand_mw" in joined
    assert "temperature_2m_fc_pop" in joined
    assert str(joined.index.tz) == config.IST_NAME


def test_build_dataset_inner_joins_partial_weather():
    """Hours without weather are dropped, not carried half-populated."""
    grid_frame = _grid_frame()
    weather = _weather_frame(grid_frame.index[: len(grid_frame) // 2])
    joined = build.build_dataset(grid_frame, weather)

    assert len(joined) == len(grid_frame) // 2
    assert joined["temperature_2m_fc_pop"].notna().all()


def test_build_dataset_rejects_naive_index():
    grid_frame = _grid_frame()
    weather = _weather_frame(grid_frame.index)
    naive = grid_frame.copy()
    naive.index = naive.index.tz_localize(None)

    with pytest.raises(ValueError, match="timezone-aware"):
        build.build_dataset(naive, weather)


def test_build_dataset_rejects_wrong_timezone():
    grid_frame = _grid_frame()
    weather = _weather_frame(grid_frame.index)
    shifted = grid_frame.copy()
    shifted.index = shifted.index.tz_convert("UTC")

    with pytest.raises(ValueError, match="expected Asia/Kolkata"):
        build.build_dataset(shifted, weather)


# --------------------------------------------------------------------------
# Quality flags
# --------------------------------------------------------------------------


def test_quality_flags_catch_scada_dropouts():
    frame = _grid_frame()
    frame.loc[frame.index[10], "demand_mw"] = 0.0       # dropout
    frame.loc[frame.index[11], "demand_mw"] = 500_000.0  # impossible high
    flagged = build.flag_quality(frame)

    assert bool(flagged["demand_implausible"].iloc[10])
    assert bool(flagged["demand_implausible"].iloc[11])
    assert not bool(flagged["is_valid"].iloc[10])
    assert not bool(flagged["is_valid"].iloc[11])
    assert bool(flagged["is_valid"].iloc[12])


def test_quality_flags_catch_undersampled_hours():
    frame = _grid_frame()
    frame.loc[frame.index[5], "n_samples"] = 1  # far below the modal 12
    flagged = build.flag_quality(frame)

    assert bool(flagged["hour_undersampled"].iloc[5])
    assert not bool(flagged["is_valid"].iloc[5])


def test_valid_hours_are_the_overwhelming_majority_of_clean_data():
    flagged = build.flag_quality(_grid_frame())
    assert flagged["is_valid"].all()


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


def test_hot_period_label_is_research_defined_not_imd():
    frame = build.build_dataset(_grid_frame(), _weather_frame(_grid_frame().index))
    labelled = build.add_hot_period_label(frame, percentile=0.9)

    definition = labelled.attrs["hot_period_definition"]
    assert "Research-defined" in definition
    assert "NOT an India Meteorological Department" in definition
    assert labelled.attrs["hot_period_threshold_c"] > 0


def test_hot_period_threshold_comes_from_the_reference_period():
    """The threshold must be fitted on the reference slice, not the whole frame."""
    grid_frame = _grid_frame()
    weather = _weather_frame(grid_frame.index)
    # Warm the series day by day, so an early reference slice and the whole
    # frame genuinely have different daily-maximum distributions.
    day_number = (weather.index.normalize() - weather.index.normalize().min()).days
    for column in [c for c in weather.columns if c.startswith("temperature")]:
        weather[column] = weather[column] + 0.25 * day_number

    frame = build.build_dataset(grid_frame, weather)
    reference = frame.iloc[: 24 * 10]

    from_reference = build.add_hot_period_label(frame, reference=reference)
    from_all = build.add_hot_period_label(frame)

    assert (
        from_reference.attrs["hot_period_threshold_c"]
        != from_all.attrs["hot_period_threshold_c"]
    )


def test_evening_window_flags_configured_hours():
    frame = build.build_dataset(_grid_frame(), _weather_frame(_grid_frame().index))
    out = build.add_evening_window(frame)
    start, end = config.EVENING_WINDOW

    evening = out[out["is_evening"]]
    assert evening.index.hour.min() == start
    assert evening.index.hour.max() == end


def test_residual_ramp_is_nan_across_index_gaps():
    """A diff must not be computed across a hole in the hourly index."""
    frame = build.build_dataset(_grid_frame(), _weather_frame(_grid_frame().index))
    with_gap = pd.concat([frame.iloc[:10], frame.iloc[20:30]])
    out = build.add_evening_window(with_gap)

    assert np.isnan(out["residual_ramp_mw_per_h"].iloc[10])


# --------------------------------------------------------------------------
# Quality report
# --------------------------------------------------------------------------


def test_quality_report_counts_missing_hours():
    frame = build.build_dataset(_grid_frame(), _weather_frame(_grid_frame().index))
    with_gap = pd.concat([frame.iloc[:100], frame.iloc[105:]])
    report = build.quality_report(with_gap)

    assert report["coverage"]["hours_missing"] == 5
    assert report["coverage"]["longest_missing_run_hours"] == 5
    assert report["coverage"]["pct_complete"] < 100


def test_quality_report_is_json_serialisable():
    import json

    frame = build.build_dataset(_grid_frame(), _weather_frame(_grid_frame().index))
    report = build.quality_report(build.finalise(frame, train_end="2022-01-20"))
    json.dumps(report, default=str)  # must not raise

    assert report["target"]["unit"] == "MW"
    assert report["coverage"]["duplicate_timestamps"] == 0


# --------------------------------------------------------------------------
# Web API
# --------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_health_endpoint_reports_artefact_state(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert set(body["artefacts"]) == {"overview", "models", "series", "methodology"}


def test_missing_artefact_returns_actionable_503(client, monkeypatch):
    """A missing pipeline output must say what to run, not return a 500."""
    from pathlib import Path

    from app.services import results

    def boom():
        raise results.ArtefactMissing(Path("evaluation.json"), "python -m solaris.cli train")

    monkeypatch.setattr(results, "overview", boom)
    response = client.get("/api/overview")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "pipeline_artefact_missing"
    assert "solaris.cli train" in detail["command"]


def test_overview_endpoint_shape(client, monkeypatch):
    from app.services import results

    monkeypatch.setattr(results, "overview", lambda: {
        "purpose": "test",
        "data_coverage": {"is_historical": True},
        "kpis": [{"label": "Day-ahead MAE", "value": 3398.0, "unit": "MW"}],
    })
    body = client.get("/api/overview").json()

    assert body["data_coverage"]["is_historical"] is True
    assert body["kpis"][0]["unit"] == "MW"


def test_figure_name_cannot_escape_the_figures_directory():
    """A traversal attempt must resolve inside reports/figures, not above it."""
    from app.services import results

    with pytest.raises(results.ArtefactMissing) as info:
        results.figure("../../../../etc/passwd")
    assert info.value.path.parent == results.DATA_DIR / "figures"


def test_series_rejects_an_out_of_range_hours_parameter(client):
    assert client.get("/api/series?hours=5").status_code == 422
    assert client.get("/api/series?hours=99999").status_code == 422


def test_clean_maps_nan_to_none():
    from solaris import webexport

    assert webexport._clean(np.nan) is None
    assert webexport._clean(float("inf")) is None
    assert webexport._clean(None) is None
    assert webexport._clean(np.float64(3.5)) == 3.5
    assert webexport._clean(np.int64(7)) == 7
    assert webexport._clean(True) is True


def test_web_app_needs_no_modelling_stack():
    """The deployed function must not import pandas, numpy or pyarrow.

    This is what keeps the serverless bundle small, so it is asserted rather
    than left to discipline.
    """
    import ast
    from pathlib import Path

    heavy = {"pandas", "numpy", "pyarrow", "lightgbm", "shap", "sklearn",
             "scipy", "plotly", "openpyxl", "holidays", "solaris"}
    app_dir = Path(__file__).resolve().parents[1] / "app"

    offenders = []
    for source in app_dir.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in heavy:
                    offenders.append(f"{source.name}: {name}")
    assert not offenders, f"web app imports heavy dependencies: {offenders}"


def test_series_window_filtering():
    """Date filtering must include the whole of the end day."""
    from app.services import results

    try:
        full = results.series()
    except results.ArtefactMissing:
        pytest.skip("web bundle not exported in this environment")

    first_day = full["timestamps"][0][:10]
    window = results.series(start=first_day, end=first_day)
    assert len(window["timestamps"]) == 24
    assert all(t.startswith(first_day) for t in window["timestamps"])


def test_series_max_hours_keeps_the_most_recent():
    from app.services import results

    try:
        window = results.series(max_hours=48)
    except results.ArtefactMissing:
        pytest.skip("web bundle not exported in this environment")

    full = results.series()
    assert len(window["timestamps"]) == 48
    assert window["timestamps"][-1] == full["timestamps"][-1]


def test_series_rejects_a_malformed_date():
    from app.services import results

    with pytest.raises(ValueError, match="not an ISO date"):
        results.series(start="last-tuesday")

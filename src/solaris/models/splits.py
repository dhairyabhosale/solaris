"""Chronological train/validation/test splits and rolling-origin evaluation.

A random split would leak the future into the past for a time series, so every
split in this project is a contiguous block of calendar time. The boundaries
live in ``solaris.config`` and are quoted in every report.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from solaris import config


@dataclass(frozen=True)
class Split:
    """One chronological split, as explicit index masks."""

    train: pd.DatetimeIndex
    valid: pd.DatetimeIndex
    test: pd.DatetimeIndex

    def describe(self) -> dict:
        return {
            part: {
                "start": str(index.min()) if len(index) else None,
                "end": str(index.max()) if len(index) else None,
                "hours": int(len(index)),
            }
            for part, index in (
                ("train", self.train),
                ("valid", self.valid),
                ("test", self.test),
            )
        }


def chronological_split(
    frame: pd.DataFrame,
    train_end: str | None = None,
    valid_end: str | None = None,
    test_end: str | None = None,
) -> Split:
    """Split a tz-aware hourly frame into three disjoint, ordered blocks."""
    train_end = pd.Timestamp(train_end or config.TRAIN_END, tz=config.IST)
    valid_end = pd.Timestamp(valid_end or config.VALID_END, tz=config.IST)
    test_end = pd.Timestamp(test_end or config.TEST_END, tz=config.IST)

    if not (train_end < valid_end <= test_end):
        raise ValueError("Split boundaries must be strictly increasing in time.")

    index = frame.index
    return Split(
        train=index[index < train_end],
        valid=index[(index >= train_end) & (index < valid_end)],
        test=index[(index >= valid_end) & (index < test_end)],
    )


@dataclass(frozen=True)
class Fold:
    """One rolling-origin fold: everything before ``test`` is available to fit."""

    label: str
    train: pd.DatetimeIndex
    test: pd.DatetimeIndex


def rolling_origin_folds(
    frame: pd.DataFrame,
    start: str,
    end: str,
    *,
    step: str = "MS",
    min_train_days: int = 180,
) -> list[Fold]:
    """Expanding-window folds over ``[start, end)``, one per period of ``step``.

    Each fold trains on every hour strictly before the fold's test block, which
    is how a forecaster would actually retrain: the model for January is fitted
    on everything up to the end of December.
    """
    index = frame.index
    boundaries = pd.date_range(
        pd.Timestamp(start, tz=config.IST), pd.Timestamp(end, tz=config.IST), freq=step
    )
    if len(boundaries) < 2:
        raise ValueError("Need at least two period boundaries to form a fold.")

    folds: list[Fold] = []
    for fold_start, fold_end in zip(boundaries[:-1], boundaries[1:]):
        train = index[index < fold_start]
        test = index[(index >= fold_start) & (index < fold_end)]
        if len(test) == 0:
            continue
        if len(train) < min_train_days * 24:
            continue
        folds.append(
            Fold(label=fold_start.strftime("%Y-%m"), train=train, test=test)
        )
    return folds

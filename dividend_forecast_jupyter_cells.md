# Dividend Forecast Snapshot — Jupyter Cells

Run these cells after `EVENTS` is available as raw or normalized dividend
history from the current API snapshot. The code does not receive futures
contracts or expiries; Excel applies that filter after importing the event
file.

## 1. Reload the model

```python
from pathlib import Path
import importlib

import pandas as pd
import dividend_forecast_v2

importlib.reload(dividend_forecast_v2)

from dividend_forecast_v2 import build_dividend_export_bundle
```

## 2. Set run inputs

```python
# EVENTS: historical API rows, preferably including operation-period columns.
# AS_OF_DATE: valuation/snapshot date for this run.
# OUTPUT_DIR: existing or new output directory for snapshot files.

AS_OF_DATE = pd.Timestamp.today().normalize()
OUTPUT_DIR = Path("dividend_model_output")
```

## 3. Build and write one snapshot

```python
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

bundle = build_dividend_export_bundle(
    EVENTS,
    as_of_date=AS_OF_DATE,
)

event_export = bundle["events"]
review_export = bundle["review"]

# Deliberately overwrite the current snapshot; do not append these derived files.
event_export.to_csv(
    OUTPUT_DIR / "dividend_events_export.csv",
    index=False,
)
review_export.to_csv(
    OUTPUT_DIR / "dividend_forecast_review.csv",
    index=False,
)
```

## 4. Audit before importing into Excel

```python
display(
    event_export.groupby("status", dropna=False)
    .size()
    .rename("rows")
    .to_frame()
)

display(
    review_export.groupby("review_reason", dropna=False)
    .size()
    .rename("rows")
    .to_frame()
)

display(
    event_export.loc[
        :,
        [
            "symbol",
            "cycle",
            "timing_family",
            "status",
            "watch_status",
            "target_period_end",
            "expected_announce_date",
            "effective_xd",
            "inclusion_xd",
            "effective_dps",
            "event_probability",
            "date_confidence",
            "amount_confidence",
        ],
    ]
)
```

For a normal future row, `effective_xd` is the model's median expected XD.
For a forecast whose median XD has passed but whose timing window is still
open, `watch_status` becomes `OVERDUE` and `inclusion_xd` is set to the end of
that window. This keeps the row available to Excel; it must not be treated as
an actual XD date.

Use `inclusion_xd` to decide whether an event remains in the snapshot, rather
than dropping an overdue forecast merely because `effective_xd` is earlier
than `AsOfDate`. For the carry calculation, use the forecast timing window
(`expected_xd_window_start` / `expected_xd_window_end`) and
`event_probability`; confirmed rows use `effective_xd` and probability `1.0`.

An API event whose `announceDate` is already on or before `AsOfDate` suppresses
a duplicate forecast for the same timing slot, even when its XD date has
already passed. This is separate from the PTT-style case where no actual
announcement exists: that case may remain as `FORECAST` with
`watch_status = OVERDUE` while its XD window is still open.

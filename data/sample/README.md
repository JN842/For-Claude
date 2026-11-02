# SYNTHETIC sample dataset

`synthetic_sample/` is a small **generated** dataset (8 securities per market,
Jan-Jun 2024) committed so the schemas can be inspected without running the
generator.  It is **not real market data** - every price, volume, timestamp,
index event and borrow record was produced by `auction_alpha/data/synthetic.py`
(see `manifest.json` for the generator configuration and planted effects).

Open it with DuckDB:

```python
from auction_alpha.data.store import DataStore
st = DataStore("data/sample")
st.query("synthetic_sample", "SELECT market, count(*) FROM daily_bars GROUP BY 1")
```

The full synthetic datasets used by the experiments are regenerated
deterministically on first use under `data/store/` (git-ignored).

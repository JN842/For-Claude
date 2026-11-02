"""Dataset store: Parquet files, DuckDB for research queries, SQLite for metadata.

Layout::

    <root>/<dataset>/<table>.parquet
    <root>/<dataset>/manifest.json
    <root>/metadata.sqlite            (datasets, tables, hashes, provenance)

A dataset's *data version* is a hash over its tables' content hashes plus
the manifest, so every experiment result can cite exactly which data it used.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import ALL_SCHEMAS


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class DataStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.root / "metadata.sqlite"
        with sqlite3.connect(self.meta_path) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS datasets(
                name TEXT PRIMARY KEY, created_utc TEXT, is_synthetic INTEGER,
                data_version TEXT, provenance TEXT)""")
            con.execute("""CREATE TABLE IF NOT EXISTS tables(
                dataset TEXT, table_name TEXT, rows INTEGER, sha256 TEXT, path TEXT,
                PRIMARY KEY(dataset, table_name))""")

    # ---------------------------------------------------------------- write
    def write_dataset(self, name: str, tables: dict[str, pd.DataFrame], *, is_synthetic: bool,
                      provenance: dict) -> str:
        ddir = self.root / name
        ddir.mkdir(parents=True, exist_ok=True)
        hashes = {}
        rows = []
        for tname, df in tables.items():
            if tname not in ALL_SCHEMAS:
                raise ValueError(f"unknown table {tname}")
            path = ddir / f"{tname}.parquet"
            tbl = pa.Table.from_pandas(df, preserve_index=False)
            pq.write_table(tbl, path, compression="zstd")
            hashes[tname] = _file_hash(path)
            rows.append((name, tname, len(df), hashes[tname], str(path.relative_to(self.root))))
        manifest = {
            "dataset": name, "is_synthetic": is_synthetic,
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "tables": {t: {"rows": len(tables[t]), "sha256": hashes[t]} for t in tables},
            "provenance": provenance,
        }
        version = hashlib.sha256(json.dumps({"t": hashes, "p": provenance, "s": is_synthetic},
                                            sort_keys=True, default=str).encode()).hexdigest()[:12]
        manifest["data_version"] = version
        (ddir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
        with sqlite3.connect(self.meta_path) as con:
            con.execute("DELETE FROM tables WHERE dataset=?", (name,))
            con.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?,?)",
                        (name, manifest["created_utc"], int(is_synthetic), version,
                         json.dumps(provenance, default=str)))
            con.executemany("INSERT INTO tables VALUES (?,?,?,?,?)", rows)
        return version

    # ----------------------------------------------------------------- read
    def has(self, name: str) -> bool:
        return (self.root / name / "manifest.json").exists()

    def manifest(self, name: str) -> dict:
        return json.loads((self.root / name / "manifest.json").read_text())

    def datasets(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/manifest.json"))

    def table_path(self, name: str, table: str) -> Path:
        return self.root / name / f"{table}.parquet"

    def read(self, name: str, table: str, markets: list[str] | None = None) -> pd.DataFrame:
        path = self.table_path(name, table)
        if not path.exists():
            return pd.DataFrame(columns=ALL_SCHEMAS[table].column_names)
        filters = [("market", "in", list(markets))] if markets and "market" in ALL_SCHEMAS[table].column_names else None
        df = pq.read_table(path, filters=filters).to_pandas()
        for c in ALL_SCHEMAS[table].columns:
            if c.dtype == "date" and c.name in df:
                df[c.name] = pd.to_datetime(df[c.name])
        return df

    def load(self, name: str, markets: list[str] | None = None) -> dict[str, pd.DataFrame]:
        man = self.manifest(name)
        return {t: self.read(name, t, markets) for t in man["tables"]}

    def duck(self, name: str) -> duckdb.DuckDBPyConnection:
        """DuckDB connection with one view per table of the dataset."""
        con = duckdb.connect()
        for t in self.manifest(name)["tables"]:
            con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{self.table_path(name, t)}')")
        return con

    def query(self, name: str, sql: str) -> pd.DataFrame:
        con = self.duck(name)
        try:
            return con.execute(sql).df()
        finally:
            con.close()

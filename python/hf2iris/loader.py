"""
hf2iris - load Hugging Face datasets into InterSystems IRIS tables.

Runs inside IRIS embedded Python (uses the `iris` module), but the SQL layer is
pluggable so the logic can also be tested outside IRIS.
"""
import json
import re
import time

import numpy as np
import pandas as pd

RESERVED = set("""
add all alter and any as asc between by case cast check column constraint count
create cross current date default delete desc distinct drop else end escape exists
false for foreign from full group having id in index inner insert into is join key
label language left level like max min name not null of on or order outer position
primary references right select set size some sum table text then time timestamp
to top true type union unique update user using value values view when where with
""".split())


# ---------------------------------------------------------------- naming ---
def safe_column(name, used):
    """Turn any dataset column name into a valid, unique SQL column name."""
    col = re.sub(r"[^A-Za-z0-9_]", "_", str(name)).strip("_") or "col"
    if col[0].isdigit():
        col = "c_" + col
    if col.lower() in RESERVED:
        col = col + "_"
    base, n = col, 1
    while col.lower() in used:
        n += 1
        col = f"{base}_{n}"
    used.add(col.lower())
    return col


def safe_table(name):
    """'Demo.IMDB' -> 'Demo.IMDB'; 'stanfordnlp/imdb' -> 'HF.stanfordnlp_imdb'."""
    if "." not in name:
        name = "HF." + name
    schema, table = name.split(".", 1)
    clean = lambda s: re.sub(r"[^A-Za-z0-9_]", "_", s).strip("_")
    return f"{clean(schema)}.{clean(table)}"


# ----------------------------------------------------------- type mapping ---
def sql_type(series, max_varchar=4000):
    """Map a pandas column to an IRIS SQL type."""
    dt = series.dtype
    if pd.api.types.is_bool_dtype(dt):
        return "BIT"
    if pd.api.types.is_integer_dtype(dt):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dt):
        return "DOUBLE"
    if pd.api.types.is_datetime64_any_dtype(dt):
        return "TIMESTAMP"
    # object / string: lists, dicts, long text.  Scan the whole column, not a
    # sample: one long value late in the data would overflow a VARCHAR.
    values = series.dropna()
    if len(values) and values.map(lambda v: isinstance(v, (list, dict, np.ndarray))).any():
        return "LONGVARCHAR"          # stored as JSON
    longest = int(values.astype(str).str.len().max()) if len(values) else 0
    if longest > max_varchar // 2:
        return "LONGVARCHAR"
    return f"VARCHAR({max_varchar})"


def to_cell(v, typ):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (list, dict)) or hasattr(v, "tolist"):
        v = v.tolist() if hasattr(v, "tolist") and not isinstance(v, (list, dict)) else v
        return json.dumps(v, default=str)
    if typ == "BIT":
        return 1 if v else 0
    if typ == "TIMESTAMP":
        return str(pd.Timestamp(v).to_pydatetime().replace(microsecond=0))
    if hasattr(v, "item"):            # numpy scalar
        return v.item()
    return v


# ---------------------------------------------------------- SQL backends ---
class IrisSQL:
    """Embedded Python backend (inside IRIS)."""

    def __init__(self):
        import iris  # noqa: available only inside IRIS
        self.iris = iris

    def execute(self, sql, *args):
        return self.iris.sql.exec(sql, *args)

    def executemany(self, sql, rows):
        stmt = self.iris.sql.prepare(sql)
        for r in rows:
            stmt.execute(*r)

    def table_exists(self, table):
        schema, name = table.split(".", 1)
        rs = self.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", schema, name)
        return list(rs)[0][0] > 0


# ------------------------------------------------------------- core API ---
def build_ddl(table, df):
    used, cols, types = set(), [], []
    for c in df.columns:
        cols.append(safe_column(c, used))
        types.append(sql_type(df[c]))
    body = ",\n  ".join(f"{c} {t}" for c, t in zip(cols, types))
    return f"CREATE TABLE {table} (\n  {body}\n)", cols, types


def load_dataframe(df, table, db, mode="replace", batch_size=1000, log=print):
    """Create (or replace/append) `table` from a DataFrame and insert all rows."""
    table = safe_table(table)
    ddl, cols, types = build_ddl(table, df)

    exists = db.table_exists(table)
    if exists and mode == "fail":
        raise ValueError(f"Table {table} already exists")
    if exists and mode == "replace":
        db.execute(f"DROP TABLE {table}")
        exists = False
    if not exists:
        db.execute(ddl)
        log(f"Created {table} ({len(cols)} columns)")

    ins = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    t0, total = time.time(), len(df)
    for start in range(0, total, batch_size):
        chunk = df.iloc[start:start + batch_size]
        rows = [[to_cell(v, t) for v, t in zip(rec, types)]
                for rec in chunk.itertuples(index=False, name=None)]
        db.executemany(ins, rows)
        log(f"  {min(start + batch_size, total):,}/{total:,} rows")
    secs = round(time.time() - t0, 2)
    return {"table": table, "rows": total, "columns": dict(zip(cols, types)),
            "seconds": secs, "ddl": ddl}


def resolve_name(name, lookup=None):
    """Legacy ids without a namespace ('rotten_tomatoes') are rejected by recent
    huggingface_hub; ask the Hub for the canonical 'owner/name' id."""
    if "/" in name:
        return name
    if lookup is None:
        from huggingface_hub import HfApi
        lookup = lambda n: HfApi().dataset_info(n).id
    return lookup(name)


def fetch_dataset(name, split="train", config=None, limit=None, revision=None):
    """Download a dataset from the Hugging Face Hub into a pandas DataFrame."""
    from datasets import load_dataset
    name = resolve_name(name)
    sel = f"{split}[:{int(limit)}]" if limit else split
    ds = load_dataset(name, config, split=sel, revision=revision)
    return ds.to_pandas()


def load(name, table=None, split="train", config=None, limit=None,
         mode="replace", batch_size=1000, db=None, log=print):
    """One line: hf2iris.load("stanfordnlp/imdb", "Demo.IMDB", limit=1000)."""
    db = db or IrisSQL()
    table = table or name.split("/")[-1]
    log(f"Fetching {name} [{config or 'default'}:{split}]"
        + (f" limit {limit}" if limit else ""))
    df = fetch_dataset(name, split, config, limit)
    result = load_dataframe(df, table, db, mode, batch_size, log)
    result.update({"dataset": name, "split": split, "config": config})
    return result


def info(name):
    """List configs and splits for a dataset, without downloading data."""
    from datasets import get_dataset_config_names, get_dataset_split_names
    name = resolve_name(name)
    configs = get_dataset_config_names(name)
    out = {}
    for c in configs[:20]:
        try:
            out[c] = get_dataset_split_names(name, c)
        except Exception as e:  # gated or broken configs
            out[c] = f"error: {e}"
    return {"dataset": name, "configs": out}

"""
hf2iris - load Hugging Face datasets into InterSystems IRIS tables.

Runs inside IRIS embedded Python (uses the `iris` module), but the SQL layer is
pluggable so the logic can also be tested outside IRIS.
"""
import base64
import datetime
import json
import math
import os
import re
import time
import unicodedata

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

MODES = ("replace", "append", "fail")
COMMIT = re.compile(r"[0-9a-f]{40}")
# caps for loads without a row limit
MAX_ROWS = int(os.environ.get("HF2IRIS_MAX_ROWS", 1_000_000))
MAX_DOWNLOAD_MB = int(os.environ.get("HF2IRIS_MAX_DOWNLOAD_MB", 500))
MAX_SIZE_FILES = 1000


class LoadError(ValueError):
    """Expected failure; the message is shown to the user as is."""


class SplitNotFound(LoadError):
    pass


class LimitExceeded(LoadError):
    """A full load over HF2IRIS_MAX_ROWS / HF2IRIS_MAX_DOWNLOAD_MB."""


class TableConflict(LoadError):
    """The target table exists and may not be changed (mode 'fail', or not ours)."""


class Cancelled(LoadError):
    pass


RESERVED = set("""
add all alter and any as asc between by case cast check column constraint count
create cross current date default delete desc distinct drop else end escape exists
false for foreign from full group having id in index inner insert into is join key
label language left level like max min name not null of on or order outer position
primary references right select set size some sum table text then time timestamp
to top true type union unique update user using value values view when where with
""".split())


# ---------------------------------------------------------------- naming ---
def is_reserved(word, own_list=True):
    """IRIS reserved word, or in RESERVED (kept so old column names stay text_, label_)."""
    if own_list and word.lower() in RESERVED:
        return True
    try:
        import iris  # noqa: available only inside IRIS
        return bool(iris.system.SQL.IsReservedWord(word))
    except Exception:
        return False


def ascii_name(text):
    """'Café' -> 'Cafe'"""
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")


def safe_column(name, used):
    """Turn any dataset column name into a valid, unique SQL column name."""
    col = re.sub(r"[^A-Za-z0-9_]", "_", ascii_name(name)).strip("_") or "col"
    if col[0].isdigit():
        col = "c_" + col
    if is_reserved(col):
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
    def clean(s):
        s = re.sub(r"[^A-Za-z0-9_]", "_", ascii_name(s)).strip("_") or "t"
        s = "t_" + s if s[0].isdigit() else s
        return s + "_" if is_reserved(s, own_list=False) else s
    return f"{clean(schema)}.{clean(table)}"


# ----------------------------------------------------------- type mapping ---
# Types come from the Arrow schema, not pandas (pandas turns ints with nulls into floats).
MIN_VARCHAR = 4000
LONG_VALUE = 1_000_000        # longer values -> LONGVARCHAR
ROW_BUDGET = 3_000_000        # an IRIS row must fit in one string (3,641,144 chars)
INT64_MAX = 2 ** 63 - 1


class Column:
    def __init__(self, sql_type, values, lengths=None):
        self.sql_type = sql_type
        self.values = values          # None = NULL
        self.lengths = lengths        # text columns: UTF-16 length of each value


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return base64.b64encode(o).decode("ascii")
    if hasattr(o, "tolist"):          # numpy arrays and scalars
        return o.tolist()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def _json(v):
    return json.dumps(v, default=_json_default, ensure_ascii=False)


def _is_null(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def to_arrow_array(series):
    """A pandas column as an Arrow array; mixed Python objects become JSON text."""
    try:
        return pa.array(series, from_pandas=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
        return pa.array([None if _is_null(v) else _json(v) for v in series], pa.string())


def to_arrow(data):
    """pandas DataFrame or pyarrow Table -> pyarrow Table."""
    if isinstance(data, pa.Table):
        return data
    return pa.table([to_arrow_array(data[c]) for c in data.columns],
                    names=[str(c) for c in data.columns])


def _text(values):
    """VARCHAR column sized to its longest value (at least MIN_VARCHAR)."""
    lengths = np.array([0 if v is None else len(v.encode("utf-16-le")) // 2 for v in values],
                       dtype=np.int64)
    longest = int(lengths.max()) if len(lengths) else 0
    if longest > LONG_VALUE:
        return Column("LONGVARCHAR", values, lengths)
    return Column(f"VARCHAR({max(MIN_VARCHAR, -(-longest // 1000) * 1000)})", values, lengths)


def sql_column(arr):
    """IRIS SQL type and bind values for one Arrow column."""
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    t = arr.type
    if pa.types.is_dictionary(t):
        arr = arr.dictionary_decode()
        t = arr.type
    if pa.types.is_null(t):
        return Column(f"VARCHAR({MIN_VARCHAR})", [None] * len(arr), np.zeros(len(arr), dtype=np.int64))
    if pa.types.is_boolean(t):
        return Column("BIT", [None if v is None else int(v) for v in arr.to_pylist()])
    if pa.types.is_integer(t):
        vals = arr.to_pylist()
        if not pa.types.is_uint64(t) or all(v is None or v <= INT64_MAX for v in vals):
            return Column("BIGINT", vals)
        return _text([None if v is None else str(v) for v in vals])
    if pa.types.is_floating(t):
        vals = arr.to_pylist()
        if not pa.types.is_float64(t):
            # float32 1.4 would otherwise become 1.399999976158142
            narrow = np.float32 if pa.types.is_float32(t) else np.float16
            vals = [None if v is None else float(str(narrow(v))) for v in vals]
        if any(v is not None and math.isinf(v) for v in vals):
            return _text([None if v is None or math.isnan(v) else repr(v) for v in vals])
        return Column("DOUBLE", [None if v is None or math.isnan(v) else v for v in vals])
    if pa.types.is_decimal(t):
        vals = [None if v is None else str(v) for v in arr.to_pylist()]
        if t.precision <= 18:
            return Column(f"NUMERIC({t.precision},{t.scale})", vals)
        return _text(vals)
    if pa.types.is_date(t):
        return Column("DATE", arr.cast(pa.date32()).to_pylist())
    if pa.types.is_timestamp(t):
        vals = pc.cast(arr, pa.timestamp("us"), safe=False).to_pylist()
        return Column("TIMESTAMP", [None if v is None else v.isoformat(sep=" ") for v in vals])
    is_string_view = getattr(pa.types, "is_string_view", lambda _: False)
    if pa.types.is_string(t) or pa.types.is_large_string(t) or is_string_view(t):
        return _text(arr.to_pylist())
    if pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        return _text([None if v is None else base64.b64encode(v).decode("ascii") for v in arr.to_pylist()])
    if pa.types.is_nested(t):         # stored as JSON
        return _text([None if v is None else _json(v) for v in arr.to_pylist()])
    return _text([None if v is None else str(v) for v in arr.to_pylist()])   # time, duration, ...


def fit_rows(columns):
    """Move the widest VARCHAR columns to LONGVARCHAR until every row fits ROW_BUDGET."""
    text = [c for c in columns if c.sql_type.startswith("VARCHAR")]
    if not text or not len(text[0].lengths):
        return
    totals = sum(c.lengths for c in text)
    while text and int(totals.max()) > ROW_BUDGET:
        widest = max(text, key=lambda c: int(c.lengths.max()))
        widest.sql_type = "LONGVARCHAR"
        totals = totals - widest.lengths
        text.remove(widest)


def sql_type(data):
    """IRIS SQL type of a pandas Series or Arrow array."""
    arr = data if isinstance(data, (pa.Array, pa.ChunkedArray)) else to_arrow_array(data)
    return sql_column(arr).sql_type


# ---------------------------------------------------------- SQL backends ---
class IrisSQL:
    """Embedded Python backend (inside IRIS)."""

    def __init__(self):
        import iris  # noqa: available only inside IRIS
        self.iris = iris

    def execute(self, sql, *args):
        return self.iris.sql.exec(sql, *args)

    # iris.sql binds logical values: "" is NULL, "\x00" is '', DATE is $h
    HOROLOG_EPOCH = datetime.date(1840, 12, 31)

    @classmethod
    def bind(cls, v):
        if v is None:
            return ""
        if isinstance(v, str):
            return v or "\x00"
        if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
            return (v - cls.HOROLOG_EPOCH).days
        return v

    # default SQLUPPER ignores case and trailing spaces
    text_collation = "" if os.environ.get("HF2IRIS_EXACT", "1") == "0" else " COLLATE %EXACT"

    def executemany(self, sql, rows, first_row=1):
        stmt = self.iris.sql.prepare(sql)
        bind = self.bind
        for i, r in enumerate(rows):
            try:
                stmt.execute(*[bind(v) for v in r])
            except Exception as e:
                raise LoadError(f"Row {first_row + i:,} was rejected by IRIS: {e}") from e

    # a savepoint inside the caller's transaction, so a rollback leaves its work alone
    def begin(self):
        self.nested = self.iris.execute("return $TLEVEL") > 0
        self.execute("SAVEPOINT HF2IRIS" if self.nested else "START TRANSACTION")

    def commit(self):
        if self.nested:
            self.iris.execute("tcommit")
        else:
            self.execute("COMMIT")

    def rollback(self):
        self.execute("ROLLBACK TO SAVEPOINT HF2IRIS" if self.nested else "ROLLBACK")

    def table_exists(self, table):
        schema, name = table.split(".", 1)
        rs = self.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", schema, name)
        return list(rs)[0][0] > 0

    # older tables have no marker, only an ImportLog entry
    def owns_table(self, table):
        if self.iris.gref("^dc.hf2iris.Owned").data([table.upper()]):
            return True
        try:
            rs = self.execute("SELECT COUNT(*) FROM dc_hf2iris.ImportLog "
                              "WHERE UPPER(TableName) = ? AND Status = 'OK'", table.upper())
            return list(rs)[0][0] > 0
        except Exception:  # no ImportLog outside the IPM module
            return False

    def columns(self, table):
        schema, name = table.split(".", 1)
        rs = self.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? "
                          "AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION", schema, name)
        return [r[0] for r in rs]

    def mark_owned(self, table):
        self.iris.gref("^dc.hf2iris.Owned")[table.upper()] = 1

    def unmark_owned(self, table):
        self.iris.gref("^dc.hf2iris.Owned").kill([table.upper()])


# ------------------------------------------------------------- core API ---
def build_ddl(table, data, text_collation=""):
    """CREATE TABLE statement, SQL column names and Column objects for a
    pandas DataFrame or pyarrow Table."""
    data = to_arrow(data)
    used = set()
    cols = [safe_column(c, used) for c in data.column_names]
    columns = [sql_column(a) for a in data.columns]
    fit_rows(columns)
    collate = lambda t: text_collation if t.startswith("VARCHAR") else ""
    body = ",\n  ".join(f"{c} {col.sql_type}{collate(col.sql_type)}"
                        for c, col in zip(cols, columns))
    return f"CREATE TABLE {table} (\n  {body}\n)", cols, columns


def target_table(name, table=None):
    """The table an import of dataset `name` writes to."""
    return safe_table(table or name.split("/")[-1])


def check_target(table, db, mode):
    """(exists, owned) for the target, or TableConflict if it may not be changed."""
    if mode not in MODES:
        raise LoadError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
    exists = db.table_exists(table)
    owned = exists and db.owns_table(table)
    if exists and mode == "fail":
        raise TableConflict(f"Table {table} already exists (mode 'fail')")
    if exists and not owned:
        raise TableConflict(f"Table {table} already exists and was not created by hf2iris, so it "
                            f"is not replaced or appended to. Choose another table name.")
    return exists, owned


def load_dataframe(df, table, db, mode="replace", batch_size=1000, log=print,
                   progress=None, cancelled=None):
    """Create (or replace/append) `table` from a pandas DataFrame or pyarrow
    Table and insert all rows. progress(phase, done, total) is called per batch;
    cancelled() is checked before each batch."""
    if mode not in MODES:
        raise LoadError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
    table = safe_table(table)
    ddl, cols, columns = build_ddl(table, df, getattr(db, "text_collation", ""))

    exists = exists_before = check_target(table, db, mode)[0]
    if exists and mode == "replace":
        db.execute(f"DROP TABLE {table}")
        exists = False
    created = not exists
    if created:
        db.execute(ddl)
        db.mark_owned(table)
        log(f"Created {table} ({len(cols)} columns)")

    ins = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    t0, total = time.time(), len(columns[0].values)
    count = lambda: list(db.execute(f"SELECT COUNT(*) FROM {table}"))[0][0]
    before = 0 if created else count()
    progress = progress or (lambda *a: None)
    db.begin()
    try:
        progress("inserting", 0, total)
        for start in range(0, total, batch_size):
            if cancelled and cancelled():
                raise Cancelled("Import cancelled")
            rows = list(zip(*(c.values[start:start + batch_size] for c in columns)))
            db.executemany(ins, rows, start + 1)
            log(f"  {min(start + batch_size, total):,}/{total:,} rows")
            progress("inserting", min(start + batch_size, total), total)
        arrived = count() - before
        if arrived != total:
            raise LoadError(f"{total:,} rows were read but {arrived:,} arrived in {table}")
        db.commit()
    except Exception as e:
        db.rollback()
        if created:
            db.execute(f"DROP TABLE {table}")
            db.unmark_owned(table)
            replaced = mode == "replace" and exists_before
            note = f" {table} was removed." if replaced else f" {table} was not created."
        else:
            note = f" The rows of this import were rolled back; {table} is unchanged."
        raise (Cancelled if isinstance(e, Cancelled) else LoadError)(error_text(e) + note) from e
    secs = round(time.time() - t0, 2)
    types = {c: col.sql_type for c, col in zip(cols, columns)}
    return {"table": table, "rows": total, "columns": types, "seconds": secs, "ddl": ddl}


def resolve_name(name, lookup=None):
    """Legacy ids without a namespace ('rotten_tomatoes') are rejected by recent
    huggingface_hub; ask the Hub for the canonical 'owner/name' id."""
    if "/" in name:
        return name
    if lookup is None:
        from huggingface_hub import HfApi
        lookup = lambda n: HfApi().dataset_info(n).id
    return lookup(name)


def check_split(name, split, config, limit, revision=None, builder=None, latest=None,
                file_size=None):
    """Check split and size from metadata, before anything is downloaded."""
    if builder is None:
        from datasets import load_dataset_builder
        builder = load_dataset_builder(name, config, revision=revision)
    splits = builder.info.splits or {}
    available = list(splits) or list(builder.config.data_files or {})
    if available and split not in available:
        raise SplitNotFound(f"Split {split!r} not found in {name} (config {builder.config.name!r}). "
                            f"Available splits: {', '.join(available)}")
    if limit:
        return builder           # limited loads stream
    check_limits(split, *estimate(builder, name, split, config, revision, latest, file_size))
    return builder


def check_limits(split, rows, size):
    if rows and rows > MAX_ROWS:
        raise LimitExceeded(f"Split {split!r} has {rows:,} rows, more than the {MAX_ROWS:,} row "
                            f"limit for a full load. Set a row limit, or raise HF2IRIS_MAX_ROWS.")
    if size and size > MAX_DOWNLOAD_MB * 2 ** 20:
        raise LimitExceeded(f"Loading all rows downloads about {size / 2 ** 20:,.0f} MB, more "
                            f"than the {MAX_DOWNLOAD_MB:,} MB limit. Set a row limit, or raise "
                            f"HF2IRIS_MAX_DOWNLOAD_MB.")


def estimate(builder, name, split, config=None, revision=None, latest=None, file_size=None):
    """(rows of the split, bytes a full load downloads); None where unknown."""
    splits = builder.info.splits or {}
    rows = getattr(splits.get(split), "num_examples", None)
    size = builder.info.download_size
    if (rows is None or size is None) and revision:
        # no metadata for a commit hash: use the latest version's
        if latest is None:
            try:
                from datasets import load_dataset_builder
                latest = load_dataset_builder(name, config)
            except Exception:
                latest = None
        if latest is not None:
            rows = rows or getattr((latest.info.splits or {}).get(split), "num_examples", None)
            size = size or latest.info.download_size
    if size is None:
        # load_dataset downloads every split of the config
        files = [f for fs in (builder.config.data_files or {}).values() for f in fs]
        if len(files) > MAX_SIZE_FILES:
            raise LimitExceeded(f"{name} has {len(files):,} data files, too many for a full "
                                f"load. Set a row limit.")
        if files:
            if file_size is None:
                from huggingface_hub import HfFileSystem
                fs = HfFileSystem()
                file_size = lambda f: fs.info(f)["size"]
            size = sum(file_size(f) for f in files)
    return rows, size


def resolve_commit(name, revision=None, lookup=None):
    """Commit hash of a branch, tag or commit of the dataset (None = latest)."""
    if lookup is None:
        from huggingface_hub import HfApi
        lookup = lambda n, r: HfApi().dataset_info(n, revision=r).sha
    try:
        return lookup(name, revision or None)
    except Exception as e:
        if type(e).__name__ == "RevisionNotFoundError":
            raise LoadError(f"Revision {revision!r} not found for {name}. Use a branch, a tag "
                            f"or a 40-character commit hash of the dataset.")
        raise


def label_names(features):
    """{column: [names]} for ClassLabel columns and lists of ClassLabel."""
    out = {}
    for col, f in (features or {}).items():
        inner = getattr(f, "feature", None)
        if type(f).__name__ == "ClassLabel":
            out[col] = list(f.names)
        elif type(inner).__name__ == "ClassLabel":
            out[col] = list(inner.names)
    return out


def add_label_names(table, labels):
    """Add a <column>_name column after each label column (-1 = no label -> NULL)."""
    for col, names in labels.items():
        if col not in table.column_names:
            continue
        name = lambda c: names[c] if c is not None and 0 <= c < len(names) else None
        arr = table.column(col)
        if pa.types.is_list(arr.type) or pa.types.is_large_list(arr.type):
            values = [None if v is None else _json([name(c) for c in v]) for v in arr.to_pylist()]
        else:
            values = [name(v) for v in arr.to_pylist()]
        pos = table.column_names.index(col) + 1
        table = table.add_column(pos, f"{col}_name", pa.array(values, pa.string()))
    return table


def fetch_split(name, split="train", config=None, limit=None, revision=None):
    """Download a dataset split from the Hugging Face Hub as a datasets.Dataset."""
    import datasets
    datasets.disable_progress_bars()
    name = resolve_name(name)
    check_split(name, split, config, limit, revision)
    if limit:
        # "train[:N]" would still download the whole config
        try:
            stream = datasets.load_dataset(name, config, split=split, revision=revision, streaming=True)
            rows = list(stream.take(int(limit)))
            return datasets.Dataset.from_list(rows, features=stream.features)
        except Exception:
            pass                 # not streamable: download instead
    sel = f"{split}[:{int(limit)}]" if limit else split
    return datasets.load_dataset(name, config, split=sel, revision=revision)


def fetch_dataset(name, split="train", config=None, limit=None, revision=None):
    """Download a dataset split from the Hugging Face Hub as a pyarrow Table."""
    return fetch_split(name, split, config, limit, revision).with_format("arrow")[:]


def error_text(e):
    """One readable line for an exception, with HF_TOKEN masked."""
    text = _error_text(e)
    token = os.environ.get("HF_TOKEN", "")
    return text.replace(token, "***") if len(token) >= 4 else text


def _error_text(e):
    if isinstance(e, LoadError):
        return str(e)
    kind, msg = type(e).__name__, " ".join(str(e).split())
    if kind == "GatedRepoError":
        return ("This dataset is gated: accept its terms on huggingface.co, then set HF_TOKEN "
                "in the container environment.")
    if kind in ("DatasetNotFoundError", "RepositoryNotFoundError"):
        name = (re.search(r"Dataset '([^']+)'", msg)
                or re.search(r"/api/datasets/([^/\s]+/[^/\s]+)", msg))
        what = f"Dataset {name.group(1).rstrip('.')!r}" if name else "The dataset"
        return (f"{what} was not found on the Hub. Check the id on huggingface.co/datasets; "
                f"private datasets need HF_TOKEN.")
    m = re.search(r"Config name is missing.*?\[(.*?)\]", msg)
    if m:
        return "This dataset has several configs, choose one: " + m.group(1).replace("'", "")
    m = re.search(r"BuilderConfig '(.*?)' not found\. Available: \[(.*?)\]", msg)
    if m:
        return f"Config {m.group(1)!r} not found. Available configs: " + m.group(2).replace("'", "")
    return f"{kind}: {msg}"


def load(name, table=None, split="train", config=None, limit=None,
         mode="replace", batch_size=1000, db=None, log=print, revision=None,
         progress=None, cancelled=None):
    """One line: hf2iris.load("stanfordnlp/imdb", "Demo.IMDB", limit=1000).
    revision: branch, tag or commit hash. progress / cancelled: see load_dataframe."""
    db = db or IrisSQL()
    table = table or name.split("/")[-1]
    progress = progress or (lambda *a: None)

    def check_cancel():
        if cancelled and cancelled():
            raise Cancelled("Import cancelled")

    progress("resolving", 0, 0)
    repo = resolve_name(name)
    commit = resolve_commit(repo, revision)
    check_cancel()
    log(f"Fetching {repo} [{config or 'default'}:{split}] @ {commit[:7]}"
        + (f" limit {limit}" if limit else ""))
    progress("fetching", 0, 0)
    ds = fetch_split(repo, split, config, limit, commit)
    check_cancel()
    labels = label_names(ds.features)
    data = add_label_names(ds.with_format("arrow")[:], labels)
    result = load_dataframe(data, table, db, mode, batch_size, log, progress, cancelled)
    progress("finishing", result["rows"], result["rows"])
    result.update({"dataset": name, "split": split, "config": config,
                   "revision": revision or None, "commit": commit,
                   "pinned": bool(COMMIT.fullmatch(revision or ""))})
    if labels:
        used = set()
        sql = {c: safe_column(c, used) for c in data.column_names}
        result["classLabels"] = {sql[c]: names for c, names in labels.items() if c in sql}
    return result


def info(name):
    """List configs and splits for a dataset, without downloading data."""
    name = resolve_name(name)
    try:                      # one call to the dataset viewer instead of one per config
        from . import hub
        found = hub.splits(name)
    except Exception:
        found = None
    if found:
        return {"dataset": name, "configs": dict(list(found.items())[:20])}
    from datasets import disable_progress_bars, get_dataset_config_names, get_dataset_split_names
    disable_progress_bars()
    configs = get_dataset_config_names(name)
    out = {}
    for c in configs[:20]:
        try:
            out[c] = get_dataset_split_names(name, c)
        except Exception as e:  # gated or broken configs
            out[c] = f"error: {e}"
    return {"dataset": name, "configs": out}

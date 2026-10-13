"""Tests the loader logic with SQLite standing in for IRIS SQL."""
import sqlite3, sys, os
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
import pytest
from types import SimpleNamespace as NS
from hf2iris.loader import (LoadError, check_split, error_text, load_dataframe, resolve_name,
                            safe_column, safe_table, sql_type)


class SqliteDB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:")
        self.owned = set()
    def _fix(self, sql):  # SQLite has no schemas: Demo.IMDB -> Demo_IMDB
        import re
        return re.sub(r"\b([A-Za-z]\w*)\.([A-Za-z]\w*)\b", r"\1_\2", sql)
    def execute(self, sql, *a):
        return self.c.execute(self._fix(sql), a)
    def executemany(self, sql, rows, first_row=1):
        self.c.executemany(self._fix(sql), rows)
    def begin(self):
        self.c.commit()                      # DML from here on is one transaction
    def commit(self):
        self.c.commit()
    def rollback(self):
        self.c.rollback()
    def table_exists(self, t):
        n = t.replace(".", "_")
        return self.c.execute("select count(*) from sqlite_master where name=?", (n,)).fetchone()[0] > 0
    def owns_table(self, t):
        return t.upper() in self.owned
    def mark_owned(self, t):
        self.owned.add(t.upper())
    def unmark_owned(self, t):
        self.owned.discard(t.upper())


def sample():
    return pd.DataFrame({
        "text": ["great movie", "awful", None, "ok " * 3000],
        "label": [1, 0, 1, 0],
        "score": [0.9, 0.1, float("nan"), 0.5],
        "is_ok": [True, False, True, False],
        "tokens": [["great", "movie"], ["awful"], [], ["ok"]],
        "meta": [{"src": "a"}, {"src": "b"}, None, {"src": "d"}],
        "2nd col!": ["x", "y", "z", "w"],
        "when": pd.to_datetime(["2024-01-01", "2024-02-01", None, "2024-03-01"]),
    })


def test_names():
    used = set()
    assert safe_column("2nd col!", used) == "c_2nd_col"
    assert safe_column("label", used) == "label_"
    assert safe_column("label", used) == "label__2"
    assert safe_table("stanfordnlp/imdb") == "HF.stanfordnlp_imdb"
    assert safe_table("Demo.IMDB") == "Demo.IMDB"
    assert safe_table("Demo.2024 data") == "Demo.t_2024_data"


def test_reserved_words_ask_iris(monkeypatch):
    import sys, types
    from hf2iris.loader import is_reserved
    fake = types.SimpleNamespace(system=types.SimpleNamespace(SQL=types.SimpleNamespace(
        IsReservedWord=lambda w: int(w.upper() in ("DEC", "FOREIGN")))))
    monkeypatch.setitem(sys.modules, "iris", fake)
    assert safe_column("dec", set()) == "dec_"                 # IRIS-only reserved word
    assert safe_column("text", set()) == "text_"               # hf2iris list, kept for old tables
    assert safe_table("Demo.Foreign") == "Demo.Foreign_"
    assert safe_table("Demo.text") == "Demo.text"              # table names only follow IRIS
    assert not is_reserved("species")


def test_types():
    df = sample()
    assert sql_type(df["label"]) == "BIGINT"
    assert sql_type(df["score"]) == "DOUBLE"
    assert sql_type(df["is_ok"]) == "BIT"
    assert sql_type(df["tokens"]) == "VARCHAR(4000)"     # JSON in a plain VARCHAR
    assert sql_type(df["text"]) == "VARCHAR(9000)"       # sized to the longest value (9000 chars)
    assert sql_type(df["2nd col!"]) == "VARCHAR(4000)"
    assert sql_type(df["when"]) == "TIMESTAMP"


def test_load_replace_and_append():
    db, df = SqliteDB(), sample()
    r = load_dataframe(df, "Demo.IMDB", db, batch_size=3, log=lambda *_: None)
    assert r["rows"] == 4
    rows = db.execute("select text_, label_, score, is_ok, tokens, meta from Demo.IMDB").fetchall()
    assert rows[0] == ("great movie", 1, 0.9, 1, '["great", "movie"]', '{"src": "a"}')
    assert rows[2][0] is None and rows[2][2] is None and rows[2][5] is None
    load_dataframe(df, "Demo.IMDB", db, mode="append", log=lambda *_: None)
    assert db.execute("select count(*) from Demo.IMDB").fetchone()[0] == 8
    load_dataframe(df, "Demo.IMDB", db, mode="replace", log=lambda *_: None)
    assert db.execute("select count(*) from Demo.IMDB").fetchone()[0] == 4


def test_numpy_arrays():
    import numpy as np
    db = SqliteDB()
    df = pd.DataFrame({"emb": [np.array([0.1, 0.2]), np.array([0.3, 0.4])]})
    load_dataframe(df, "T.Emb", db, log=lambda *_: None)
    assert db.execute("select emb from T.Emb").fetchone()[0] == "[0.1, 0.2]"


def test_long_text_after_first_200_rows():
    # type must come from the whole column, or the late long value overflows the VARCHAR
    s = pd.Series(["short"] * 300 + ["x" * 5000])
    assert sql_type(s) == "VARCHAR(5000)"


def test_numpy_array_column_is_json_text():
    import numpy as np
    s = pd.Series([np.array([1, 2, 3])] * 10, dtype=object)
    assert sql_type(s) == "VARCHAR(4000)"


def test_arrow_types_are_kept():
    import datetime, decimal
    import pyarrow as pa
    from hf2iris.loader import sql_column
    ints = sql_column(pa.array([1, None, 3], pa.int64()))
    assert (ints.sql_type, ints.values) == ("BIGINT", [1, None, 3])        # pandas would make 1.0, NaN
    f32 = sql_column(pa.array([1.4, None], pa.float32()))
    assert (f32.sql_type, f32.values) == ("DOUBLE", [1.4, None])           # not 1.399999976158142
    assert sql_column(pa.array([float("nan"), 2.0])).values == [None, 2.0]
    inf = sql_column(pa.array([1.0, float("inf")]))
    assert inf.sql_type == "VARCHAR(4000)" and inf.values == ["1.0", "inf"]
    big = sql_column(pa.array([2 ** 64 - 1], pa.uint64()))
    assert big.sql_type == "VARCHAR(4000)" and big.values == ["18446744073709551615"]
    dec = sql_column(pa.array([decimal.Decimal("12.345")], pa.decimal128(10, 3)))
    assert (dec.sql_type, dec.values) == ("NUMERIC(10,3)", ["12.345"])
    d = sql_column(pa.array([datetime.date(1700, 6, 1)]))
    assert (d.sql_type, d.values) == ("DATE", [datetime.date(1700, 6, 1)])
    ts = sql_column(pa.array([datetime.datetime(2024, 1, 15, 10, 20, 30, 123456)], pa.timestamp("ns")))
    assert (ts.sql_type, ts.values) == ("TIMESTAMP", ["2024-01-15 10:20:30.123456"])
    lab = sql_column(pa.array(["a", "b", "a"]).dictionary_encode())
    assert lab.values == ["a", "b", "a"]
    emb = sql_column(pa.array([[0.5, 1.0]], pa.list_(pa.float32())))
    assert emb.sql_type == "VARCHAR(4000)" and emb.values == ["[0.5, 1.0]"]
    img = sql_column(pa.array([{"bytes": b"\x89PNG", "path": None}]))
    assert img.values == ['{"bytes": "iVBORw==", "path": null}']


def test_long_values_and_wide_rows():
    import pyarrow as pa
    from hf2iris.loader import sql_column, fit_rows, LONG_VALUE
    assert sql_column(pa.array(["x" * (LONG_VALUE + 1)])).sql_type == "LONGVARCHAR"
    assert sql_column(pa.array(["\U0001F917" * 2500])).sql_type == "VARCHAR(5000)"  # emoji = 2 UTF-16 units
    # three 900k-character columns fit one by one, but not in one IRIS row together
    cols = [sql_column(pa.array(["x" * 900_000, "y"])) for _ in range(4)]
    fit_rows(cols)
    assert [c.sql_type for c in cols].count("LONGVARCHAR") == 1


def test_iris_bind_values():
    import datetime
    from hf2iris.loader import IrisSQL
    assert [IrisSQL.bind(v) for v in (None, "", "a", 5, 1.5)] == ["", "\x00", "a", 5, 1.5]
    assert IrisSQL.bind(datetime.date(1840, 12, 31)) == 0
    assert IrisSQL.bind(datetime.date(2024, 1, 15)) == 66854                # $h, checked in IRIS
    assert IrisSQL.bind("2024-01-15 10:20:30") == "2024-01-15 10:20:30"     # timestamps stay text


def test_resolve_legacy_dataset_name():
    lookup = {"rotten_tomatoes": "cornell-movie-review-data/rotten_tomatoes"}.get
    assert resolve_name("rotten_tomatoes", lookup) == "cornell-movie-review-data/rotten_tomatoes"
    assert resolve_name("openai/gsm8k", lambda n: 1 / 0) == "openai/gsm8k"   # no lookup when namespaced


def test_foreign_table_is_never_dropped_or_appended():
    db = SqliteDB()
    db.execute("CREATE TABLE Sample.Person (Name VARCHAR(50))")
    db.execute("INSERT INTO Sample.Person VALUES ('Ann')")
    quiet = lambda *_: None
    for mode in ("replace", "append", "fail"):
        with pytest.raises(LoadError, match="already exists"):
            load_dataframe(sample(), "Sample.Person", db, mode=mode, log=quiet)
    assert db.execute("select Name from Sample.Person").fetchall() == [("Ann",)]


def test_own_table_can_be_replaced():
    db, quiet = SqliteDB(), (lambda *_: None)
    load_dataframe(sample(), "Demo.Mine", db, log=quiet)
    assert db.owns_table("DEMO.MINE")
    load_dataframe(sample(), "Demo.Mine", db, mode="replace", log=quiet)
    with pytest.raises(LoadError, match="mode must be"):
        load_dataframe(sample(), "Demo.Mine", db, mode="overwrite", log=quiet)


def builder(splits, download_size=None, config="default"):
    info = {s: NS(num_examples=n) for s, n in splits.items()}
    return NS(info=NS(splits=info, download_size=download_size), config=NS(name=config, data_files=None))


def test_check_split_before_download():
    b = builder({"train": 100, "test": 10})
    assert check_split("x/y", "test", None, None, builder=b) is b
    with pytest.raises(LoadError, match="Available splits: train, test"):
        check_split("x/y", "validation", None, None, builder=b)
    big_rows = builder({"train": 50_000_000})
    with pytest.raises(LoadError, match="row limit"):
        check_split("x/y", "train", None, None, builder=big_rows)
    big_files = builder({"train": 10}, download_size=30 * 2 ** 30)
    with pytest.raises(LoadError, match="MB limit"):
        check_split("x/y", "train", None, None, builder=big_files)
    # with a row limit only the first rows are streamed, so size does not matter
    assert check_split("x/y", "train", None, 1000, builder=big_files) is big_files


def test_error_text():
    class DatasetNotFoundError(Exception): pass
    class GatedRepoError(Exception): pass
    assert error_text(LoadError("plain")) == "plain"
    assert error_text(DatasetNotFoundError("Dataset 'a/b' doesn't exist on the Hub.")).startswith("Dataset 'a/b' was not found")
    class RepositoryNotFoundError(Exception): pass
    hub = RepositoryNotFoundError("404 Client Error. Repository Not Found for url: https://huggingface.co/api/datasets/a/b. "
                                  "If you are trying to access a private or gated repo, make sure you are authenticated.")
    assert error_text(hub).startswith("Dataset 'a/b' was not found")        # not "gated"
    assert "gated" in error_text(GatedRepoError("401"))
    missing = ValueError("Config name is missing.\nPlease pick one among the available configs: ['main', 'socratic']")
    assert error_text(missing) == "This dataset has several configs, choose one: main, socratic"
    bad = ValueError("BuilderConfig 'nope' not found. Available: ['main', 'socratic']")
    assert error_text(bad) == "Config 'nope' not found. Available configs: main, socratic"
    assert error_text(KeyError("x")) == "KeyError: 'x'"


class ClassLabel:                      # stands in for datasets.ClassLabel (matched by class name)
    def __init__(self, names):
        self.names = names


def test_label_names_add_name_columns():
    import pyarrow as pa
    from hf2iris.loader import add_label_names, label_names
    features = {"text": object(), "label": ClassLabel(["neg", "pos"]),
                "tags": NS(feature=ClassLabel(["a", "b", "c"]))}
    labels = label_names(features)
    assert labels == {"label": ["neg", "pos"], "tags": ["a", "b", "c"]}
    t = pa.table({"text": ["x", "y", "z"], "label": [1, -1, None], "tags": [[0, 2], [], None]})
    t = add_label_names(t, labels)
    assert t.column_names == ["text", "label", "label_name", "tags", "tags_name"]
    assert t.column("label_name").to_pylist() == ["pos", None, None]     # -1 = no label
    assert t.column("tags_name").to_pylist() == ['["a", "c"]', "[]", None]


def test_resolve_commit():
    from hf2iris.loader import resolve_commit
    sha = "aa13bc287fa6fcab6daf52f0dfb9994269ffea28"
    seen = []
    assert resolve_commit("a/b", "main", lambda n, r: seen.append((n, r)) or sha) == sha
    assert resolve_commit("a/b", "", lambda n, r: seen.append((n, r)) or sha) == sha
    assert seen == [("a/b", "main"), ("a/b", None)]

    class RevisionNotFoundError(Exception):
        pass

    def missing(n, r):
        raise RevisionNotFoundError("404")
    with pytest.raises(LoadError, match="Revision 'nope' not found for a/b"):
        resolve_commit("a/b", "nope", missing)


def test_load_records_commit_and_labels(monkeypatch):
    import pyarrow as pa
    import hf2iris.loader as L
    sha = "aa13bc287fa6fcab6daf52f0dfb9994269ffea28"
    table = pa.table({"text": ["good", "bad"], "label": [1, 0]})

    class FakeDS:
        features = {"text": object(), "label": ClassLabel(["neg", "pos"])}

        def with_format(self, kind):       # ds.with_format("arrow")[:] -> the table
            return self

        def __getitem__(self, key):
            return table
    asked = {}
    monkeypatch.setattr(L, "resolve_commit", lambda n, r: sha)
    monkeypatch.setattr(L, "fetch_split", lambda *a: asked.setdefault("args", a) and FakeDS())
    db = SqliteDB()
    r = L.load("x/movies", "Demo.Movies", "train", revision="main", db=db, log=lambda *_: None)
    assert asked["args"][-1] == sha                     # the data is read from the resolved commit
    assert (r["revision"], r["commit"], r["pinned"]) == ("main", sha, False)
    assert r["classLabels"] == {"label_": ["neg", "pos"]}
    assert db.execute("select text_, label_, label_name from Demo.Movies").fetchall() == [("good", 1, "pos"), ("bad", 0, "neg")]
    r = L.load("x/movies", "Demo.Movies", "train", revision=sha, db=db, log=lambda *_: None)
    assert r["pinned"] is True


def test_accented_names_are_transliterated():
    assert safe_column("Café Größe", set()) == "Cafe_Groe"
    assert safe_column("名前", set()) == "col"                   # nothing left: fallback name
    assert safe_table("Démo.Données") == "Demo.Donnees"


def test_exact_collation_only_on_varchar():
    import pyarrow as pa
    from hf2iris.loader import build_ddl, IrisSQL
    t = pa.table({"s": ["a"], "n": [1]})
    ddl = build_ddl("Demo.T", t, IrisSQL.text_collation)[0]
    assert "s VARCHAR(4000) COLLATE %EXACT" in ddl and "n BIGINT," not in ddl and "n BIGINT\n" in ddl
    assert "COLLATE" not in build_ddl("Demo.T", t)[0]          # other backends: no collation


class FailingDB(SqliteDB):
    """Rejects the insert batch that starts at row `bad`."""
    def __init__(self, bad):
        super().__init__()
        self.bad = bad
    def executemany(self, sql, rows, first_row=1):
        if first_row == self.bad:
            raise LoadError(f"Row {first_row} was rejected by IRIS: test")
        super().executemany(sql, rows, first_row)


def test_failed_new_table_is_removed():
    db = FailingDB(bad=3)
    with pytest.raises(LoadError, match="Row 3 was rejected.*Demo.New was not created"):
        load_dataframe(sample(), "Demo.New", db, batch_size=2, log=lambda *_: None)
    assert not db.table_exists("Demo.New") and not db.owns_table("Demo.New")


def test_failed_append_is_rolled_back():
    db = FailingDB(bad=99)
    load_dataframe(sample(), "Demo.Keep", db, log=lambda *_: None)              # 4 rows
    db.bad = 3
    with pytest.raises(LoadError, match="rolled back; Demo.Keep is unchanged"):
        load_dataframe(sample(), "Demo.Keep", db, mode="append", batch_size=2, log=lambda *_: None)
    assert db.execute("select count(*) from Demo.Keep").fetchone()[0] == 4      # first batch undone too


def test_row_count_is_checked():
    class LosingDB(SqliteDB):                # drops the last row of every batch
        def executemany(self, sql, rows, first_row=1):
            super().executemany(sql, rows[:-1], first_row)
    with pytest.raises(LoadError, match="4 rows were read but 2 arrived"):
        load_dataframe(sample(), "Demo.Lost", LosingDB(), batch_size=2, log=lambda *_: None)


def test_size_check_for_pinned_commit():
    # metadata of a commit hash has no sizes: fall back to the latest metadata ...
    pinned = NS(info=NS(splits=None, download_size=None), config=NS(name="c", data_files={"train": ["f1", "f2"]}))
    latest = builder({"train": 14_868_862}, download_size=30 * 2 ** 30)
    with pytest.raises(LoadError, match="row limit"):
        check_split("x/y", "train", "c", None, revision="9bb295d", builder=pinned, latest=latest)
    # ... and when that has none either, to the data file sizes
    unknown = builder({}, download_size=None)
    sizes = {"f1": 400 * 2 ** 20, "f2": 300 * 2 ** 20}
    with pytest.raises(LoadError, match="700 MB"):
        check_split("x/y", "train", "c", None, revision="9bb295d", builder=pinned, latest=unknown, file_size=sizes.get)
    sizes["f2"] = 1
    assert check_split("x/y", "train", "c", None, revision="9bb295d", builder=pinned, latest=unknown, file_size=sizes.get) is pinned


def test_token_is_masked(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_SecretValue123")
    assert error_text(RuntimeError("401 for token hf_SecretValue123")) == "RuntimeError: 401 for token ***"
    assert error_text(LoadError("bad hf_SecretValue123")) == "bad ***"


def test_progress_and_cancel():
    seen = []
    db = SqliteDB()
    load_dataframe(sample(), "Demo.P", db, batch_size=3, log=lambda *_: None,
                   progress=lambda *a: seen.append(a))
    assert seen == [("inserting", 0, 4), ("inserting", 3, 4), ("inserting", 4, 4)]
    from hf2iris.loader import Cancelled
    calls = iter([False, True])
    with pytest.raises(Cancelled, match="Import cancelled.*rolled back"):
        load_dataframe(sample(), "Demo.P", db, mode="append", batch_size=3, log=lambda *_: None,
                       cancelled=lambda: next(calls))
    assert db.execute("select count(*) from Demo.P").fetchone()[0] == 4      # first batch undone


def test_empty_table_loads():
    import pyarrow as pa
    db = SqliteDB()
    r = load_dataframe(pa.table({"t": pa.array([], pa.string())}), "Demo.Empty", db, log=lambda *_: None)
    assert r["rows"] == 0 and r["columns"] == {"t": "VARCHAR(4000)"}


def test_target_and_limits():
    from hf2iris.loader import target_table, check_target, check_limits, LimitExceeded, TableConflict
    assert target_table("stanfordnlp/imdb") == "HF.imdb" and target_table("x/y", "Demo.T") == "Demo.T"
    db = SqliteDB()
    assert check_target("Demo.New", db, "fail") == (False, False)
    db.execute("CREATE TABLE Demo.Other (a INT)")
    with pytest.raises(TableConflict):
        check_target("Demo.Other", db, "append")
    with pytest.raises(LoadError, match="mode"):
        check_target("Demo.Other", db, "bogus")
    check_limits("train", 10, 10)
    with pytest.raises(LimitExceeded, match="row limit"):
        check_limits("train", 10 ** 9, None)

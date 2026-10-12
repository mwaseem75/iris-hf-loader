"""Tests the loader logic with SQLite standing in for IRIS SQL."""
import sqlite3, sys, os
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from hf2iris.loader import load_dataframe, resolve_name, safe_column, safe_table, sql_type


class SqliteDB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:")
    def _fix(self, sql):  # SQLite has no schemas: Demo.IMDB -> Demo_IMDB
        import re
        return re.sub(r"\b([A-Za-z]\w*)\.([A-Za-z]\w*)\b", r"\1_\2", sql)
    def execute(self, sql, *a):
        return self.c.execute(self._fix(sql), a)
    def executemany(self, sql, rows):
        self.c.executemany(self._fix(sql), rows)
    def table_exists(self, t):
        n = t.replace(".", "_")
        return self.c.execute("select count(*) from sqlite_master where name=?", (n,)).fetchone()[0] > 0


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


def test_types():
    df = sample()
    assert sql_type(df["label"]) == "BIGINT"
    assert sql_type(df["score"]) == "DOUBLE"
    assert sql_type(df["is_ok"]) == "BIT"
    assert sql_type(df["tokens"]) == "LONGVARCHAR"
    assert sql_type(df["text"]) == "LONGVARCHAR"
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
    # type must come from the whole column, or the late long value overflows VARCHAR(4000)
    s = pd.Series(["short"] * 300 + ["x" * 5000])
    assert sql_type(s) == "LONGVARCHAR"


def test_numpy_array_column_is_longvarchar():
    import numpy as np
    s = pd.Series([np.array([1, 2, 3])] * 10, dtype=object)
    assert sql_type(s) == "LONGVARCHAR"


def test_resolve_legacy_dataset_name():
    lookup = {"rotten_tomatoes": "cornell-movie-review-data/rotten_tomatoes"}.get
    assert resolve_name("rotten_tomatoes", lookup) == "cornell-movie-review-data/rotten_tomatoes"
    assert resolve_name("openai/gsm8k", lambda n: 1 / 0) == "openai/gsm8k"   # no lookup when namespaced

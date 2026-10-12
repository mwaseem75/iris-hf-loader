"""Tests against real IRIS SQL (no dataset downloads).

Run inside the container with tests/run_iris_tests.sh; outside IRIS they are skipped.
Tables are created in schema HFTest and dropped again.
"""
import datetime
import decimal
import json
import os
import sys

import pytest

try:
    import iris
    if not hasattr(iris, "sql"):          # the Native API package, not embedded Python
        raise ImportError
except ImportError:
    pytest.skip("needs IRIS embedded Python (run tests/run_iris_tests.sh)", allow_module_level=True)

import pyarrow as pa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from hf2iris.loader import IrisSQL, LoadError, load_dataframe  # noqa: E402

QUIET = lambda *_: None   # noqa: E731


def rows(sql, *args):
    return [list(r) for r in iris.sql.exec(sql, *args)]


@pytest.fixture
def db():
    created = []
    db = IrisSQL()
    yield db, created
    for t in created:
        iris.sql.exec(f"DROP TABLE IF EXISTS {t}")
        db.unmark_owned(t)


def test_values_round_trip(db):
    db, created = db
    created.append("HFTest.Types")
    t = pa.table({
        "n": pa.array([1, None], pa.int64()),
        "f32": pa.array([1.4, None], pa.float32()),
        "s": pa.array(["", None]),
        "d": pa.array([datetime.date(1700, 6, 1), None]),
        "ts": pa.array([datetime.datetime(2024, 1, 15, 10, 20, 30, 123456), None]),
        "dec": pa.array([decimal.Decimal("-0.001"), None], pa.decimal128(10, 3)),
        "emoji": pa.array(["\U0001F917 café", None]),
    })
    r = load_dataframe(t, "HFTest.Types", db, log=QUIET)
    assert "dec_" in r["columns"]                                   # DEC is reserved in IRIS
    got = rows("SELECT n, f32, s, LENGTH(s), CASE WHEN s IS NULL THEN 1 ELSE 0 END, "
               "{fn CONVERT(d, SQL_VARCHAR)}, emoji FROM HFTest.Types ORDER BY %ID")
    assert got[0][:2] == [1, 1.4]                                    # float32 1.4 stays 1.4
    assert got[0][3:5] == [0, 0]                                     # empty string, not NULL
    assert got[0][5] == "1700-06-01"
    assert got[0][6] == "\U0001F917 café"
    assert got[1][0] in (None, "") and got[1][4] == 1               # NULL stays NULL
    ts = rows("SELECT %EXTERNAL(ts) FROM HFTest.Types WHERE n = 1")[0][0]
    assert str(ts).startswith("2024-01-15 10:20:30.123456")


def test_exact_collation(db):
    db, created = db
    created.append("HFTest.Coll")
    load_dataframe(pa.table({"v": ["Iris-setosa", "IRIS-SETOSA", "iris-setosa "]}), "HFTest.Coll", db, log=QUIET)
    assert rows("SELECT COUNT(DISTINCT v) FROM HFTest.Coll") == [[3]]
    assert rows("SELECT COUNT(*) FROM HFTest.Coll WHERE %SQLUPPER(v) = %SQLUPPER('iris-setosa')") == [[3]]


def test_foreign_table_untouched(db):
    db, created = db
    created.append("HFTest.People")
    iris.sql.exec("CREATE TABLE HFTest.People (Name VARCHAR(50))")
    iris.sql.exec("INSERT INTO HFTest.People (Name) VALUES ('Ann')")
    for mode in ("replace", "append"):
        with pytest.raises(LoadError, match="not created by hf2iris"):
            load_dataframe(pa.table({"x": [1]}), "HFTest.People", db, mode=mode, log=QUIET)
    assert rows("SELECT Name FROM HFTest.People") == [["Ann"]]


def test_reserved_table_name(db):
    db, created = db
    created.append("HFTest.Foreign_")
    r = load_dataframe(pa.table({"x": [1]}), "HFTest.Foreign", db, log=QUIET)
    assert r["table"] == "HFTest.Foreign_"


def test_rejected_row_rolls_back_append(db):
    db, created = db
    created.append("HFTest.Grow")
    load_dataframe(pa.table({"v": ["short"]}), "HFTest.Grow", db, log=QUIET)       # VARCHAR(4000)
    too_long = pa.table({"v": ["ok", "x" * 5000]})
    with pytest.raises(LoadError, match="Row 2 was rejected by IRIS.*rolled back"):
        load_dataframe(too_long, "HFTest.Grow", db, mode="append", log=QUIET)
    assert rows("SELECT v FROM HFTest.Grow") == [["short"]]                         # "ok" undone too


def test_failure_inside_caller_transaction(db):
    db, created = db
    created += ["HFTest.Caller", "HFTest.Grow2"]
    iris.sql.exec("CREATE TABLE HFTest.Caller (v VARCHAR(10))")
    load_dataframe(pa.table({"v": ["short"]}), "HFTest.Grow2", db, log=QUIET)
    iris.sql.exec("START TRANSACTION")
    iris.sql.exec("INSERT INTO HFTest.Caller (v) VALUES ('mine')")
    with pytest.raises(LoadError):
        load_dataframe(pa.table({"v": ["x" * 5000]}), "HFTest.Grow2", db, mode="append", log=QUIET)
    assert iris.execute("return $TLEVEL") == 1                                      # caller still in its transaction
    assert rows("SELECT v FROM HFTest.Caller") == [["mine"]]                        # and its row survived
    iris.sql.exec("ROLLBACK")


def test_wide_row_fits(db):
    db, created = db
    created.append("HFTest.Wide")
    big = "x" * 900_000
    r = load_dataframe(pa.table({f"c{i}": [big] for i in range(4)}), "HFTest.Wide", db, log=QUIET)
    assert list(r["columns"].values()).count("LONGVARCHAR") == 1
    assert rows("SELECT COUNT(*) FROM HFTest.Wide") == [[1]]


def test_sql_functions_report_errors():
    r = json.loads(rows("SELECT dc_hf2iris.load_dataset('')")[0][0])
    assert r == {"status": "ERROR", "error": "dataset is required"}
    r = json.loads(rows("SELECT dc_hf2iris.dataset_info(NULL)")[0][0])
    assert r == {"error": "dataset is required"}

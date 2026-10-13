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


@pytest.fixture
def no_trace():
    """Deletes the ImportLog and Job rows a test creates, and its HFTest tables."""
    log = rows("SELECT COALESCE(MAX(ID), 0) FROM dc_hf2iris.ImportLog")[0][0]
    job = rows("SELECT COALESCE(MAX(ID), 0) FROM dc_hf2iris.Job")[0][0]
    yield
    for sql, last in [("DELETE FROM dc_hf2iris.ImportLog WHERE ID > ? AND (Dataset = '' OR Dataset IS NULL "
                       "OR Dataset LIKE 'hftest/%')", log),
                      ("DELETE FROM dc_hf2iris.Job WHERE ID > ? AND Dataset LIKE 'hftest/%'", job)]:
        try:
            iris.sql.exec(sql, last)
        except Exception as e:     # iris.sql raises a blank SQLError when nothing was deleted
            if str(e).strip():
                raise
    for (t,) in rows("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'HFTest'"):
        iris.sql.exec(f"DROP TABLE HFTest.{t}")
        IrisSQL().unmark_owned(f"HFTest.{t}")


def test_sql_functions_report_errors(no_trace):
    r = json.loads(rows("SELECT dc_hf2iris.load_dataset('')")[0][0])
    assert r == {"status": "ERROR", "error": "dataset is required"}
    r = json.loads(rows("SELECT dc_hf2iris.dataset_info(NULL)")[0][0])
    assert r == {"error": "dataset is required"}


def test_hub_cache_on_real_global():
    from hf2iris import hub
    store = hub.GlobalCache("^CacheTemp.hf2iristest")
    try:
        store.set("k1", "9999999999\t{}")
        store.set("k2", "1\t{}")
        assert store.get("k1") == "9999999999\t{}" and store.get("nope") is None
        assert sorted(store.keys()) == ["k1", "k2"]
        assert hub._read(store, "k2", 100) is None and store.get("k2") is None   # expired, removed
        assert iris.cls("%SYS.Namespace").GetGlobalDest("USER", "CacheTemp.hf2iris").endswith("iristemp/")
    finally:
        iris.gref("^CacheTemp.hf2iristest").kill([])


def test_hub_wrappers_return_coded_errors():
    hub_cls = iris.cls("dc.hf2iris.Hub")
    assert json.loads(hub_cls.Details("a b"))["code"] == "bad_request"
    assert json.loads(hub_cls.Preview("a/b", "", "train"))["code"] == "bad_request"
    assert json.loads(hub_cls.Search("", "random"))["code"] == "bad_request"
    status = json.loads(hub_cls.Status())
    assert isinstance(status["hfToken"], bool) and status["iris"].startswith("20")
    token = os.environ.get("HF_TOKEN")
    assert not token or token not in json.dumps(status)


# ------------------------------------------------------- background jobs ---
SHA = "aa13bc287fa6fcab6daf52f0dfb9994269ffea28"
JOB = iris.cls("dc.hf2iris.Job")


class ClassLabel:
    def __init__(self, names):
        self.names = names


def fake_source(monkeypatch, n=2500, on_fetch=None):
    """Make hf2iris.load read a local table instead of the Hub (same process only)."""
    import hf2iris.loader as L
    table = pa.table({"text": [f"row {i}" for i in range(n)], "label": [i % 2 for i in range(n)]})

    class FakeDS:
        features = {"text": object(), "label": ClassLabel(["neg", "pos"])}

        def with_format(self, kind):
            return self

        def __getitem__(self, key):
            return table

    def fetch(*args):
        if on_fetch:
            on_fetch()
        return FakeDS()
    monkeypatch.setattr(L, "resolve_commit", lambda repo, rev=None, lookup=None: SHA)
    monkeypatch.setattr(L, "fetch_split", fetch)


def new_job(table, state="queued", cancel=0, dataset="hftest/fake"):
    iris.sql.exec("INSERT INTO dc_hf2iris.Job (Dataset, TableName, Split, Mode, RowLimit, State, "
                  "CancelRequested, CreatedAt) VALUES (?, ?, 'train', 'replace', 0, ?, ?, CURRENT_TIMESTAMP)",
                  dataset, table, state, cancel)
    return str(rows("SELECT MAX(ID) FROM dc_hf2iris.Job")[0][0])


def wait_for(job_id, states=("ok", "error", "cancelled", "interrupted"), seconds=20):
    import time
    end = time.time() + seconds
    while time.time() < end:
        job = json.loads(JOB.Get(job_id))
        assert "state" in job, job
        if job["state"] in states:
            return job
        time.sleep(0.3)
    raise AssertionError(f"job {job_id} did not finish: {job}")


def test_job_runs_and_records(no_trace, monkeypatch):
    fake_source(monkeypatch)
    job_id = new_job("HFTest.JobOk")
    JOB.Run(job_id)                                   # in this process, so the fake source applies
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "ok" and job["rowsDone"] == job["rowsTotal"] == 2500
    assert job["result"]["rows"] == 2500 and job["result"]["commit"] == SHA
    assert job["result"]["classLabels"] == {"label_": ["neg", "pos"]}
    assert job["phase"] is None and job["finishedAt"] and isinstance(job["elapsed"], int)
    log = rows("SELECT Status, RowCount, TableName FROM dc_hf2iris.ImportLog WHERE ID = ?", job["importLogId"])
    assert log == [["OK", 2500, "HFTest.JobOk"]]
    assert rows("SELECT COUNT(*), MAX(label_name) FROM HFTest.JobOk") == [[2500, "pos"]]


def test_job_progress_and_cancel_flag(no_trace):
    job_id = new_job("HFTest.JobP")
    JOB.Progress(job_id, "inserting", 1000, 2500)
    job = json.loads(JOB.Get(job_id))
    assert (job["phase"], job["rowsDone"], job["rowsTotal"]) == ("inserting", 1000, 2500)
    assert JOB.IsCancelled(job_id) == 0
    assert json.loads(JOB.Cancel(job_id))["cancelRequested"] is True
    assert JOB.IsCancelled(job_id) == 1


def test_job_cancelled_before_start(no_trace):
    job_id = new_job("HFTest.JobC", cancel=1)
    JOB.Run(job_id)
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "cancelled" and "before it started" in job["error"]
    assert rows("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'HFTest' AND TABLE_NAME = 'JobC'") == [[0]]


def test_job_cancelled_while_fetching(no_trace, monkeypatch):
    job_id = new_job("HFTest.JobF")
    # another process asks to cancel while the data is being downloaded
    fake_source(monkeypatch, on_fetch=lambda: iris.sql.exec(
        "UPDATE dc_hf2iris.Job SET CancelRequested = 1 WHERE ID = ?", job_id))
    JOB.Run(job_id)
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "cancelled" and "cancelled" in job["error"].lower()
    assert rows("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'HFTest' AND TABLE_NAME = 'JobF'") == [[0]]


def test_background_job_and_table_lock(no_trace):
    """Start() spawns a real background process; it stops at the table lock this test
    holds, before any network access."""
    iris.execute('lock +^dc.hf2iris.Table("HFTEST.LOCKED")')
    try:
        started = json.loads(JOB.Start(iris.cls("%DynamicObject")._FromJSON(
            json.dumps({"dataset": "hftest/fake", "table": "HFTest.Locked", "limit": 5}))))
        assert started["state"] in ("queued", "running", "error") and started["table"] == "HFTest.Locked"
        job = wait_for(started["id"])
        assert job["state"] == "error" and "Another import into HFTest.Locked is running" in job["error"]
        assert job["startedAt"] and job["importLogId"]
        # normal imports take the same lock in Loader.Run; IRIS locks are re-entrant within a
        # process, so that cannot be shown from this process
    finally:
        iris.execute('lock -^dc.hf2iris.Table("HFTEST.LOCKED")')


def test_start_checks_first(no_trace):
    start = lambda **kw: json.loads(JOB.Start(iris.cls("%DynamicObject")._FromJSON(json.dumps(kw))))
    assert start(dataset="a b")["code"] == "bad_request"
    assert start(dataset="hftest/fake", mode="zap")["code"] == "bad_request"
    iris.sql.exec("CREATE TABLE HFTest.Theirs (a INT)")
    assert start(dataset="hftest/fake", table="HFTest.Theirs")["code"] == "conflict"
    ids = [new_job("HFTest.Q1"), new_job("HFTest.Q2")]           # two young queued jobs
    busy = start(dataset="hftest/fake", table="HFTest.Q3")
    assert busy["code"] == "busy" and "imports are already running" in busy["error"]
    for job_id in ids:
        iris.sql.exec("UPDATE dc_hf2iris.Job SET State = 'error' WHERE ID = ?", job_id)


def test_interrupted_and_finished_jobs(no_trace):
    job_id = new_job("HFTest.Gone", state="running")              # no process holds its lock
    job = json.loads(JOB.Get(job_id))
    assert job["state"] == "interrupted" and "ended unexpectedly" in job["error"]
    assert json.loads(JOB.Cancel(job_id))["code"] == "conflict"
    assert json.loads(JOB.Get("999999999"))["code"] == "not_found"
    listed = json.loads(JOB.List(5))
    assert listed["count"] <= 5 and listed["items"][0]["id"] == job_id


def test_plan_in_iris(no_trace, monkeypatch):
    from hf2iris import hub, loader, plan
    monkeypatch.setattr(loader, "resolve_commit", lambda repo, rev=None, lookup=None: SHA)
    meta = pa.schema([])
    builder = type("B", (), {})()
    builder.info = type("I", (), {"splits": {"train": type("S", (), {"num_examples": 10})()},
                                  "download_size": 1000, "features": None})()
    builder.config = type("C", (), {"name": "default", "data_files": None})()
    monkeypatch.setattr(plan, "_builder", lambda *a: builder)
    monkeypatch.setattr(hub, "sample", lambda *a: {"features": {
        "text": {"dtype": "string", "_type": "Value"}, "label": {"names": ["neg", "pos"], "_type": "ClassLabel"}},
        "rows": [{"text": "hello", "label": 1}]})
    iris.sql.exec("CREATE TABLE HFTest.Theirs (a INT)")
    body = lambda **kw: iris.cls("%DynamicObject")._FromJSON(json.dumps(kw))
    r = json.loads(iris.cls("dc.hf2iris.Loader").Plan(body(dataset="hftest/fake", table="HFTest.Theirs")))
    assert r["action"] == "refused" and "not created by hf2iris" in r["reason"]
    r = json.loads(iris.cls("dc.hf2iris.Loader").Plan(body(dataset="hftest/fake", table="HFTest.Planned")))
    assert r["action"] == "create" and [c["name"] for c in r["columns"]] == ["text_", "label_", "label_name"]
    assert "COLLATE %EXACT" in r["ddl"]
    assert rows("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'HFTest' AND TABLE_NAME = 'Planned'") == [[0]]


def test_tables_list_and_detail(no_trace):
    load_dataframe(pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]}), "HFTest.Listed", IrisSQL(), log=QUIET)
    iris.sql.exec("CREATE TABLE HFTest.NotOurs (a INT)")
    tables = iris.cls("dc.hf2iris.Tables")
    items = json.loads(tables.List())["items"]
    names = [t["table"] for t in items]
    assert "HFTest.Listed" in names and "HFTest.NotOurs" not in names
    item = next(t for t in items if t["table"] == "HFTest.Listed")
    assert (item["rows"], item["columnCount"], item["dataset"]) == (3, 2, None)
    d = json.loads(tables.Detail("hftest.listed"))                      # case-insensitive
    assert d["table"] == "HFTest.Listed"
    assert d["columns"] == [{"name": "a", "type": "BIGINT"}, {"name": "b", "type": "VARCHAR(4000)"}]
    for bad in ("HFTest.NotOurs", "x;drop", "HFTest.Missing"):
        assert json.loads(tables.Detail(bad))["code"] == "not_found", bad

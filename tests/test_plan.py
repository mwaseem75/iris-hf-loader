"""Offline tests of the import plan (hf2iris.plan): Hub, viewer and datasets are faked."""
import os
import sys
from types import SimpleNamespace as NS

import pyarrow as pa
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from hf2iris import hub, loader, plan  # noqa: E402
from hf2iris.hub import HubError  # noqa: E402
from tests.test_loader import SqliteDB  # noqa: E402

SHA = "aa13bc287fa6fcab6daf52f0dfb9994269ffea28"


class ClassLabel:
    def __init__(self, names):
        self.names = names


class FakeFeatures(dict):
    """Stands in for datasets.Features built from the viewer's serialized features."""

    def __init__(self, serialized):
        super().__init__()
        fields = []
        for name, f in serialized.items():
            kind = f.get("_type")
            if kind == "ClassLabel":
                self[name] = ClassLabel(f["names"])
                fields.append(pa.field(name, pa.int64()))
            elif kind == "Image":
                self[name] = NS()
                fields.append(pa.field(name, pa.struct([("bytes", pa.binary()), ("path", pa.string())])))
            else:
                self[name] = NS()
                fields.append(pa.field(name, pa.string() if f.get("dtype") == "string" else pa.int64()))
        self.arrow_schema = pa.schema(fields)


SERIALIZED = {"text": {"dtype": "string", "_type": "Value"},
              "label": {"names": ["neg", "pos"], "_type": "ClassLabel"}}


def builder(rows=1066, download=699_000, features=None, config="default", splits=("train", "test")):
    info = NS(splits={s: NS(num_examples=rows) for s in splits}, download_size=download,
              features=features)
    return NS(info=info, config=NS(name=config, data_files=None))


@pytest.fixture
def fakes(monkeypatch):
    calls = {}
    monkeypatch.setattr(hub, "resolve", lambda d: "cornell-movie-review-data/rotten_tomatoes" if "/" not in d else d)
    monkeypatch.setattr(loader, "resolve_commit", lambda repo, rev, lookup=None: SHA)
    monkeypatch.setattr(plan, "features_from_dict", FakeFeatures)
    sample = {"features": SERIALIZED, "rows": [{"text": "x" * 4500, "label": 1}, {"text": "ok", "label": 0}]}
    monkeypatch.setattr(hub, "sample", lambda repo, config, split, rows: calls.setdefault("sample", sample))
    return calls


def test_plan_create_with_columns_and_labels(fakes):
    r = plan.plan("rotten_tomatoes", "Demo.Reviews", "test", db=SqliteDB(), builder=builder())
    assert (r["repo"], r["commit"], r["action"], r["reason"]) == (
        "cornell-movie-review-data/rotten_tomatoes", SHA, "create", None)
    assert r["table"] == "Demo.Reviews" and r["tableExists"] is False and r["rowsEstimate"] == 1066
    assert r["columns"] == [
        {"source": "text", "name": "text_", "type": "VARCHAR(5000)", "note": "renamed from 'text'"},
        {"source": "label", "name": "label_", "type": "BIGINT", "note": "renamed from 'label'"},
        {"source": "label_name", "name": "label_name", "type": "VARCHAR(4000)", "note": "label names: neg, pos"}]
    assert r["ddl"].startswith("CREATE TABLE Demo.Reviews") and r["sample"] == "viewer"
    assert r["download"] == {"fullLoad": True, "rows": 1066, "bytes": 699_000, "withinLimits": True}
    assert any("first 2 rows" in w for w in r["warnings"])


def test_plan_default_table_and_row_limit(fakes):
    r = plan.plan("stanfordnlp/imdb", split="test", limit=50, db=SqliteDB(), builder=builder())
    assert r["table"] == "HF.imdb" and r["rowsEstimate"] == 50
    assert r["download"]["fullLoad"] is False and r["download"]["bytes"] is None


def test_plan_actions(fakes):
    db = SqliteDB()
    loader.load_dataframe(pa.table({"a": [1]}), "Demo.Mine", db, log=lambda *_: None)
    db.execute("CREATE TABLE Demo.Theirs (a INT)")
    b = builder()
    assert plan.plan("a/b", "Demo.Mine", "test", db=db, builder=b)["action"] == "replace"
    append = plan.plan("a/b", "Demo.Mine", "test", mode="append", db=db, builder=b)
    assert append["action"] == "append" and append["ownedByLoader"] is True
    fail = plan.plan("a/b", "Demo.Mine", "test", mode="fail", db=db, builder=b)
    assert fail["action"] == "refused" and "already exists" in fail["reason"]
    theirs = plan.plan("a/b", "Demo.Theirs", "test", db=db, builder=b)
    assert theirs["action"] == "refused" and "not created by hf2iris" in theirs["reason"]
    assert theirs["columns"]                                # the plan is still shown
    bogus = plan.plan("a/b", "Demo.New", "test", mode="overwrite", db=db, builder=b)
    assert bogus["action"] == "refused" and "mode must be" in bogus["reason"]
    assert db.execute("select count(*) from Demo.Mine").fetchone()[0] == 1   # nothing written


def test_plan_over_the_limits(fakes):
    r = plan.plan("a/b", "Demo.Big", "test", db=SqliteDB(), builder=builder(rows=50_000_000))
    assert r["action"] == "refused" and "row limit" in r["reason"]
    assert r["download"]["withinLimits"] is False
    ok = plan.plan("a/b", "Demo.Big", "test", limit=100, db=SqliteDB(), builder=builder(rows=50_000_000))
    assert ok["action"] == "create"                          # with a row limit it streams


def test_plan_errors(fakes):
    with pytest.raises(HubError) as e:
        plan.plan("a/b", "Demo.X", "validation", db=SqliteDB(), builder=builder())
    assert e.value.code == "not_found" and "Available splits: train, test" in str(e.value)
    for kwargs in [{"dataset": "a b"}, {"dataset": "a/b", "limit": "x"}, {"dataset": "a/b", "limit": -1},
                   {"dataset": "a/b", "split": "a;b"}]:
        with pytest.raises(HubError) as e:
            plan.plan(db=SqliteDB(), builder=builder(), **kwargs)
        assert e.value.code == "bad_request"


def test_plan_without_viewer_uses_metadata(fakes, monkeypatch):
    monkeypatch.setattr(hub, "sample", lambda *a: None)
    meta = NS(to_dict=lambda: SERIALIZED)
    r = plan.plan("a/b", "Demo.M", "test", db=SqliteDB(), builder=builder(features=meta))
    assert r["sample"] == "metadata" and r["columns"][0]["type"] == "VARCHAR(4000)"
    r = plan.plan("a/b", "Demo.M", "test", db=SqliteDB(), builder=builder(features=None))
    assert r["sample"] == "none" and r["columns"] == [] and r["ddl"] is None


def test_plan_with_revision_skips_viewer(fakes, monkeypatch):
    monkeypatch.setattr(hub, "sample", lambda *a: pytest.fail("viewer used for a revision"))
    meta = NS(to_dict=lambda: SERIALIZED)
    r = plan.plan("a/b", "Demo.M", "test", revision="v1.0", db=SqliteDB(), builder=builder(features=meta))
    assert any("latest version" in w for w in r["warnings"])


def test_plan_media_columns(fakes, monkeypatch):
    serialized = {"image": {"_type": "Image"}, "label": {"names": ["a"], "_type": "ClassLabel"}}
    monkeypatch.setattr(hub, "sample", lambda *a: {"features": serialized, "rows": [{"image": None, "label": 0}]})
    r = plan.plan("ylecun/mnist", "Demo.Mnist", "test", db=SqliteDB(), builder=builder())
    assert r["columns"][0]["note"] == "image data, stored as JSON with base64 bytes"


def test_precheck(monkeypatch):
    db = SqliteDB()
    db.execute("CREATE TABLE Demo.Theirs (a INT)")
    assert plan.precheck("stanfordnlp/imdb", db=db) == "HF.imdb"
    for kwargs, code in [({"dataset": "a b"}, "bad_request"),
                         ({"dataset": "a/b", "mode": "zap"}, "bad_request"),
                         ({"dataset": "a/b", "table": "Demo.Theirs"}, "conflict")]:
        with pytest.raises(HubError) as e:
            plan.precheck(db=db, **kwargs)
        assert e.value.code == code


def test_call_json(fakes, monkeypatch):
    import json
    monkeypatch.setattr(loader, "IrisSQL", SqliteDB)
    monkeypatch.setattr(plan, "_builder", lambda *a: builder())
    out = json.loads(plan.call(json.dumps({"dataset": "a/b", "table": "Demo.J", "split": "test", "limit": ""})))
    assert out["action"] == "create"
    assert json.loads(plan.call(json.dumps({"dataset": "a/b", "zap": 1})))["code"] == "bad_request"
    assert json.loads(plan.call_precheck(json.dumps({"dataset": "a b"})))["code"] == "bad_request"

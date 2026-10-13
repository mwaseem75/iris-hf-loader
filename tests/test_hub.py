"""Offline tests of the discovery layer (hf2iris.hub): HTTP and HfApi are faked."""
import json
import os
import socket
import sys
import time
import urllib.parse
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from hf2iris import hub  # noqa: E402

TOKEN = "hf_SecretValue1234567890"


# ------------------------------------------------------------------ fakes ---
class RepositoryNotFoundError(Exception):
    pass


class GatedRepoError(RepositoryNotFoundError):
    pass


class HfHubHTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = NS(status_code=status)


def dataset(id="stanfordnlp/imdb", **kw):
    fields = dict(id=id, author=id.split("/")[0], downloads=173262, likes=1287, trending_score=12,
                  last_modified=None, gated=False, private=False, sha="e628166" + "0" * 33,
                  description="Large Movie Review Dataset.  Binary sentiment.",
                  tags=["task_categories:text-classification", "size_categories:10K<n<100K",
                        "language:en", "license:other"], card_data=None)
    fields.update(kw)
    return NS(**fields)


class FakeApi:
    def __init__(self, datasets=(), info=None, raise_info=None, raise_list=None):
        self.datasets, self.info, self.raise_info, self.raise_list = list(datasets), info, raise_info, raise_list
        self.calls = []

    def list_datasets(self, **kw):
        self.calls.append(("list", kw))
        if self.raise_list:
            raise self.raise_list
        return iter(self.datasets)

    def dataset_info(self, repo, revision=None, timeout=None):
        self.calls.append(("info", repo, revision))
        if self.raise_info:
            raise self.raise_info
        return self.info or dataset(repo)


class FakeHttp:
    """Answers viewer URLs from a dict {endpoint: (status, body)} and records requests."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        parsed = urllib.parse.urlparse(url)
        answer = self.answers.get(parsed.path.strip("/"), (404, {"error": "not found"}))
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return status, body, "17" if status == 429 else None


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(hub, "_store", hub.MemoryCache())
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(hub, "SHOW_PRIVATE", False)
    monkeypatch.setattr(hub, "CACHE_TTL", 600)
    monkeypatch.setattr(hub, "SEARCH_TTL", 300)


def use(monkeypatch, api=None, http=None):
    api = api or FakeApi()
    http = http or FakeHttp({})
    monkeypatch.setattr(hub, "hf_api", lambda: api)
    monkeypatch.setattr(hub, "http_get", http)
    return api, http


INFO = {"dataset_info": {
    "main": {"features": {"question": {"dtype": "string", "_type": "Value"},
                          "answer": {"dtype": "string", "_type": "Value"}},
             "splits": {"train": {"num_examples": 7473}, "test": {"num_examples": 1319}}},
    "socratic": {"features": {"question": {"dtype": "string", "_type": "Value"}},
                 "splits": {"train": {"num_examples": 7473}}}},
    "pending": [], "failed": [], "partial": False}
SIZE = {"size": {"splits": [{"config": "main", "split": "train", "num_bytes_parquet_files": 2306545},
                            {"config": "main", "split": "test", "num_bytes_parquet_files": 419088}]}}
ROWS = {"features": [{"name": "text", "type": {"dtype": "string", "_type": "Value"}},
                     {"name": "label", "type": {"names": ["neg", "pos"], "_type": "ClassLabel"}},
                     {"name": "image", "type": {"_type": "Image"}},
                     {"name": "meta", "type": {"src": {"dtype": "string", "_type": "Value"}}}],
        "rows": [{"row_idx": 100, "truncated_cells": [],
                  "row": {"text": "x" * 800, "label": 1,
                          "image": {"src": "https://datasets-server.huggingface.co/assets/a/b/image.jpg?Expires=1&Signature=abc"},
                          "meta": {"src": "https://datasets-server.huggingface.co/assets/x.jpg?Expires=1&Signature=zz"}}}],
        "num_rows_total": 25000, "partial": False}


# ----------------------------------------------------------------- search ---
def test_search_maps_fields_and_parameters(monkeypatch):
    api, _ = use(monkeypatch, FakeApi([dataset(), dataset("mteb/imdb", downloads=4296)]))
    r = hub.search("imdb", sort="likes", task="text-classification", size="10K<n<100K", language="en", limit="5")
    assert r["count"] == 2 and r["cached"] is False
    item = r["items"][0]
    assert (item["id"], item["downloads"], item["likes"], item["gated"]) == ("stanfordnlp/imdb", 173262, 1287, False)
    assert item["tasks"] == ["text-classification"] and item["sizeCategory"] == "10K<n<100K"
    assert item["license"] == ["other"] and item["languages"] == ["en"]
    assert item["description"] == "Large Movie Review Dataset. Binary sentiment."
    kw = api.calls[0][1]
    assert kw["search"] == "imdb" and kw["sort"] == "likes" and kw["full"] is True
    assert kw["task_categories"] == "text-classification" and kw["size_categories"] == "10K<n<100K"
    assert kw["language"] == "en"


def test_search_sort_names_and_limits(monkeypatch):
    api, _ = use(monkeypatch, FakeApi([dataset(f"o/d{i}") for i in range(120)]))
    assert hub.search(sort="trending", limit=500)["count"] == 50          # clamped to 50
    assert api.calls[-1][1]["sort"] == "trending_score"
    hub.search(sort="updated", limit="x")                               # bad number -> default 20
    assert api.calls[-1][1]["sort"] == "last_modified"
    with pytest.raises(hub.HubError) as e:
        hub.search(sort="random")
    assert e.value.code == "bad_request"
    with pytest.raises(hub.HubError, match="100 characters"):
        hub.search("x" * 101)
    with pytest.raises(hub.HubError, match="task"):
        hub.search(task="a;b")


def test_search_hides_private_unless_opted_in(monkeypatch):
    api, _ = use(monkeypatch, FakeApi([dataset("me/secret", private=True), dataset("pub/data")]))
    assert [i["id"] for i in hub.search()["items"]] == ["pub/data"]
    monkeypatch.setattr(hub, "SHOW_PRIVATE", True)
    assert [i["id"] for i in hub.search(refresh=True)["items"]] == ["me/secret", "pub/data"]


def test_search_long_description_is_cut(monkeypatch):
    use(monkeypatch, FakeApi([dataset(description="word " * 200)]))
    assert len(hub.search()["items"][0]["description"]) <= hub.DESCRIPTION_CHARS


# ---------------------------------------------------------------- details ---
def test_details_from_hub_info_and_viewer(monkeypatch):
    api, http = use(monkeypatch, FakeApi(info=dataset("openai/gsm8k")),
                    FakeHttp({"info": (200, INFO), "size": (200, SIZE)}))
    r = hub.details("openai/gsm8k")
    assert r["id"] == "openai/gsm8k" and r["hubUrl"] == "https://huggingface.co/datasets/openai/gsm8k"
    assert r["viewer"] == {"available": True, "partial": False} and r["resolvedFrom"] is None
    main = r["configs"][0]
    assert main["name"] == "main"
    assert main["features"] == [{"name": "question", "type": "string"}, {"name": "answer", "type": "string"}]
    assert main["splits"] == [{"name": "train", "rows": 7473, "bytes": 2306545},
                              {"name": "test", "rows": 1319, "bytes": 419088}]
    assert r["configs"][1]["splits"][0]["bytes"] is None
    assert all("dataset=openai%2Fgsm8k" in url for url, _ in http.requests)


def test_details_resolves_old_ids(monkeypatch):
    api = FakeApi(info=dataset("cornell-movie-review-data/rotten_tomatoes"))
    use(monkeypatch, api, FakeHttp({"info": (200, INFO), "size": (200, SIZE)}))
    r = hub.details("rotten_tomatoes")
    assert r["resolvedFrom"] == "rotten_tomatoes"
    assert r["hubUrl"].endswith("/cornell-movie-review-data/rotten_tomatoes")


def test_details_falls_back_when_viewer_cannot_help(monkeypatch):
    use(monkeypatch, FakeApi(), FakeHttp({"info": (500, {"error": "not ready"})}))
    monkeypatch.setattr(hub, "_configs_from_datasets",
                        lambda repo, rev: [{"name": "default", "features": [], "splits": [{"name": "train", "rows": None, "bytes": None}]}])
    r = hub.details("stanfordnlp/imdb")
    assert r["viewer"]["available"] is False and r["configs"][0]["name"] == "default"


def test_details_of_gated_dataset_without_access(monkeypatch):
    # metadata is public (gated="auto"), the viewer and the datasets library are refused
    use(monkeypatch, FakeApi(info=dataset("bigcode/the-stack", gated="auto")), FakeHttp({"info": (401, {})}))

    def refused(repo, rev):
        raise hub.HubError("not_found", "Dataset 'bigcode/the-stack' was not found on the Hub.")
    monkeypatch.setattr(hub, "_configs_from_datasets", refused)
    with pytest.raises(hub.HubError) as e:
        hub.details("bigcode/the-stack")
    assert e.value.code == "gated" and "HF_TOKEN" in str(e.value)


def test_details_with_revision_skips_viewer(monkeypatch):
    api, http = use(monkeypatch, FakeApi(), FakeHttp({"info": (200, INFO)}))
    monkeypatch.setattr(hub, "_configs_from_datasets", lambda repo, rev: [{"name": rev, "features": [], "splits": []}])
    r = hub.details("stanfordnlp/imdb", revision="v1.0")
    assert http.requests == [] and r["configs"][0]["name"] == "v1.0"
    assert ("info", "stanfordnlp/imdb", "v1.0") in api.calls


def test_feature_types():
    f = hub.feature_type
    assert f({"dtype": "int64", "_type": "Value"}) == "int64"
    assert f({"names": list("abcdefg"), "_type": "ClassLabel"}) == "ClassLabel(a, b, c, d, e, ...)"
    assert f({"feature": {"names": ["x"], "_type": "ClassLabel"}, "_type": "Sequence"}) == "list<ClassLabel(x)>"
    assert f({"_type": "Image"}) == "Image"
    assert f({"a": {"dtype": "string", "_type": "Value"}}) == "struct{a: string}"
    assert f([{"dtype": "float32", "_type": "Value"}]) == "list<float32>"


# ---------------------------------------------------------------- preview ---
def test_preview_rows_paging_truncation_and_media(monkeypatch):
    _, http = use(monkeypatch, http=FakeHttp({"rows": (200, ROWS)}))
    r = hub.preview("stanfordnlp/imdb", "plain_text", "test", offset=100, rows=5)
    assert r["columns"] == ["text", "label", "image", "meta"] and r["total"] == 25000
    text, label, image, meta = r["rows"][0]
    assert len(text) == hub.CELL_CHARS and r["truncated"] is True
    assert label == 1 and image == "[image]" and meta == "[media]"
    assert "Signature" not in json.dumps(r) and "/assets/" not in json.dumps(r)
    assert r["features"][1] == {"name": "label", "type": "ClassLabel(neg, pos)"}
    query = urllib.parse.parse_qs(urllib.parse.urlparse(http.requests[0][0]).query)
    assert query == {"dataset": ["stanfordnlp/imdb"], "config": ["plain_text"], "split": ["test"],
                     "offset": ["100"], "length": ["5"]}


def test_preview_parameter_checks(monkeypatch):
    use(monkeypatch)
    for kwargs, text in [({"config": "", "split": "t"}, "config is required"),
                         ({"config": "a b", "split": "t"}, "config"),
                         ({"config": "c", "split": "t", "source": "s3"}, "source"),
                         ({"config": "c", "split": "t", "source": "stream", "offset": 5}, "offset")]:
        with pytest.raises(hub.HubError, match=text) as e:
            hub.preview("a/b", **kwargs)
        assert e.value.code == "bad_request"


def test_preview_never_streams_without_asking(monkeypatch):
    use(monkeypatch, http=FakeHttp({"rows": (500, {"error": "The split is not ready"})}))
    monkeypatch.setattr(hub, "_stream_preview", lambda *a: pytest.fail("streaming started"))
    with pytest.raises(hub.HubError) as e:
        hub.preview("a/b", "c", "train")
    assert e.value.code == "preview_unavailable" and "source=stream" in str(e.value)


def test_preview_stream_when_asked(monkeypatch):
    use(monkeypatch)
    seen = {}
    monkeypatch.setattr(hub, "_stream_preview", lambda repo, c, s, n: seen.setdefault("args", (repo, c, s, n)) and {"rows": []})
    hub.preview("a/b", "c", "train", rows=100, source="stream")
    assert seen["args"] == ("a/b", "c", "train", 20)                  # stream is capped at 20 rows


# ----------------------------------------------------------------- errors ---
def test_viewer_401_is_split_into_missing_and_gated(monkeypatch):
    http = FakeHttp({"rows": (401, {"error": "does not exist, or is not accessible"})})
    use(monkeypatch, FakeApi(raise_info=RepositoryNotFoundError("nope")), http)
    with pytest.raises(hub.HubError) as e:
        hub.preview("no/such", "c", "s")
    assert e.value.code == "not_found"
    use(monkeypatch, FakeApi(raise_info=GatedRepoError("gated")), http)
    with pytest.raises(hub.HubError) as e:
        hub.preview("bigcode/the-stack", "c", "s")
    assert e.value.code == "gated" and "HF_TOKEN" in str(e.value)
    use(monkeypatch, FakeApi(info=dataset("o/p", gated="manual")), http)
    with pytest.raises(hub.HubError) as e:
        hub.preview("o/p", "c", "s")
    assert e.value.code == "gated"


def test_unknown_config_or_split(monkeypatch):
    use(monkeypatch, FakeApi(), FakeHttp({"rows": (404, {"error": "not found"})}))
    with pytest.raises(hub.HubError) as e:
        hub.preview("a/b", "nope", "train")
    assert e.value.code == "not_found" and "'nope'" in str(e.value)


def test_unknown_split_when_viewer_answers_500(monkeypatch):
    splits = {"splits": [{"config": "plain_text", "split": "train"}, {"config": "plain_text", "split": "test"}]}
    use(monkeypatch, http=FakeHttp({"rows": (500, {"error": "Unexpected error."}), "splits": (200, splits)}))
    with pytest.raises(hub.HubError) as e:
        hub.preview("stanfordnlp/imdb", "plain_text", "nope")
    assert e.value.code == "not_found" and "Available splits: train, test" in str(e.value)
    with pytest.raises(hub.HubError) as e:
        hub.preview("stanfordnlp/imdb", "other", "train")
    assert e.value.code == "not_found" and "Available configs: plain_text" in str(e.value)
    with pytest.raises(hub.HubError) as e:
        hub.preview("stanfordnlp/imdb", "plain_text", "train")    # split exists: the viewer is the problem
    assert e.value.code == "preview_unavailable" and "error.." not in str(e.value)


def test_rate_limit_timeout_and_upstream(monkeypatch):
    use(monkeypatch, http=FakeHttp({"rows": (429, {})}))
    with pytest.raises(hub.HubError) as e:
        hub.preview("a/b", "c", "s")
    assert e.value.code == "rate_limited" and "17 seconds" in str(e.value)
    use(monkeypatch, http=FakeHttp({"rows": hub.HubError("timeout", "slow")}))
    with pytest.raises(hub.HubError) as e:
        hub.preview("a/b", "c", "s", refresh=True)
    assert e.value.code == "timeout"
    use(monkeypatch, FakeApi(raise_list=HfHubHTTPError(429)))
    with pytest.raises(hub.HubError) as e:
        hub.search()
    assert e.value.code == "rate_limited"
    use(monkeypatch, FakeApi(raise_list=HfHubHTTPError(503)))
    with pytest.raises(hub.HubError) as e:
        hub.search(refresh=True)
    assert e.value.code == "upstream"


def test_http_get_maps_network_failures(monkeypatch):
    class Opener:
        def __init__(self, exc):
            self.exc = exc

        def open(self, req, timeout):
            raise self.exc
    import urllib.error
    monkeypatch.setattr(hub, "_opener", Opener(socket.timeout("slow")))
    with pytest.raises(hub.HubError) as e:
        hub.http_get(hub.VIEWER + "/rows?x=1", {})
    assert e.value.code == "timeout"
    monkeypatch.setattr(hub, "_opener", Opener(urllib.error.URLError("dns failure")))
    with pytest.raises(hub.HubError) as e:
        hub.http_get(hub.VIEWER + "/rows?x=1", {})
    assert e.value.code == "upstream"


def test_only_the_viewer_host_is_contacted():
    with pytest.raises(hub.HubError, match="other than"):
        hub.http_get("https://evil.example.com/rows", {})
    handler = hub._SameHosts()
    with pytest.raises(hub.HubError, match="redirected"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example.com/x")


def test_ids_are_validated(monkeypatch):
    use(monkeypatch)
    for bad in ["", "a b", "../etc", "a/b/c", "a/../b", "-a/b", "a/b?x=1", "a" * 200]:
        with pytest.raises(hub.HubError) as e:
            hub.details(bad)
        assert e.value.code == "bad_request", bad


# ------------------------------------------------------------------ token ---
def test_token_is_sent_but_never_returned(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    _, http = use(monkeypatch, http=FakeHttp({"rows": (200, ROWS)}))
    r = hub.preview("a/b", "c", "s")
    assert http.requests[0][1]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in json.dumps(r)
    assert all(TOKEN not in k and TOKEN not in str(v) for k, v in hub._store.items.items())
    assert hub.status()["hfToken"] is True and TOKEN not in json.dumps(hub.status())


def test_token_is_masked_in_errors(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    use(monkeypatch, FakeApi(raise_list=RuntimeError(f"bad header Bearer {TOKEN}")))
    out = json.loads(hub.call("search", "{}"))
    assert out["code"] == "upstream" and TOKEN not in out["error"] and "***" in out["error"]


def test_status_without_token(monkeypatch):
    s = hub.status()
    assert s["hfToken"] is False and set(s) >= {"version", "maxRows", "maxDownloadMb", "cacheTtl"}


# ------------------------------------------------------------------ cache ---
def test_cache_hit_refresh_and_expiry(monkeypatch):
    api, _ = use(monkeypatch, FakeApi([dataset()]))
    assert hub.search("imdb")["cached"] is False
    assert hub.search("imdb")["cached"] is True and len(api.calls) == 1
    assert hub.search("imdb", refresh="1")["cached"] is False and len(api.calls) == 2
    for key, value in list(hub._store.items.items()):
        hub._store.items[key] = "1\t" + value.partition("\t")[2]          # expired long ago
    assert hub.search("imdb")["cached"] is False and len(api.calls) == 3


def test_cache_is_separate_per_token(monkeypatch):
    api, _ = use(monkeypatch, FakeApi([dataset()]))
    hub.search("imdb")
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    assert hub.search("imdb")["cached"] is False                         # not the anonymous result
    monkeypatch.setenv("HF_TOKEN", TOKEN + "x")
    assert hub.search("imdb")["cached"] is False                         # nor another token's
    assert len(api.calls) == 3


def test_errors_are_not_cached(monkeypatch):
    api = FakeApi(raise_list=HfHubHTTPError(503))
    use(monkeypatch, api)
    for _ in range(2):
        with pytest.raises(hub.HubError):
            hub.search()
    assert len(api.calls) == 2 and hub._store.items == {}


def test_cache_limits(monkeypatch):
    use(monkeypatch, FakeApi([dataset()]))
    monkeypatch.setattr(hub, "CACHE_MAX_ENTRIES", 3)
    for i in range(6):
        hub.search(f"q{i}")
    assert len(hub._store.items) <= 3
    monkeypatch.setattr(hub, "CACHE_MAX_BYTES", 10)
    hub._store.items.clear()
    hub.search("big")
    assert hub._store.items == {}                                         # too big to keep
    monkeypatch.setattr(hub, "SEARCH_TTL", 0)
    hub.search("off")
    assert hub._store.items == {}                                         # cache off


def test_response_with_token_is_not_cached(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    use(monkeypatch, FakeApi([dataset(description=f"leaked {TOKEN}")]))
    hub.search()
    assert hub._store.items == {}


# ------------------------------------------------------------ call / info ---
def test_call_returns_json_with_codes(monkeypatch):
    use(monkeypatch, FakeApi([dataset()]))
    assert json.loads(hub.call("search", json.dumps({"search": "imdb", "limit": ""})))["count"] == 1
    assert json.loads(hub.call("details", json.dumps({"id": "a b"})))["code"] == "bad_request"
    assert json.loads(hub.call("details", json.dumps({"id": "a/b", "bogus": 1})))["code"] == "bad_request"
    assert json.loads(hub.call("nope", "{}"))["code"] == "bad_request"


def test_splits_and_info_keep_their_shape(monkeypatch):
    splits = {"splits": [{"config": "main", "split": "train"}, {"config": "main", "split": "test"},
                         {"config": "socratic", "split": "train"}]}
    use(monkeypatch, http=FakeHttp({"splits": (200, splits)}))
    assert hub.splits("openai/gsm8k") == {"main": ["train", "test"], "socratic": ["train"]}
    from hf2iris import loader
    assert loader.info("openai/gsm8k") == {"dataset": "openai/gsm8k",
                                           "configs": {"main": ["train", "test"], "socratic": ["train"]}}


def test_info_falls_back_to_datasets(monkeypatch):
    use(monkeypatch, http=FakeHttp({"splits": (401, {})}))
    assert hub.splits("a/b") is None
    fake = NS(disable_progress_bars=lambda: None,
              get_dataset_config_names=lambda n: ["default"],
              get_dataset_split_names=lambda n, c: ["train"])
    monkeypatch.setitem(sys.modules, "datasets", fake)
    from hf2iris import loader
    assert loader.info("a/b") == {"dataset": "a/b", "configs": {"default": ["train"]}}

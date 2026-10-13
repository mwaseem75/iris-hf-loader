"""
Read-only discovery of Hugging Face datasets: search, details and row previews.

Hub metadata comes from huggingface_hub; splits, sizes, features and rows come from
the dataset viewer (datasets-server.huggingface.co). Results are cached for a few
minutes in ^CacheTemp.hf2iris (IRISTEMP), or in memory outside IRIS.
"""
import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .loader import LoadError, error_text, MAX_ROWS, MAX_DOWNLOAD_MB

VIEWER = "https://datasets-server.huggingface.co"
HUB_URL = "https://huggingface.co/datasets/"
ALLOWED_HOSTS = {"datasets-server.huggingface.co"}
TIMEOUT = 10
CACHE_TTL = int(os.environ.get("HF2IRIS_CACHE_TTL", 600))
SEARCH_TTL = min(300, CACHE_TTL)
CACHE_MAX_ENTRIES = 500
CACHE_MAX_BYTES = 1_000_000
SHOW_PRIVATE = os.environ.get("HF2IRIS_SHOW_PRIVATE", "0") == "1"
CELL_CHARS = 500
DESCRIPTION_CHARS = 300
MAX_CONFIGS = 50
SORTS = {"downloads": "downloads", "likes": "likes", "trending": "trending_score",
         "updated": "last_modified"}
MEDIA = ("Image", "Audio", "Video", "Pdf")

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}(/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?")
NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")          # config, split, revision
FILTER = re.compile(r"[A-Za-z0-9 ._<>=+-]{1,60}")     # task, size category, language


class HubError(LoadError):
    """code: bad_request, not_found, gated, preview_unavailable, rate_limited, timeout, upstream"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------- input ---
def _check_id(value):
    value = (value or "").strip()
    if not value:
        raise HubError("bad_request", "id is required, for example stanfordnlp/imdb")
    if not ID.fullmatch(value):
        raise HubError("bad_request", f"{value!r} is not a Hugging Face dataset id (owner/name)")
    return value


def _check_name(value, what, required=True):
    value = (value or "").strip()
    if not value:
        if required:
            raise HubError("bad_request", f"{what} is required")
        return None
    if not NAME.fullmatch(value):
        raise HubError("bad_request", f"{what} {value!r} may only contain letters, digits, . _ -")
    return value


def _check_filter(value, what):
    value = (value or "").strip()
    if value and not FILTER.fullmatch(value):
        raise HubError("bad_request", f"{what} {value!r} is not valid")
    return value or None


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _flag(value):
    return str(value).lower() in ("1", "true", "yes")


# ------------------------------------------------------------------- token ---
def _token():
    return os.environ.get("HF_TOKEN", "").strip() or None


def _fingerprint():
    token = _token()
    return hashlib.sha256(token.encode()).hexdigest()[:8] if token else "anon"


# ------------------------------------------------------------------- cache ---
class MemoryCache:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def set(self, key, value):
        self.items[key] = value

    def kill(self, key):
        self.items.pop(key, None)

    def keys(self):
        return list(self.items)


class GlobalCache:
    """^CacheTemp.hf2iris(key) = "expires<TAB>json"; IRISTEMP, shared, not journaled."""

    def __init__(self, name="^CacheTemp.hf2iris"):
        import iris  # noqa: available only inside IRIS
        self.g = iris.gref(name)

    def get(self, key):
        return self.g.get([key], None)

    def set(self, key, value):
        self.g.set([key], value)

    def kill(self, key):
        self.g.kill([key])

    def keys(self):
        out, key = [], ""
        while True:
            key = self.g.order([key])
            if not key:
                return out
            out.append(key)


_store = None


def cache():
    global _store
    if _store is None:
        try:
            _store = GlobalCache()
        except Exception:
            _store = MemoryCache()
    return _store


def _read(store, key, now):
    raw = store.get(key)
    if raw is None:
        return None
    expires, _, payload = str(raw).partition("\t")
    if float(expires or 0) <= now:
        store.kill(key)
        return None
    return payload


def _prune(store, now):
    entries = []
    for key in store.keys():
        raw = store.get(key)
        expires = float(str(raw).partition("\t")[0] or 0) if raw is not None else 0
        if expires <= now:
            store.kill(key)
        else:
            entries.append((expires, key))
    for _, key in sorted(entries)[:max(0, len(entries) - CACHE_MAX_ENTRIES + 1)]:
        store.kill(key)


def cached(kind, params, ttl, fetch, refresh=False):
    """fetch() unless a fresh entry exists; errors are raised, never cached."""
    if ttl <= 0:
        return dict(fetch(), cached=False)
    store, now = cache(), time.time()
    key = hashlib.sha1(json.dumps([kind, params, _fingerprint()], sort_keys=True).encode()).hexdigest()
    if not refresh:
        payload = _read(store, key, now)
        if payload is not None:
            return dict(json.loads(payload), cached=True)
    data = fetch()
    payload = json.dumps(data)
    token = _token()
    if len(payload) <= CACHE_MAX_BYTES and not (token and token in payload):
        _prune(store, now)
        store.set(key, f"{now + ttl:.0f}\t{payload}")
    return dict(data, cached=False)


# -------------------------------------------------------------------- HTTP ---
class _SameHosts(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).hostname not in ALLOWED_HOSTS:
            raise HubError("upstream", "The dataset viewer redirected to another host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameHosts)


def http_get(url, headers):
    """(status, parsed JSON body, Retry-After); network failures raise HubError."""
    if urllib.parse.urlparse(url).hostname not in ALLOWED_HOSTS:
        raise HubError("upstream", "Refusing to contact a host other than the dataset viewer")
    try:
        with _opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read() or b"{}"), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        return e.code, body, e.headers.get("Retry-After")
    except (socket.timeout, TimeoutError):
        raise _timeout()
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise _timeout()
        raise HubError("upstream", f"Could not reach the Hugging Face dataset viewer: {e.reason}")


def _timeout():
    return HubError("timeout", f"Hugging Face did not answer within {TIMEOUT} seconds. Try again.")


def _rate_limited(retry_after=None):
    wait = f" Try again in {retry_after} seconds." if retry_after else " Try again in a minute."
    hint = "" if _token() else " Setting HF_TOKEN gives a higher limit."
    return HubError("rate_limited", "Hugging Face rate limit reached." + wait + hint)


class _ViewerDenied(Exception):
    """401/403/404 from the viewer: missing, gated or private, or unknown config/split."""

    def __init__(self, status):
        self.status = status


class _ViewerFailed(Exception):
    pass


def viewer(endpoint, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Accept": "application/json", "User-Agent": "iris-hf-loader"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, body, retry_after = http_get(f"{VIEWER}/{endpoint}?{query}", headers)
    if status == 200:
        return body
    if status == 429:
        raise _rate_limited(retry_after)
    if status in (401, 403, 404):
        raise _ViewerDenied(status)
    raise _ViewerFailed(body.get("error") or f"HTTP {status}")


# --------------------------------------------------------------------- Hub ---
def hf_api():
    from huggingface_hub import HfApi
    return HfApi()


def hub_call(fn, what="the dataset"):
    try:
        return fn()
    except HubError:
        raise
    except Exception as e:
        raise _hub_error(e, what)


def _hub_error(e, what):
    kind = type(e).__name__
    status = getattr(getattr(e, "response", None), "status_code", None)
    if kind == "GatedRepoError":          # a subclass of RepositoryNotFoundError
        return _gated(what)
    if kind == "RevisionNotFoundError":
        return HubError("not_found", f"Revision not found for {what}.")
    if kind in ("RepositoryNotFoundError", "DatasetNotFoundError") or status == 404:
        return HubError("not_found", f"Dataset {what!r} was not found on the Hub. "
                                     f"Check the id on huggingface.co/datasets.")
    if status == 429:
        return _rate_limited()
    if status in (401, 403):
        return _gated(what)
    if "timeout" in kind.lower() or isinstance(e, (socket.timeout, TimeoutError)):
        return _timeout()
    return HubError("upstream", f"Hugging Face error: {error_text(e)}")


def _gated(what):
    if _token():
        return HubError("gated", f"{what} is gated or private and HF_TOKEN has no access to it. "
                                 f"Accept its terms on huggingface.co with the token's account.")
    return HubError("gated", f"{what} is gated or private. Accept its terms on huggingface.co, "
                             f"then start the container with HF_TOKEN set.")


def resolve(dataset):
    """Old ids without an owner ('rotten_tomatoes') -> 'owner/name'."""
    if "/" in dataset:
        return dataset
    return hub_call(lambda: hf_api().dataset_info(dataset, timeout=TIMEOUT).id, dataset)


def _classify(repo):
    """After a viewer 401/403/404: raise not_found or gated, or return if the Hub sees it."""
    info = hub_call(lambda: hf_api().dataset_info(repo, timeout=TIMEOUT), repo)
    if getattr(info, "private", False) or getattr(info, "gated", False):
        raise _gated(repo)


# ---------------------------------------------------------------- mapping ---
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _tags(tags, prefix):
    return [t.split(":", 1)[1] for t in tags or [] if t.startswith(prefix + ":")]


def _as_list(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def summary(d):
    """Dataset fields shared by search results and details."""
    tags = getattr(d, "tags", None) or []
    card = getattr(d, "card_data", None) or {}
    get = card.get if hasattr(card, "get") else (lambda k, default=None: default)
    description = " ".join(str(getattr(d, "description", None) or "").split())
    if len(description) > DESCRIPTION_CHARS:
        description = description[:DESCRIPTION_CHARS - 3].rstrip() + "..."
    sizes = _tags(tags, "size_categories") or _as_list(get("size_categories"))
    return {
        "id": d.id,
        "author": getattr(d, "author", None) or d.id.split("/")[0],
        "downloads": getattr(d, "downloads", None) or 0,
        "likes": getattr(d, "likes", None) or 0,
        "trending": getattr(d, "trending_score", None) or 0,
        "lastModified": _iso(getattr(d, "last_modified", None)),
        "gated": getattr(d, "gated", None) or False,
        "private": bool(getattr(d, "private", False)),
        "license": _tags(tags, "license") or _as_list(get("license")),
        "tasks": _tags(tags, "task_categories") or _as_list(get("task_categories")),
        "sizeCategory": sizes[0] if sizes else None,
        "languages": _tags(tags, "language") or _as_list(get("language")),
        "description": description,
    }


def feature_type(f):
    """Short readable type of a serialized datasets feature."""
    if isinstance(f, list):
        return f"list<{feature_type(f[0])}>" if f else "list"
    if not isinstance(f, dict):
        return str(f)
    kind = f.get("_type")
    if kind == "Value":
        return f.get("dtype", "?")
    if kind == "ClassLabel":
        names = [str(n) for n in f.get("names") or []]
        return "ClassLabel(" + ", ".join(names[:5]) + (", ..." if len(names) > 5 else "") + ")"
    if kind in ("Sequence", "List", "LargeList"):
        return f"list<{feature_type(f.get('feature'))}>"
    if kind is None:
        text = "struct{" + ", ".join(f"{k}: {feature_type(v)}" for k, v in f.items()) + "}"
        return text if len(text) <= 200 else text[:196] + "...}"
    return kind


def _media(f):
    """'image', 'audio', ... if the feature is or contains a media type."""
    if isinstance(f, list):
        return _media(f[0]) if f else None
    if not isinstance(f, dict):
        return None
    if f.get("_type") in MEDIA:
        return f["_type"].lower()
    if f.get("_type") in ("Sequence", "List", "LargeList"):
        return _media(f.get("feature"))
    if f.get("_type") is None:
        for v in f.values():
            found = _media(v)
            if found:
                return found
    return None


def _cell(value, feature):
    """(value for the browser, truncated?). Media never leaves the server."""
    media = _media(feature)
    if media:
        return f"[{media}]", False
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, str):
        if "/assets/" in value and "Signature=" in value:
            return "[media]", False
        if len(value) > CELL_CHARS:
            return value[:CELL_CHARS], True
    elif value is not None and not isinstance(value, (int, float, bool)):
        return str(value)[:CELL_CHARS], False
    return value, False


# ------------------------------------------------------------- endpoints ---
def search(search="", sort="downloads", task=None, size=None, language=None, limit=20,
           refresh=False):
    query = (search or "").strip()
    if len(query) > 100:
        raise HubError("bad_request", "search is limited to 100 characters")
    sort = (sort or "downloads").strip()
    if sort not in SORTS:
        raise HubError("bad_request", f"sort must be one of {', '.join(SORTS)}")
    task, size, language = (_check_filter(task, "task"), _check_filter(size, "size"),
                            _check_filter(language, "language"))
    limit = _clamp(limit, 1, 50, 20)
    params = {"search": query, "sort": sort, "task": task, "size": size, "language": language,
              "limit": limit, "private": SHOW_PRIVATE}

    def fetch():
        kwargs = {"search": query or None, "sort": SORTS[sort], "full": True,
                  "limit": limit if SHOW_PRIVATE else limit * 2}
        if task:
            kwargs["task_categories"] = task
        if size:
            kwargs["size_categories"] = size
        if language:
            kwargs["language"] = language
        found = hub_call(lambda: list(hf_api().list_datasets(**kwargs)), "the search")
        items = [summary(d) for d in found if SHOW_PRIVATE or not getattr(d, "private", False)]
        items = items[:limit]
        return {"items": items, "count": len(items), "fetchedAt": _now()}

    return cached("search", params, SEARCH_TTL, fetch, _flag(refresh))


def splits(dataset, refresh=False):
    """{config: [split, ...]} from one viewer call, or None if the viewer cannot tell."""
    repo = _check_id(dataset)

    def fetch():
        try:
            body = viewer("splits", dataset=repo)
        except (_ViewerDenied, _ViewerFailed):
            return {"configs": None}
        out = {}
        for s in body.get("splits", []):
            out.setdefault(s["config"], []).append(s["split"])
        return {"configs": out or None}

    return cached("splits", {"id": repo}, CACHE_TTL, fetch, _flag(refresh))["configs"]


def details(id, revision=None, refresh=False):
    dataset = _check_id(id)
    revision = _check_name(revision, "revision", required=False)

    def fetch():
        repo = resolve(dataset)
        info = hub_call(lambda: hf_api().dataset_info(repo, revision=revision, timeout=TIMEOUT), repo)
        out = summary(info)
        out.update({"sha": getattr(info, "sha", None), "hubUrl": HUB_URL + repo,
                    "resolvedFrom": dataset if repo != dataset else None})
        try:
            out["configs"], out["viewer"] = _configs(repo, revision)
        except HubError as e:
            # the metadata of a gated dataset is public, its files are not
            if e.code == "not_found" and (out["gated"] or out["private"]):
                raise _gated(repo)
            raise
        out["fetchedAt"] = _now()
        return out

    return cached("details", {"id": dataset, "revision": revision}, CACHE_TTL, fetch,
                  _flag(refresh))


def _configs(repo, revision):
    """(configs, viewer) from the viewer's /info and /size; the viewer only knows the
    latest revision, so for a given revision the datasets library is asked instead."""
    if not revision:
        try:
            info = viewer("info", dataset=repo)
            try:
                size = viewer("size", dataset=repo).get("size", {})
            except (_ViewerDenied, _ViewerFailed):
                size = {}
            nbytes = {(s["config"], s["split"]): s.get("num_bytes_parquet_files")
                      for s in size.get("splits", [])}
            configs = []
            for name, ci in list(info.get("dataset_info", {}).items())[:MAX_CONFIGS]:
                features = [{"name": k, "type": feature_type(v)}
                            for k, v in (ci.get("features") or {}).items()]
                configs.append({"name": name, "features": features, "splits": [
                    {"name": s, "rows": si.get("num_examples"), "bytes": nbytes.get((name, s))}
                    for s, si in (ci.get("splits") or {}).items()]})
            if configs:
                partial = bool(info.get("partial") or info.get("pending") or info.get("failed"))
                return configs, {"available": True, "partial": partial}
        except (_ViewerDenied, _ViewerFailed):
            pass
    return _configs_from_datasets(repo, revision), {"available": False, "partial": False}


def _configs_from_datasets(repo, revision):
    from datasets import get_dataset_config_names, get_dataset_split_names
    names = hub_call(lambda: get_dataset_config_names(repo, revision=revision), repo)
    configs = []
    for name in names[:20]:
        try:
            split_names = get_dataset_split_names(repo, name, revision=revision)
        except Exception:
            split_names = []
        configs.append({"name": name, "features": [],
                        "splits": [{"name": s, "rows": None, "bytes": None} for s in split_names]})
    return configs


def preview(id, config=None, split=None, offset=0, rows=20, source="viewer", refresh=False):
    dataset = _check_id(id)
    config = _check_name(config, "config")
    split = _check_name(split, "split")
    source = (source or "viewer").strip()
    if source not in ("viewer", "stream"):
        raise HubError("bad_request", "source must be viewer or stream")
    offset = _clamp(offset, 0, 10 ** 9, 0)
    rows = _clamp(rows, 1, 100, 20)
    if source == "stream":
        if offset:
            raise HubError("bad_request", "source=stream reads from the start; offset must be 0")
        rows = min(rows, 20)
    params = {"id": dataset, "config": config, "split": split, "offset": offset, "rows": rows,
              "source": source}

    def fetch():
        repo = resolve(dataset)
        if source == "stream":
            return _stream_preview(repo, config, split, rows)
        try:
            body = viewer("rows", dataset=repo, config=config, split=split, offset=offset, length=rows)
        except _ViewerDenied as e:
            _classify(repo)
            _check_split_exists(repo, config, split)
            if e.status == 404:
                raise HubError("not_found", f"Config {config!r} or split {split!r} not found in {repo}.")
            raise _unavailable(repo, config, split, "access denied by the viewer")
        except _ViewerFailed as e:
            _check_split_exists(repo, config, split)   # the viewer answers 500 for unknown splits
            raise _unavailable(repo, config, split, str(e))
        raw = [(f["name"], f.get("type")) for f in body.get("features", [])]
        out, truncated = _rows(raw, [r.get("row", {}) for r in body.get("rows", [])])
        truncated = truncated or any(r.get("truncated_cells") for r in body.get("rows", []))
        return {"id": repo, "config": config, "split": split, "offset": offset,
                "columns": [n for n, _ in raw],
                "features": [{"name": n, "type": feature_type(t)} for n, t in raw],
                "rows": out, "total": body.get("num_rows_total"), "partial": bool(body.get("partial")),
                "truncated": truncated, "source": "viewer", "fetchedAt": _now()}

    return cached("preview", params, CACHE_TTL, fetch, _flag(refresh))


def sample(repo, config, split, rows=100):
    """{"features": {name: serialized feature}, "rows": [records]} from the viewer, with
    media values removed, or None if the viewer cannot serve the split."""
    def fetch():
        try:
            body = viewer("rows", dataset=repo, config=config, split=split, offset=0, length=rows)
        except (_ViewerDenied, _ViewerFailed):
            return {"features": None, "rows": []}
        features = {f["name"]: f.get("type") for f in body.get("features", [])}
        media = {name for name, f in features.items() if _media(f)}
        records = [{k: (None if k in media else v) for k, v in r.get("row", {}).items()}
                   for r in body.get("rows", [])]
        return {"features": features, "rows": records}

    out = cached("sample", {"id": repo, "config": config, "split": split, "rows": rows},
                 CACHE_TTL, fetch)
    return out if out["features"] else None


def _rows(raw, records):
    out, truncated = [], False
    for record in records:
        row = []
        for name, ftype in raw:
            value, cut = _cell(record.get(name), ftype)
            truncated = truncated or cut
            row.append(value)
        out.append(row)
    return out, truncated


def _check_split_exists(repo, config, split):
    known = splits(repo)
    if not known:
        return
    if config not in known:
        raise HubError("not_found", f"Config {config!r} not found in {repo}. "
                                    f"Available configs: {', '.join(known)}")
    if split not in known[config]:
        raise HubError("not_found", f"Split {split!r} not found in {repo} (config {config!r}). "
                                    f"Available splits: {', '.join(known[config])}")


def _unavailable(repo, config, split, reason):
    return HubError("preview_unavailable",
                    f"The dataset viewer cannot show {repo} ({config}/{split}): {reason.rstrip('.')}. "
                    f"Add source=stream to read the first rows directly; that can take a minute.")


def _stream_preview(repo, config, split, rows):
    import datasets
    datasets.disable_progress_bars()

    def read():
        stream = datasets.load_dataset(repo, config, split=split, streaming=True)
        try:
            stream = stream.decode(False)          # keep media undecoded; it is masked anyway
        except Exception:
            pass
        features = stream.features.to_dict() if stream.features is not None else {}
        return features, list(stream.take(rows))

    features, records = hub_call(read, repo)
    raw = list(features.items()) or [(k, None) for k in (records[0] if records else {})]
    out, truncated = _rows(raw, records)
    return {"id": repo, "config": config, "split": split, "offset": 0,
            "columns": [n for n, _ in raw],
            "features": [{"name": n, "type": feature_type(t)} for n, t in raw],
            "rows": out, "total": None, "partial": False, "truncated": truncated,
            "source": "stream", "fetchedAt": _now()}


def status():
    from . import __version__
    return {"version": __version__, "hfToken": bool(_token()), "maxRows": MAX_ROWS,
            "maxDownloadMb": MAX_DOWNLOAD_MB,
            "exactCollation": os.environ.get("HF2IRIS_EXACT", "1") != "0",
            "cacheTtl": CACHE_TTL, "showPrivate": SHOW_PRIVATE}


ENDPOINTS = {"search": search, "details": details, "preview": preview, "status": status}


def call(name, args="{}"):
    """JSON in, JSON out, for the ObjectScript wrappers. Errors come back as
    {"error": ..., "code": ...} with HF_TOKEN masked."""
    import inspect
    try:
        fn = ENDPOINTS.get(name)
        if fn is None:
            raise HubError("bad_request", f"unknown endpoint {name!r}")
        kwargs = {k: v for k, v in json.loads(args or "{}").items() if v not in ("", None)}
        unknown = set(kwargs) - set(inspect.signature(fn).parameters)
        if unknown:
            raise HubError("bad_request", f"unknown parameters: {', '.join(sorted(unknown))}")
        return json.dumps(fn(**kwargs))
    except Exception as e:
        err = e if isinstance(e, HubError) else _hub_error(e, "the dataset")
        return json.dumps({"error": error_text(err), "code": err.code})

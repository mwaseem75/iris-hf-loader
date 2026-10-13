# REST API

Base path: `/api/hf2iris`. Every endpoint needs an IRIS login (basic authentication) and answers JSON.

```bash
curl -u _SYSTEM:SYS "http://localhost:52773/api/hf2iris/datasets?search=imdb"
```

Hugging Face is only ever called from the IRIS server. `HF_TOKEN`, if you set one, stays in the container and never shows up in a response.

## Errors

The endpoints added in version 1.1 answer errors like this:

```json
{"error": "Split 'validation' not found in stanfordnlp/imdb (config 'plain_text'). Available splits: train, test, unsupervised", "code": "not_found"}
```

| code | HTTP | Meaning |
|---|---|---|
| `bad_request` | 400 | a parameter is missing or not valid |
| `gated` | 403 | the dataset is gated or private and the token is missing or has no access |
| `not_found` | 404 | dataset, config, split, revision, job or table does not exist |
| `conflict` | 409 | the target table belongs to someone else, mode `fail` and the table exists, or the job has already finished |
| `preview_unavailable` | 409 | the dataset viewer cannot show these rows (see `source=stream` below) |
| `rate_limited` | 429 | Hugging Face is limiting requests; wait, or set `HF_TOKEN` |
| `busy` | 429 | the maximum number of background imports is already running |
| `upstream` | 502 | Hugging Face answered with an error |
| `timeout` | 504 | Hugging Face did not answer within 10 seconds |

The four original endpoints (`/info`, `/load`, `/history`, `/preview/:table`) answer `{"error": "..."}` without a `code`, as before.

## Discovery

### `GET /datasets`

Search the Hub.

| Parameter | Default | |
|---|---|---|
| `search` | empty | up to 100 characters; empty lists popular datasets |
| `sort` | `downloads` | `downloads`, `likes`, `trending` or `updated` |
| `task`, `size`, `language` | | filters, e.g. `text-classification`, `10K<n<100K`, `en` |
| `limit` | 20 | 1 to 50 |
| `refresh` | | `1` skips the cache |

```json
{"items": [{"id": "stanfordnlp/imdb", "author": "stanfordnlp", "downloads": 173262, "likes": 1287,
            "trending": 12, "lastModified": "2024-01-04T12:05:16+00:00", "gated": false, "private": false,
            "license": ["other"], "tasks": ["text-classification"], "sizeCategory": "10K<n<100K",
            "languages": ["en"], "description": "Large Movie Review Dataset. ..."}],
 "count": 1, "fetchedAt": "2026-10-13T10:00:00Z", "cached": false}
```

Private datasets are left out unless the container runs with `HF2IRIS_SHOW_PRIVATE=1`.

### `GET /dataset?id=owner/name`

Details, configs and splits. Old ids without an owner are resolved (`resolvedFrom` tells you). Optional `revision` (branch, tag or commit) and `refresh`.

```json
{"id": "openai/gsm8k", "sha": "740312a...", "hubUrl": "https://huggingface.co/datasets/openai/gsm8k",
 "gated": false, "private": false, "license": ["mit"], "tasks": ["text2text-generation"], "...": "...",
 "viewer": {"available": true, "partial": false},
 "configs": [{"name": "main",
              "features": [{"name": "question", "type": "string"}, {"name": "answer", "type": "string"}],
              "splits": [{"name": "train", "rows": 7473, "bytes": 2306545},
                         {"name": "test", "rows": 1319, "bytes": 419088}]}],
 "resolvedFrom": null, "cached": false}
```

### `GET /dataset/preview`

Rows of a split, without importing anything.

| Parameter | Default | |
|---|---|---|
| `id`, `config`, `split` | | required |
| `offset` | 0 | |
| `rows` | 20 | 1 to 100 |
| `source` | `viewer` | `stream` reads the first rows from the dataset files instead (at most 20, `offset` must be 0, can take a minute) |

```json
{"id": "stanfordnlp/imdb", "config": "plain_text", "split": "test", "offset": 100,
 "columns": ["text", "label"],
 "features": [{"name": "text", "type": "string"}, {"name": "label", "type": "ClassLabel(neg, pos)"}],
 "rows": [["I love sci-fi and am willing to put up with a lot...", 0]],
 "total": 25000, "partial": false, "truncated": true, "source": "viewer", "cached": false}
```

Text longer than 500 characters is cut (`truncated`). Images, audio and other media come back as `"[image]"`, `"[audio]"` and so on; their URLs are never passed on.

### `GET /status`

```json
{"version": "1.1.0", "iris": "2026.1 (Build 234)", "hfToken": false, "maxRows": 1000000,
 "maxDownloadMb": 500, "exactCollation": true, "cacheTtl": 600, "showPrivate": false}
```

`hfToken` only says whether a token is set.

## Importing

### `POST /plan`

What an import would create, without writing anything or downloading data files. Same body as `/load`.

```json
{"dataset": "rotten_tomatoes", "split": "test", "table": "Demo.Reviews"}
```

```json
{"dataset": "rotten_tomatoes", "repo": "cornell-movie-review-data/rotten_tomatoes", "commit": "aa13bc2...",
 "config": "default", "split": "test", "limit": 0, "mode": "replace", "rowsEstimate": 1066,
 "table": "Demo.Reviews", "tableExists": false, "ownedByLoader": false, "action": "create", "reason": null,
 "columns": [{"source": "text", "name": "text_", "type": "VARCHAR(4000)", "note": "renamed from 'text'"},
             {"source": "label", "name": "label_", "type": "BIGINT", "note": "renamed from 'label'"},
             {"source": "label_name", "name": "label_name", "type": "VARCHAR(4000)", "note": "label names: neg, pos"}],
 "ddl": "CREATE TABLE Demo.Reviews (...)",
 "download": {"fullLoad": true, "rows": 1066, "bytes": 699000, "withinLimits": true},
 "warnings": ["VARCHAR lengths come from the first 100 rows; the import measures every row."], "sample": "viewer"}
```

`action` is `create`, `replace`, `append` or `refused`. A refused plan has a `reason` (table not created by hf2iris, mode `fail` and the table exists, over the size limits, unknown mode); it is still a 200 answer.

### `POST /load`

Imports synchronously and answers when the import is done.

```json
{"dataset": "stanfordnlp/imdb", "table": "Demo.IMDB", "split": "train", "limit": 1000,
 "config": "", "mode": "replace", "revision": ""}
```

Only `dataset` is required. The answer is the import result (table, rows, columns, seconds, commit, classLabels). Long imports can run into the web gateway's 60 second timeout; use `/jobs` for those.

### `POST /jobs`

Starts the same import in the background and answers `202` right away with the job (see below). The body is the one of `/load`. At most two background imports run at the same time (`HF2IRIS_MAX_JOBS`).

### `GET /jobs`, `GET /jobs/:id`

`GET /jobs?limit=50` lists the newest jobs as `{"items": [...], "count": n}`.

```json
{"id": 17, "state": "running", "phase": "inserting", "rowsDone": 3000, "rowsTotal": 10000,
 "dataset": "stanfordnlp/imdb", "table": "Demo.IMDB", "split": "test", "config": null, "mode": "replace",
 "revision": null, "limit": 10000, "user": "_SYSTEM", "createdAt": "2026-10-13 10:00:00",
 "startedAt": "2026-10-13 10:00:01", "finishedAt": null, "elapsed": 42, "cancelRequested": false,
 "result": null, "error": null, "importLogId": null}
```

`state` is `queued`, `running`, `ok`, `error`, `cancelled` or `interrupted` (the process ended, for example because IRIS restarted; its transaction was rolled back). `phase` is `resolving`, `fetching`, `inserting` or `finishing`. When the job is done, `result` holds what `/load` would have answered.

### `POST /jobs/:id/cancel`

The import stops before its next batch of 1000 rows and is rolled back: a new table is removed, rows appended to an existing table are taken out again. A download that is already running is not interrupted; the cancel takes effect when it ends.

## History and tables

### `GET /history`

The last 50 imports from any path (web page, REST, SQL, ObjectScript), newest first.

### `GET /tables`

Tables created by hf2iris that still exist.

```json
{"items": [{"table": "Demo.IMDB", "rows": 3000, "columnCount": 3, "dataset": "stanfordnlp/imdb",
            "config": null, "split": "test", "commit": "e628166...", "lastImport": "2026-10-13 10:05:12"}],
 "count": 1}
```

### `GET /tables/:table`

The same fields plus `columns` (`[{"name": "text_", "type": "VARCHAR(4000)"}, ...]`) and the last 10 `imports` into the table. Other tables answer `not_found`.

### `GET /preview/:table`

Rows of a table created by hf2iris: `?rows=20` (up to 200) and, since 1.1, `?offset=` for paging.

```json
{"table": "Demo.IMDB", "total": 3000, "columns": ["text_", "label_", "label_name"],
 "rows": [["I love sci-fi ...", "0", "neg"]], "offset": 50}
```

## Caching

Search results are cached for 5 minutes, details and previews for 10 (`HF2IRIS_CACHE_TTL`, `0` turns the cache off). The cache lives in IRISTEMP, so it is shared by all IRIS processes and gone after a restart. Results seen with a token are never handed to requests without one, errors are not cached, and `refresh=1` asks Hugging Face again.

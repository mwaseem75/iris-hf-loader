# iris-hf-loader

**Dataset Explorer and Import Studio for InterSystems IRIS.** Find a dataset on the [Hugging Face Hub](https://huggingface.co/datasets), look inside it, and load it into an IRIS table: from a web page, with one line of ObjectScript, or with one SQL statement.

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![InterSystems IRIS 2023.1+](https://img.shields.io/badge/InterSystems%20IRIS-2023.1%2B-1f6feb)
![Version 1.1.0](https://img.shields.io/badge/version-1.1.0-informational)

> **[Screenshot: `docs/screenshots/explore.png`]**
> The Explore page with search results for "imdb".

Built for the InterSystems Community Bounty, idea [DPI-I-543](https://ideas.intersystems.com/ideas/DPI-I-543) "Load Datasets from Hugging Face into IRIS".

```objectscript
do ##class(dc.hf2iris.Loader).Load("stanfordnlp/imdb", "Demo.IMDB", "test", 1000)
```

```sql
SELECT dc_hf2iris.load_dataset('stanfordnlp/imdb', 'Demo.IMDB', 'test', 1000)
```

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Tutorial](#tutorial)
  1. [Start IRIS](#1-start-iris)
  2. [Find a dataset](#2-find-a-dataset)
  3. [Look inside a dataset](#3-look-inside-a-dataset)
  4. [Plan and run an import](#4-plan-and-run-an-import)
  5. [Follow your imports](#5-follow-your-imports)
  6. [Work with the imported tables](#6-work-with-the-imported-tables)
  7. [Query the table with SQL](#7-query-the-table-with-sql)
  8. [Import from an IRIS terminal](#8-import-from-an-iris-terminal)
  9. [Import from SQL](#9-import-from-sql)
  10. [Configs, splits and pinned versions](#10-configs-splits-and-pinned-versions)
  11. [Label columns](#11-label-columns)
  12. [Gated and private datasets](#12-gated-and-private-datasets)
  13. [The REST API](#13-the-rest-api)
  14. [Python](#14-python)
  15. [Themes and phones](#15-themes-and-phones)
- [Installing with IPM](#installing-with-ipm)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Safety and limits](#safety-and-limits)
- [Running the tests](#running-the-tests)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [License](#license)

## Features

**Discover**
- Search the Hugging Face Hub by name or keyword, sort by downloads, likes, trending or recent updates, and filter by task, size and language.
- See each dataset's description, license, tasks, size, downloads and whether it is gated.
- Page through the real rows of any split before importing anything. Images and audio are shown as placeholders; media files are never downloaded to the browser.
- Configs and splits with their row counts and sizes, read in one request.

**Import**
- Plan an import before running it: the target table, every IRIS column and type, the expected row count, and a clear reason if the import would not be allowed.
- Background imports with live progress, so large datasets do not hit the web gateway's 60 second limit. Cancel at any time; nothing of a cancelled import is kept.
- Row limits that only stream the rows you ask for, instead of downloading the whole dataset.
- Replace, append or fail when the table already exists.
- Pin an import to a commit so the same rows come back every time. Every import records the commit it read.
- The same import from the web page, ObjectScript, SQL, REST or Python.

**Data quality**
- Column types come from the dataset's own schema: integers stay integers even with missing values, `float32` values keep their digits, decimals, dates and timestamps keep their types.
- Text and nested values go into normal `VARCHAR` columns, so `=`, `LIKE`, `GROUP BY` and `DISTINCT` work as usual.
- Class labels keep their names in an extra `<column>_name` column.
- NULL and empty strings stay different; text compares exactly.
- Column names are cleaned for SQL, including reserved words and accents.

**Manage**
- An Imports page with every background import and the full history of all imports, whatever started them.
- A Tables page with the tables the loader created: rows, columns, the imports into each table, and one-click re-import.
- Existing tables that the loader did not create are never changed.
- Every import is all or nothing.

**Operate**
- Dark and light theme, keyboard navigation and screen reader support, and a layout that works on a phone.
- Your Hugging Face token stays on the IRIS server; the browser never sees it.
- Short-lived caching of Hugging Face answers, shared by all IRIS processes.
- A Docker image with pinned versions, and a test suite that covers the Python code, the IRIS classes, the REST API and the web app in a real browser.

## Quick start

```bash
git clone https://github.com/mwaseem75/iris-hf-loader.git
cd iris-hf-loader
docker compose up -d --build --wait
```

Open **http://localhost:52773/hf2iris/index.html** and sign in with `_SYSTEM` / `SYS`.

## Tutorial

The tutorial takes about 15 minutes. You need Docker Desktop (or Docker with Compose v2), git, internet access for huggingface.co, and about 5 GB of free disk space.

### 1. Start IRIS

```bash
git clone https://github.com/mwaseem75/iris-hf-loader.git
cd iris-hf-loader
docker compose up -d --build --wait
```

On older Docker installations the command is `docker-compose` with a dash.

The first build takes a few minutes. It downloads the IRIS image and installs the Python libraries for embedded Python (`datasets`, `pandas`, `pyarrow`). `--wait` returns when IRIS is up and the package is installed in the `USER` namespace.

The container listens on your own machine only:

| | Address |
|---|---|
| Web app | http://localhost:52773/hf2iris/index.html |
| Management Portal | http://localhost:52773/csp/sys/UtilHome.csp |
| SQL (DBeaver, JDBC, ODBC) | localhost:1972, namespace `USER` |

The login is `_SYSTEM` / `SYS`. This is a demo setup; do not expose it to a network. If port 52773 or 1972 is taken, choose other ones:

```bash
IRIS_WEB_PORT=8080 IRIS_SQL_PORT=51972 docker compose up -d --wait
```

### 2. Find a dataset

Open http://localhost:52773/hf2iris/index.html and sign in. The address needs the `index.html` at the end.

The **Explore** page starts with the most downloaded datasets on the Hub. Type `imdb` in the search box. You can sort the results by **Downloads**, **Likes**, **Trending** or **Recently updated**, and narrow them down by **Task**, **Size** and **Language**. The search and filters are part of the page address, so you can bookmark or share a search.

Each card shows the dataset id, a short description, its task, size and license, the number of downloads and likes, and when it was last updated. A yellow **Gated** badge means the dataset needs a token (see [step 12](#12-gated-and-private-datasets)).

> **[Screenshot: `docs/screenshots/explore.png`]**
> Search results for "imdb" on the Explore page.

### 3. Look inside a dataset

Click **stanfordnlp/imdb**. The dataset page shows the description, badges, downloads and likes, and a link to the dataset on Hugging Face.

Pick a **Config** and a **Split**; each split shows its row count and size. Then use the three tabs:

- **Overview** lists the dataset id, the latest commit, the configs, the selected split and whether Hugging Face's dataset viewer covers this dataset.
- **Preview** shows the actual rows, 20 per page, with **Previous** and **Next**. Long texts are shortened (hover over a cell to read more) and media cells show `[image]` or `[audio]`. Nothing is imported or downloaded into IRIS for this.
- **Schema** lists every column with its Hugging Face type next to the IRIS column name and type that an import would create.

> **[Screenshot: `docs/screenshots/dataset-preview.png`]**
> The Preview tab of `openai/gsm8k` with the import panel on the right.

> **[Screenshot: `docs/screenshots/dataset-schema.png`]**
> The Schema tab: Hugging Face types next to the planned IRIS columns.

The tabs work with the keyboard too: the arrow keys, Home and End move between them.

If the dataset viewer cannot show a dataset, the Preview tab offers **Read the first rows directly**. That reads the dataset files themselves and can take up to a minute, so it only happens when you click it.

### 4. Plan and run an import

The **Import into IRIS** panel sits next to the tabs. For the IMDB example:

1. Choose the `test` split.
2. Set **Target table** to `Demo.IMDB`. The default is `HF.<dataset name>`.
3. Leave **Row limit** at `1000`. `0` imports all rows.
4. Leave **If the table exists** on **Replace**. **Append** adds rows to a table the loader created; **Fail** stops if the table exists.
5. Leave **Revision** empty for the latest version (more in [step 10](#10-configs-splits-and-pinned-versions)).

Click **Review plan**. Nothing is written yet. The plan says whether the table will be created, replaced or appended to, how many rows to expect, which commit will be read and how much has to be downloaded. If the import would not be allowed, for example because the target table exists and was not created by this tool, the plan says why and **Start import** stays disabled.

Click **Start import**. The import runs in the background inside IRIS, and the panel follows it: "Looking up the dataset", "Downloading rows from Hugging Face", then "Writing rows to IRIS" with a progress bar. **Cancel import** stops it before the next batch of rows; a cancelled import leaves nothing behind. When it is done, the panel links to the new table.

> **[Screenshot: `docs/screenshots/import-progress.png`]**
> An import running in the background, with its progress and the Cancel button.

Under the panel, **Same import in ObjectScript or SQL** shows the one-line command for exactly this import, with copy buttons.

One thing worth knowing about IMDB: the train split is sorted by label, so its first 1000 rows are all negative reviews. That is why this example uses the `test` split.

### 5. Follow your imports

Open **Imports** in the side menu.

- **Background imports** lists the recent background imports with their state (running, ok, error, cancelled or interrupted), who started them and when. Running imports show their progress and a **Cancel** button, and the page updates itself while something runs. Open a finished import to see its columns, commit and label names, or the full error message.
- **Import history** lists the last 50 imports from every source: this page, the REST API, SQL and ObjectScript. Filter it by status or by dataset and table name. Dataset and table names link to their pages.

> **[Screenshot: `docs/screenshots/imports.png`]**
> The Imports page with a finished background import and the import history.

### 6. Work with the imported tables

Open **Tables** in the side menu. It lists every table the loader created, with its rows, columns, source dataset, commit and last import. Filter the list by name.

Click a table to open it:

- **Rows**: the table's rows, 50 per page.
- **Columns**: each column with its IRIS type.
- **Imports**: the imports into this table, with their status and messages.

**Copy SELECT** copies a ready-made query. **Re-import** opens the dataset page with the same config, split and table filled in, which is the quickest way to refresh a table.

> **[Screenshot: `docs/screenshots/table-detail.png`]**
> The `Demo.IMDB` table with its rows, columns and imports.

### 7. Query the table with SQL

The imported table is an ordinary IRIS table, so every SQL tool can use it.

1. Open the Management Portal at http://localhost:52773/csp/sys/UtilHome.csp and log in.
2. Go to **System Explorer > SQL** and switch the namespace to **USER**.
3. Under **Tables**, filter the schema by `Demo` and click `Demo.IMDB` to see its columns.
4. In **Execute Query**, run:

```sql
SELECT TOP 5 label_, label_name, text_ FROM Demo.IMDB
```

Notice the column names: `text` and `label` became `text_` and `label_`. The loader renames column names that are SQL reserved words, replaces spaces and symbols with `_`, and removes accents (`Café` becomes `Cafe`). `label_name` was added by the loader; see [step 11](#11-label-columns).

Text columns compare exactly, so `'Iris-setosa'` and `'IRIS-SETOSA'` are different values. For a case-insensitive search use `%SQLUPPER`:

```sql
SELECT COUNT(*) FROM Demo.IMDB WHERE %SQLUPPER(label_name) = %SQLUPPER('NEG')
```

### 8. Import from an IRIS terminal

```bash
docker compose exec iris iris session iris -U USER
```

```objectscript
USER>do ##class(dc.hf2iris.Loader).Load("stanfordnlp/imdb","Demo.IMDB","test",1000)

Fetching stanfordnlp/imdb [default:test] @ e628166 limit 1000
Created Demo.IMDB (3 columns)
  1,000/1,000 rows
Loaded 1000 rows into Demo.IMDB in .48s (commit e628166)
```

With only a dataset id, the whole train split goes into `HF.<name>`:

```objectscript
do ##class(dc.hf2iris.Loader).Load("scikit-learn/iris")
```

The arguments of `Load`:

| Argument | Default | Meaning |
|---|---|---|
| dataset | | Hugging Face id, for example `stanfordnlp/imdb` |
| table | `HF.<name>` | target table, `Schema.Table` |
| split | `train` | which split to load |
| limit | `0` | maximum number of rows; `0` means all |
| config | `""` | config (subset), needed when a dataset has several |
| mode | `replace` | `replace`, `append`, or `fail` when the table exists |
| revision | `""` | branch, tag or commit hash; empty means the latest version |
| result | | output: the result as a `%DynamicObject` |

`Load` returns a `%Status`. To see which configs and splits a dataset has before loading it:

```objectscript
write ##class(dc.hf2iris.Loader).Info("openai/gsm8k").%ToJSON()
{"dataset":"openai/gsm8k","configs":{"main":["train","test"],"socratic":["train","test"]}}
```

### 9. Import from SQL

The loader is also available as two SQL functions, so any SQL client can import: the Management Portal, DBeaver, JDBC, ODBC or Python DB-API. The client needs neither Python nor a Hugging Face library.

```sql
SELECT dc_hf2iris.load_dataset('scikit-learn/iris', 'HF.iris')

SELECT dc_hf2iris.dataset_info('openai/gsm8k')
```

The arguments are the same as in the table above, in the same order; leave out trailing ones or pass `NULL`. `load_dataset` returns the result as JSON with `"status":"OK"`. A failed import does not raise an SQL error; it returns `{"status":"ERROR","error":"..."}`, which is easier to handle in scripts.

### 10. Configs, splits and pinned versions

Some datasets have several configs. `openai/gsm8k` (grade school math questions) has `main` and `socratic`, and if you leave out the config you are told which ones exist:

```sql
SELECT dc_hf2iris.load_dataset('openai/gsm8k', 'Demo.GSM8K', 'test', 200)
-- {"status":"ERROR","error":"This dataset has several configs, choose one: main, socratic"}

SELECT dc_hf2iris.load_dataset('openai/gsm8k', 'Demo.GSM8K', 'test', 200, 'main')
```

A wrong split name works the same way: the error lists the splits that exist. These checks happen before anything is downloaded.

Datasets change over time. When you need the same rows every time, for a demo, a test or a report, pass a commit hash as the revision:

```sql
SELECT dc_hf2iris.load_dataset('rotten_tomatoes', 'Demo.Reviews', 'test', 0, NULL, NULL,
       'aa13bc287fa6fcab6daf52f0dfb9994269ffea28')
```

The result then says `"pinned": true`. Every import, pinned or not, records the commit it read; you see it in the result, on the Imports and Tables pages and in `dc_hf2iris.ImportLog`. A branch or tag name works too and is turned into a commit once, at the start of the import.

`rotten_tomatoes` is an old style id without an owner. Recent versions of the Hugging Face library reject those, so the loader looks up the full id (`cornell-movie-review-data/rotten_tomatoes`) first.

### 11. Label columns

Classification datasets store their labels as numbers: IMDB uses `0` and `1`, and you would have to look up what they mean. The loader keeps the names by adding a `<column>_name` column right after each label column:

```sql
SELECT label_, label_name, COUNT(*) FROM Demo.Reviews GROUP BY label_, label_name
```

Multi-label datasets work too. In `google-research-datasets/go_emotions` (config `simplified`) a row can have several labels, and you get both lists, for example `[2]` and `["anger"]`. The value `-1`, which some datasets use for "no label", becomes NULL. The full list of label names is in the import result (`classLabels`) and in `ImportLog.ClassLabels`.

### 12. Gated and private datasets

Some datasets are gated: you accept their terms on huggingface.co before you can download them. For those, and for your own private datasets, create an access token in your Hugging Face settings and start the container with it:

```bash
HF_TOKEN=hf_xxx docker compose up -d --wait
```

In PowerShell:

```powershell
$env:HF_TOKEN = "hf_xxx"
docker compose up -d --wait
```

The status badge in the top bar then says **HF token set**. Without a token, a gated dataset's page explains what to do instead of showing an error.

The token is only passed into the container when it is set. All Hugging Face requests go out from IRIS, so the token never reaches the browser, and it is replaced by `***` if it ever appears in an error message or the import log.

### 13. The REST API

The web app is built on a REST API that you can use directly. It uses basic authentication. [API.md](API.md) describes every endpoint with its parameters, responses and error codes.

```bash
# search and look inside datasets
curl -u _SYSTEM:SYS "http://localhost:52773/api/hf2iris/datasets?search=squad&sort=likes"
curl -u _SYSTEM:SYS "http://localhost:52773/api/hf2iris/dataset?id=openai/gsm8k"
curl -u _SYSTEM:SYS "http://localhost:52773/api/hf2iris/dataset/preview?id=openai/gsm8k&config=main&split=test&rows=5"

# plan and start a background import, then follow it
curl -u _SYSTEM:SYS -X POST -H "Content-Type: application/json" \
     -d '{"dataset":"openai/gsm8k","config":"main","split":"test","table":"Demo.GSM8K"}' \
     http://localhost:52773/api/hf2iris/plan
curl -u _SYSTEM:SYS -X POST -H "Content-Type: application/json" \
     -d '{"dataset":"openai/gsm8k","config":"main","split":"test","table":"Demo.GSM8K"}' \
     http://localhost:52773/api/hf2iris/jobs
curl -u _SYSTEM:SYS http://localhost:52773/api/hf2iris/jobs/1

# history and tables
curl -u _SYSTEM:SYS http://localhost:52773/api/hf2iris/history
curl -u _SYSTEM:SYS http://localhost:52773/api/hf2iris/tables
curl -u _SYSTEM:SYS "http://localhost:52773/api/hf2iris/preview/Demo.GSM8K?rows=5&offset=0"
```

`POST /load` also imports, but it answers only when the import is finished. Use `/jobs` for anything that takes longer than a few seconds: the web gateway in front of IRIS gives up on a request after 60 seconds, while a background job keeps running.

### 14. Python

Inside IRIS embedded Python (a `Language = python` method, or `irispython`) the package can be used directly:

```python
import hf2iris
r = hf2iris.load("stanfordnlp/imdb", "Demo.IMDB", split="test", limit=1000)
print(r["rows"], r["columns"])
```

To load a pandas DataFrame or a pyarrow Table you already have:

```python
from hf2iris.loader import IrisSQL, load_dataframe
load_dataframe(df, "Demo.Mine", IrisSQL())
```

### 15. Themes and phones

The button in the top bar switches between the dark and the light theme; the choice is remembered. Both themes meet the WCAG contrast guidelines.

> **[Screenshot: `docs/screenshots/light-theme.png`]**
> A dataset page in the light theme.

On a phone the side menu becomes a bar under the top bar, results show in one column, and the import panel moves below the dataset with a **Jump to import** button.

> **[Screenshot: `docs/screenshots/phone-explore.png`]**
> The Explore page on a phone. This image is tall; show it at about 260 pixels wide.

## Installing with IPM

On your own IRIS (2023.1 or newer, with embedded Python), once the package is published on Open Exchange:

```objectscript
zpm "install iris-hf-loader"
```

Until then, or to install your own changes, load it from a clone of this repository:

```objectscript
zpm "load /path/to/iris-hf-loader"
```

IPM installs the Python libraries from `requirements.txt`, the classes, the REST application `/api/hf2iris` and the web app `/hf2iris/index.html`.

## Configuration

Set these in your shell before `docker compose up`; they are passed into the container when set.

| Variable | Default | Effect |
|---|---|---|
| `HF_TOKEN` | | Hugging Face token for gated and private datasets |
| `HF2IRIS_MAX_ROWS` | 1000000 | the largest split a load without a row limit may import |
| `HF2IRIS_MAX_DOWNLOAD_MB` | 500 | the largest download a load without a row limit may start |
| `HF2IRIS_EXACT` | 1 | `0` creates text columns with the IRIS default collation instead of exact |
| `IRIS_WEB_PORT`, `IRIS_SQL_PORT` | 52773, 1972 | host ports of the container |

These are read by the loader too; to use them with Docker, add them to the `environment:` list in `docker-compose.yml`:

| Variable | Default | Effect |
|---|---|---|
| `HF2IRIS_CACHE_TTL` | 600 | seconds Hugging Face answers are cached (search: at most 300); `0` turns the cache off |
| `HF2IRIS_SHOW_PRIVATE` | 0 | `1` lists your token's private datasets in search results |
| `HF2IRIS_MAX_JOBS` | 2 | how many background imports may run at the same time |

## How it works

1. The browser talks only to the IRIS REST API (`dc.hf2iris.REST`). All Hugging Face requests go out from IRIS.
2. Discovery (`dc.hf2iris.Hub`, Python `hf2iris.hub`) uses the Hugging Face Hub API for search and metadata, and the dataset viewer for splits, sizes and preview rows. Answers are cached in IRISTEMP for a few minutes.
3. A plan (`hf2iris.plan`) checks the dataset, config, split and target table, and computes the table definition from the dataset's schema and a sample of rows, without writing anything.
4. An import (`dc.hf2iris.Loader`, Python `hf2iris.loader`) reads the split as an Arrow table, streaming only the requested rows when there is a row limit, maps each column to an IRIS type, creates the table and inserts the rows in batches inside one transaction.
5. Background imports (`dc.hf2iris.Job`) run the same import in their own IRIS process and report their progress, so the web request returns at once.
6. Every import is recorded in `dc_hf2iris.ImportLog`.

Column types:

| Dataset type | IRIS type |
|---|---|
| integers, also with missing values | `BIGINT` |
| float16, float32, float64 | `DOUBLE` (a float32 `1.4` stays `1.4`) |
| bool | `BIT` |
| decimal | `NUMERIC(p,s)` |
| date | `DATE` |
| timestamp | `TIMESTAMP` (UTC) |
| text | `VARCHAR(n)` |
| lists, structs, embeddings | `VARCHAR(n)` holding JSON |
| text longer than 1,000,000 characters | `LONGVARCHAR` |
| class label | `BIGINT`, plus a `<column>_name` column with the label |

`n` is the length of the longest value, rounded up to a multiple of 1000 and at least 4000.

## Safety and limits

- **Existing tables are safe.** `replace` and `append` only touch tables the loader created. Any other existing table makes the import stop without changing it.
- **Imports are all or nothing.** If IRIS rejects a row, the error names it and nothing of that import stays behind: a new table is dropped again, rows appended to an existing table are rolled back. A failed `replace` leaves no table, because the old one was already dropped.
- **One import per table.** Two imports never write to the same table at the same time.
- **Background imports survive the browser.** Closing the page does not stop them. If IRIS restarts during one, it is marked "interrupted" and its transaction is rolled back.
- **Full loads have limits.** Without a row limit, the loader refuses splits over 1,000,000 rows or downloads over 500 MB (see [Configuration](#configuration)). With a row limit, only those rows are streamed and no limit applies.
- **The browser never sees your token**, and only tables created by the loader can be previewed through the API.

## Running the tests

Everything at once, against the running container:

```bash
tests/run_all.sh                  # changes no data
tests/run_all.sh --with-imports   # also imports real datasets, then removes everything it added
```

Or one part at a time:

```bash
pip install pandas pyarrow pytest
pytest tests                          # Python logic with SQLite and fakes: no IRIS, no network
node --test tests/ui/state.test.mjs   # UI helpers, contrast of both themes
node tests/ui/mocked.mjs              # import monitoring in the UI against a fake API

tests/run_iris_tests.sh               # the Python tests inside IRIS, plus tests against real IRIS SQL
tests/smoke.sh discovery              # every REST endpoint against the live Hub, read-only
tests/smoke.sh import                 # real imports and background jobs; drops its tables afterwards
node tests/ui/live.mjs                # the web pages against the server
node tests/ui/browser.mjs             # the app in headless Chrome
```

The browser check starts Chrome headless with an empty, throwaway profile and only opens the local app. It covers sign-in, search, navigation, the theme, the phone layout and console errors; `--screenshots` saves fresh images to `docs/screenshots`. You need Node 24, and Chrome for that check. The scripts are bash; on Windows run them from Git Bash. GitHub Actions runs the offline tests on every push.

## Troubleshooting

**"Not Found" at http://localhost:52773/hf2iris/.** Add `index.html`: http://localhost:52773/hf2iris/index.html.

**The page says "Unauthorized".** Reload with Ctrl+F5 and sign in. If it keeps happening, click **Sign out** in the top bar and sign in again.

**The page looks outdated after an update.** IRIS lets the browser keep static files for an hour. Reload with Ctrl+F5.

**I changed the code and nothing happened.** In the `USER` namespace run `zpm "load /home/irisowner/dev/ -v -bypass-py-deps"`, then `docker compose restart`. The web server processes keep the old Python code in memory until they restart. `-bypass-py-deps` keeps IPM from reinstalling the Python libraries, which would replace the pinned versions of the image.

**An import is slow.** Use a row limit first, and use the background import (the web page always does). Downloads are cached in the container under `/home/irisowner/.cache/huggingface`.

**"Hugging Face rate limit reached."** Wait a minute, or start the container with `HF_TOKEN`; requests with a token get a higher limit.

## Project layout

```
python/hf2iris/     Python package: loader.py (download, types, insert), hub.py (search,
                    details, previews), plan.py (import plans)
src/dc/hf2iris/     Loader.cls (ObjectScript and SQL entry points), Hub.cls, Job.cls
                    (background imports), Tables.cls, ImportLog.cls, REST.cls
web/                the web app: index.html, app.css, js/ (no framework, no build step)
tests/              Python tests, UI tests (tests/ui), run_all.sh and the other scripts
docs/screenshots/   images for this README
module.xml          IPM package
Dockerfile, docker-compose.yml, iris.script
requirements.txt    Python libraries for IPM installs
constraints.txt     exact versions used in the Docker image
API.md              REST API reference
```

The Docker image is pinned to IRIS 2026.1.0.234 and to the package versions in `constraints.txt`, the versions everything was tested with.

## License

MIT. See [LICENSE](LICENSE).

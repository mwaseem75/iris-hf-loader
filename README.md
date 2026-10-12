# iris-hf-loader

Load a dataset from the [Hugging Face Hub](https://huggingface.co/datasets) into an InterSystems IRIS table with one line of ObjectScript, one SQL statement, or a couple of clicks in a small web page.

I wrote it for the InterSystems Community Bounty, idea [DPI-I-543](https://ideas.intersystems.com/ideas/DPI-I-543) "Load Datasets from Hugging Face into IRIS". The rest of this page is a walkthrough: you start the container, load a few datasets in different ways and look at the result in SQL. It takes about 15 minutes.

```objectscript
do ##class(dc.hf2iris.Loader).Load("stanfordnlp/imdb", "Demo.IMDB", "train", 1000)
```

```sql
SELECT dc_hf2iris.load_dataset('stanfordnlp/imdb', 'Demo.IMDB', 'train', 1000)
```

## What you need

- Docker Desktop (or Docker with Compose v2)
- git
- internet access, because the datasets come from huggingface.co
- about 5 GB of free disk space for the image

If you want to install the package into an IRIS you already have instead, skip to [Installing with IPM](#installing-with-ipm).

## Step 1. Start IRIS

```bash
git clone https://github.com/mwaseem75/iris-hf-loader.git
cd iris-hf-loader
docker compose up -d --build --wait
```

On older Docker installations the command is `docker-compose` with a dash.

The first build takes a few minutes: it downloads the IRIS image and installs the Python libraries (`datasets`, `pandas`, `pyarrow`) for embedded Python. `--wait` returns when IRIS is up and the package is installed in the `USER` namespace.

The container listens on localhost only:

- web: http://localhost:52773 (Management Portal and the loader page)
- SQL: localhost:1972, namespace `USER`, for DBeaver, JDBC or ODBC

Login is `_SYSTEM` / `SYS`. Keep it on your own machine; it is a demo setup.

If port 52773 or 1972 is already used on your machine, pick other ones:

```bash
IRIS_WEB_PORT=8080 IRIS_SQL_PORT=51972 docker compose up -d --wait
```

## Step 2. Import a dataset from the web page

Open http://localhost:52773/hf2iris/index.html and sign in with `_SYSTEM` / `SYS`.

1. The dataset field already says `stanfordnlp/imdb` (50,000 movie reviews). Click **Check**. The page asks the Hub which configs and splits exist, without downloading anything, and shows them as chips: config `plain_text`, splits `train`, `test` and `unsupervised`.
2. Click the split you want. Leave the table as `Demo.IMDB` and the row limit at `1000`.
3. Click **Import into IRIS**.

After a few seconds you see the new table: number of rows, the column types, the commit of the dataset that was read, and the label names (`0 = neg`, `1 = pos`). Below it there is a preview of the first rows, and the import history lists every run. Click a table name in the history to preview it again.

The grey box under the form shows the ObjectScript line that does the same import, so you can copy it into a terminal or a class.

One thing that surprised me: the IMDB train split is sorted by label, so the first 1000 rows are all negative reviews. Use a bigger limit, or the `test` split, if you want both.

## Step 3. Look at the table in the Management Portal

1. Open http://localhost:52773/csp/sys/UtilHome.csp and log in.
2. Go to **System Explorer > SQL**.
3. Switch the namespace (top of the page) to **USER**.
4. On the left, open **Tables** and type `Demo` in the schema filter. Click `Demo.IMDB` to see its columns.
5. In **Execute Query** run:

```sql
SELECT TOP 5 label_, label_name, text_ FROM Demo.IMDB
```

Notice the column names. `text` and `label` became `text_` and `label_`, because the loader renames column names that are SQL reserved words (or that I treat as such). Spaces and symbols become `_`, and accents are removed, so `Größe` becomes `Groe`. The `label_name` column was added by the loader; more on that in step 7.

## Step 4. Load from an IRIS terminal

```bash
docker compose exec iris iris session iris -U USER
```

```objectscript
USER>do ##class(dc.hf2iris.Loader).Load("stanfordnlp/imdb","Demo.IMDB","train",1000)

Fetching stanfordnlp/imdb [default:train] @ e628166 limit 1000
Created Demo.IMDB (3 columns)
  1,000/1,000 rows
Loaded 1000 rows into Demo.IMDB in .48s (commit e628166)
```

With only a dataset id, the whole train split goes into `HF.<name>`:

```objectscript
do ##class(dc.hf2iris.Loader).Load("scikit-learn/iris")
```

All arguments of `Load`:

| Argument | Default | Meaning |
|---|---|---|
| dataset | | Hugging Face id, for example `stanfordnlp/imdb` |
| table | `HF.<name>` | target table, `Schema.Table` |
| split | `train` | which split to load |
| limit | `0` | maximum number of rows, `0` = all |
| config | `""` | config (subset), needed when a dataset has several |
| mode | `replace` | `replace`, `append`, or `fail` when the table exists |
| revision | `""` | branch, tag or commit hash of the dataset; empty = latest |
| result | | output: the result as a `%DynamicObject` |

`Load` returns a `%Status`. To check before loading which configs and splits a dataset has:

```objectscript
write ##class(dc.hf2iris.Loader).Info("openai/gsm8k").%ToJSON()
{"dataset":"openai/gsm8k","configs":{"main":["train","test"],"socratic":["train","test"]}}
```

## Step 5. Load from SQL

The same loader is available as two SQL functions, so any SQL client works: the Management Portal, DBeaver, JDBC, ODBC or Python DB-API. The client needs no Python and no Hugging Face library.

```sql
SELECT dc_hf2iris.load_dataset('scikit-learn/iris', 'HF.iris')

SELECT dc_hf2iris.dataset_info('openai/gsm8k')
```

The arguments are the ones from the table above, in the same order. Leave out the ones at the end or pass `NULL`. The function returns the result as JSON with `"status":"OK"`. If the import fails it does not raise an SQL error; you get `{"status":"ERROR","error":"..."}` instead, which is easier to handle in scripts.

## Step 6. Configs, splits and pinned versions

Some datasets have several configs. `openai/gsm8k` (grade school math questions) has `main` and `socratic`, and if you forget the config you are told so:

```sql
SELECT dc_hf2iris.load_dataset('openai/gsm8k', 'Demo.GSM8K', 'test', 200)
-- {"status":"ERROR","error":"This dataset has several configs, choose one: main, socratic"}

SELECT dc_hf2iris.load_dataset('openai/gsm8k', 'Demo.GSM8K', 'test', 200, 'main')
```

A wrong split name works the same way: the error lists the splits that exist. These checks happen before anything is downloaded.

Datasets change. If you need the same rows every time, for a demo, a test or a report, pass a commit hash as the revision:

```sql
SELECT dc_hf2iris.load_dataset('rotten_tomatoes', 'Demo.Reviews', 'test', 0, NULL, NULL,
       'aa13bc287fa6fcab6daf52f0dfb9994269ffea28')
```

The result then says `"pinned": true`. Every import, pinned or not, records the commit it read. You can see it in the result, in the web page (`@aa13bc2` in the history) and in `dc_hf2iris.ImportLog`. A branch or tag name works too; it is turned into a commit once, at the start of the import.

By the way, `rotten_tomatoes` is an old style id without an owner. Newer versions of the Hugging Face library reject those, so the loader asks the Hub for the full id (`cornell-movie-review-data/rotten_tomatoes`) first.

## Step 7. Label columns

Classification datasets store labels as numbers. IMDB has `0` and `1`, and you would have to look up on the dataset page what they mean. The loader keeps the names: for each label column it adds a `<column>_name` column right after it.

```sql
SELECT label_, label_name, COUNT(*) FROM Demo.Reviews GROUP BY label_, label_name
```

Multi-label datasets work too. In `google-research-datasets/go_emotions` (config `simplified`) a row can have several labels, and you get both lists, for example `[2]` and `["anger"]`. `-1`, which some datasets use for "no label", gives NULL. The full list of names is also in the result (`classLabels`) and in `ImportLog.ClassLabels`.

## Step 8. Gated and private datasets

Some datasets are gated: you have to accept their terms on huggingface.co before you can download them. For those, and for your own private datasets, create an access token in your Hugging Face settings and start the container with it:

```bash
HF_TOKEN=hf_xxx docker compose up -d --wait
```

In PowerShell:

```powershell
$env:HF_TOKEN = "hf_xxx"
docker compose up -d --wait
```

The token is only passed into the container when it is set, and it is replaced by `***` if it ever shows up in an error message or the import log.

## Step 9. The REST API

The web page uses a small REST API, and you can call it yourself. It uses basic authentication.

```bash
curl -u _SYSTEM:SYS "http://localhost:52773/api/hf2iris/info?dataset=stanfordnlp/imdb"

curl -u _SYSTEM:SYS -X POST -H "Content-Type: application/json" \
     -d '{"dataset":"stanfordnlp/imdb","table":"Demo.IMDB","split":"test","limit":500}' \
     http://localhost:52773/api/hf2iris/load

curl -u _SYSTEM:SYS http://localhost:52773/api/hf2iris/history

curl -u _SYSTEM:SYS "http://localhost:52773/api/hf2iris/preview/Demo.IMDB?rows=5"
```

`/load` also accepts `config`, `mode` and `revision`. `/preview` only shows tables that were created by the loader.

## Step 10. From Python

Inside IRIS embedded Python (a `Language = python` method, or `irispython`) the package can be used directly:

```python
import hf2iris
r = hf2iris.load("stanfordnlp/imdb", "Demo.IMDB", split="test", limit=1000)
print(r["rows"], r["columns"])
```

To load a pandas DataFrame or a pyarrow Table that you already have:

```python
from hf2iris.loader import IrisSQL, load_dataframe
load_dataframe(df, "Demo.Mine", IrisSQL())
```

## Installing with IPM

On your own IRIS (2023.1 or newer, with embedded Python), once the package is published on Open Exchange:

```objectscript
zpm "install iris-hf-loader"
```

Until then, or to install your own changes, load it from a clone of this repository:

```objectscript
zpm "load /path/to/iris-hf-loader"
```

IPM installs the Python libraries from `requirements.txt`, the classes, the REST application `/api/hf2iris` and the web page `/hf2iris/index.html`.

## How columns are typed

Hugging Face datasets come as Arrow tables, and the column types are taken from there:

| Dataset type | IRIS type |
|---|---|
| integers, also with missing values | `BIGINT` |
| float16, float32, float64 | `DOUBLE` (a float32 `1.4` stays `1.4`) |
| bool | `BIT` |
| decimal | `NUMERIC(p,s)` |
| date | `DATE` |
| timestamp | `TIMESTAMP` (UTC) |
| text | `VARCHAR(n)` |
| lists, structs, embeddings | `VARCHAR(n)` with the value as JSON |
| text longer than 1,000,000 characters | `LONGVARCHAR` |

`n` is the length of the longest value, rounded up to a multiple of 1000 and at least 4000. Because text stays in normal VARCHAR columns, `=`, `LIKE`, `GROUP BY` and `DISTINCT` work on it as usual. NULL and the empty string are kept apart.

Text columns use exact collation, so `'Iris-setosa'` and `'IRIS-SETOSA'` are two different values. That is what the data says, but it is not the IRIS default. For a case-insensitive search use `%SQLUPPER`:

```sql
SELECT COUNT(*) FROM HF.iris WHERE %SQLUPPER(Species) = %SQLUPPER('iris-setosa')
```

## Good to know

- **Existing tables are safe.** `replace` and `append` only touch tables the loader created itself. If the target is some other table, the import stops and the table is left alone.
- **Imports are all or nothing.** If IRIS rejects a row, the error tells you which one, and nothing of that import stays in the database: a new table is dropped again, rows appended to an existing table are rolled back. A failed `replace` leaves no table, because the old one was already dropped.
- **Row limits are cheap.** With a limit, only those rows are streamed from the Hub. Without a limit the whole split is downloaded, and the loader refuses splits over 1,000,000 rows or 500 MB. Change that with `HF2IRIS_MAX_ROWS` and `HF2IRIS_MAX_DOWNLOAD_MB` when you start the container.
- **History.** Every run, also the failed ones, is in `dc_hf2iris.ImportLog`.
- **Exact collation off.** Start the container with `HF2IRIS_EXACT=0` if you prefer the IRIS default collation for new tables.

## Troubleshooting

**The page says "Unauthorized".** Reload it with Ctrl+F5 and sign in. If it keeps happening, use **Sign out** at the top right and sign in again.

**I changed the Python code and nothing happened.** Run `zpm "load /home/irisowner/dev/ -v"` in the `USER` namespace and then `docker compose restart`. The web server processes keep the old Python module in memory until they restart.

**A load takes long or seems stuck.** Use a row limit first. Large datasets are downloaded into the container's cache (`/home/irisowner/.cache/huggingface`).

**Port already in use.** See the end of step 1.

## Running the tests

```bash
pip install pandas pyarrow pytest
pytest tests                  # no IRIS and no network needed

tests/run_iris_tests.sh       # same tests inside the container, plus tests against real IRIS SQL
tests/smoke.sh                # loads real datasets from the Hub, a few minutes
```

The scripts are bash; on Windows run them from Git Bash. `run_iris_tests.sh` installs pytest into the container the first time. `smoke.sh` loads seven datasets, checks eight loads that have to fail with a clear message, and drops its tables afterwards.

## Project layout

```
python/hf2iris/     the Python package: download, type mapping, insert
src/dc/hf2iris/     Loader.cls (ObjectScript and SQL entry points), ImportLog.cls, REST.cls
web/index.html      the web page
tests/              pytest tests, run_iris_tests.sh, smoke.sh
module.xml          IPM package
Dockerfile, docker-compose.yml, iris.script
requirements.txt    Python libraries for IPM installs
constraints.txt     exact versions used in the Docker image
```

The Docker image is pinned to IRIS 2026.1.0.234 and to the package versions in `constraints.txt`, which are the versions everything was tested with.

## License

MIT

"""
Import plan (dry run): what an import would create, without writing anything or
downloading data files. Uses the same functions as the import itself.
"""
import json

import pyarrow as pa

from . import hub, loader
from .hub import HubError

PARAMS = ("dataset", "table", "split", "limit", "config", "mode", "revision")
SAMPLE_ROWS = 100


def features_from_dict(features):
    """Serialized features (as the dataset viewer returns them) -> datasets.Features."""
    from datasets import Features
    return Features.from_dict(features)


def _builder(repo, config, revision):
    try:
        from datasets import load_dataset_builder
        return load_dataset_builder(repo, config, revision=revision)
    except Exception as e:
        raise _error(e, repo)


def _error(e, repo):
    if isinstance(e, HubError):
        return e
    text = loader.error_text(e)
    if "several configs" in text:
        return HubError("bad_request", text)
    if text.startswith("Config "):
        return HubError("not_found", text)
    return hub._hub_error(e, repo)


def _sample_table(schema, records, media):
    arrays = []
    for field in schema:
        values = [None if field.name in media else r.get(field.name) for r in records]
        try:
            arrays.append(pa.array(values, type=field.type))
        except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
            arrays.append(pa.array([None] * len(records), type=field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


def _int(value, what):
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise HubError("bad_request", f"{what} must be a number")
    if number < 0:
        raise HubError("bad_request", f"{what} must be 0 or more")
    return number


def precheck(dataset, table=None, mode="replace", db=None):
    """Checks without network, before a background import starts: the target table,
    or HubError bad_request / conflict."""
    db = db or loader.IrisSQL()
    dataset = hub._check_id(dataset)
    mode = (mode or "replace").strip()
    if mode not in loader.MODES:
        raise HubError("bad_request", f"mode must be one of {', '.join(loader.MODES)}, not {mode!r}")
    target = loader.target_table(dataset, table)
    try:
        loader.check_target(target, db, mode)
    except loader.TableConflict as e:
        raise HubError("conflict", str(e))
    return target


def plan(dataset, table=None, split="train", limit=0, config=None, mode="replace", revision=None,
         db=None, builder=None):
    db = db or loader.IrisSQL()
    dataset = hub._check_id(dataset)
    split = hub._check_name(split or "train", "split")
    config = hub._check_name(config, "config", required=False)
    revision = hub._check_name(revision, "revision", required=False)
    limit = _int(limit, "limit")
    mode = (mode or "replace").strip()
    warnings, reason = [], None

    repo = hub.resolve(dataset)
    commit = loader.resolve_commit(repo, revision, lookup=lambda n, r: hub.hub_call(
        lambda: hub.hf_api().dataset_info(n, revision=r, timeout=hub.TIMEOUT).sha, n))
    builder = builder or _builder(repo, config, commit)
    try:
        loader.check_split(repo, split, config, 1, commit, builder=builder)   # split exists?
    except loader.SplitNotFound as e:
        raise HubError("not_found", str(e))

    split_rows = getattr((builder.info.splits or {}).get(split), "num_examples", None)
    if limit:
        rows, size = (min(limit, split_rows) if split_rows else limit), None
    else:
        try:
            rows, size = loader.estimate(builder, repo, split, config, commit)
            loader.check_limits(split, rows, size)
        except loader.LimitExceeded as e:
            reason = str(e)
            rows, size = split_rows, None

    target = loader.target_table(dataset, table)
    exists = db.table_exists(target)
    owned = exists and db.owns_table(target)
    if mode not in loader.MODES:
        reason = reason or f"mode must be one of {', '.join(loader.MODES)}, not {mode!r}"
    else:
        try:
            loader.check_target(target, db, mode)
        except loader.TableConflict as e:
            reason = reason or str(e)
    action = "refused" if reason else "create" if not exists else mode

    columns, ddl, source = [], None, "none"
    sample = hub.sample(repo, builder.config.name, split, SAMPLE_ROWS) if not revision else None
    if sample:
        serialized, records, source = sample["features"], sample["rows"], "viewer"
        warnings.append(f"VARCHAR lengths come from the first {len(records)} rows; "
                        f"the import measures every row.")
    elif builder.info.features is not None:
        serialized, records, source = builder.info.features.to_dict(), [], "metadata"
        warnings.append("Column types come from the dataset's metadata; text lengths show the "
                        "4000 minimum until the import measures the data.")
    else:
        serialized, records = None, []
        warnings.append("The column types are only known once the data is read.")
    if revision and source != "viewer":
        warnings.append("Sample rows are only available for the latest version, not for a revision.")

    if serialized:
        features = features_from_dict(serialized)
        media = {name for name, f in serialized.items() if hub._media(f)}
        labels = loader.label_names(features)
        data = loader.add_label_names(_sample_table(features.arrow_schema, records, media), labels)
        ddl, names, cols = loader.build_ddl(target, data, getattr(db, "text_collation", ""))
        for src, name, col in zip(data.column_names, names, cols):
            entry = {"source": src, "name": name, "type": col.sql_type}
            base = src[:-len("_name")] if src.endswith("_name") else None
            if base in labels and src not in serialized:
                entry["note"] = "label names: " + ", ".join(labels[base][:5]) + (
                    ", ..." if len(labels[base]) > 5 else "")
            elif src in media:
                entry["note"] = f"{hub._media(serialized[src])} data, stored as JSON with base64 bytes"
            elif name.lower() != src.lower():
                entry["note"] = f"renamed from {src!r}"
            columns.append(entry)
        if exists and mode == "append" and hasattr(db, "columns"):
            current = [c.lower() for c in db.columns(target)]
            if current and current != [c["name"].lower() for c in columns]:
                warnings.append(f"{target} has the columns {', '.join(db.columns(target))}; "
                                f"appending needs the same columns.")

    return {"dataset": dataset, "repo": repo, "commit": commit, "config": builder.config.name,
            "split": split, "limit": limit, "mode": mode, "rowsEstimate": rows,
            "table": target, "tableExists": exists, "ownedByLoader": bool(owned),
            "action": action, "reason": reason, "columns": columns, "ddl": ddl,
            "download": {"fullLoad": not limit, "rows": rows, "bytes": size,
                         "withinLimits": not (reason and "limit" in reason)},
            "warnings": warnings, "sample": source}


def _json_call(fn, args, allowed):
    try:
        kwargs = {k: v for k, v in json.loads(args or "{}").items() if v not in ("", None)}
        unknown = set(kwargs) - set(allowed)
        if unknown:
            raise HubError("bad_request", f"unknown parameters: {', '.join(sorted(unknown))}")
        return json.dumps(fn(**kwargs))
    except Exception as e:
        err = e if isinstance(e, HubError) else HubError("bad_request", str(e)) \
            if isinstance(e, loader.LoadError) else hub._hub_error(e, "the dataset")
        return json.dumps({"error": loader.error_text(err), "code": err.code})


def call(args="{}"):
    """JSON in, JSON out, for Loader.Plan."""
    return _json_call(plan, args, PARAMS)


def call_precheck(args="{}"):
    """JSON in, {"table": ...} or an error, for Job.Start."""
    return _json_call(lambda **kw: {"table": precheck(**kw)}, args, ("dataset", "table", "mode"))

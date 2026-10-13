"""Remember the import log, jobs and loader tables before a test run, and remove what the
run added afterwards. Runs inside IRIS (irispython); used by tests/run_all.sh --with-imports.
Usage: irispython tests/iris_snapshot.py snapshot|restore
"""
import json
import sys

import iris

STATE = "/tmp/hf2iris-snapshot.json"


def one(sql, *args):
    return list(iris.sql.exec(sql, *args))[0][0]


def owned_tables():
    g, key, out = iris.gref("^dc.hf2iris.Owned"), "", []
    while True:
        key = g.order([key])
        if not key:
            return out
        out.append(key)


def snapshot():
    state = {"log": one("SELECT COALESCE(MAX(ID), 0) FROM dc_hf2iris.ImportLog"),
             "job": one("SELECT COALESCE(MAX(ID), 0) FROM dc_hf2iris.Job"),
             "tables": owned_tables()}
    json.dump(state, open(STATE, "w"))
    print(f"snapshot: import log up to {state['log']}, jobs up to {state['job']}, {len(state['tables'])} loader tables")


def restore():
    state = json.load(open(STATE))
    logs = one("SELECT COUNT(*) FROM dc_hf2iris.ImportLog WHERE ID > ?", state["log"])
    jobs = one("SELECT COUNT(*) FROM dc_hf2iris.Job WHERE ID > ?", state["job"])
    if logs:
        iris.sql.exec("DELETE FROM dc_hf2iris.ImportLog WHERE ID > ?", state["log"])
    if jobs:
        iris.sql.exec("DELETE FROM dc_hf2iris.Job WHERE ID > ?", state["job"])
    dropped = []
    for table in owned_tables():
        if table in state["tables"]:
            continue
        schema, name = table.split(".", 1)
        if one("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE UPPER(TABLE_SCHEMA) = ? AND UPPER(TABLE_NAME) = ?",
               schema, name):
            iris.sql.exec(f"DROP TABLE {table}")
        iris.gref("^dc.hf2iris.Owned").kill([table])
        dropped.append(table)
    print(f"restore: removed {logs} import log rows, {jobs} jobs, tables: {', '.join(dropped) or 'none'}")


{"snapshot": snapshot, "restore": restore}[sys.argv[1]]()

/// One-line import of Hugging Face datasets into IRIS tables.
/// <example>
/// do ##class(dc.hf2iris.Loader).Load("stanfordnlp/imdb","Demo.IMDB",,1000)
/// </example>
Class dc.hf2iris.Loader
{

/// Load a dataset split into a table.
/// dataset : Hugging Face id, e.g. "stanfordnlp/imdb"
/// table   : target table "Schema.Table" (default HF.&lt;name&gt;)
/// split   : "train", "test", ...
/// limit   : max rows (0 = all)
/// config  : dataset subset/config name, if the dataset has several
/// mode    : "replace" | "append" | "fail" (only tables created by hf2iris are changed)
/// revision: branch, tag or commit hash ("" = latest)
ClassMethod Load(dataset As %String, table As %String = "", split As %String = "train", limit As %Integer = 0, config As %String = "", mode As %String = "replace", revision As %String = "", Output result As %DynamicObject) As %Status
{
    set sc = $$$OK, result = ""
    try {
        set result = ..Run(dataset, table, split, limit, config, mode, revision, 1)
        write !,"Loaded ",result.rows," rows into ",result.table," in ",result.seconds,"s (commit ",$extract(result.commit, 1, 7),")",!
    } catch ex {
        set sc = ex.AsStatus()
        write !,"Import failed: ",..ErrorText(ex),!
    }
    quit sc
}

/// Same as Load but returns the result object (used by the REST API, no terminal output).
ClassMethod LoadQuiet(dataset As %String, table As %String = "", split As %String = "train", limit As %Integer = 0, config As %String = "", mode As %String = "replace", revision As %String = "") As %DynamicObject
{
    quit ..Run(dataset, table, split, limit, config, mode, revision, 0)
}

/// SQL function: SELECT dc_hf2iris.load_dataset('stanfordnlp/imdb','Demo.IMDB','train',1000)
/// Trailing arguments can be left out (table, split, limit, config, mode, revision, as in Load).
/// Returns the import result as JSON with "status":"OK"; a failed import returns
/// {"status":"ERROR","error":...} instead of raising an SQL error.
ClassMethod SqlLoad(dataset As %String(MAXLEN="") = "", table As %String(MAXLEN="") = "", split As %String(MAXLEN="") = "", limit As %Integer = 0, config As %String(MAXLEN="") = "", mode As %String(MAXLEN="") = "", revision As %String(MAXLEN="") = "") As %String(MAXLEN="") [ SqlName = load_dataset, SqlProc ]
{
    try {
        set r = ..LoadQuiet(..Arg(dataset), ..Arg(table), ..Arg(split), +limit, ..Arg(config), ..Arg(mode), ..Arg(revision))
        set r.status = "OK"
    } catch ex {
        set r = {"status":"ERROR", "error":(..ErrorText(ex))}
    }
    quit r.%ToJSON()
}

/// SQL function: SELECT dc_hf2iris.dataset_info('openai/gsm8k')
/// Configs and splits of a dataset as JSON, without downloading data.
ClassMethod SqlInfo(dataset As %String(MAXLEN="") = "") As %String(MAXLEN="") [ SqlName = dataset_info, SqlProc ]
{
    try { set r = ..Info(..Arg(dataset)) }
    catch ex { set r = {"error":(..ErrorText(ex))} }
    quit r.%ToJSON()
}

/// Import plan (dry run): what Load would create, as JSON. Nothing is written and no
/// data files are downloaded. args: {dataset, table, split, limit, config, mode, revision}
ClassMethod Plan(args As %DynamicObject) As %String
{
    quit ..PlanPy(args.%ToJSON())
}

/// List configs and splits of a dataset without downloading the data.
ClassMethod Info(dataset As %String) As %DynamicObject
{
    if $zstrip(dataset, "<>W") = "" $$$ThrowStatus($$$ERROR($$$GeneralError, "dataset is required"))
    set r = {}.%FromJSON(..InfoPy(dataset))
    if r.error '= "" $$$ThrowStatus($$$ERROR($$$GeneralError, r.error))
    quit r
}

/// The error message without the "ERROR #5001: " prefix of general errors.
ClassMethod ErrorText(ex As %Exception.AbstractException) As %String
{
    set t = $system.Status.GetErrorText(ex.AsStatus())
    quit $select($extract(t, 1, 13) = "ERROR #5001: ":$extract(t, 14, *), 1:t)
}

/// SQL passes NULL as "" and '' as $c(0); both mean "not given".
ClassMethod Arg(v As %String) As %String [ Internal ]
{
    quit $select(v = $char(0):"", 1:$zstrip(v, "<>W"))
}

/// The import behind Load, LoadQuiet, SqlLoad and background jobs. Only one import at a
/// time may write to a table. jobId: report progress to dc.hf2iris.Job and honour cancel.
ClassMethod Run(dataset, table, split, limit, config, mode, revision, verbose, jobId As %String = "", Output logId As %String, Output cancelled As %Boolean) As %DynamicObject [ Internal ]
{
    set:split="" split = "train"
    set:mode="" mode = "replace"
    set (logId, locked) = "", cancelled = 0
    try {
        if $zstrip(dataset, "<>W") = "" $$$ThrowStatus($$$ERROR($$$GeneralError, "dataset is required"))
        set target = ..TargetPy(dataset, table)
        lock +^dc.hf2iris.Table($zconvert(target, "U")):0
        if '$test $$$ThrowStatus($$$ERROR($$$GeneralError, "Another import into "_target_" is running. Try again when it has finished."))
        set locked = $zconvert(target, "U")
        set result = {}.%FromJSON(..LoadPy(dataset, table, split, +limit, config, mode, revision, verbose, jobId))
        if result.error '= "" {
            set cancelled = +result.cancelled
            $$$ThrowStatus($$$ERROR($$$GeneralError, result.error))
        }
        do ##class(dc.hf2iris.ImportLog).Record(result, "OK", "", .logId)
    } catch ex {
        do ##class(dc.hf2iris.ImportLog).Record({"dataset":(dataset),"config":(config),"split":(split),"table":(table),"revision":(revision)}, "ERROR", ..ErrorText(ex), .logId)
        lock:locked'="" -^dc.hf2iris.Table(locked)
        throw ex
    }
    lock -^dc.hf2iris.Table(locked)
    quit result
}

ClassMethod TargetPy(dataset, table) As %String [ Internal, Language = python ]
{
from hf2iris import loader
return loader.target_table(dataset, table or None)
}

ClassMethod PlanPy(args As %String) As %String [ Internal, Language = python ]
{
from hf2iris import plan
return plan.call(args)
}

ClassMethod LoadPy(dataset, table, split, limit, config, mode, revision, verbose, jobId = "") As %String [ Language = python ]
{
import json, hf2iris
log = print if verbose else (lambda *a: None)
progress = cancelled = None
if jobId:
    import iris
    job = iris.cls("dc.hf2iris.Job")

    def progress(phase, done=0, total=0):
        job.Progress(jobId, phase, done or 0, total or 0)

    def cancelled():
        return bool(job.IsCancelled(jobId))
try:
    r = hf2iris.load(dataset, table or None, split or "train", config or None,
                     int(limit or 0) or None, mode or "replace", log=log, revision=revision or None,
                     progress=progress, cancelled=cancelled)
except Exception as e:
    r = {"error": hf2iris.error_text(e), "cancelled": isinstance(e, hf2iris.loader.Cancelled)}
return json.dumps(r, default=str)
}

ClassMethod InfoPy(dataset) As %String [ Language = python ]
{
import json, hf2iris
try:
    r = hf2iris.info(dataset)
except Exception as e:
    r = {"error": hf2iris.error_text(e)}
return json.dumps(r)
}

}

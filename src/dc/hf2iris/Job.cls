/// Background imports. Start() returns at once; the import runs in its own process
/// through the same code as Loader.Load and reports its progress here. Every method
/// returns JSON; errors come back as {"error":...,"code":...}.
/// After creation, rows are only changed with column UPDATEs, so the import process
/// and a cancel request from another process never overwrite each other.
Class dc.hf2iris.Job Extends %Persistent
{

Property State As %String(VALUELIST = ",queued,running,ok,error,cancelled,interrupted") [ InitialExpression = "queued" ];

/// resolving, fetching, inserting, finishing
Property Phase As %String(MAXLEN = 20);

Property RowsDone As %Integer [ InitialExpression = 0 ];

Property RowsTotal As %Integer [ InitialExpression = 0 ];

Property Dataset As %String(MAXLEN = 200);

Property TableName As %String(MAXLEN = 200);

Property Split As %String(MAXLEN = 100);

Property Config As %String(MAXLEN = 200);

Property Mode As %String(MAXLEN = 20);

Property Revision As %String(MAXLEN = 200);

Property RowLimit As %Integer [ InitialExpression = 0 ];

Property Username As %String(MAXLEN = 160);

Property CreatedAt As %TimeStamp [ InitialExpression = {$zdatetime($horolog,3)} ];

Property StartedAt As %TimeStamp;

Property FinishedAt As %TimeStamp;

Property CancelRequested As %Boolean [ InitialExpression = 0 ];

/// The same JSON that POST /load returns
Property Result As %String(MAXLEN = "");

Property Error As %String(MAXLEN = 2000);

Property ImportLogId As %String(MAXLEN = 20);

Property Pid As %String(MAXLEN = 20);

Index StateIdx On State;

/// args: {dataset, table, split, limit, config, mode, revision}, like POST /load
ClassMethod Start(args As %DynamicObject) As %String
{
    try {
        set check = {}.%FromJSON(..PrecheckPy({"dataset":(args.dataset), "table":(args.table), "mode":(args.mode)}.%ToJSON()))
        if check.code '= "" return check.%ToJSON()
        do ..Reap()
        &sql(SELECT COUNT(*) INTO :running FROM dc_hf2iris.Job WHERE State IN ('queued', 'running'))
        if running >= ..MaxJobs() {
            return {"error":($select(running=1:"One import is", 1:running_" imports are")_" already running. Try again when one has finished."), "code":"busy"}.%ToJSON()
        }
        set job = ..%New()
        set job.Dataset = args.dataset, job.TableName = check.table, job.Split = $select(args.split="":"train", 1:args.split)
        set job.Config = args.config, job.Mode = $select(args.mode="":"replace", 1:args.mode)
        set job.Revision = args.revision, job.RowLimit = +args.limit, job.Username = $username
        $$$ThrowOnError(job.%Save())
        set id = job.%Id()
        job ##class(dc.hf2iris.Job).Run(id)::10
        if '$test {
            set now = ..Now(), msg = "Could not start a background process"
            &sql(UPDATE dc_hf2iris.Job SET State = 'error', Error = :msg, FinishedAt = :now WHERE ID = :id)
        }
        return ..Get(id)
    } catch ex {
        return {"error":(##class(dc.hf2iris.Loader).ErrorText(ex)), "code":"upstream"}.%ToJSON()
    }
}

/// Runs in the background process started by Start()
ClassMethod Run(id As %String)
{
    lock +^dc.hf2iris.Job(id):10
    if '$test quit
    &sql(SELECT State, CancelRequested, Dataset, TableName, Split, RowLimit, Config, Mode, Revision
         INTO :state, :cancel, :dataset, :table, :split, :limit, :config, :mode, :revision
         FROM dc_hf2iris.Job WHERE ID = :id)
    if (SQLCODE '= 0) || (state '= "queued") { lock -^dc.hf2iris.Job(id) quit }
    set now = ..Now(), pid = $job
    if cancel {
        set msg = "Import cancelled before it started"
        &sql(UPDATE dc_hf2iris.Job SET State = 'cancelled', Error = :msg, StartedAt = :now, FinishedAt = :now WHERE ID = :id)
        lock -^dc.hf2iris.Job(id)
        quit
    }
    &sql(UPDATE dc_hf2iris.Job SET State = 'running', Phase = 'resolving', StartedAt = :now, Pid = :pid WHERE ID = :id)
    set (logId, cancelled) = ""
    try {
        set result = ##class(dc.hf2iris.Loader).Run(dataset, table, split, limit, config, mode, revision, 0, id, .logId, .cancelled)
        set json = result.%ToJSON(), rows = result.rows
        &sql(UPDATE dc_hf2iris.Job SET State = 'ok', Result = :json, RowsDone = :rows, RowsTotal = :rows WHERE ID = :id)
    } catch ex {
        set state = $select(cancelled:"cancelled", 1:"error"), msg = $extract(##class(dc.hf2iris.Loader).ErrorText(ex), 1, 2000)
        &sql(UPDATE dc_hf2iris.Job SET State = :state, Error = :msg WHERE ID = :id)
    }
    set now = ..Now()
    &sql(UPDATE dc_hf2iris.Job SET Phase = NULL, ImportLogId = :logId, FinishedAt = :now WHERE ID = :id)
    lock -^dc.hf2iris.Job(id)
}

/// Called by the import (Python) after each batch
ClassMethod Progress(id As %String, phase As %String, done As %Integer = 0, total As %Integer = 0)
{
    &sql(UPDATE dc_hf2iris.Job SET Phase = :phase, RowsDone = :done, RowsTotal = :total WHERE ID = :id)
}

ClassMethod IsCancelled(id As %String) As %Boolean
{
    &sql(SELECT CancelRequested INTO :cancel FROM dc_hf2iris.Job WHERE ID = :id)
    quit (SQLCODE = 0) && cancel
}

/// The import stops before its next batch of rows; a download in progress is not interrupted
ClassMethod Cancel(id As %String) As %String
{
    do ..Reap()
    &sql(SELECT State INTO :state FROM dc_hf2iris.Job WHERE ID = :id)
    if SQLCODE '= 0 quit ..NotFound(id)
    if (state '= "queued") && (state '= "running") {
        quit {"error":("Job "_id_" has already finished ("_state_")."), "code":"conflict"}.%ToJSON()
    }
    &sql(UPDATE dc_hf2iris.Job SET CancelRequested = 1 WHERE ID = :id)
    quit ..Get(id)
}

ClassMethod Get(id As %String) As %String
{
    do ..Reap()
    set rs = ##class(%SQL.Statement).%ExecDirect(, ..#SELECT_" WHERE ID = ?", id)
    quit $select(rs.%Next():..Row(rs).%ToJSON(), 1:..NotFound(id))
}

ClassMethod List(limit As %Integer = 50) As %String
{
    do ..Reap()
    set limit = $select(+limit < 1:50, +limit > 200:200, 1:+limit), items = []
    set rs = ##class(%SQL.Statement).%ExecDirect(, $replace(..#SELECT, "SELECT ", "SELECT TOP "_limit_" ")_" ORDER BY ID DESC")
    while rs.%Next() { do items.%Push(..Row(rs)) }
    quit {"items":(items), "count":(items.%Size())}.%ToJSON()
}

/// Running or queued jobs whose process is gone (IRIS restarted, process killed)
/// are marked interrupted; their transaction was rolled back by IRIS.
ClassMethod Reap()
{
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT ID, State, DATEDIFF('s', CreatedAt, CURRENT_TIMESTAMP) AS age FROM dc_hf2iris.Job WHERE State IN ('queued', 'running')")
    while rs.%Next() {
        set id = rs.ID
        if (rs.State = "queued") && (rs.age < 60) continue      // still starting
        lock +^dc.hf2iris.Job(id):0
        if '$test continue                                      // its process holds the lock
        set now = ..Now(), msg = "The import process ended unexpectedly (for example, IRIS was restarted)."
        &sql(UPDATE dc_hf2iris.Job SET State = 'interrupted', Phase = NULL, Error = :msg, FinishedAt = :now
             WHERE ID = :id AND State IN ('queued', 'running'))
        lock -^dc.hf2iris.Job(id)
    }
}

Parameter SELECT = "SELECT ID, State, Phase, RowsDone, RowsTotal, Dataset, TableName, Split, Config, Mode, Revision, RowLimit, Username, CreatedAt, StartedAt, FinishedAt, CancelRequested, Result, Error, ImportLogId, DATEDIFF('s', StartedAt, COALESCE(FinishedAt, CURRENT_TIMESTAMP)) AS Elapsed FROM dc_hf2iris.Job";

ClassMethod Row(rs) As %DynamicObject [ Private ]
{
    set o = {"id":(rs.ID), "state":(rs.State), "phase":(rs.Phase), "rowsDone":(+rs.RowsDone), "rowsTotal":(+rs.RowsTotal),
        "dataset":(rs.Dataset), "table":(rs.TableName), "split":(rs.Split), "config":(rs.Config), "mode":(rs.Mode),
        "revision":(rs.Revision), "limit":(+rs.RowLimit), "user":(rs.Username), "createdAt":(rs.CreatedAt),
        "startedAt":(rs.StartedAt), "finishedAt":(rs.FinishedAt), "error":(rs.Error), "importLogId":(rs.ImportLogId)}
    do o.%Set("cancelRequested", +rs.CancelRequested, "boolean")
    do o.%Set("elapsed", $select(rs.StartedAt="":"", 1:+rs.Elapsed), $select(rs.StartedAt="":"null", 1:"number"))
    if rs.Result '= "" { do o.%Set("result", {}.%FromJSON(rs.Result)) } else { do o.%Set("result", "", "null") }
    for key = "phase", "config", "revision", "startedAt", "finishedAt", "error", "importLogId" {
        do:o.%Get(key)="" o.%Set(key, "", "null")
    }
    quit o
}

ClassMethod NotFound(id As %String) As %String [ Private ]
{
    quit {"error":("Job "_id_" not found."), "code":"not_found"}.%ToJSON()
}

ClassMethod MaxJobs() As %Integer [ Private ]
{
    set n = +$system.Util.GetEnviron("HF2IRIS_MAX_JOBS")
    quit $select(n > 0:n, 1:2)
}

ClassMethod Now() As %String [ Private ]
{
    quit $zdatetime($horolog, 3)
}

ClassMethod PrecheckPy(args As %String) As %String [ Internal, Language = python ]
{
from hf2iris import plan
return plan.call_precheck(args)
}

}

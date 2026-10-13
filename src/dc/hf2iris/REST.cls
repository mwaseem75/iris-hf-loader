/// REST API behind the web UI.  Mounted at /api/hf2iris
Class dc.hf2iris.REST Extends %CSP.REST
{

Parameter CONTENTTYPE = "application/json";

Parameter CHARSET = "utf-8";

XData UrlMap [ XMLNamespace = "http://www.intersystems.com/urlmap" ]
{
<Routes>
  <Route Url="/info" Method="GET" Call="Info"/>
  <Route Url="/load" Method="POST" Call="Load"/>
  <Route Url="/history" Method="GET" Call="History"/>
  <Route Url="/preview/:table" Method="GET" Call="Preview"/>
  <Route Url="/datasets" Method="GET" Call="SearchDatasets"/>
  <Route Url="/dataset" Method="GET" Call="DatasetDetails"/>
  <Route Url="/dataset/preview" Method="GET" Call="DatasetPreview"/>
  <Route Url="/status" Method="GET" Call="Status"/>
  <Route Url="/plan" Method="POST" Call="PlanImport"/>
  <Route Url="/jobs" Method="POST" Call="StartJob"/>
  <Route Url="/jobs" Method="GET" Call="ListJobs"/>
  <Route Url="/jobs/:id" Method="GET" Call="GetJob"/>
  <Route Url="/jobs/:id/cancel" Method="POST" Call="CancelJob"/>
  <Route Url="/tables" Method="GET" Call="ListTables"/>
  <Route Url="/tables/:table" Method="GET" Call="TableDetail"/>
</Routes>
}

ClassMethod Info() As %Status
{
    set ds = $get(%request.Data("dataset", 1))
    if ds = "" quit ..JsonError(400, "dataset is required")
    try { write ##class(dc.hf2iris.Loader).Info(ds).%ToJSON() }
    catch ex { return ..JsonError(400, ##class(dc.hf2iris.Loader).ErrorText(ex)) }
    quit $$$OK
}

ClassMethod Load() As %Status
{
    set b = {}.%FromJSON(%request.Content)
    if b.dataset = "" quit ..JsonError(400, "dataset is required")
    try {
        set r = ##class(dc.hf2iris.Loader).LoadQuiet(b.dataset, b.table, b.split, +b.limit, b.config, b.mode, b.revision)
        write r.%ToJSON()
    } catch ex { return ..JsonError(400, ##class(dc.hf2iris.Loader).ErrorText(ex)) }
    quit $$$OK
}

ClassMethod History() As %Status
{
    set out = []
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT TOP 50 ID, Dataset, Config, Split, TableName, RowCount, Seconds, Status, Message, CreatedAt, Revision, CommitHash "_
        "FROM dc_hf2iris.ImportLog ORDER BY ID DESC")
    while rs.%Next() {
        do out.%Push({"id":(rs.ID),"dataset":(rs.Dataset),"config":(rs.Config),"split":(rs.Split),
            "table":(rs.TableName),"rows":(rs.RowCount),"seconds":(rs.Seconds),
            "status":(rs.Status),"message":(rs.Message),"createdAt":(rs.CreatedAt),
            "revision":(rs.Revision),"commit":(rs.CommitHash)})
    }
    write out.%ToJSON()
    quit $$$OK
}

ClassMethod Preview(table As %String) As %Status
{
    if '##class(dc.hf2iris.ImportLog).IsKnownTable(table) quit ..JsonError(404, "unknown table")
    set n = $get(%request.Data("rows", 1), 20), n = $select(n > 200:200, n < 1:20, 1:+n)
    // optional paging; without offset the query and the response are as before
    set offset = $get(%request.Data("offset", 1))
    if offset '= "" {
        set offset = $select(offset?1.9N:+offset, 1:0)
        set rs = ##class(%SQL.Statement).%ExecDirect(,
            "SELECT * FROM (SELECT TOP ALL * FROM "_table_" ORDER BY %ID) WHERE %VID BETWEEN ? AND ?", offset + 1, offset + n)
    } else {
        set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TOP "_n_" * FROM "_table)
    }
    if rs.%SQLCODE < 0 quit ..JsonError(500, rs.%Message)
    set meta = rs.%GetMetadata(), cols = []
    set lobs = ""
    for i = 1:1:meta.columnCount {
        do cols.%Push(meta.columns.GetAt(i).colName)
        // clientType 12/13 = binary/character stream: %GetData returns an OID, not the text
        set:$listfind($listbuild(12, 13), meta.columns.GetAt(i).clientType) lobs = lobs_$listbuild(i)
    }
    set rows = []
    while rs.%Next() {
        set row = []
        for i = 1:1:meta.columnCount {
            set v = rs.%GetData(i)
            if $listfind(lobs, i), v '= "" { set s = ##class(%Stream.Object).%Open(v), v = $select($isobject(s):s.Read(300), 1:"") }
            do row.%Push($extract(v, 1, 300))
        }
        do rows.%Push(row)
    }
    set cnt = ##class(%SQL.Statement).%ExecDirect(, "SELECT COUNT(*) AS c FROM "_table)
    do cnt.%Next()
    set out = {"table":(table),"total":(cnt.c),"columns":(cols),"rows":(rows)}
    set:offset'="" out.offset = offset
    write out.%ToJSON()
    quit $$$OK
}

/// Hugging Face search: ?search=&sort=&task=&size=&language=&limit=&refresh=
ClassMethod SearchDatasets() As %Status
{
    quit ..Reply(##class(dc.hf2iris.Hub).Search(..Param("search"), ..Param("sort"), ..Param("task"),
        ..Param("size"), ..Param("language"), ..Param("limit"), ..Param("refresh")))
}

/// Dataset details: ?id=owner/name&revision=&refresh=
ClassMethod DatasetDetails() As %Status
{
    quit ..Reply(##class(dc.hf2iris.Hub).Details(..Param("id"), ..Param("revision"), ..Param("refresh")))
}

/// Rows of a dataset on the Hub: ?id=&config=&split=&offset=&rows=&source=viewer|stream&refresh=
ClassMethod DatasetPreview() As %Status
{
    quit ..Reply(##class(dc.hf2iris.Hub).Preview(..Param("id"), ..Param("config"), ..Param("split"),
        ..Param("offset"), ..Param("rows"), ..Param("source"), ..Param("refresh")))
}

ClassMethod Status() As %Status
{
    quit ..Reply(##class(dc.hf2iris.Hub).Status())
}

/// Import plan (dry run), body like POST /load
ClassMethod PlanImport() As %Status
{
    set body = ..Body()
    if '$isobject(body) quit ..JsonError(400, "The request body must be a JSON object", "bad_request")
    quit ..Reply(##class(dc.hf2iris.Loader).Plan(body))
}

/// Background import, body like POST /load; answers 202 with the job
ClassMethod StartJob() As %Status
{
    set body = ..Body()
    if '$isobject(body) quit ..JsonError(400, "The request body must be a JSON object", "bad_request")
    quit ..Reply(##class(dc.hf2iris.Job).Start(body), "202 Accepted")
}

ClassMethod ListJobs() As %Status
{
    quit ..Reply(##class(dc.hf2iris.Job).List(..Param("limit")))
}

ClassMethod GetJob(id As %String) As %Status
{
    if id '? 1.9N quit ..JsonError(404, "Job "_id_" not found.", "not_found")
    quit ..Reply(##class(dc.hf2iris.Job).Get(id))
}

ClassMethod CancelJob(id As %String) As %Status
{
    if id '? 1.9N quit ..JsonError(404, "Job "_id_" not found.", "not_found")
    quit ..Reply(##class(dc.hf2iris.Job).Cancel(id))
}

ClassMethod ListTables() As %Status
{
    quit ..Reply(##class(dc.hf2iris.Tables).List())
}

ClassMethod TableDetail(table As %String) As %Status
{
    quit ..Reply(##class(dc.hf2iris.Tables).Detail(table))
}

ClassMethod Body() As %DynamicObject [ Private ]
{
    try { set body = {}.%FromJSON(%request.Content) } catch { set body = "" }
    quit $select($isobject(body) && body.%IsA("%DynamicObject"):body, 1:"")
}

ClassMethod Param(name As %String) As %String [ Private ]
{
    quit $get(%request.Data(name, 1))
}

/// Writes a Hub result, or maps its error code to an HTTP status
ClassMethod Reply(json As %String, okStatus As %String = "") As %Status [ Private ]
{
    set r = {}.%FromJSON(json)
    if r.code '= "" {
        quit ..JsonError($case(r.code, "bad_request":400, "not_found":404, "gated":403,
            "preview_unavailable":409, "conflict":409, "rate_limited":429, "busy":429,
            "timeout":504, :502), r.error, r.code)
    }
    set:okStatus'="" %response.Status = okStatus
    write json
    quit $$$OK
}

ClassMethod JsonError(code As %Integer, msg As %String, errorCode As %String = "") As %Status
{
    set %response.Status = code_" "_$case(code, 400:"Bad Request", 403:"Forbidden", 404:"Not Found",
        409:"Conflict", 429:"Too Many Requests", 502:"Bad Gateway", 504:"Gateway Timeout", :"Error")
    set body = {"error":(msg)}
    set:errorCode'="" body.code = errorCode
    write body.%ToJSON()
    quit $$$OK
}

}

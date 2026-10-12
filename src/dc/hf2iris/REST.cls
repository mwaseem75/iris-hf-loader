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
</Routes>
}

ClassMethod Info() As %Status
{
    set ds = $get(%request.Data("dataset", 1))
    if ds = "" quit ..JsonError(400, "dataset is required")
    try { write ##class(dc.hf2iris.Loader).Info(ds).%ToJSON() }
    catch ex { return ..JsonError(502, $system.Status.GetErrorText(ex.AsStatus())) }
    quit $$$OK
}

ClassMethod Load() As %Status
{
    set b = {}.%FromJSON(%request.Content)
    if b.dataset = "" quit ..JsonError(400, "dataset is required")
    try {
        set r = ##class(dc.hf2iris.Loader).LoadQuiet(b.dataset, b.table, b.split, +b.limit, b.config, b.mode)
        write r.%ToJSON()
    } catch ex { return ..JsonError(500, $system.Status.GetErrorText(ex.AsStatus())) }
    quit $$$OK
}

ClassMethod History() As %Status
{
    set out = []
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT TOP 50 ID, Dataset, Config, Split, TableName, RowCount, Seconds, Status, Message, CreatedAt "_
        "FROM dc_hf2iris.ImportLog ORDER BY ID DESC")
    while rs.%Next() {
        do out.%Push({"id":(rs.ID),"dataset":(rs.Dataset),"config":(rs.Config),"split":(rs.Split),
            "table":(rs.TableName),"rows":(rs.RowCount),"seconds":(rs.Seconds),
            "status":(rs.Status),"message":(rs.Message),"createdAt":(rs.CreatedAt)})
    }
    write out.%ToJSON()
    quit $$$OK
}

ClassMethod Preview(table As %String) As %Status
{
    if '##class(dc.hf2iris.ImportLog).IsKnownTable(table) quit ..JsonError(404, "unknown table")
    set n = $get(%request.Data("rows", 1), 20), n = $select(n > 200:200, n < 1:20, 1:+n)
    set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TOP "_n_" * FROM "_table)
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
    write {"table":(table),"total":(cnt.c),"columns":(cols),"rows":(rows)}.%ToJSON()
    quit $$$OK
}

ClassMethod JsonError(code As %Integer, msg As %String) As %Status
{
    set %response.Status = code_" "_$case(code, 400:"Bad Request", 404:"Not Found", 502:"Bad Gateway", :"Error")
    write {"error":(msg)}.%ToJSON()
    quit $$$OK
}

}

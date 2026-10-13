/// Tables created by the loader: list and details for the web UI. Only tables marked
/// in ^dc.hf2iris.Owned, or with a successful ImportLog entry (older tables), are shown.
/// Every method returns JSON; errors come back as {"error":...,"code":...}.
Class dc.hf2iris.Tables
{

ClassMethod List() As %String
{
    set items = []
    set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TABLE_SCHEMA AS s, TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES")
    while rs.%Next() {
        set name = rs.s_"."_rs.t
        continue:'..IsOwned(name)
        set item = ..Summary(name)
        do:$isobject(item) items.%Push(item)
    }
    // newest import first
    set sorted = [], n = items.%Size()
    for i = 0:1:n-1 { set key(items.%Get(i).lastImport_" ", i) = "" }
    set k = "" for { set k = $order(key(k), -1) quit:k=""  set i = "" for { set i = $order(key(k, i)) quit:i=""  do sorted.%Push(items.%Get(i)) } }
    quit {"items":(sorted), "count":(n)}.%ToJSON()
}

ClassMethod Detail(table As %String) As %String
{
    set name = ..Resolve(table)
    if name = "" quit {"error":("Table "_table_" was not found among the tables created by hf2iris."), "code":"not_found"}.%ToJSON()
    set out = ..Summary(name), cols = []
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT COLUMN_NAME AS c, DATA_TYPE AS t, CHARACTER_MAXIMUM_LENGTH AS l, NUMERIC_PRECISION AS p, NUMERIC_SCALE AS sc "_
        "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
        $piece(name, "."), $piece(name, ".", 2))
    while rs.%Next() { do cols.%Push({"name":(rs.c), "type":(..TypeName(rs.t, rs.l, rs.p, rs.sc))}) }
    set out.columns = cols, imports = []
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT TOP 10 ID, Dataset, Config, Split, RowCount, Status, Message, CreatedAt, CommitHash FROM dc_hf2iris.ImportLog "_
        "WHERE UPPER(TableName) = ? ORDER BY ID DESC", $zconvert(name, "U"))
    while rs.%Next() {
        do imports.%Push({"id":(rs.ID), "dataset":(rs.Dataset), "config":(rs.Config), "split":(rs.Split), "rows":(rs.RowCount),
            "status":(rs.Status), "message":(rs.Message), "createdAt":(rs.CreatedAt), "commit":(rs.CommitHash)})
    }
    set out.imports = imports
    quit out.%ToJSON()
}

/// The exact Schema.Table name of a loader table, or "" if it is not one or does not exist
ClassMethod Resolve(table As %String) As %String
{
    if table '? 1(1A,1"_").(1AN,1"_")1"."1(1A,1"_").(1AN,1"_") quit ""
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT TABLE_SCHEMA AS s, TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES WHERE UPPER(TABLE_SCHEMA) = ? AND UPPER(TABLE_NAME) = ?",
        $zconvert($piece(table, "."), "U"), $zconvert($piece(table, ".", 2), "U"))
    if 'rs.%Next() quit ""
    set name = rs.s_"."_rs.t
    quit $select(..IsOwned(name):name, 1:"")
}

ClassMethod IsOwned(name As %String) As %Boolean
{
    set upper = $zconvert(name, "U")
    if $data(^dc.hf2iris.Owned(upper)) quit 1
    &sql(SELECT COUNT(*) INTO :n FROM dc_hf2iris.ImportLog WHERE UPPER(TableName) = :upper AND Status = 'OK')
    quit (SQLCODE = 0) && (n > 0)
}

ClassMethod Summary(name As %String) As %DynamicObject [ Private ]
{
    set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT COUNT(*) AS n FROM "_name)
    quit:rs.%SQLCODE<0 ""
    do rs.%Next()
    set out = {"table":(name), "rows":(+rs.n)}
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
        $piece(name, "."), $piece(name, ".", 2))
    do rs.%Next()
    set out.columnCount = +rs.n
    set rs = ##class(%SQL.Statement).%ExecDirect(,
        "SELECT TOP 1 Dataset, Config, Split, CommitHash, CreatedAt FROM dc_hf2iris.ImportLog "_
        "WHERE UPPER(TableName) = ? AND Status = 'OK' ORDER BY ID DESC", $zconvert(name, "U"))
    if rs.%Next() {
        set out.dataset = rs.Dataset, out.config = rs.Config, out.split = rs.Split
        set out.commit = rs.CommitHash, out.lastImport = rs.CreatedAt
    }
    for key = "dataset", "config", "split", "commit", "lastImport" { do:out.%Get(key)="" out.%Set(key, "", "null") }
    quit out
}

ClassMethod TypeName(type As %String, length As %String, precision As %String, scale As %String) As %String [ Private ]
{
    set t = $zconvert(type, "U")
    if (t = "VARCHAR") && (length '= "") quit t_"("_length_")"
    if (t = "NUMERIC") && (precision '= "") quit t_"("_precision_","_+scale_")"
    quit t
}

}

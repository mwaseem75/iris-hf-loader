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
/// mode    : "replace" | "append" | "fail"
ClassMethod Load(dataset As %String, table As %String = "", split As %String = "train", limit As %Integer = 0, config As %String = "", mode As %String = "replace", Output result As %DynamicObject) As %Status
{
    set sc = $$$OK, result = ""
    try {
        set json = ..LoadPy(dataset, table, split, limit, config, mode, 1)
        set result = {}.%FromJSON(json)
        write !,"Loaded ",result.rows," rows into ",result.table," in ",result.seconds,"s",!
        do ##class(dc.hf2iris.ImportLog).Record(result, "OK", "")
    } catch ex {
        set sc = ex.AsStatus()
        set msg = $system.Status.GetErrorText(sc)
        write !,"Import failed: ",msg,!
        do ##class(dc.hf2iris.ImportLog).Record({"dataset":(dataset),"config":(config),"split":(split),"table":(table)}, "ERROR", msg)
    }
    quit sc
}

/// Same as Load but returns the result object (used by the REST API, no terminal output).
ClassMethod LoadQuiet(dataset As %String, table As %String = "", split As %String = "train", limit As %Integer = 0, config As %String = "", mode As %String = "replace") As %DynamicObject
{
    try {
        set result = {}.%FromJSON(..LoadPy(dataset, table, split, limit, config, mode, 0))
        do ##class(dc.hf2iris.ImportLog).Record(result, "OK", "")
    } catch ex {
        set msg = $system.Status.GetErrorText(ex.AsStatus())
        do ##class(dc.hf2iris.ImportLog).Record({"dataset":(dataset),"config":(config),"split":(split),"table":(table)}, "ERROR", msg)
        throw ex
    }
    quit result
}

/// List configs and splits of a dataset without downloading the data.
ClassMethod Info(dataset As %String) As %DynamicObject
{
    quit {}.%FromJSON(..InfoPy(dataset))
}

ClassMethod LoadPy(dataset, table, split, limit, config, mode, verbose) As %String [ Language = python ]
{
import json, hf2iris
log = print if verbose else (lambda *a: None)
r = hf2iris.load(dataset, table or None, split or "train", config or None,
                 int(limit or 0) or None, mode or "replace", log=log)
return json.dumps(r, default=str)
}

ClassMethod InfoPy(dataset) As %String [ Language = python ]
{
import json, hf2iris
return json.dumps(hf2iris.info(dataset))
}

}

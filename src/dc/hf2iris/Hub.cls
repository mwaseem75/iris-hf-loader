/// Read-only discovery of Hugging Face datasets (search, details, row preview) for the
/// web UI. Every method returns JSON; errors come back as {"error":...,"code":...}.
/// The work is done in Python (hf2iris.hub); HF_TOKEN never leaves the server.
Class dc.hf2iris.Hub
{

ClassMethod Search(search As %String = "", sort As %String = "", task As %String = "", size As %String = "", language As %String = "", limit As %String = "", refresh As %String = "") As %String
{
    quit ..Call("search", {"search":(search), "sort":(sort), "task":(task), "size":(size), "language":(language), "limit":(limit), "refresh":(refresh)})
}

ClassMethod Details(id As %String = "", revision As %String = "", refresh As %String = "") As %String
{
    quit ..Call("details", {"id":(id), "revision":(revision), "refresh":(refresh)})
}

ClassMethod Preview(id As %String = "", config As %String = "", split As %String = "", offset As %String = "", rows As %String = "", source As %String = "", refresh As %String = "") As %String
{
    quit ..Call("preview", {"id":(id), "config":(config), "split":(split), "offset":(offset), "rows":(rows), "source":(source), "refresh":(refresh)})
}

/// Package and IRIS version, limits, and whether HF_TOKEN is set (never its value)
ClassMethod Status() As %String
{
    set r = {}.%FromJSON(..Call("status", {}))
    set:r.error="" r.iris = $system.Version.GetNumber()_" (Build "_$system.Version.GetBuildNumber()_")"
    quit r.%ToJSON()
}

ClassMethod Call(name As %String, args As %DynamicObject) As %String [ Internal ]
{
    quit ..CallPy(name, args.%ToJSON())
}

ClassMethod CallPy(name As %String, args As %String) As %String [ Internal, Language = python ]
{
from hf2iris import hub
return hub.call(name, args)
}

}

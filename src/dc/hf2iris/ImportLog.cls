/// History of every import run.
Class dc.hf2iris.ImportLog Extends %Persistent
{

Property Dataset As %String(MAXLEN = 200);

Property Config As %String(MAXLEN = 200);

Property Split As %String(MAXLEN = 100);

Property TableName As %String(MAXLEN = 200);

Property RowCount As %Integer;

Property Seconds As %Numeric(SCALE = 2);

Property Status As %String(VALUELIST = ",OK,ERROR");

Property Message As %String(MAXLEN = 2000);

Property ColumnsJSON As %String(MAXLEN = "");

/// Branch, tag or commit asked for ("" = latest)
Property Revision As %String(MAXLEN = 200);

/// Commit hash the data was read from
Property CommitHash As %String(MAXLEN = 40);

/// {"column": ["name0", "name1", ...]} for class-label columns
Property ClassLabels As %String(MAXLEN = "");

Property CreatedAt As %TimeStamp [ InitialExpression = {$zdatetime($horolog,3)} ];

Index TableIdx On TableName;

ClassMethod Record(r As %DynamicObject, status As %String, msg As %String) As %Status
{
    set o = ..%New()
    set o.Dataset = r.dataset, o.Config = r.config, o.Split = r.split
    set o.Revision = r.revision, o.CommitHash = r.commit
    set o.TableName = r.table, o.RowCount = r.rows, o.Seconds = r.seconds
    set o.Status = status, o.Message = $extract(msg, 1, 2000)
    set:$isobject(r.columns) o.ColumnsJSON = r.columns.%ToJSON()
    set:$isobject(r.classLabels) o.ClassLabels = r.classLabels.%ToJSON()
    quit o.%Save()
}

/// Only tables created by this tool may be previewed through the API.
ClassMethod IsKnownTable(table As %String) As %Boolean
{
    &sql(SELECT COUNT(*) INTO :n FROM dc_hf2iris.ImportLog WHERE TableName = :table AND Status = 'OK')
    quit (SQLCODE = 0) && (n > 0)
}

}

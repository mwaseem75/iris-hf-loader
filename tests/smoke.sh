#!/usr/bin/env bash
# Loads real datasets through dc_hf2iris.load_dataset (needs internet, a few minutes).
# Tables go to schema HFSmoke and are dropped at the end.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
if docker compose version >/dev/null 2>&1; then dc=(docker compose); else dc=(docker-compose); fi

out=$("${dc[@]}" exec -T iris iris session IRIS -U USER <<'OS'
 set pin = "aa13bc287fa6fcab6daf52f0dfb9994269ffea28"
 // name, dataset, table, split, limit, config, mode, revision, expected status, expected rows or error text
 set c($i(c)) = $lb("imdb","stanfordnlp/imdb","HFSmoke.imdb","train",200,"","","","OK",200)
 set c($i(c)) = $lb("iris full","scikit-learn/iris","HFSmoke.iris","train",0,"","","","OK",150)
 set c($i(c)) = $lb("gsm8k config","openai/gsm8k","HFSmoke.gsm8k","test",50,"main","","","OK",50)
 set c($i(c)) = $lb("legacy id, pinned","rotten_tomatoes","HFSmoke.rotten","test",50,"","","aa13bc287fa6fcab6daf52f0dfb9994269ffea28","OK",50)
 set c($i(c)) = $lb("multi-label","google-research-datasets/go_emotions","HFSmoke.emotions","train",50,"simplified","","","OK",50)
 set c($i(c)) = $lb("stream 30 GB config","HuggingFaceFW/fineweb","HFSmoke.fineweb","train",20,"sample-10BT","","","OK",20)
 set c($i(c)) = $lb("append","scikit-learn/iris","HFSmoke.iris","train",10,"","append","","OK",10)
 set c($i(c)) = $lb("no config","openai/gsm8k","HFSmoke.x","test",5,"","","","ERROR","several configs, choose one: main, socratic")
 set c($i(c)) = $lb("bad config","openai/gsm8k","HFSmoke.x","test",5,"nope","","","ERROR","Config 'nope' not found")
 set c($i(c)) = $lb("bad split","stanfordnlp/imdb","HFSmoke.x","validation",5,"","","","ERROR","Available splits: train, test, unsupervised")
 set c($i(c)) = $lb("missing dataset","no-such-user/no-such-dataset","HFSmoke.x","train",5,"","","","ERROR","Dataset 'no-such-user/no-such-dataset' was not found on the Hub")
 set c($i(c)) = $lb("bad revision","rotten_tomatoes","HFSmoke.x","test",5,"","","no-such-branch","ERROR","Revision 'no-such-branch' not found")
 set c($i(c)) = $lb("too big","HuggingFaceFW/fineweb","HFSmoke.x","train",0,"sample-10BT","","","ERROR","row limit")
 set c($i(c)) = $lb("foreign table","scikit-learn/iris","dc_hf2iris.ImportLog","train",5,"","","","ERROR","not created by hf2iris")
 set c($i(c)) = $lb("bad mode","scikit-learn/iris","HFSmoke.iris","train",5,"","overwrite","","ERROR","mode must be one of")
 set q = "SELECT dc_hf2iris.load_dataset(?,?,?,?,?,?,?) AS r", fails = 0
 for i=1:1:c { set a = c(i), t = $zh, rs = ##class(%SQL.Statement).%ExecDirect(, q, $li(a,2), $li(a,3), $li(a,4), $li(a,5), $li(a,6), $li(a,7), $li(a,8)) do rs.%Next() set r = {}.%FromJSON(rs.r), ok = (r.status = $li(a,9)) && $s(r.status = "OK": r.rows = $li(a,10), 1: r.error [ $li(a,10)), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  ", $li(a), " (", $j($zh - t, 0, 1), "s)  ", $s(r.status = "OK": r.rows_" rows into "_r.table_" @"_$e(r.commit, 1, 7), 1: r.error) }
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT COUNT(*) AS n FROM HFSmoke.iris") do rs.%Next() set ok = (rs.n = 160), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  iris table has 150 + 10 appended rows: ", rs.n
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT COUNT(*) AS n FROM HFSmoke.rotten WHERE label_name IN ('pos', 'neg')") do rs.%Next() set ok = (rs.n = 50), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  label_name filled for rotten_tomatoes: ", rs.n
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TOP 1 CommitHash AS h FROM dc_hf2iris.ImportLog WHERE TableName = 'HFSmoke.rotten' ORDER BY ID DESC") do rs.%Next() set ok = (rs.h = pin), fails = fails + 'ok  write !, $s(ok:"PASS", 1:"FAIL"), "  pinned commit logged: ", rs.h
 set rs = ##class(%SQL.Statement).%ExecDirect(, "SELECT TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'HFSmoke'") while rs.%Next() { set x = ##class(%SQL.Statement).%ExecDirect(, "DROP TABLE HFSmoke."_rs.t) kill ^dc.hf2iris.Owned($zcvt("HFSMOKE."_rs.t, "U")) }
 write !, "SMOKE ", $s(fails:"FAILED: "_fails_" check(s)", 1:"PASSED"), !
 if fails do $system.Process.Terminate(, 1)
 halt
OS
) || true
grep -aE "^(PASS|FAIL|SMOKE)" <<<"$out" || echo "$out"
grep -aq "^SMOKE PASSED" <<<"$out"

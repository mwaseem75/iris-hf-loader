#!/usr/bin/env bash
# Live checks against the Hugging Face Hub (needs internet).
#   tests/smoke.sh discovery   read-only: search, details, preview and import plans over REST
#   tests/smoke.sh import      loads real datasets into schema HFSmoke (also as background
#                              jobs) and drops them again
#   tests/smoke.sh             both
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
if docker compose version >/dev/null 2>&1; then dc=(docker compose); else dc=(docker-compose); fi
mode=${1:-all}
B="http://localhost:${IRIS_WEB_PORT:-52773}/api/hf2iris"
AUTH="${IRIS_USERNAME:-_SYSTEM}:${IRIS_PASSWORD:-SYS}"
fails=0

# check NAME STATUS PATTERN [ABSENT] -- curl args; PATTERN must match the body, ABSENT must not
check() {
  local name=$1 want=$2 pattern=$3 absent=$4 out status
  shift 5
  out=$(curl -s -m 60 -w '\n%{http_code}' "$@" || true)
  status=${out##*$'\n'}
  out=${out%$'\n'*}
  if [ "$status" = "$want" ] && { [ -z "$pattern" ] || grep -qE -- "$pattern" <<<"$out"; } \
     && { [ -z "$absent" ] || ! grep -qE -- "$absent" <<<"$out"; }; then
    echo "PASS  $name (HTTP $status)"
  else
    echo "FAIL  $name (HTTP $status, expected $want) ${out:0:200}"
    fails=$((fails + 1))
  fi
}

discovery() {
  local imdb="id=stanfordnlp/imdb&config=plain_text&split=test"
  for path in "datasets?search=imdb" "dataset?id=a/b" "dataset/preview?id=a/b&config=c&split=s" "status" \
              "jobs" "jobs/1" "tables" "tables/Demo.IMDB"; do
    check "no login: GET /$path" 401 "" "" -- "$B/$path"
  done
  for path in "plan" "jobs" "jobs/1/cancel"; do
    check "no login: POST /$path" 401 "" "" -- -X POST -H "Content-Type: application/json" -d '{}' "$B/$path"
  done
  check "status" 200 '"hfToken": ?(true|false)' 'hf_[A-Za-z0-9]{20}' -- -u "$AUTH" "$B/status"
  check "search imdb" 200 '"id": "stanfordnlp/imdb"' "" -- -u "$AUTH" "$B/datasets?search=imdb&limit=10"
  check "search again is cached" 200 '"cached": ?true' "" -- -u "$AUTH" "$B/datasets?search=imdb&limit=10"
  check "search, bad sort" 400 '"code":"bad_request"' "" -- -u "$AUTH" "$B/datasets?sort=random"
  check "details gsm8k" 200 '"name": "test", "rows": 1319' "" -- -u "$AUTH" "$B/dataset?id=openai/gsm8k"
  check "details, old id" 200 '"resolvedFrom": "rotten_tomatoes"' "" -- -u "$AUTH" "$B/dataset?id=rotten_tomatoes"
  check "details, missing dataset" 404 '"code":"not_found"' "" -- -u "$AUTH" "$B/dataset?id=no-such-user/no-such-dataset"
  check "details, bad id" 400 '"code":"bad_request"' "" -- -u "$AUTH" "$B/dataset?id=a%20b"
  if grep -qE '"hfToken": ?false' <<<"$(curl -s -u "$AUTH" "$B/status")"; then
    check "details, gated without token" 403 '"code":"gated"' "" -- -u "$AUTH" "$B/dataset?id=bigcode/the-stack"
  else
    echo "SKIP  details, gated without token (HF_TOKEN is set)"
  fi
  check "preview rows 100-104" 200 '"offset": 100.*"total": 25000' "" -- -u "$AUTH" "$B/dataset/preview?$imdb&offset=100&rows=5"
  check "preview, unknown split" 404 '"code":"not_found"' "" -- -u "$AUTH" "$B/dataset/preview?id=stanfordnlp/imdb&config=plain_text&split=nope"
  check "preview, images masked" 200 '"\[image\]"' 'Signature=|/assets/' -- -u "$AUTH" "$B/dataset/preview?id=ylecun/mnist&config=mnist&split=test&rows=2"
  local json=(-u "$AUTH" -X POST -H "Content-Type: application/json")
  check "plan gsm8k" 200 '"action": "(create|replace)".*"name": "question"' "" -- "${json[@]}" \
    -d '{"dataset":"openai/gsm8k","config":"main","split":"test","limit":50,"table":"HFSmoke.plan_gsm8k"}' "$B/plan"
  check "plan, old id with labels" 200 '"repo": "cornell-movie-review-data/rotten_tomatoes".*"label_name"' "" -- "${json[@]}" \
    -d '{"dataset":"rotten_tomatoes","split":"test","table":"HFSmoke.plan_rt"}' "$B/plan"
  check "plan, foreign table refused" 200 '"action": "refused".*not created by hf2iris' "" -- "${json[@]}" \
    -d '{"dataset":"openai/gsm8k","config":"main","split":"test","limit":5,"table":"dc_hf2iris.ImportLog"}' "$B/plan"
  check "plan, over the limits refused" 200 '"action": "refused".*row limit' "" -- "${json[@]}" \
    -d '{"dataset":"HuggingFaceFW/fineweb","config":"sample-10BT","split":"train","limit":0,"table":"HFSmoke.big"}' "$B/plan"
  check "plan, bad body" 400 '"code":"bad_request"' "" -- "${json[@]}" -d 'not json' "$B/plan"
  check "jobs list" 200 '"items":' "" -- -u "$AUTH" "$B/jobs?limit=5"
  check "job, unknown id" 404 '"code":"not_found"' "" -- -u "$AUTH" "$B/jobs/999999999"
  check "job start, foreign table" 409 '"code":"conflict"' "" -- "${json[@]}" \
    -d '{"dataset":"openai/gsm8k","table":"dc_hf2iris.ImportLog","mode":"append"}' "$B/jobs"
  check "job start, bad id" 400 '"code":"bad_request"' "" -- "${json[@]}" -d '{"dataset":"a b"}' "$B/jobs"
  check "job cancel, unknown id" 404 '"code":"not_found"' "" -- "${json[@]}" -d '{}' "$B/jobs/999999999/cancel"
  check "tables list" 200 '"items":' "" -- -u "$AUTH" "$B/tables"
  check "table, not created by hf2iris" 404 '"code":"not_found"' "" -- -u "$AUTH" "$B/tables/dc_hf2iris.ImportLog"
  local first
  first=$(curl -s -u "$AUTH" "$B/tables" | grep -oE '"table":"[A-Za-z0-9_]+\.[A-Za-z0-9_]+"' | head -1 | cut -d'"' -f4 || true)
  if [ -n "$first" ]; then
    check "table detail ($first)" 200 '"columns":\[\{"name":' "" -- -u "$AUTH" "$B/tables/$first"
    check "table rows with offset" 200 '"offset":1' "" -- -u "$AUTH" "$B/preview/$first?rows=2&offset=1"
    check "table rows without offset keep their shape" 200 '"rows":' '"offset"' -- -u "$AUTH" "$B/preview/$first?rows=2"
  else
    echo "SKIP  table detail and paging (no tables yet)"
  fi
  check "old /info keeps its shape" 200 '^\{"dataset":"openai/gsm8k","configs":\{"main":\["train","test"\],"socratic":\["train","test"\]\}\}$' "" -- -u "$AUTH" "$B/info?dataset=openai/gsm8k"
  echo "DISCOVERY $([ "$fails" -eq 0 ] && echo PASSED || echo "FAILED: $fails check(s)")"
}

imports() {
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
grep -aq "^SMOKE PASSED" <<<"$out" || fails=$((fails + 1))
}

# background import: start, poll until finished, check the rows; then a cancelled one
jobs() {
  local json=(-u "$AUTH" -H "Content-Type: application/json") id state body i
  body=$(curl -s "${json[@]}" -X POST "$B/jobs" \
    -d '{"dataset":"stanfordnlp/imdb","split":"test","limit":300,"table":"HFSmoke.job_imdb"}')
  id=$(grep -oE '"id":"?[0-9]+' <<<"$body" | grep -oE '[0-9]+' || true)
  for i in $(seq 1 60); do
    state=$(curl -s -u "$AUTH" "$B/jobs/$id" | grep -oE '"state":"[a-z]+"' | cut -d'"' -f4 || true)
    [[ "$state" =~ ^(ok|error|cancelled|interrupted)$ ]] && break
    sleep 2
  done
  check "background job finishes" 200 '"state":"ok".*"rowsDone":300' "" -- -u "$AUTH" "$B/jobs/$id"
  body=$(curl -s "${json[@]}" -X POST "$B/jobs" \
    -d '{"dataset":"stanfordnlp/imdb","split":"train","limit":5000,"table":"HFSmoke.job_cancel"}')
  id=$(grep -oE '"id":"?[0-9]+' <<<"$body" | grep -oE '[0-9]+' || true)
  curl -s "${json[@]}" -X POST -d '{}' "$B/jobs/$id/cancel" >/dev/null
  for i in $(seq 1 60); do
    state=$(curl -s -u "$AUTH" "$B/jobs/$id" | grep -oE '"state":"[a-z]+"' | cut -d'"' -f4 || true)
    [[ "$state" =~ ^(ok|error|cancelled|interrupted)$ ]] && break
    sleep 2
  done
  check "cancelled job" 200 '"state":"cancelled"' "" -- -u "$AUTH" "$B/jobs/$id"
  "${dc[@]}" exec -T iris iris session IRIS -U USER >/dev/null <<'OS'
 for t = "job_imdb", "job_cancel" { set x = ##class(%SQL.Statement).%ExecDirect(, "DROP TABLE HFSmoke."_t) kill ^dc.hf2iris.Owned($zcvt("HFSMOKE."_t, "U")) }
 halt
OS
}

case "$mode" in
  discovery) discovery ;;
  import) imports; jobs ;;
  all) discovery; imports; jobs ;;
  *) echo "usage: tests/smoke.sh [discovery|import]"; exit 2 ;;
esac
[ "$fails" -eq 0 ]

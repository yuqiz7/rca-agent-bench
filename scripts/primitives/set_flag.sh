#!/usr/bin/env bash
# set_flag — misconfig / mem_leak 类注入原语（fault_schema v1.0 §3）
# class: misconfig | mem_leak（取决于 flag，见下表）
#
# 接口（比前三个原语多一个参数）：
#   apply  <service> <flag>=<variant>   备份 json -> 改 defaultVariant -> 验证生效；打印 t_inject
#   revert <service> <flag>=<variant>   恢复备份 -> 验证回原值 -> 删 state；打印 t_revert
#   probe  <service> <flag>=<variant>   查当前 variant，等于 state 记的目标值即 injected=true
#
# 实现方式（2026-08-24 查实，非假设）：
#   compose 里 flagd 的 command 是 `start --uri file:./etc/flagd/demo.flagd.json`，
#   宿主机 src/flagd 挂到容器 /etc/flagd；flagd 启动日志有
#   "Starting filepath sync notifier"。实测改挂载的 json 后约 6 秒内 OFREP
#   即返回新 variant，撤除后回原值 —— 因此走「改文件 + 自动重载」，不重启容器。
#
# 求值接口用 OFREP（容器 8016，宿主机端口由 docker port 动态取，因为 compose
# 未钉死该端口）：POST /ofrep/v1/evaluate/flags/<key>
#
# targeting 型开关（决策 018 附注 / O-P2-11）：demo.flagd.json 里有些开关带
# "targeting" 规则，而 flagd 中 targeting 的优先级**高于** defaultVariant ——
# 改 defaultVariant 对它们完全无效。productCatalogFailure 出厂时两个分支都是
# "off"（`"if": [<cond>, "off", "off"]`），所以怎么改 defaultVariant 都评估为 false。
# 对这类开关，apply 改的是**命中分支的变体**（第一个分支 off -> on），规则条件不动；
# revert 照旧从备份整体恢复。
#
# 运行在宿主机，不进容器、不需要 nsenter（flagd 走网络接口）。

set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$SELF_DIR/state"
DEMO_DIR="$(cd "$SELF_DIR/../.." && pwd)/opentelemetry-demo"
FLAG_JSON="$DEMO_DIR/src/flagd/demo.flagd.json"
mkdir -p "$STATE_DIR"

usage() { echo "usage: $0 {apply|revert|probe} <service> <flag>=<variant>" >&2; exit 2; }
ts()    { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

[ $# -eq 3 ] || usage
cmd="$1"; svc="$2"; spec="$3"
case "$spec" in *=*) ;; *) usage ;; esac
flag="${spec%%=*}"; variant="${spec#*=}"

# flag -> 影响服务映射表。来源：2026-08-24 对 src/ 各服务代码中 flag 判断处的
# 逐个定位（见 decisions.md 018）。apply 时校验，防止把 flag 记到错的靶子上。
# 开关 -> 求值上下文。targeting 规则按上下文变量做判断，probe 必须带上同样的
# 上下文才能看到真实结果。来源：各服务代码里传给 flag 求值的 EvaluationContext
# （productCatalogFailure 见 product-catalog/main.go:420，传的是 product_id=请求的商品 ID；
# 规则里写死的目标 ID 见 demo.flagd.json 的 targeting 条件）。
flag_context() {
  case "$1" in
    productCatalogFailure) echo '{"product_id":"OLJCESPC7Z"}' ;;
    *)                     echo '{}' ;;
  esac
}

# 该开关是否带 targeting 规则
flag_has_targeting() {
  python3 -c "
import json,sys
d=json.load(open('$FLAG_JSON'))
sys.exit(0 if 'targeting' in d['flags'].get('$1',{}) else 1)
"
}

flag_service() {
  case "$1" in
    adFailure|adHighCpu|adManualGc)            echo ad ;;
    cartFailure|failedReadinessProbe)          echo cart ;;
    emailMemoryLeak)                           echo email ;;
    imageSlowLoad)                             echo frontend ;;
    intlShippingSlowdown)                      echo shipping ;;
    kafkaQueueProblems|paymentUnreachable)     echo checkout ;;
    loadGeneratorTraffic|loadGeneratorVUs)     echo load-generator ;;
    paymentFailure)                            echo payment ;;
    productCatalogFailure)                     echo product-catalog ;;
    recommendationCacheFailure)                echo recommendation ;;
    *) echo "" ;;
  esac
}

expect="$(flag_service "$flag")"
if [ -z "$expect" ]; then
  echo "error: unknown flag '$flag'; not in the flag->service table" >&2; exit 1
fi
if [ "$expect" != "$svc" ]; then
  echo "error: flag '$flag' affects service '$expect', not '$svc'" >&2
  echo "       靶子必须与 flag 的影响服务一致，否则 ground_truth 会记错服务。" >&2
  exit 1
fi
[ -f "$FLAG_JSON" ] || { echo "error: missing $FLAG_JSON" >&2; exit 1; }

ofrep_base() {
  local hp
  hp="$(docker port flagd 8016 2>/dev/null | head -1 | sed 's/.*://')"
  [ -n "$hp" ] || { echo "error: cannot resolve flagd OFREP host port" >&2; exit 1; }
  echo "http://localhost:$hp"
}

current_variant() {
  local ctx; ctx="$(flag_context "$flag")"
  curl -s --max-time 10 -X POST "$(ofrep_base)/ofrep/v1/evaluate/flags/$flag" \
       -H 'Content-Type: application/json' -d "{\"context\":$ctx}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin).get("variant",""))' 2>/dev/null || echo ""
}

# flagd 的文件监视是异步的，改完等它生效；最多等 30 秒
wait_variant() {
  local want="$1" i cur
  for i in $(seq 1 30); do
    cur="$(current_variant)"
    [ "$cur" = "$want" ] && { echo "$cur"; return 0; }
    sleep 1
  done
  echo "$cur"; return 1
}

STATE_FILE="$STATE_DIR/$svc.flag"

case "$cmd" in
  apply)
    [ -s "$STATE_FILE" ] && { echo "error: injection already applied for '$svc'" >&2; exit 1; }
    orig="$(current_variant)"
    [ -n "$orig" ] || { echo "error: cannot read current variant of '$flag'" >&2; exit 1; }
    [ "$orig" != "$variant" ] || { echo "error: '$flag' already at '$variant'; nothing to inject" >&2; exit 1; }
    python3 -c "
import json,sys
d=json.load(open('$FLAG_JSON'))
if '$variant' not in d['flags']['$flag']['variants']:
    sys.exit(\"variant '$variant' not defined for flag '$flag'\")
" || exit 1
    bak="$STATE_DIR/$svc.flag.bak.json"
    cp "$FLAG_JSON" "$bak"
    if flag_has_targeting "$flag"; then
      # targeting 优先于 defaultVariant：改命中分支的变体，规则条件不动
      python3 -c "
import json,sys
p='$FLAG_JSON'; d=json.load(open(p))
t=d['flags']['$flag']['targeting']
if 'if' not in t or len(t['if']) < 2:
    sys.exit('unsupported targeting shape for $flag: expected an \'if\' with a match branch')
t['if'][1]='$variant'          # 命中分支 -> 目标变体；条件与未命中分支保持原样
json.dump(d,open(p,'w'),indent=2)
" || { rm -f "$bak"; exit 1; }
    else
      python3 -c "
import json
p='$FLAG_JSON'; d=json.load(open(p))
d['flags']['$flag']['defaultVariant']='$variant'
json.dump(d,open(p,'w'),indent=2)
"
    fi
    got="$(wait_variant "$variant")" || {
      echo "error: flagd did not pick up '$variant' within 30s (got '$got'); rolling back" >&2
      cp "$bak" "$FLAG_JSON"; rm -f "$bak"; exit 1; }
    printf 'flag=%s orig=%s target=%s backup=%s\n' "$flag" "$orig" "$variant" "$bak" > "$STATE_FILE"
    echo "t_inject=$(ts)"
    echo "service=$svc flag=$flag orig_variant=$orig variant=$got"
    ;;

  revert)
    if [ ! -s "$STATE_FILE" ]; then
      echo "t_revert=$(ts)"
      echo "warn: no state file for '$svc'; nothing to revert" >&2
      echo "service=$svc flag=$flag variant=$(current_variant)"
      exit 0
    fi
    orig="$(sed -n 's/.*orig=\([^ ]*\).*/\1/p' "$STATE_FILE")"
    bak="$(sed -n 's/.*backup=\([^ ]*\).*/\1/p' "$STATE_FILE")"
    [ -s "$bak" ] || { echo "error: backup '$bak' missing" >&2; exit 1; }
    cp "$bak" "$FLAG_JSON"
    got="$(wait_variant "$orig")" || {
      echo "error: flagd did not return to '$orig' within 30s (got '$got')" >&2; exit 1; }
    rm -f "$bak" "$STATE_FILE"
    echo "t_revert=$(ts)"
    echo "service=$svc flag=$flag variant=$got restored_from=$orig"
    ;;

  probe)
    cur="$(current_variant)"
    want=""
    [ -s "$STATE_FILE" ] && want="$(sed -n 's/.*target=\([^ ]*\).*/\1/p' "$STATE_FILE")"
    if [ -n "$want" ] && [ "$cur" = "$want" ]; then echo "injected=true"; else echo "injected=false"; fi
    echo "service=$svc flag=$flag variant=$cur target=${want:-none}"
    ;;
  *) usage ;;
esac

#!/usr/bin/env bash
# kill_container — crash 类注入原语（fault_schema v0.9 §3）
#
# 固定三子命令接口，后续所有原语脚本共用：
#   apply  <service>   记录原 restart 策略 -> --restart=no -> docker kill；打印 t_inject
#   revert <service>   docker start -> 恢复原 restart 策略；打印 t_revert
#   probe  <service>   打印 injected=true/false（inspect 状态 != running 即 true）
#
# 运行在宿主机，不进容器、不进 compose 项目、不进 OTel 管线。
# 输出不含场景元数据（id/class/title/primitive 名）。

set -euo pipefail

STATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/state"
mkdir -p "$STATE_DIR"

usage() { echo "usage: $0 {apply|revert|probe} <service>" >&2; exit 2; }
ts()    { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

[ $# -eq 2 ] || usage
cmd="$1"; svc="$2"

# 解析 service -> 容器名。compose 给这些容器设了 container_name: <service>，
# 但不依赖该约定：先按 compose label 查，查不到再退回同名容器。
resolve() {
  local c
  c="$(docker ps -a --filter "label=com.docker.compose.service=$svc" \
       --filter "label=com.docker.compose.project=opentelemetry-demo" \
       --format '{{.Names}}' | head -1)"
  [ -n "$c" ] || c="$(docker ps -a --format '{{.Names}}' | grep -Fx "$svc" | head -1)"
  [ -n "$c" ] || { echo "error: no container for service '$svc'" >&2; exit 1; }
  echo "$c"
}
cid="$(resolve)"

case "$cmd" in
  apply)
    pol="$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$cid")"
    # 只在首次 apply 时落盘，避免重复 apply 把 "no" 记成原值
    [ -s "$STATE_DIR/$svc.restart" ] || printf '%s\n' "$pol" > "$STATE_DIR/$svc.restart"
    docker update --restart=no "$cid" >/dev/null
    docker kill "$cid" >/dev/null
    echo "t_inject=$(ts)"
    echo "service=$svc container=$cid saved_restart=$(cat "$STATE_DIR/$svc.restart")"
    ;;
  revert)
    docker start "$cid" >/dev/null
    if [ -s "$STATE_DIR/$svc.restart" ]; then
      pol="$(cat "$STATE_DIR/$svc.restart")"
      docker update --restart="$pol" "$cid" >/dev/null
      rm -f "$STATE_DIR/$svc.restart"
    else
      pol="(no saved policy; left as-is)"
    fi
    echo "t_revert=$(ts)"
    echo "service=$svc container=$cid restored_restart=$pol"
    ;;
  probe)
    st="$(docker inspect -f '{{.State.Status}}' "$cid")"
    if [ "$st" = "running" ]; then echo "injected=false"; else echo "injected=true"; fi
    echo "service=$svc container=$cid status=$st"
    ;;
  *) usage ;;
esac

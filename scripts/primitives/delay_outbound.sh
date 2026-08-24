#!/usr/bin/env bash
# delay_outbound — latency 类注入原语（fault_schema v0.9 §3）
# class: latency
#
# 接口与 kill_container.sh / drop_inbound.sh 完全一致（多一个可选参数）：
#   apply  <service> [delay_ms]   给 B 服务端口发出的响应包加延迟；打印 t_inject
#   revert <service> [delay_ms]   删除本脚本建的根 qdisc；打印 t_revert
#   probe  <service> [delay_ms]   netem 存在且过滤器 sport = 服务端口即 injected=true
# delay_ms 默认 800，依据见决策 014。
#
# 注入作用面（§3 硬规则 / decision 007）：只延迟**从 B 服务端口发出**的包，
# 即 u32 匹配 tcp sport = service_ports.env 中该服务的端口。B 自身向下游发出的
# 请求（sport 是临时端口）与进入 B 的请求包一律不延迟 —— 否则症状漂到下游，
# 根因不再唯一。
#
# 为何选出口不选入口：入口整形必须把流量重定向到 ifb 虚设备再整形，多一层机关
# 且要额外加载 ifb 模块；出口方向 tc 原生支持，零额外部件。而"B 响应变慢"这件
# 事在出口方向就能精确表达。
#
# 服务端口不写死也不猜：从 scripts/service_ports.env 读（由 compose 合并配置生成）。
#
# 运行在宿主机。nsenter 进容器 netns 需要 root，故用 sudo；不进容器内部、
# 不依赖容器镜像里有 tc。

set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$SELF_DIR/state"
PORTS_ENV="$SELF_DIR/service_ports.env"
mkdir -p "$STATE_DIR"

usage() { echo "usage: $0 {apply|revert|probe} <service> [delay_ms]" >&2; exit 2; }
ts()    { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

[ $# -ge 2 ] && [ $# -le 3 ] || usage
cmd="$1"; svc="$2"; delay_ms="${3:-800}"

case "$delay_ms" in
  ''|*[!0-9]*) echo "error: delay_ms must be a positive integer, got '$delay_ms'" >&2; exit 2 ;;
esac
[ "$delay_ms" -gt 0 ] || { echo "error: delay_ms must be > 0" >&2; exit 2; }

resolve() {
  local c
  c="$(docker ps -a --filter "label=com.docker.compose.service=$svc" \
       --filter "label=com.docker.compose.project=opentelemetry-demo" \
       --format '{{.Names}}' | head -1)"
  [ -n "$c" ] || c="$(docker ps -a --format '{{.Names}}' | grep -Fx "$svc" | head -1)"
  [ -n "$c" ] || { echo "error: no container for service '$svc'" >&2; exit 1; }
  echo "$c"
}

svc_port() {
  local key val
  key="$(echo "$svc" | tr 'a-z-' 'A-Z_')_PORT"
  [ -f "$PORTS_ENV" ] || { echo "error: missing $PORTS_ENV" >&2; exit 1; }
  val="$(grep -E "^${key}=" "$PORTS_ENV" | head -1 | cut -d= -f2 | awk '{print $1}')"
  if [ -z "$val" ] || [ "$val" = "None" ]; then
    echo "error: service port for '$svc' not resolved in $PORTS_ENV (key $key)." >&2
    echo "       该服务在 compose 里发布了多个端口，需先人工判定服务端口再登记。" >&2
    exit 1
  fi
  echo "$val"
}

cid="$(resolve)"
port="$(svc_port)"
pid="$(docker inspect -f '{{.State.Pid}}' "$cid")"
[ "$pid" != "0" ] || { echo "error: container '$cid' not running (pid 0)" >&2; exit 1; }

in_ns() { sudo -n nsenter -t "$pid" -n "$@"; }

# 接口名不写死 eth0：取 netns 内非 lo 的接口；多于一个则停下报错，不猜。
pick_iface() {
  local ifaces n
  ifaces="$(in_ns ip -o link show | awk -F': ' '$2 != "lo" {print $2}' | cut -d'@' -f1)"
  n="$(printf '%s\n' "$ifaces" | grep -c .)"
  if [ "$n" -eq 0 ]; then
    echo "error: no non-lo interface in netns of '$cid'" >&2; exit 1
  elif [ "$n" -gt 1 ]; then
    echo "error: container '$cid' has $n non-lo interfaces: $(printf '%s ' $ifaces)" >&2
    echo "       多网卡容器需人工判定注入接口，本脚本不猜。" >&2
    exit 1
  fi
  printf '%s\n' "$ifaces"
}
iface="$(pick_iface)"

STATE_FILE="$STATE_DIR/$svc.delay"

# prio 根 qdisc：3 个 band。priomap 全部指向 band 2（handle 1:3）作为"直通"，
# 只有被 u32 过滤器显式导入 band 0（handle 1:1）的流量才经过 netem。
ROOT_HANDLE="1:"
NETEM_BAND="1:1"
NETEM_HANDLE="10:"

case "$cmd" in
  apply)
    # 不覆盖别人的规则：root 已有非本脚本创建的 qdisc 则拒绝
    existing="$(in_ns tc qdisc show dev "$iface" root 2>/dev/null || true)"
    if printf '%s' "$existing" | grep -qv '^$' && ! printf '%s' "$existing" | grep -qE 'qdisc (noqueue|pfifo_fast|mq) '; then
      if [ ! -s "$STATE_FILE" ]; then
        echo "error: dev $iface already has a non-default root qdisc, refusing to overwrite:" >&2
        printf '  %s\n' "$existing" >&2
        exit 1
      fi
      echo "error: injection already applied for '$svc' (state file exists)" >&2
      exit 1
    fi

    rollback() { in_ns tc qdisc del dev "$iface" root >/dev/null 2>&1 || true; }

    in_ns tc qdisc add dev "$iface" root handle "$ROOT_HANDLE" prio bands 3 \
        priomap 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 \
      || { echo "error: tc qdisc add prio failed" >&2; rollback; exit 1; }
    in_ns tc qdisc add dev "$iface" parent "$NETEM_BAND" handle "$NETEM_HANDLE" \
        netem delay "${delay_ms}ms" \
      || { echo "error: tc qdisc add netem failed" >&2; rollback; exit 1; }
    in_ns tc filter add dev "$iface" protocol ip parent "$ROOT_HANDLE" prio 1 u32 \
        match ip protocol 6 0xff \
        match ip sport "$port" 0xffff \
        flowid "$NETEM_BAND" \
      || { echo "error: tc filter add u32 sport=$port failed" >&2; rollback; exit 1; }

    printf 'delay_ms=%s iface=%s port=%s\n' "$delay_ms" "$iface" "$port" > "$STATE_FILE"
    echo "t_inject=$(ts)"
    echo "service=$svc container=$cid pid=$pid iface=$iface sport=$port delay_ms=$delay_ms"
    ;;

  revert)
    if [ ! -s "$STATE_FILE" ]; then
      echo "t_revert=$(ts)"
      echo "warn: no state file for '$svc'; nothing to delete" >&2
      echo "service=$svc container=$cid pid=$pid iface=$iface sport=$port delay_ms=$delay_ms"
      exit 0
    fi
    in_ns tc qdisc del dev "$iface" root >/dev/null 2>&1 || true
    rm -f "$STATE_FILE"
    left="$(in_ns tc qdisc show dev "$iface" | grep -cE 'netem|prio ' || true)"
    if [ "$left" -ne 0 ]; then
      echo "error: residual qdisc after revert on dev $iface:" >&2
      in_ns tc qdisc show dev "$iface" >&2
      exit 1
    fi
    echo "t_revert=$(ts)"
    echo "service=$svc container=$cid pid=$pid iface=$iface sport=$port delay_ms=$delay_ms"
    ;;

  probe)
    qd="$(in_ns tc qdisc show dev "$iface" 2>/dev/null || true)"
    fl="$(in_ns tc filter show dev "$iface" parent "$ROOT_HANDLE" 2>/dev/null || true)"
    st="$(in_ns tc -s qdisc show dev "$iface" 2>/dev/null || true)"
    has_netem=0; printf '%s' "$qd" | grep -q 'netem' && has_netem=1
    # u32 把端口编码成 match <hex>/0000ffff：sport 在高 16 位
    hexport="$(printf '%04x0000' "$port")"
    has_filter=0; printf '%s' "$fl" | grep -qi "match $hexport" && has_filter=1
    if [ -s "$STATE_FILE" ] && [ "$has_netem" -eq 1 ] && [ "$has_filter" -eq 1 ]; then
      echo "injected=true"
    else
      echo "injected=false"
    fi
    echo "service=$svc container=$cid pid=$pid iface=$iface sport=$port delay_ms=$delay_ms"
    echo "--- tc qdisc show ---"; printf '%s\n' "$qd"
    echo "--- tc filter show ---"; printf '%s\n' "$fl"
    echo "--- tc -s qdisc show ---"; printf '%s\n' "$st"
    ;;
  *) usage ;;
esac

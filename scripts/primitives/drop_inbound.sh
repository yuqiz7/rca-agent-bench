#!/usr/bin/env bash
# drop_inbound — dep_timeout 类注入原语（fault_schema v0.9 §3）
#
# 接口与 kill_container.sh 完全一致：
#   apply  <service>   在目标容器的 netns 内丢弃进入其服务端口的 TCP 包；打印 t_inject
#   revert <service>   删除同一条规则；打印 t_revert
#   probe  <service>   netns 内 iptables -S INPUT 含该规则即 injected=true
#
# 注入作用面（§3 硬规则 / decision 007）：只丢**进入 B 服务端口**的包。
# 不动整块网卡 —— 否则 B 自身的对外调用与遥测上报会一并中断，把"B 不可达"
# 变成"B 崩了"的指纹，污染标准答案。
#
# 服务端口不写死也不猜：从 scripts/service_ports.env 读，该文件由 compose
# 合并配置生成（见文件头注释）。
#
# 运行在宿主机。nsenter 进入容器 netns 需要 root，故用 sudo；不进容器内部、
# 不依赖容器镜像里有 iptables。

set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$SELF_DIR/state"
PORTS_ENV="$SELF_DIR/service_ports.env"
mkdir -p "$STATE_DIR"

usage() { echo "usage: $0 {apply|revert|probe} <service>" >&2; exit 2; }
ts()    { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

[ $# -eq 2 ] || usage
cmd="$1"; svc="$2"

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

# 只匹配「进入该端口」这一条，不碰其它端口
RULE=(-p tcp --dport "$port" -j DROP)
in_ns() { sudo -n nsenter -t "$pid" -n "$@"; }

case "$cmd" in
  apply)
    in_ns iptables -I INPUT "${RULE[@]}"
    printf '%s\n' "$port" > "$STATE_DIR/$svc.dport"
    echo "t_inject=$(ts)"
    echo "service=$svc container=$cid pid=$pid dport=$port"
    ;;
  revert)
    # -D 用与 -I 相同的匹配条件删除；规则不存在时不让 set -e 掀桌子
    if in_ns iptables -D INPUT "${RULE[@]}" 2>/dev/null; then
      echo "t_revert=$(ts)"
    else
      echo "t_revert=$(ts)"
      echo "warn: rule not present; nothing to delete" >&2
    fi
    rm -f "$STATE_DIR/$svc.dport"
    echo "service=$svc container=$cid pid=$pid dport=$port"
    ;;
  probe)
    if in_ns iptables -S INPUT | grep -qE -- "-p tcp -m tcp --dport $port -j DROP|--dport $port -j DROP"; then
      echo "injected=true"
    else
      echo "injected=false"
    fi
    echo "service=$svc container=$cid pid=$pid dport=$port"
    ;;
  *) usage ;;
esac

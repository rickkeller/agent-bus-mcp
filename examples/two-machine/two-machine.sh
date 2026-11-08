#!/bin/sh
# Isolated two-machine test kit for agent-bus-remote.
# The script contains no keys. prepare-host and csr create keys on the machine
# where they run. sign accepts only a CSR and returns a certificate.
# It refuses 0.0.0.0 and public IPv4 addresses.
# 100.64.0.0/10 is refused unless AGENT_BUS_ALLOW_TAILSCALE=1.
# That opt-in allows only that CGNAT range. It does not allow any other
# address, and it does not change TLS.
set -eu

usage() {
  echo "usage: sh two-machine.sh install|prepare-host|csr|sign|host|client|stop|clean ..." >&2
  echo "  install DIR WHEEL_DIR" >&2
  echo "  prepare-host DIR HOST_IP PORT" >&2
  echo "  csr DIR ENDPOINT" >&2
  echo "  sign DIR ENDPOINT CSR" >&2
  echo "  host DIR" >&2
  echo "  client DIR ENDPOINT TOOL REQUEST_ID [remote-call args, such as --wait-seconds N]" >&2
  echo "  stop DIR" >&2
  echo "  clean DIR" >&2
  echo "  AGENT_BUS_ALLOW_TAILSCALE=1 allows 100.64.0.0/10 and no other address" >&2
  exit 2
}

address_class() {
  python3 -c 'import ipaddress, sys
text = sys.argv[1]
try:
    address = ipaddress.IPv4Address(text)
except ValueError:
    print("refused")
    raise SystemExit(0)
private = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
)
if any(address in network for network in private):
    print("private")
elif address in ipaddress.IPv4Network("100.64.0.0/10"):
    print("tailscale")
else:
    print("refused")
' "$1"
}

require_host_ip() {
  class=$(address_class "$1")
  case "$class" in
    private) return 0 ;;
    tailscale)
      if [ "${AGENT_BUS_ALLOW_TAILSCALE:-}" = "1" ]; then
        return 0
      fi
      echo "refusing $1 until AGENT_BUS_ALLOW_TAILSCALE=1; that opt-in allows 100.64.0.0/10 only" >&2
      return 1
      ;;
  esac
  echo "refusing non-private HOST_IP" >&2
  return 1
}

run_tool() {
  dir=$1
  name=$2
  shift 2
  if [ -x "$dir/venv/bin/$name" ]; then
    "$dir/venv/bin/$name" "$@"
    return
  fi
  case "$name" in
    agent-bus-enroll) python3 -m agent_bus_mcp.enroll "$@" ;;
    agent-bus-remote) python3 -m agent_bus_mcp.remote "$@" ;;
    agent-bus-remote-call) python3 -m agent_bus_mcp.remote_call "$@" ;;
    *) echo "unknown tool $name" >&2; return 127 ;;
  esac
}

listener_command() {
  ps -p "$1" -o args= 2>/dev/null || true
}

is_our_listener() {
  command_text=$(listener_command "$1")
  case "$command_text" in
    *agent-bus-remote*|*agent_bus_mcp.remote*) return 0 ;;
  esac
  return 1
}

port_closed() {
  host_ip=$1
  port=$2
  python3 -c 'import socket, sys
s = socket.socket()
s.settimeout(0.3)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(0)
else:
    sys.exit(1)
' "$host_ip" "$port"
}

stop_listener() {
  dir=$1
  host_ip=$(cat "$dir/host_ip")
  port=$(cat "$dir/port")
  if [ ! -f "$dir/host.pid" ]; then
    if port_closed "$host_ip" "$port"; then
      echo "host is not running"
      return 0
    fi
    echo "port $port is open and this directory has no listener pid; not killing another process" >&2
    return 1
  fi
  pid=$(tr -d '[:space:]' < "$dir/host.pid")
  case "$pid" in
    ''|*[!0-9]*) echo "listener pid file is not a process id; not killing" >&2; return 1 ;;
  esac
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$dir/host.pid"
    if port_closed "$host_ip" "$port"; then
      echo "host is not running"
      return 0
    fi
    echo "port $port is still open after a stale pid; not killing another process" >&2
    return 1
  fi
  if ! is_our_listener "$pid"; then
    echo "process $pid is not this host; not killing it" >&2
    return 1
  fi
  kill "$pid" 2>/dev/null || true
  i=0
  while [ "$i" -lt 50 ]; do
    if ! kill -0 "$pid" 2>/dev/null && port_closed "$host_ip" "$port"; then
      rm -f "$dir/host.pid"
      echo "host stopped"
      return 0
    fi
    i=$((i + 1))
    sleep 0.1
  done
  echo "listener $pid did not release port $port" >&2
  return 1
}

[ "$#" -ge 1 ] || usage
cmd=$1
shift

case "$cmd" in
  install)
    [ "$#" -eq 2 ] || usage
    dir=$1
    wheels=$2
    python3 -m venv "$dir/venv"
    "$dir/venv/bin/python" -m pip install --disable-pip-version-check "$wheels"/*.whl
    ;;
  prepare-host)
    [ "$#" -eq 3 ] || usage
    dir=$1
    host_ip=$2
    port=$3
    require_host_ip "$host_ip" || exit 2
    case "$port" in
      ''|*[!0-9]*) echo "PORT must be numeric" >&2; exit 2 ;;
    esac
    mkdir -p "$dir/certs" "$dir/state"
    chmod 700 "$dir" "$dir/certs" "$dir/state"
    openssl req -x509 -newkey rsa:2048 -keyout "$dir/certs/ca.key" -out "$dir/certs/ca.pem" \
      -days 2 -nodes -subj "/CN=Synthetic Test CA" \
      -addext basicConstraints=critical,CA:TRUE \
      -addext keyUsage=critical,keyCertSign,cRLSign \
      -addext subjectKeyIdentifier=hash >/dev/null 2>&1
    openssl req -newkey rsa:2048 -keyout "$dir/certs/server.key" -out "$dir/certs/server.csr" \
      -nodes -subj "/CN=$host_ip" >/dev/null 2>&1
    printf '%s\n' \
      "basicConstraints=critical,CA:FALSE" \
      "keyUsage=critical,digitalSignature,keyEncipherment" \
      "extendedKeyUsage=serverAuth" \
      "subjectAltName=IP:$host_ip" \
      "subjectKeyIdentifier=hash" \
      "authorityKeyIdentifier=keyid,issuer" > "$dir/certs/server.ext"
    openssl x509 -req -in "$dir/certs/server.csr" -CA "$dir/certs/ca.pem" -CAkey "$dir/certs/ca.key" \
      -CAcreateserial -out "$dir/certs/server.pem" -days 2 -extfile "$dir/certs/server.ext" >/dev/null 2>&1
    chmod 600 "$dir/certs/ca.key" "$dir/certs/server.key"
    cat > "$dir/endpoints.json" <<'EOF'
{
  "endpoints": ["north", "south"],
  "allow": [
    {"from": "north", "to": "south", "kinds": ["chat", "handoff"]},
    {"from": "south", "to": "north", "kinds": ["chat"]}
  ],
  "boards": [{"id": "room", "members": ["north", "south"]}]
}
EOF
    printf '%s\n' '{"credentials":[]}' > "$dir/enrollment.json"
    chmod 600 "$dir/enrollment.json"
    printf '%s\n' "$host_ip" > "$dir/host_ip"
    printf '%s\n' "$port" > "$dir/port"
    echo "host prepared. Copy only $dir/certs/ca.pem to the client."
    echo "Leave ca.key and server.key on this machine."
    ;;
  csr)
    [ "$#" -eq 2 ] || usage
    dir=$1
    endpoint=$2
    mkdir -p "$dir/certs"
    chmod 700 "$dir" "$dir/certs"
    openssl req -newkey rsa:2048 -keyout "$dir/certs/$endpoint.key" -out "$dir/certs/$endpoint.csr" \
      -nodes -subj "/CN=$endpoint" >/dev/null 2>&1
    chmod 600 "$dir/certs/$endpoint.key"
    echo "CSR is $dir/certs/$endpoint.csr"
    echo "Copy only the CSR to the host. The private key stays here."
    ;;
  sign)
    [ "$#" -eq 3 ] || usage
    dir=$1
    endpoint=$2
    csr=$3
    printf '%s\n' \
      "basicConstraints=critical,CA:FALSE" \
      "keyUsage=critical,digitalSignature" \
      "extendedKeyUsage=clientAuth" \
      "subjectKeyIdentifier=hash" \
      "authorityKeyIdentifier=keyid,issuer" > "$dir/certs/$endpoint.ext"
    openssl x509 -req -in "$csr" -CA "$dir/certs/ca.pem" -CAkey "$dir/certs/ca.key" \
      -CAcreateserial -out "$dir/certs/$endpoint.pem" -days 2 -extfile "$dir/certs/$endpoint.ext" >/dev/null 2>&1
    run_tool "$dir" agent-bus-enroll add --file "$dir/enrollment.json" --endpoint "$endpoint" --cert "$dir/certs/$endpoint.pem"
    echo "Signed certificate is $dir/certs/$endpoint.pem"
    echo "Copy that certificate and ca.pem back to the client. Do not copy a private key."
    ;;
  host)
    [ "$#" -eq 1 ] || usage
    dir=$1
    host_ip=$(cat "$dir/host_ip")
    port=$(cat "$dir/port")
    require_host_ip "$host_ip" || exit 2
    export AGENT_BUS_STATE_DIR="$dir/state"
    export AGENT_BUS_CONFIG="$dir/endpoints.json"
    export AGENT_BUS_ENROLLMENT="$dir/enrollment.json"
    export AGENT_BUS_TLS_CERT="$dir/certs/server.pem"
    export AGENT_BUS_TLS_KEY="$dir/certs/server.key"
    export AGENT_BUS_TLS_CLIENT_CA="$dir/certs/ca.pem"
    export AGENT_BUS_REMOTE_HOST="$host_ip"
    export AGENT_BUS_REMOTE_PORT="$port"
    export AGENT_BUS_REMOTE_PIDFILE="$dir/host.pid"
    if [ -f "$dir/host.pid" ]; then
      existing=$(tr -d '[:space:]' < "$dir/host.pid")
      case "$existing" in
        ''|*[!0-9]*)
          echo "listener pid file is not a process id; not removing it" >&2
          exit 1
          ;;
      esac
      if kill -0 "$existing" 2>/dev/null; then
        if is_our_listener "$existing"; then
          echo "host already running" >&2
          exit 1
        fi
        echo "process $existing is not this host; not removing its pid file" >&2
        exit 1
      fi
      rm -f "$dir/host.pid"
    fi
    echo "listening on $host_ip:$port"
    echo "stop with Ctrl-C, or: sh two-machine.sh stop $dir"
    if [ -x "$dir/venv/bin/agent-bus-remote" ]; then
      "$dir/venv/bin/agent-bus-remote" &
    else
      python3 -m agent_bus_mcp.remote &
    fi
    listener=$!
    i=0
    while [ "$i" -lt 50 ]; do
      if [ -s "$dir/host.pid" ]; then
        break
      fi
      if ! kill -0 "$listener" 2>/dev/null; then
        echo "host exited before it recorded its process id" >&2
        wait "$listener" || true
        exit 1
      fi
      i=$((i + 1))
      sleep 0.1
    done
    if [ ! -s "$dir/host.pid" ]; then
      kill "$listener" 2>/dev/null || true
      echo "host did not record its process id" >&2
      exit 1
    fi
    recorded=$(cat "$dir/host.pid")
    if [ "$recorded" != "$listener" ]; then
      echo "refusing to track a process id that is not the listener" >&2
      exit 1
    fi
    trap 'stop_listener "$dir"; exit 0' INT TERM
    wait "$listener"
    ;;
  client)
    [ "$#" -ge 4 ] || usage
    dir=$1
    endpoint=$2
    tool_name=$3
    request_id=$4
    shift 4
    host_ip=$(cat "$dir/host_ip")
    port=$(cat "$dir/port")
    export AGENT_BUS_REMOTE_URL="https://$host_ip:$port"
    export AGENT_BUS_REMOTE_CA="$dir/certs/ca.pem"
    export AGENT_BUS_CLIENT_CERT="$dir/certs/$endpoint.pem"
    export AGENT_BUS_CLIENT_KEY="$dir/certs/$endpoint.key"
    run_tool "$dir" agent-bus-remote-call --tool "$tool_name" --request-id "$request_id" "$@"
    ;;
  stop)
    [ "$#" -eq 1 ] || usage
    stop_listener "$1"
    ;;
  clean)
    [ "$#" -eq 1 ] || usage
    dir=$1
    if [ -f "$dir/host.pid" ]; then
      stop_listener "$dir" || true
    fi
    rm -rf "$dir"
    echo "removed $dir"
    ;;
  *)
    usage
    ;;
esac

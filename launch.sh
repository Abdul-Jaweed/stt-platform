#!/usr/bin/env bash
#
# One command to run the whole platform.
#
#   ./launch.sh            start (builds on first run, generates .env if missing)
#   ./launch.sh gpu        start with GPU passthrough for the local model
#   ./launch.sh mlflow     add the MLflow tracking server (evaluations log there)
#   ./launch.sh status     what is up, what is healthy, what it answered
#   ./launch.sh logs [svc] follow logs (all services, or one)
#   ./launch.sh restart    restart the application containers
#   ./launch.sh stop       stop everything, keep data
#   ./launch.sh clean      stop and delete containers, volumes and images
#   ./launch.sh rebuild    rebuild the application images, then restart
#
# Works in bash on Linux, macOS and Windows (git-bash). Docker must be running.

set -euo pipefail

cd "$(dirname "$0")"

COMPOSE=(docker compose -f docker-compose.yml)
PROJECT=stt-platform
API_URL=http://127.0.0.1:8200
UI_URL=http://127.0.0.1:8080
READY_TIMEOUT=${READY_TIMEOUT:-300}

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

need_docker() {
  command -v docker >/dev/null 2>&1 || die "docker is not on PATH — install Docker Desktop or Docker Engine."
  docker info >/dev/null 2>&1 || die "the Docker daemon is not answering — start Docker and try again."
  docker compose version >/dev/null 2>&1 || die "docker compose (v2) is required."
}

# A Python that actually runs, or nothing. On Windows the Microsoft Store
# "app execution alias" puts a python3.exe on PATH that only prints an error,
# so every candidate has to prove itself rather than merely exist.
find_python() {
  local candidate
  for candidate in python python3 py; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import secrets' >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

random_hex() {
  local py
  if py=$(find_python); then "$py" -c 'import secrets;print(secrets.token_hex(32))'; return 0; fi
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32; return 0; fi
  return 1
}

random_password() {
  local py
  if py=$(find_python); then
    "$py" -c 'import secrets,string;a=string.ascii_letters+string.digits;print("".join(secrets.choice(a) for _ in range(24)))'
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then openssl rand -base64 32 | tr -d '/+=\n' | cut -c1-24; return 0; fi
  return 1
}

# .env holds the deployment's secrets; it is gitignored and created once.
ensure_env() {
  if [ ! -f .env ]; then
    [ -f .env.example ] || die ".env.example is missing — is this the repository root?"
    cp .env.example .env
    info "created .env from .env.example"
  fi

  local generated_password="" generated_secret=""
  if ! grep -q '^STT_UI_PASSWORD=.\+' .env; then
    generated_password=$(random_password) ||
      die "cannot generate a UI password: install python or openssl, or set STT_UI_PASSWORD in .env yourself."
    replace_env STT_UI_PASSWORD "$generated_password"
  fi
  if ! grep -q '^STT_SESSION_SECRET=.\+' .env; then
    generated_secret=$(random_hex) ||
      die "cannot generate a session secret: install python or openssl, or set STT_SESSION_SECRET in .env yourself."
    replace_env STT_SESSION_SECRET "$generated_secret"
  fi

  if [ -n "$generated_password" ]; then
    bold "Generated UI credentials (also written to .env):"
    info "username: $(env_value STT_UI_USERNAME stt)"
    info "password: $generated_password"
    info "Change it in .env and re-run ./launch.sh restart."
  fi
  if [ -n "$generated_secret" ]; then
    info "Generated a session secret so logins survive a restart."
  fi
}

replace_env() {
  local key=$1 value=$2
  if grep -q "^${key}=" .env; then
    # Portable in-place edit (sed -i differs between GNU and BSD).
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{print k "=" v; next} {print}' .env > .env.tmp
    mv .env.tmp .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

env_value() {
  local key=$1 default=${2:-}
  local line
  line=$(grep "^${key}=" .env 2>/dev/null | tail -1 || true)
  if [ -z "$line" ]; then printf '%s' "$default"; else printf '%s' "${line#*=}"; fi
}

# Compose reads .env itself; this keeps the script's own view in step.
load_env() { set -a; . ./.env; set +a; }

has_gpu() {
  docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"' || return 1
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  nvidia-smi >/dev/null 2>&1 || return 1
  return 0
}

wait_for_http() {
  local url=$1 label=$2 deadline=$((SECONDS + READY_TIMEOUT))
  printf '  waiting for %s' "$label"
  while [ $SECONDS -lt $deadline ]; do
    # Redirect the body with the shell, not with curl's -o: on MSYS/git-bash
    # `curl -o /dev/null` exits 23 (write error) even for a healthy endpoint,
    # which made this wait report "did not answer" for a stack that was up.
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      printf ' — ready\n'
      return 0
    fi
    printf '.'
    sleep 3
  done
  printf '\n'
  warn "$label did not answer within ${READY_TIMEOUT}s — check ./launch.sh logs"
  return 1
}

start() {
  need_docker
  ensure_env
  load_env

  if [ "${1:-}" = "gpu" ]; then
    if has_gpu; then
      COMPOSE+=(-f docker-compose.gpu.yml)
      bold "GPU passthrough enabled (NVIDIA runtime detected)."
      info "model: $(env_value STT_WHISPER_MODEL_GPU large-v3-turbo) on CUDA (float16)"
      info "the CPU default in .env (STT_WHISPER_MODEL) does not apply here"
    else
      warn "no NVIDIA container runtime (or no working nvidia-smi): starting on CPU."
      info "model: $(env_value STT_WHISPER_MODEL small) (set STT_WHISPER_MODEL to change it)"
      warn "See docker-compose.gpu.yml for what a GPU host needs."
    fi
  fi

  bold "Building images (cached after the first run)…"
  "${COMPOSE[@]}" build

  bold "Starting the stack…"
  "${COMPOSE[@]}" up -d

  echo
  bold "Waiting for the platform to come up…"
  wait_for_http "$API_URL/v1/health/ready" "the API" || true
  wait_for_http "$UI_URL/" "the console" || true

  echo
  bold "The platform is up:"
  info "console   $UI_URL"
  info "API       $API_URL/v1  (docs at $API_URL/docs)"
  info "health    $API_URL/v1/health/ready"
  info "Temporal  http://127.0.0.1:28080"
  info "storage   http://127.0.0.1:29001 (console)"
  if [ "$(env_value STT_MLFLOW_ENABLED false)" = "true" ]; then
    info "MLflow    http://127.0.0.1:5000"
  else
    info "MLflow    not enabled — ./launch.sh mlflow turns it on"
  fi
  echo
  info "Sign in with the credentials in .env (STT_UI_USERNAME / STT_UI_PASSWORD)."
  info "First transcription downloads the model if the cache is cold: ./launch.sh logs api"
}

status() {
  need_docker
  "${COMPOSE[@]}" ps
  echo
  for probe in "$API_URL/v1/health/ready|API" "$UI_URL/health|console→API" \
               "http://127.0.0.1:28080|Temporal UI" "http://127.0.0.1:29000/health|object storage"; do
    url=${probe%%|*}
    label=${probe##*|}
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      printf '  \033[32mok\033[0m    %s (%s)\n' "$label" "$url"
    else
      printf '  \033[31mdown\033[0m  %s (%s)\n' "$label" "$url"
    fi
  done
}

mlflow() {
  need_docker
  ensure_env
  if [ "$(env_value STT_MLFLOW_ENABLED false)" != "true" ]; then
    replace_env STT_MLFLOW_ENABLED true
    info "set STT_MLFLOW_ENABLED=true in .env"
  fi
  bold "Starting MLflow (profile: mlflow)…"
  "${COMPOSE[@]}" --profile mlflow up -d mlflow
  wait_for_http "http://127.0.0.1:5000/health" "MLflow" || true
  bold "Restarting the API and worker so they pick up the tracking server…"
  "${COMPOSE[@]}" up -d api worker
  info "MLflow is at http://127.0.0.1:5000 — evaluations log there."
}

case "${1:-start}" in
  start)   start ;;
  gpu)     start gpu ;;
  stop)    need_docker; bold "Stopping (data volumes kept)…"; "${COMPOSE[@]}" down ;;
  restart) need_docker; bold "Restarting…"; "${COMPOSE[@]}" up -d --force-recreate api worker frontend; wait_for_http "$API_URL/v1/health/ready" "the API" || true ;;
  rebuild) need_docker; bold "Rebuilding…"; "${COMPOSE[@]}" build; "${COMPOSE[@]}" up -d; wait_for_http "$API_URL/v1/health/ready" "the API" || true ;;
  logs)    need_docker; shift || true; "${COMPOSE[@]}" logs -f --tail=100 "$@" ;;
  status)  status ;;
  mlflow)  mlflow ;;
  clean)
    need_docker
    bold "This deletes containers, the database, stored audio and both images."
    read -r -p "  Type 'yes' to continue: " answer
    [ "$answer" = "yes" ] || { info "nothing was deleted."; exit 0; }
    "${COMPOSE[@]}" --profile mlflow down -v --remove-orphans
    docker image rm -f stt-platform-backend:latest stt-platform-frontend:latest >/dev/null 2>&1 || true
    info "clean."
    ;;
  help|-h|--help)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    die "unknown command '$1' — try ./launch.sh help"
    ;;
esac

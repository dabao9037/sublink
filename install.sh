#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="SubLink"
APP_SLUG="sublink"
INSTALL_DIR="${SUBLINK_INSTALL_DIR:-/opt/sublink}"
STATE_DIR="${SUBLINK_STATE_DIR:-/etc/sublink}"
STATE_FILE="$STATE_DIR/config.env"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
REPO_URL="${SUBLINK_REPO_URL:-https://github.com/dabao9037/sublink.git}"
RAW_BASE="${SUBLINK_RAW_BASE:-https://raw.githubusercontent.com/dabao9037/sublink/main}"
DEFAULT_PORT="${SUBLINK_DEFAULT_PORT:-8096}"
ACTION="${1:-menu}"
ARG2="${2:-}"
ARG3="${3:-}"

C_RESET='\033[0m'; C_GREEN='\033[32m'; C_YELLOW='\033[33m'; C_RED='\033[31m'; C_CYAN='\033[36m'; C_BOLD='\033[1m'

info(){ echo -e "${C_CYAN}[信息]${C_RESET} $*"; }
success(){ echo -e "${C_GREEN}[成功]${C_RESET} $*"; }
warn(){ echo -e "${C_YELLOW}[提示]${C_RESET} $*"; }
die(){ echo -e "${C_RED}[错误]${C_RESET} $*" >&2; exit 1; }

require_root(){ [ "${EUID:-$(id -u)}" -eq 0 ] || die "请使用 root 用户运行：sudo bash install.sh"; }
command_exists(){ command -v "$1" >/dev/null 2>&1; }
random_string(){ tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-16}" || true; }
fernet_key(){
  if command_exists openssl; then
    openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
  else
    python3 - <<'PY'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
  fi
}

load_state(){
  [ -f "$STATE_FILE" ] || die "尚未安装 $APP_NAME，请先选择 1 安装。"
  # Old releases did not have SESSION_SECRET. Define it before sourcing while
  # set -u is active so upgrading those installations never aborts.
  SESSION_SECRET="${SESSION_SECRET-}"
  # shellcheck disable=SC1090
  source "$STATE_FILE"
}

save_state(){
  mkdir -p "$STATE_DIR"
  umask 077
  cat >"$STATE_FILE" <<EOF
SUBLINK_PORT=${SUBLINK_PORT}
ADMIN_USER=${ADMIN_USER}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
APP_SECRET=${APP_SECRET}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-}
DOMAIN=${DOMAIN:-}
DOMAIN_HTTPS=${DOMAIN_HTTPS:-0}
EOF
  chmod 600 "$STATE_FILE"
}

install_docker(){
  if command_exists docker && docker compose version >/dev/null 2>&1; then return; fi
  info "正在安装 Docker Engine 与 Compose..."
  command_exists curl || { apt-get update -y && apt-get install -y curl ca-certificates; }
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
  docker compose version >/dev/null 2>&1 || die "Docker Compose 安装失败。"
}

install_packages(){
  local packages=(curl ca-certificates git openssl)
  local missing=()
  for p in "${packages[@]}"; do command_exists "$p" || missing+=("$p"); done
  if ((${#missing[@]})); then
    info "安装基础依赖：${missing[*]}"
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
  fi
}

validate_port(){
  [[ "$1" =~ ^[0-9]+$ ]] && ((1 <= 10#$1 && 10#$1 <= 65535)) || die "端口必须为 1-65535。"
}

port_in_use(){
  local p="$1"
  if command_exists ss; then ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$p$"; else return 1; fi
}

find_free_port(){
  local p="$DEFAULT_PORT"
  while port_in_use "$p"; do p=$((p+1)); [ "$p" -le 65535 ] || die "没有可用端口。"; done
  echo "$p"
}

public_ip(){
  local ip=""
  ip=$(curl -4fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)
  [ -n "$ip" ] || ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo "${ip:-127.0.0.1}"
}

dns_points_to_this_host(){
  local domain="$1" public addresses
  public="$(public_ip)"
  addresses="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u || true)"
  [[ -n "$public" && -n "$addresses" ]] || return 0
  if ! grep -Fxq "$public" <<<"$addresses"; then
    warn "域名当前解析到：$(tr '\n' ' ' <<<"$addresses")"
    warn "本机公网 IPv4：$public"
    die "域名未解析到本机，或 Cloudflare 代理已开启。首次签发证书时请先把该 DNS 记录改为‘仅 DNS（灰云）’，签发成功后再开启代理"
  fi
}

fetch_source(){
  local tmp data_keep=""
  tmp=$(mktemp -d)
  if [ -f "$(cd "$(dirname "$0")" && pwd)/Dockerfile" ] && [ -d "$(cd "$(dirname "$0")" && pwd)/app" ]; then
    info "使用当前目录中的项目文件。"
    cp -a "$(cd "$(dirname "$0")" && pwd)/." "$tmp/"
  elif command_exists git && git clone --depth 1 "$REPO_URL" "$tmp/repo" >/dev/null 2>&1; then
    cp -a "$tmp/repo/." "$tmp/"
    rm -rf "$tmp/repo"
  else
    info "从 GitHub 下载项目文件..."
    curl -fsSL "${REPO_URL%.git}/archive/refs/heads/main.tar.gz" | tar -xz --strip-components=1 -C "$tmp"
  fi
  mkdir -p "$INSTALL_DIR"
  if [ -d "$INSTALL_DIR/data" ]; then
    data_keep=$(mktemp -d)
    mv "$INSTALL_DIR/data" "$data_keep/data"
  fi
  find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  cp -a "$tmp/." "$INSTALL_DIR/"
  if [ -n "$data_keep" ] && [ -d "$data_keep/data" ]; then
    rm -rf "$INSTALL_DIR/data"
    mv "$data_keep/data" "$INSTALL_DIR/data"
    rmdir "$data_keep" 2>/dev/null || true
  fi
  rm -rf "$tmp"
  mkdir -p "$INSTALL_DIR/data"
  chmod 777 "$INSTALL_DIR/data"
  rm -rf "$INSTALL_DIR/.git" "$INSTALL_DIR/.github" "$INSTALL_DIR/tests" "$INSTALL_DIR/.pytest_cache" 2>/dev/null || true
}

write_compose(){
  cat >"$COMPOSE_FILE" <<'YAML'
services:
  sublink:
    build: .
    container_name: sublink
    restart: unless-stopped
    environment:
      DB_PATH: /data/subscriptions.db
      APP_SECRET: ${APP_SECRET}
      ADMIN_USER: ${ADMIN_USER}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL}
    volumes:
      - ./data:/data
    ports:
      - "${SUBLINK_PORT}:8080"
YAML
}

write_runtime_env(){
  umask 077
  cat >"$INSTALL_DIR/.env" <<EOF
SUBLINK_PORT=${SUBLINK_PORT}
ADMIN_USER=${ADMIN_USER}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
APP_SECRET=${APP_SECRET}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-}
EOF
  chmod 600 "$INSTALL_DIR/.env"
}

open_firewall_port(){
  local port="$1"
  if command_exists ufw && ufw status 2>/dev/null | grep -q '^Status: active'; then ufw allow "${port}/tcp" >/dev/null; fi
  if command_exists firewall-cmd && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null
    firewall-cmd --reload >/dev/null
  fi
}

close_firewall_port(){
  local port="$1"
  if command_exists ufw && ufw status 2>/dev/null | grep -q '^Status: active'; then ufw delete allow "${port}/tcp" >/dev/null 2>&1 || true; fi
  if command_exists firewall-cmd && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --remove-port="${port}/tcp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
  fi
}

start_app(){
  write_compose
  write_runtime_env
  (cd "$INSTALL_DIR" && docker compose up -d --build)
  local i
  for i in {1..30}; do
    curl -fsS "http://127.0.0.1:${SUBLINK_PORT}/healthz" >/dev/null 2>&1 && return 0
    sleep 1
  done
  docker logs --tail 80 sublink 2>&1 || true
  die "服务启动失败，请检查上方日志。"
}

show_credentials(){
  local ip scheme host url
  ip=$(public_ip)
  if [ -n "${DOMAIN:-}" ]; then
    scheme="http"; [ "${DOMAIN_HTTPS:-0}" = "1" ] && scheme="https"
    host="$DOMAIN"; url="${scheme}://${host}"
  else
    url="http://${ip}:${SUBLINK_PORT}"
  fi
  echo
  echo -e "${C_GREEN}${C_BOLD}══════════ $APP_NAME 已就绪 ══════════${C_RESET}"
  echo -e "后台地址：${C_BOLD}${url}${C_RESET}"
  echo -e "用户名：  ${C_BOLD}${ADMIN_USER}${C_RESET}"
  echo -e "密码：    ${C_BOLD}${ADMIN_PASSWORD}${C_RESET}"
  echo -e "管理命令：${C_BOLD}sublink${C_RESET}"
  echo -e "配置文件：${C_BOLD}${STATE_FILE}${C_RESET}"
  echo -e "${C_GREEN}${C_BOLD}═══════════════════════════════════${C_RESET}"
  echo
}

install_app(){
  require_root
  install_packages
  install_docker
  if [ -f "$STATE_FILE" ]; then
    load_state
    warn "检测到已有安装，将保留数据并更新程序。"
  else
    SUBLINK_PORT=$(find_free_port)
    ADMIN_USER="admin_$(random_string 6)"
    ADMIN_PASSWORD=$(random_string 18)
    APP_SECRET=$(fernet_key)
    PUBLIC_BASE_URL=""
    DOMAIN=""
    DOMAIN_HTTPS=0
  fi
  fetch_source
  save_state
  start_app
  open_firewall_port "$SUBLINK_PORT"
  install -m 755 "$INSTALL_DIR/install.sh" /usr/local/bin/sublink
  success "$APP_NAME 安装完成。"
  show_credentials
}

change_port(){
  require_root; load_state
  local new_port="${ARG2:-}" old_port="$SUBLINK_PORT"
  if [ -z "$new_port" ]; then read -rp "请输入新端口 [当前 ${SUBLINK_PORT}]：" new_port; fi
  validate_port "$new_port"
  if [ "$new_port" != "$SUBLINK_PORT" ] && port_in_use "$new_port"; then die "端口 $new_port 已被占用。"; fi
  SUBLINK_PORT="$new_port"
  if [ -z "${DOMAIN:-}" ]; then PUBLIC_BASE_URL=""; fi
  save_state; start_app
  open_firewall_port "$SUBLINK_PORT"
  [ "$old_port" = "$SUBLINK_PORT" ] || close_firewall_port "$old_port"
  if [ -n "${DOMAIN:-}" ] && [ -f /etc/nginx/sites-available/sublink.conf ]; then
    sed -i "s#127.0.0.1:${old_port}#127.0.0.1:${SUBLINK_PORT}#g" /etc/nginx/sites-available/sublink.conf
    nginx -t && systemctl reload nginx
  fi
  success "端口已修改。"
  show_credentials
}

change_credentials(){
  require_root; load_state
  local new_user="${ARG2:-}" new_pass="${ARG3:-}"
  if [ -z "$new_user" ]; then read -rp "请输入新用户名 [留空随机生成]：" new_user; fi
  if [ -z "$new_pass" ]; then read -rsp "请输入新密码 [留空随机生成]：" new_pass; echo; fi
  new_user="${new_user:-admin_$(random_string 6)}"
  new_pass="${new_pass:-$(random_string 18)}"
  [[ "$new_user" =~ ^[A-Za-z0-9_.-]{3,64}$ ]] || die "用户名仅允许字母、数字、点、下划线和短横线，长度 3-64。"
  [[ "$new_pass" =~ ^[A-Za-z0-9_.@#%+=:-]{5,128}$ ]] || die "密码需为 5-128 位，仅允许字母、数字及 ._@#%+=:-。"
  ADMIN_USER="$new_user"; ADMIN_PASSWORD="$new_pass"
  save_state; start_app
  success "后台账号密码已修改。"
  show_credentials
}

ensure_certbot(){
  if ! command_exists certbot; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y certbot
  fi
}

listener_summary(){
  ss -H -ltnp "sport = :$1" 2>/dev/null || true
}

port_is_listening(){ listener_summary "$1" | grep -q .; }

port_owned_only_by(){
  local port="$1" pattern="$2" listeners
  listeners="$(listener_summary "$port")"
  [ -n "$listeners" ] || return 1
  grep -Eq "$pattern" <<<"$listeners" && ! grep -Eqv "$pattern" <<<"$listeners"
}

caddy_config_file(){
  local fragment_dir="/etc/caddy/conf.d"
  if [ -f /etc/caddy/Caddyfile ] && grep -Eq '^\s*import\s+conf\.d/\*' /etc/caddy/Caddyfile; then
    mkdir -p "$fragment_dir"
    printf '%s' "$fragment_dir/sublink.caddy"
  else
    printf '%s' /etc/caddy/Caddyfile
  fi
}

bind_domain_caddy(){
  local domain="$1" backup="$2" config begin='# BEGIN SUBLINK MANAGED' end='# END SUBLINK MANAGED' temporary
  config="$(caddy_config_file)"
  command_exists caddy || die "检测到 80 端口由 Caddy 占用，但找不到 caddy 命令"
  mkdir -p "$(dirname "$config")"
  [ ! -e "$config" ] || cp -a "$config" "$backup/caddy.conf"
  temporary="$(mktemp)"
  if [ -f "$config" ]; then
    awk -v begin="$begin" -v end="$end" '
      $0 == begin {skip=1; next}
      $0 == end {skip=0; next}
      !skip {print}
    ' "$config" >"$temporary"
  fi
  cat >>"$temporary" <<EOF

$begin
${domain} {
    encode zstd gzip
    reverse_proxy 127.0.0.1:${SUBLINK_PORT}
}
$end
EOF
  install -m 0644 "$temporary" "$config"
  rm -f "$temporary"
  if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
    rm -f "$config"
    [ ! -e "$backup/caddy.conf" ] || cp -a "$backup/caddy.conf" "$config"
    die "Caddy 配置校验失败，已恢复原配置"
  fi
  if ! caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
    rm -f "$config"
    [ ! -e "$backup/caddy.conf" ] || cp -a "$backup/caddy.conf" "$config"
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 && \
      caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
    die "Caddy 管理接口重载失败，已恢复原配置；请检查 caddy 是否启用了 admin API"
  fi
  local i
  for i in {1..45}; do
    if curl -fsS --connect-timeout 2 --max-time 5 --resolve "${domain}:443:127.0.0.1" "https://${domain}/" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  journalctl -u caddy --no-pager -n 60 2>/dev/null || true
  die "Caddy 已加载反向代理，但 45 秒内未完成 HTTPS；请确认域名为灰云且 80/443 可从公网访问"
}

bind_domain_apache(){
  local domain="$1" backup="$2"
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-certbot-apache
  a2enmod proxy proxy_http headers ssl rewrite >/dev/null
  mkdir -p /etc/apache2/sites-available
  [ ! -e /etc/apache2/sites-available/sublink.conf ] || cp -a /etc/apache2/sites-available/sublink.conf "$backup/apache.conf"
  cat >/etc/apache2/sites-available/sublink.conf <<EOF
<VirtualHost *:80>
    ServerName ${domain}
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:${SUBLINK_PORT}/
    ProxyPassReverse / http://127.0.0.1:${SUBLINK_PORT}/
    RequestHeader set X-Forwarded-Proto "http"
    ErrorLog \${APACHE_LOG_DIR}/sublink-error.log
    CustomLog \${APACHE_LOG_DIR}/sublink-access.log combined
</VirtualHost>
EOF
  a2ensite sublink.conf >/dev/null
  apache2ctl configtest || die "Apache 配置校验失败"
  systemctl reload apache2 || die "Apache 重载失败"
  if ! certbot --apache -d "$domain" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
    rm -f /etc/apache2/sites-enabled/sublink.conf /etc/apache2/sites-available/sublink.conf
    [ ! -e "$backup/apache.conf" ] || { cp -a "$backup/apache.conf" /etc/apache2/sites-available/sublink.conf; a2ensite sublink.conf >/dev/null; }
    apache2ctl configtest >/dev/null 2>&1 && systemctl reload apache2 || true
    die "HTTPS 证书申请失败，已恢复 Apache 配置"
  fi
}

bind_domain_nginx(){
  local domain="$1" backup="$2" was_active=0
  command_exists nginx || DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-certbot-nginx
  mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
  [ ! -e /etc/nginx/sites-available/sublink.conf ] || cp -a /etc/nginx/sites-available/sublink.conf "$backup/nginx.conf"
  systemctl is-active --quiet nginx && was_active=1
  cat >/etc/nginx/sites-available/sublink.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    client_max_body_size 300k;
    location / {
        proxy_pass http://127.0.0.1:${SUBLINK_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  ln -sfn /etc/nginx/sites-available/sublink.conf /etc/nginx/sites-enabled/sublink.conf
  nginx -t || die "Nginx 配置校验失败"
  systemctl enable nginx >/dev/null
  if (( was_active )); then systemctl reload nginx; else systemctl start nginx; fi || die "Nginx 启动或重载失败"
  certbot --nginx -d "$domain" --non-interactive --agree-tos --register-unsafely-without-email --redirect || die "HTTPS 证书申请失败"
}

bind_domain(){
  require_root; load_state
  local domain="${ARG2:-}" enable_https="${ARG3:-}" backup
  [ -n "$domain" ] || read -rp "请输入已解析到本机 IP 的域名：" domain
  domain="${domain#http://}"; domain="${domain#https://}"; domain="${domain%%/*}"; domain="${domain,,}"
  [[ "$domain" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || die "域名格式不正确。"
  [ -n "$enable_https" ] || read -rp "是否自动申请 HTTPS 证书？[Y/n]：" enable_https
  if [[ "$enable_https" =~ ^[Nn]$ ]]; then
    warn "未申请证书；请自行把现有 Web 服务反向代理到 127.0.0.1:${SUBLINK_PORT}。"
    return
  fi
  dns_points_to_this_host "$domain"
  ensure_certbot
  backup="$(mktemp -d)"
  if port_is_listening 80 && port_owned_only_by 80 '"caddy"'; then
    if port_is_listening 443 && ! port_owned_only_by 443 '"caddy"'; then
      listener_summary 443 >&2; rm -rf "$backup"; die "443 端口被非 Caddy 程序占用，未修改现有服务"
    fi
    bind_domain_caddy "$domain" "$backup"
    success "域名绑定完成：https://${domain}/（复用现有 Caddy）"
  elif port_is_listening 80 && port_owned_only_by 80 '"(apache2|httpd)"'; then
    bind_domain_apache "$domain" "$backup"
    success "域名绑定完成：https://${domain}/（复用现有 Apache）"
  elif ! port_is_listening 80 || port_owned_only_by 80 '"nginx"'; then
    bind_domain_nginx "$domain" "$backup"
    success "域名绑定完成：https://${domain}/（使用 Nginx）"
  else
    warn "80 端口占用详情："; listener_summary 80 >&2
    rm -rf "$backup"
    die "80 端口由其他程序占用；未停止现有服务，也未修改配置"
  fi
  rm -rf "$backup"
  DOMAIN="$domain"; DOMAIN_HTTPS=1; PUBLIC_BASE_URL="https://${domain}"
  save_state; write_runtime_env
  (cd "$INSTALL_DIR" && docker compose up -d --force-recreate sublink)
  show_credentials
}

uninstall_app(){
  require_root
  [ -f "$STATE_FILE" ] || { warn "$APP_NAME 未安装。"; return; }
  load_state
  local confirm="${ARG2:-}"
  if [ -z "$confirm" ]; then read -rp "确定卸载 $APP_NAME？输入 YES 继续：" confirm; fi
  [ "$confirm" = "YES" ] || { warn "已取消卸载。"; return; }
  local delete_data="${ARG3:-}"
  if [ -z "$delete_data" ]; then read -rp "是否同时删除订阅数据？[y/N]：" delete_data; fi
  if [ -f "$COMPOSE_FILE" ]; then (cd "$INSTALL_DIR" && docker compose down --remove-orphans) || true; fi
  close_firewall_port "$SUBLINK_PORT"
  rm -f /usr/local/bin/sublink
  rm -f /etc/nginx/sites-enabled/sublink.conf /etc/nginx/sites-available/sublink.conf
  command_exists nginx && nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  if [[ "$delete_data" =~ ^[Yy]$ ]]; then
    rm -rf "$INSTALL_DIR" "$STATE_DIR"
    success "$APP_NAME 及订阅数据已全部删除。"
  else
    mkdir -p "$STATE_DIR"
    [ -d "$INSTALL_DIR/data" ] && warn "订阅数据及解密配置已保留，下次安装可直接恢复。"
    find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {} + 2>/dev/null || true
    success "$APP_NAME 已卸载，订阅数据和加密配置已保留。"
  fi
}

show_status(){
  require_root
  if [ ! -f "$STATE_FILE" ]; then warn "$APP_NAME 尚未安装。"; return; fi
  load_state
  show_credentials
  docker ps --filter name='^/sublink$' --format '容器状态：{{.Status}}  端口：{{.Ports}}' || true
}

show_menu(){
  while true; do
    clear 2>/dev/null || true
    echo -e "${C_GREEN}${C_BOLD}┌──────────────────────────────────┐${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}│       SubLink 一键管理脚本       │${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}└──────────────────────────────────┘${C_RESET}"
    echo "  1. 一键安装 / 更新"
    echo "  2. 更换访问端口"
    echo "  3. 重设后台账号密码"
    echo "  4. 绑定域名与 HTTPS"
    echo "  5. 安全卸载"
    echo "  6. 查看运行信息"
    echo "  0. 退出"
    echo
    read -rp "请选择 [0-6]：" choice
    case "$choice" in
      1) install_app;; 2) change_port;; 3) change_credentials;; 4) bind_domain;; 5) uninstall_app;; 6) show_status;; 0) exit 0;; *) warn "无效选项。";;
    esac
    echo
    read -rp "按回车键返回菜单..." _ || true
  done
}

case "$ACTION" in
  menu|'') show_menu;; install) install_app;; port) change_port;; credentials) change_credentials;; domain) bind_domain;; uninstall) uninstall_app;; status) show_status;; *) die "未知命令：$ACTION";;
esac

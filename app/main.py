from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
from io import BytesIO
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml
import qrcode
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", "/data/subscriptions.db"))
APP_SECRET = os.environ.get("APP_SECRET", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
MAX_NODES = 200
MAX_INPUT_CHARS = 250_000
SUPPORTED_SCHEMES = {"vless", "vmess", "trojan", "ss"}
CLASH_USER_AGENT_MARKERS = (
    "clash", "mihomo", "stash", "flclash", "clashx", "clash-verge",
)

if not APP_SECRET:
    raise RuntimeError("APP_SECRET is required")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD is required")

fernet = Fernet(APP_SECRET.encode())
security = HTTPBasic()
app = FastAPI(title="节点转订阅", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static", html=False), name="static")


class SubscriptionInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    nodes: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                encrypted_nodes BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user_ok = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
            headers={"WWW-Authenticate": 'Basic realm="Node Subscription Admin"'},
        )
    return credentials.username


def split_nodes(raw: str) -> list[str]:
    lines: list[str] = []
    for piece in raw.replace("\r", "\n").split("\n"):
        node = piece.strip()
        if not node or node.startswith("#"):
            continue
        lines.append(node)
    unique = list(dict.fromkeys(lines))
    if not unique:
        raise HTTPException(422, "没有识别到节点链接")
    if len(unique) > MAX_NODES:
        raise HTTPException(422, f"单个订阅最多 {MAX_NODES} 个节点")
    errors = []
    for index, node in enumerate(unique, 1):
        scheme = node.split("://", 1)[0].lower() if "://" in node else ""
        if scheme not in SUPPORTED_SCHEMES:
            errors.append(f"第 {index} 行：不支持 {scheme or '未知'} 协议")
            continue
        try:
            parse_node(node, index)
        except ValueError as exc:
            errors.append(f"第 {index} 行：{exc}")
    if errors:
        raise HTTPException(422, "；".join(errors[:8]))
    return unique


def encrypt_nodes(nodes: list[str]) -> bytes:
    return fernet.encrypt(json.dumps(nodes, ensure_ascii=False).encode())


def decrypt_nodes(blob: bytes) -> list[str]:
    try:
        return json.loads(fernet.decrypt(blob).decode())
    except (InvalidToken, json.JSONDecodeError) as exc:
        raise HTTPException(500, "订阅数据无法解密") from exc


def public_url(request: Request, token: str, clash: bool = False) -> str:
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    suffix = "/clash" if clash else ""
    return f"{base}/s/{token}{suffix}"


def serialize_row(request: Request, row: sqlite3.Row, include_nodes: bool = False) -> dict[str, Any]:
    nodes = decrypt_nodes(row["encrypted_nodes"])
    data: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "node_count": len(nodes),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "links": {
            "universal": public_url(request, row["token"]),
            "qrcode": f"{public_url(request, row['token'])}/qr",
        },
    }
    if include_nodes:
        data["nodes"] = "\n".join(nodes)
    return data


def decode_b64(value: str) -> bytes:
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value)


def node_name(parsed, default: str) -> str:
    return unquote(parsed.fragment).strip() or default


def int_port(value: Any) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("端口范围无效")
    return port


def bool_value(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on", "tls"}


def add_transport(proxy: dict[str, Any], query: dict[str, list[str]]) -> None:
    network = query.get("type", [query.get("net", ["tcp"])[0]])[0] or "tcp"
    if network != "tcp":
        proxy["network"] = network
    if network == "ws":
        ws: dict[str, Any] = {}
        path = query.get("path", [""])[0]
        host = query.get("host", [""])[0]
        if path:
            ws["path"] = unquote(path)
        if host:
            ws["headers"] = {"Host": host}
        if ws:
            proxy["ws-opts"] = ws
    elif network == "grpc":
        service = query.get("serviceName", query.get("service-name", [""]))[0]
        if service:
            proxy["grpc-opts"] = {"grpc-service-name": unquote(service)}
    elif network == "http":
        path = query.get("path", [""])[0]
        host = query.get("host", [""])[0]
        opts: dict[str, Any] = {}
        if path:
            opts["path"] = [unquote(path)]
        if host:
            opts["headers"] = {"Host": [host]}
        if opts:
            proxy["http-opts"] = opts


def add_tls(proxy: dict[str, Any], query: dict[str, list[str]], tls_enabled: bool) -> None:
    if not tls_enabled:
        return
    proxy["tls"] = True
    servername = query.get("sni", query.get("servername", [""]))[0]
    if servername:
        proxy["servername"] = servername
    if bool_value(query.get("allowInsecure", query.get("skip-cert-verify", ["0"]))[0]):
        proxy["skip-cert-verify"] = True
    fingerprint = query.get("fp", query.get("client-fingerprint", [""]))[0]
    if fingerprint:
        proxy["client-fingerprint"] = fingerprint
    security_name = query.get("security", [""])[0]
    if security_name == "reality":
        reality: dict[str, Any] = {}
        public_key = query.get("pbk", query.get("public-key", [""]))[0]
        short_id = query.get("sid", query.get("short-id", [""]))[0]
        if public_key:
            reality["public-key"] = public_key
        if short_id:
            reality["short-id"] = short_id
        if reality:
            proxy["reality-opts"] = reality


def parse_vless_or_trojan(uri: str, kind: str, index: int) -> dict[str, Any]:
    parsed = urlparse(uri)
    if not parsed.hostname or not parsed.port or not parsed.username:
        raise ValueError(f"{kind.upper()} 链接缺少服务器、端口或凭据")
    query = parse_qs(parsed.query, keep_blank_values=True)
    proxy: dict[str, Any] = {
        "name": node_name(parsed, f"{kind.upper()}-{index}"),
        "type": kind,
        "server": parsed.hostname,
        "port": int_port(parsed.port),
        "udp": True,
    }
    if kind == "vless":
        proxy["uuid"] = unquote(parsed.username)
        flow = query.get("flow", [""])[0]
        if flow:
            proxy["flow"] = flow
    else:
        proxy["password"] = unquote(parsed.username)
    security_name = query.get("security", [""])[0]
    tls_enabled = security_name in {"tls", "reality"} or (kind == "trojan" and security_name != "none")
    add_tls(proxy, query, tls_enabled)
    add_transport(proxy, query)
    return proxy


def parse_vmess(uri: str, index: int) -> dict[str, Any]:
    try:
        payload = json.loads(decode_b64(uri.split("://", 1)[1]).decode())
    except Exception as exc:
        raise ValueError("VMess Base64/JSON 格式错误") from exc
    server = payload.get("add")
    uuid = payload.get("id")
    if not server or not uuid or not payload.get("port"):
        raise ValueError("VMess 链接缺少服务器、端口或 UUID")
    proxy: dict[str, Any] = {
        "name": str(payload.get("ps") or f"VMess-{index}"),
        "type": "vmess",
        "server": server,
        "port": int_port(payload["port"]),
        "uuid": uuid,
        "alterId": int(payload.get("aid") or 0),
        "cipher": payload.get("scy") or "auto",
        "udp": True,
    }
    query = {
        "type": [str(payload.get("net") or "tcp")],
        "path": [str(payload.get("path") or "")],
        "host": [str(payload.get("host") or "")],
        "serviceName": [str(payload.get("path") or "")],
        "sni": [str(payload.get("sni") or "")],
        "allowInsecure": [str(payload.get("allowInsecure") or "0")],
        "fp": [str(payload.get("fp") or "")],
    }
    add_tls(proxy, query, str(payload.get("tls") or "").lower() == "tls")
    add_transport(proxy, query)
    return proxy


def parse_ss(uri: str, index: int) -> dict[str, Any]:
    body, _, fragment = uri.split("://", 1)[1].partition("#")
    body = body.split("?", 1)[0]
    try:
        if "@" in body:
            userinfo, hostport = body.rsplit("@", 1)
            if ":" not in userinfo:
                userinfo = decode_b64(userinfo).decode()
        else:
            decoded = decode_b64(body).decode()
            userinfo, hostport = decoded.rsplit("@", 1)
        method, password = userinfo.split(":", 1)
        host, port_text = hostport.rsplit(":", 1)
    except Exception as exc:
        raise ValueError("Shadowsocks 链接格式错误") from exc
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return {
        "name": unquote(fragment).strip() or f"SS-{index}",
        "type": "ss",
        "server": host,
        "port": int_port(port_text),
        "cipher": unquote(method),
        "password": unquote(password),
        "udp": True,
    }


def parse_node(uri: str, index: int) -> dict[str, Any]:
    scheme = uri.split("://", 1)[0].lower()
    if scheme == "vmess":
        return parse_vmess(uri, index)
    if scheme in {"vless", "trojan"}:
        return parse_vless_or_trojan(uri, scheme, index)
    if scheme == "ss":
        return parse_ss(uri, index)
    raise ValueError("不支持的协议")


def unique_proxy_names(proxies: list[dict[str, Any]]) -> None:
    seen: dict[str, int] = {}
    for proxy in proxies:
        original = str(proxy["name"])
        seen[original] = seen.get(original, 0) + 1
        if seen[original] > 1:
            proxy["name"] = f"{original} ({seen[original]})"


def clash_config(name: str, nodes: list[str]) -> str:
    proxies = [parse_node(node, i) for i, node in enumerate(nodes, 1)]
    unique_proxy_names(proxies)
    names = [proxy["name"] for proxy in proxies]
    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "ipv6": True,
        "unified-delay": True,
        "tcp-concurrent": True,
        "proxies": proxies,
        "proxy-groups": [
            {"name": "节点选择", "type": "select", "proxies": ["自动选择", "DIRECT", *names]},
            {"name": "自动选择", "type": "url-test", "proxies": names, "url": "https://www.gstatic.com/generate_204", "interval": 300, "tolerance": 50},
        ],
        "rules": ["MATCH,节点选择"],
    }
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.url.path.startswith("/api") or request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sw.js")
def retired_service_worker() -> Response:
    return Response(
        "self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil((async()=>{for(const k of await caches.keys())await caches.delete(k);await self.registration.unregister();})()));",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Clear-Site-Data": '"cache"'},
    )


@app.get("/", response_class=HTMLResponse)
def index(_: str = Depends(require_admin)) -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "static" / "index.html").read_text())


@app.get("/api/subscriptions")
def list_subscriptions(request: Request, _: str = Depends(require_admin)) -> list[dict[str, Any]]:
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM subscriptions ORDER BY id DESC").fetchall()
    return [serialize_row(request, row) for row in rows]


@app.post("/api/subscriptions", status_code=201)
def create_subscription(payload: SubscriptionInput, request: Request, _: str = Depends(require_admin)) -> dict[str, Any]:
    nodes = split_nodes(payload.nodes)
    token = secrets.token_urlsafe(32)
    timestamp = now_iso()
    with closing(db()) as conn:
        cursor = conn.execute(
            "INSERT INTO subscriptions (name, token, encrypted_nodes, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (payload.name.strip(), token, encrypt_nodes(nodes), timestamp, timestamp),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return serialize_row(request, row, include_nodes=True)


@app.get("/api/subscriptions/{subscription_id}")
def get_subscription(subscription_id: int, request: Request, _: str = Depends(require_admin)) -> dict[str, Any]:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
    if not row:
        raise HTTPException(404, "订阅不存在")
    return serialize_row(request, row, include_nodes=True)


@app.put("/api/subscriptions/{subscription_id}")
def update_subscription(subscription_id: int, payload: SubscriptionInput, request: Request, _: str = Depends(require_admin)) -> dict[str, Any]:
    nodes = split_nodes(payload.nodes)
    with closing(db()) as conn:
        existing = conn.execute("SELECT id FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "订阅不存在")
        conn.execute(
            "UPDATE subscriptions SET name = ?, encrypted_nodes = ?, updated_at = ? WHERE id = ?",
            (payload.name.strip(), encrypt_nodes(nodes), now_iso(), subscription_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
    return serialize_row(request, row, include_nodes=True)


@app.delete("/api/subscriptions/{subscription_id}", status_code=204)
def delete_subscription(subscription_id: int, _: str = Depends(require_admin)) -> Response:
    with closing(db()) as conn:
        cursor = conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
        conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(404, "订阅不存在")
    return Response(status_code=204)


def find_public_subscription(token: str) -> sqlite3.Row:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(404, "订阅不存在")
    return row


def wants_clash(user_agent: str) -> bool:
    agent = (user_agent or "").lower()
    return any(marker in agent for marker in CLASH_USER_AGENT_MARKERS)


def subscription_headers(name: str) -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        "Profile-Update-Interval": "24",
        "Subscription-Title": base64.b64encode(name.encode()).decode(),
    }


@app.get("/s/{token}")
def universal_subscription(token: str, request: Request) -> PlainTextResponse:
    row = find_public_subscription(token)
    nodes = decrypt_nodes(row["encrypted_nodes"])
    if wants_clash(request.headers.get("user-agent", "")):
        return PlainTextResponse(
            clash_config(row["name"], nodes),
            media_type="text/yaml; charset=utf-8",
            headers=subscription_headers(row["name"]),
        )
    encoded = base64.b64encode("\n".join(nodes).encode()).decode()
    return PlainTextResponse(encoded, headers=subscription_headers(row["name"]))


@app.get("/s/{token}/qr")
def subscription_qrcode(token: str, request: Request) -> Response:
    row = find_public_subscription(token)
    url = public_url(request, row["token"])
    image = qrcode.make(url, box_size=8, border=3)
    output = BytesIO()
    image.save(output, format="PNG")
    return Response(
        output.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
    )


# 保留旧地址，兼容已复制的 Clash Meta 链接；新页面不再展示。
@app.get("/s/{token}/clash")
def clash_subscription(token: str) -> PlainTextResponse:
    row = find_public_subscription(token)
    nodes = decrypt_nodes(row["encrypted_nodes"])
    return PlainTextResponse(
        clash_config(row["name"], nodes),
        media_type="text/yaml; charset=utf-8",
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache", "Profile-Update-Interval": "24"},
    )

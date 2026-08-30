# SubLink

一个轻量、自托管的“多个节点转一个订阅”工具。

支持将多条 **VLESS / VMess / Trojan / Shadowsocks** 分享链接合并为一个长期订阅地址，并自动生成二维码。统一订阅地址会根据客户端自动输出对应格式：

- Clash Meta / Mihomo / FlClash / Stash → Clash YAML
- v2rayN / Shadowrocket 等其他客户端 → 通用 Base64

## 一键安装

支持 Ubuntu / Debian，需使用 root 用户：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/1828006573/sublink/main/install.sh)
```

脚本打开后会显示菜单：

```text
1. 一键安装 / 更新
2. 更换访问端口
3. 重设后台账号密码
4. 绑定域名与 HTTPS
5. 安全卸载
6. 查看运行信息
0. 退出
```

选择 **1** 后脚本会自动：

1. 安装 Docker 与 Docker Compose（若尚未安装）
2. 自动寻找空闲端口
3. 自动生成后台用户名和强密码
4. 构建并启动 SubLink
5. 输出 IP 访问地址、用户名和密码
6. 安装 `sublink` 管理命令

安装完成后，再次管理只需运行：

```bash
sublink
```

## 非交互命令

适合自动化测试或二次封装：

```bash
# 安装/更新
sudo bash install.sh install

# 修改端口
sudo bash install.sh port 18096

# 修改账号密码
sudo bash install.sh credentials NewAdmin NewPassword

# 绑定域名并申请 HTTPS（第三个参数为 y）
sudo bash install.sh domain sub.example.com y

# 仅绑定 HTTP 域名
sudo bash install.sh domain sub.example.com n

# 查看信息
sudo bash install.sh status

# 卸载，保留数据
sudo bash install.sh uninstall YES n

# 卸载并删除全部订阅数据
sudo bash install.sh uninstall YES y
```

## 域名绑定要求

选择菜单 **4** 前，请先把域名的 A 记录解析到服务器公网 IP，并确认：

- 80 和 443 端口已在云防火墙/安全组放行
- 域名未被其他网站占用
- 若申请 HTTPS，域名必须已正确解析到当前服务器

脚本会自动安装 Nginx、Certbot，并申请 Let's Encrypt 证书。

## 数据与配置

- 安装目录：`/opt/sublink`
- 订阅数据库：`/opt/sublink/data/subscriptions.db`
- 管理配置：`/etc/sublink/config.env`
- Docker 容器：`sublink`
- 管理命令：`/usr/local/bin/sublink`

修改端口、修改账号密码、更新程序都不会删除订阅数据。

## 安全说明

- 后台使用 HTTP Basic Auth
- 节点链接使用 Fernet 加密后存入 SQLite
- 订阅 URL 使用高强度随机 Token
- Nginx 域名配置会关闭 `/s/` 路径访问日志，避免订阅 Token 进入日志
- 不抓取远程订阅，不主动连接节点服务器

如果直接通过 IP + HTTP 使用，后台密码会以 HTTP Basic Auth 方式传输，**公网长期使用强烈建议绑定 HTTPS 域名**。

## 手动 Docker 部署

```bash
git clone https://github.com/1828006573/sublink.git
cd sublink
cp .env.example .env
# 编辑 .env
mkdir -p data && chmod 777 data
docker compose up -d --build
```

## 开发测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
PYTHONPATH=. .venv/bin/pytest -q
```

## License

MIT

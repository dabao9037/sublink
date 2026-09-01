import subprocess
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


CADDY_LISTENER_SAMPLE = 'LISTEN 0 8192 *:80 *:* users:(("caddy",pid=429372,fd=3))'


def test_installer_reuses_caddy_before_apache_or_nginx():
    source = INSTALLER.read_text(encoding="utf-8")
    caddy = source.index("bind_domain_caddy")
    apache = source.index("bind_domain_apache")
    nginx = source.index("bind_domain_nginx")
    assert caddy < apache < nginx
    assert "port_owned_only_by 80 '\"caddy\"'" in source
    assert "reverse_proxy 127.0.0.1:${SUBLINK_PORT}" in source
    reload_command = "caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile"
    assert source.count(reload_command) >= 2
    assert "systemctl reload caddy" not in source
    assert "复用现有 Caddy" in source


def test_real_ss_caddy_listener_matches_caddy_branch():
    source = INSTALLER.read_text(encoding="utf-8")
    definitions = source.split('\ncase "$ACTION" in\n', maxsplit=1)[0]
    harness = f"""{definitions}
listener_summary() {{ printf '%s\\n' '{CADDY_LISTENER_SAMPLE}'; }}
if port_is_listening 80 && port_owned_only_by 80 '"caddy"'; then
  printf caddy
else
  printf other
  exit 1
fi
"""
    result = subprocess.run(
        ["bash"], input=harness, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "caddy"


def test_installer_does_not_force_nginx_start():
    source = INSTALLER.read_text(encoding="utf-8")
    assert "systemctl enable --now nginx" not in source

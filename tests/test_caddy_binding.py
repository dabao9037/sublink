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
    assert "apply_caddy_config \"$managed_by_systemd\"" in source
    assert "systemctl restart caddy" in source
    assert '"https://${domain}/healthz"' in source
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


def run_installer_harness(body: str):
    source = INSTALLER.read_text(encoding="utf-8")
    definitions = source.split('\ncase "$ACTION" in\n', maxsplit=1)[0]
    return subprocess.run(
        ["bash"], input=f"{definitions}\n{body}\n", text=True, capture_output=True, check=False
    )


def test_admin_off_falls_back_to_systemd_restart():
    result = run_installer_harness(
        r'''
calls=""
caddy() {
  calls="${calls}caddy:$1 "
  [ "$1" != reload ]
}
systemctl() {
  calls="${calls}systemctl:$1 "
  case "$1" in
    restart|is-active) return 0 ;;
    *) return 1 ;;
  esac
}
apply_caddy_config 1 || exit 20
printf '%s' "$calls"
'''
    )
    assert result.returncode == 0, result.stderr
    assert "caddy:reload" in result.stdout
    assert "systemctl:restart" in result.stdout
    assert "systemctl:is-active" in result.stdout


def test_admin_off_without_systemd_fails_without_stopping_caddy():
    result = run_installer_harness(
        r'''
caddy() { [ "$1" != reload ]; }
systemctl() { printf 'systemctl must not run' >&2; return 99; }
if apply_caddy_config 0; then
  exit 21
fi
printf safe
'''
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "safe"


def test_working_admin_api_does_not_restart_caddy():
    result = run_installer_harness(
        r'''
caddy() { [ "$1" = reload ]; }
systemctl() { printf 'unexpected systemctl' >&2; return 99; }
apply_caddy_config 1 || exit 22
printf reloaded
'''
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "reloaded"

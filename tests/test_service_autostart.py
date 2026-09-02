import subprocess
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def installer_definitions() -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    return source.split('\ncase "$ACTION" in\n', maxsplit=1)[0]


def run_harness(body: str):
    return subprocess.run(
        ["bash"],
        input=f"{installer_definitions()}\n{body}\n",
        text=True,
        capture_output=True,
        check=False,
    )


def function_body(name: str, next_name: str) -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    return source.split(f"{name}(){{", maxsplit=1)[1].split(
        f"\n{next_name}(){{", maxsplit=1
    )[0]


def test_existing_docker_is_enabled_and_started_for_reboot():
    result = run_harness(
        r'''
calls=""
docker() { [ "$1 $2" = "compose version" ]; }
systemctl() {
  calls="${calls}$*\n"
  case "$1" in
    enable) return 0 ;;
    is-enabled|is-active) return 0 ;;
  esac
  return 1
}
command_exists() { [ "$1" = docker ] || [ "$1" = systemctl ]; }
install_docker
printf '%b' "$calls"
'''
    )
    assert result.returncode == 0, result.stderr
    assert "enable --now docker.service" in result.stdout
    assert "is-enabled --quiet docker.service" in result.stdout
    assert "is-active --quiet docker.service" in result.stdout


def test_apache_binding_persists_service_before_reload():
    body = function_body("bind_domain_apache", "bind_domain_nginx")
    assert 'require_active_systemd_listener apache2.service "Apache"' in body
    ensure = body.index('ensure_systemd_service apache2.service "Apache"')
    reload_service = body.index("systemctl reload apache2")
    assert ensure < reload_service


def test_caddy_binding_requires_persistent_systemd_service():
    body = function_body("bind_domain_caddy", "bind_domain_apache")
    assert 'require_active_systemd_listener caddy.service "Caddy"' in body
    assert 'ensure_systemd_service caddy.service "Caddy"' in body
    assert 'managed_by_systemd=1' in body


def test_existing_domain_repairs_only_uniquely_managed_service():
    result = run_harness(
        r'''
DOMAIN=example.com
calls=""
port_is_listening() { return 1; }
sublink_managed_web_services() { printf '%s\n' caddy.service; }
ensure_systemd_service() { calls="${calls}$1"; }
ensure_existing_domain_service
printf '%s' "$calls"
'''
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("caddy.service")


def test_existing_domain_does_not_start_competing_web_services():
    result = run_harness(
        r'''
DOMAIN=example.com
calls=""
port_is_listening() { return 1; }
sublink_managed_web_services() { printf '%s\n' apache2.service nginx.service; }
ensure_systemd_service() { calls="${calls}$1 "; }
ensure_existing_domain_service
printf 'calls=%s' "$calls"
'''
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("calls=")
    assert "多个 Web 服务" in result.stdout


def test_existing_domain_does_not_adopt_unmanaged_listener():
    result = run_harness(
        r'''
DOMAIN=example.com
calls=""
port_is_listening() { [ "$1" = 80 ]; }
port_owned_only_by() { [ "$2" = '"caddy"' ]; }
sublink_managed_web_services() { printf '%s\n' nginx.service; }
ensure_systemd_service() { calls="${calls}$1 "; }
ensure_existing_domain_service
printf 'calls=%s' "$calls"
'''
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("calls=")
    assert "没有 SubLink 托管配置" in result.stdout

from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def test_installer_reuses_caddy_before_apache_or_nginx():
    source = INSTALLER.read_text(encoding="utf-8")
    caddy = source.index("bind_domain_caddy")
    apache = source.index("bind_domain_apache")
    nginx = source.index("bind_domain_nginx")
    assert caddy < apache < nginx
    assert 'port_owned_only_by 80 \'users:\\\(\\\(("caddy")\'' in source
    assert "reverse_proxy 127.0.0.1:${SUBLINK_PORT}" in source
    assert "caddy validate --config /etc/caddy/Caddyfile" in source
    assert "systemctl reload caddy" in source
    assert "复用现有 Caddy" in source


def test_installer_does_not_force_nginx_start():
    source = INSTALLER.read_text(encoding="utf-8")
    assert "systemctl enable --now nginx" not in source

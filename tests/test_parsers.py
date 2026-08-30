import base64
import json

from app.main import clash_config, parse_node, split_nodes


def vmess_uri():
    payload = {"v": "2", "ps": "VM 测试", "add": "vm.example.com", "port": "443", "id": "11111111-1111-1111-1111-111111111111", "aid": "0", "net": "ws", "type": "none", "host": "cdn.example.com", "path": "/ws", "tls": "tls", "sni": "vm.example.com"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "vmess://" + encoded


def test_parse_protocols():
    nodes = [
        "vless://11111111-1111-1111-1111-111111111111@vl.example.com:443?security=reality&type=tcp&sni=www.example.com&fp=chrome&pbk=abc&sid=12#VLESS",
        vmess_uri(),
        "trojan://secret@tr.example.com:443?security=tls&type=grpc&sni=tr.example.com&serviceName=test#Trojan",
        "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ@ss.example.com:8388#SS",
    ]
    parsed = [parse_node(node, i) for i, node in enumerate(nodes, 1)]
    assert [p["type"] for p in parsed] == ["vless", "vmess", "trojan", "ss"]
    assert parsed[0]["reality-opts"]["public-key"] == "abc"
    assert parsed[1]["ws-opts"]["path"] == "/ws"
    assert parsed[2]["grpc-opts"]["grpc-service-name"] == "test"
    assert parsed[3]["password"] == "password"


def test_clash_config_and_dedupe():
    node = "trojan://secret@tr.example.com:443?security=tls#Same"
    output = clash_config("Test", [node, node])
    assert "Same (2)" in output
    assert "MATCH,节点选择" in output


def test_split_nodes_rejects_bad_scheme():
    try:
        split_nodes("http://example.com")
        assert False
    except Exception as exc:
        assert "不支持" in str(exc.detail)


def test_clash_user_agent_detection():
    from app.main import wants_clash
    assert wants_clash("ClashMetaForAndroid/2.11.17")
    assert wants_clash("clash-verge/v2.2.3")
    assert wants_clash("Mihomo/1.19")
    assert wants_clash("FlClash/0.8")
    assert not wants_clash("v2rayN/7.12")
    assert not wants_clash("Shadowrocket/2.2")

"""Mihomo 代理桥单元测试。"""

import pytest
from app.core.mihomo import MihomoManager, SUBSCRIPTIONS_FILE


def test_parse_clash_yaml():
    mgr = MihomoManager()
    yaml_text = """
proxies:
  - name: "香港 01"
    type: ss
    server: 1.2.3.4
    port: 8388
    cipher: aes-128-gcm
    password: "pwd"
  - name: "日本 02"
    type: trojan
    server: 2.3.4.5
    port: 443
    password: "pwd2"
"""
    nodes = mgr.parse_subscription_text(yaml_text)
    assert len(nodes) == 2
    assert nodes[0]["name"] == "香港 01"
    assert nodes[0]["type"] == "ss"
    assert nodes[1]["name"] == "日本 02"
    assert nodes[1]["type"] == "trojan"


def test_parse_node_links():
    mgr = MihomoManager()
    text = """
ss://YWVzLTEyOC1nY206cGFzc3dvcmQ=@1.2.3.4:8388#HK-SS
trojan://trojanpwd@5.6.7.8:443#JP-Trojan
"""
    nodes = mgr.parse_subscription_text(text)
    assert len(nodes) == 2
    assert nodes[0]["name"] == "HK-SS"
    assert nodes[0]["server"] == "1.2.3.4"
    assert nodes[1]["name"] == "JP-Trojan"
    assert nodes[1]["type"] == "trojan"


def test_port_allocation_and_yaml_gen():
    mgr = MihomoManager()
    mgr.subscriptions = [{
        "id": "sub_test",
        "name": "Test Sub",
        "nodes": [
            {"name": "Node A", "type": "ss", "server": "1.1.1.1", "port": 8388, "cipher": "aes-128-gcm", "password": "p1"},
            {"name": "Node B", "type": "ss", "server": "2.2.2.2", "port": 8388, "cipher": "aes-128-gcm", "password": "p2"}
        ]
    }]
    p1 = mgr.allocate_port_for_node("Node A")
    p2 = mgr.allocate_port_for_node("Node B")
    assert p1 == 10801
    assert p2 == 10802

    yaml_content = mgr.generate_runtime_yaml()
    assert "socks-10801" in yaml_content
    assert "socks-10802" in yaml_content
    assert "Node A" in yaml_content
    assert "Node B" in yaml_content


def test_assign_nodes_to_accounts():
    from app.core.config import CamelAccount
    mgr = MihomoManager()
    mgr.subscriptions = [{
        "id": "sub_test",
        "name": "Test Sub",
        "nodes": [
            {"name": "Fast Node", "type": "ss", "server": "1.1.1.1", "port": 8388, "cipher": "aes-128-gcm", "password": "p1"},
            {"name": "Slow Node", "type": "ss", "server": "2.2.2.2", "port": 8388, "cipher": "aes-128-gcm", "password": "p2"}
        ]
    }]
    mgr.node_health = {
        "Fast Node": {"delay_ms": 45, "ok": True},
        "Slow Node": {"delay_ms": 300, "ok": True}
    }
    acc1 = CamelAccount(email="user1@a.com", enabled=True)
    acc2 = CamelAccount(email="user2@a.com", enabled=True)
    acc3 = CamelAccount(email="user3@a.com", enabled=False)

    assigned = mgr.assign_nodes_to_accounts([acc1, acc2, acc3])
    assert assigned[0].mihomo_node == "Fast Node"
    assert "1080" in assigned[0].proxy
    assert assigned[1].mihomo_node == "Slow Node"
    assert assigned[2].proxy == ""  # disabled account skipped


def test_filter_garbage_nodes():
    mgr = MihomoManager()
    yaml_with_garbage = """
proxies:
  - name: "sub://cm.soso.edu.kg优选订阅生成器异常:"
    type: vless
    server: 127.0.0.1
    port: 1234
    uuid: "some-uuid"
  - name: "剩余流量：100GB"
    type: ss
    server: 8.8.8.8
    port: 8388
    cipher: aes-128-gcm
    password: "pwd"
  - name: "官网发布页 https://example.com"
    type: trojan
    server: 1.1.1.1
    port: 443
    password: "pwd"
  - name: "有效日本 01"
    type: vmess
    server: 104.16.1.1
    port: 443
    uuid: "valid-uuid"
"""
    nodes = mgr.parse_subscription_text(yaml_with_garbage)
    assert len(nodes) == 1
    assert nodes[0]["name"] == "有效日本 01"
    assert nodes[0]["server"] == "104.16.1.1"


def test_init_from_config():
    mgr = MihomoManager()
    mgr.init_from_config({
        "enabled": True,
        "binary_path": "/usr/local/bin/mihomo",
        "base_port": 20801,
        "api_port": 29090
    })
    assert mgr.enabled is True
    assert mgr.binary_path == "/usr/local/bin/mihomo"
    assert mgr.base_port == 20801
    assert mgr.api_port == 29090


@pytest.mark.asyncio
async def test_get_mihomo_nodes_assigned_by_proxy_port(monkeypatch):
    from app.api.admin_routes import get_mihomo_nodes
    from app.core.config import config_manager, CamelAccount
    from app.core.mihomo import mihomo_manager

    acc1 = CamelAccount(email="port_user@a.com", proxy="socks5://127.0.0.1:20846", mihomo_node="")
    acc2 = CamelAccount(email="named_user@b.com", proxy="", mihomo_node="Named-Node")

    monkeypatch.setattr(config_manager.config, "camel_accounts", [acc1, acc2])
    monkeypatch.setattr(mihomo_manager, "get_all_nodes", lambda: [
        {"name": "Port-Node", "local_port": 20846, "server": "1.1.1.1", "port": 443, "type": "ss"},
        {"name": "Named-Node", "local_port": 20847, "server": "2.2.2.2", "port": 443, "type": "ss"},
        {"name": "Unbound-Node", "local_port": 20848, "server": "3.3.3.3", "port": 443, "type": "ss"},
    ])

    res = await get_mihomo_nodes("admin")
    nodes = res["nodes"]
    assert nodes[0]["assigned_account"] == "port_user@a.com"
    assert nodes[1]["assigned_account"] == "named_user@b.com"
    assert nodes[2]["assigned_account"] is None

"""Mihomo 代理桥（一号一 IP）— 管理本地 Mihomo 子进程，解析机场订阅并为账号分配独占出口。"""

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
import urllib.parse

import httpx

from app.core.logger import get_logger

logger = get_logger(__name__)

DATA_DIR = Path("data/mihomo")
SUBSCRIPTIONS_FILE = Path("mihomo_subscriptions.json")
RUNTIME_YAML = DATA_DIR / "runtime.yaml"
RUNTIME_LOG = DATA_DIR / "mihomo.log"


class MihomoManager:
    """管理 Mihomo 进程、订阅节点解析、端口映射与一号一 IP 调度。"""

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.enabled: bool = False
        self.binary_path: str = ""
        self.base_port: int = 10801
        self.api_port: int = 19090
        self.subscriptions: List[dict] = []
        self.port_map: Dict[str, int] = {}  # node_name -> local_port
        self.node_health: Dict[str, dict] = {}  # node_name -> {delay_ms: int, ok: bool, time: str}
        self.load_subscriptions()

    def init_from_config(self, cfg):
        """从全局 Config 初始化运行时参数。"""
        if isinstance(cfg, dict):
            self.enabled = bool(cfg.get("enabled", False))
            self.binary_path = str(cfg.get("binary_path", "") or "")
            self.base_port = int(cfg.get("base_port", 10801) or 10801)
            self.api_port = int(cfg.get("api_port", 19090) or 19090)
        else:
            self.enabled = bool(getattr(cfg, "enabled", False))
            self.binary_path = str(getattr(cfg, "binary_path", "") or "")
            self.base_port = int(getattr(cfg, "base_port", 10801) or 10801)
            self.api_port = int(getattr(cfg, "api_port", 19090) or 19090)

    def load_subscriptions(self):
        """加载 mihomo_subscriptions.json 独立配置文件。"""
        if SUBSCRIPTIONS_FILE.exists():
            try:
                with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.subscriptions = data.get("subscriptions", [])
                    self.port_map = data.get("port_map", {})
                    self.node_health = data.get("node_health", {})
            except Exception as e:
                logger.error("[Mihomo] Failed to load %s: %s", SUBSCRIPTIONS_FILE, e)

    def save_subscriptions(self):
        """保存订阅与端口映射。"""
        try:
            with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "subscriptions": self.subscriptions,
                    "port_map": self.port_map,
                    "node_health": self.node_health
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("[Mihomo] Failed to save %s: %s", SUBSCRIPTIONS_FILE, e)

    def detect_binary(self) -> Optional[str]:
        """探测 mihomo / mihomo.exe 可执行文件。"""
        if self.binary_path and Path(self.binary_path).exists():
            return self.binary_path

        exe_name = "mihomo.exe" if sys.platform == "win32" else "mihomo"
        candidates = [
            Path(exe_name),
            DATA_DIR / exe_name,
            Path("bin") / exe_name,
        ]
        for p in candidates:
            if p.exists():
                return str(p.resolve())

        which_path = shutil.which(exe_name) or shutil.which("mihomo") or shutil.which("clash-meta")
        if which_path:
            return which_path

        return None

    def _is_valid_node(self, node: dict) -> bool:
        """过滤广告、异常占位、localhost 或参数明显非法的垃圾节点。"""
        if not isinstance(node, dict):
            return False

        name = str(node.get("name", "")).strip()
        server = str(node.get("server", "")).strip()
        node_type = str(node.get("type", "")).lower().strip()

        # 1. 基础字段检查
        if not name or not server or not node_type:
            return False

        # 2. 检查 server 是否为本地/无效回环地址
        if server.lower() in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "null", "undefined"):
            return False

        # 3. 检查端口合法性
        try:
            port = int(node.get("port", 0))
            if port < 1 or port > 65535:
                return False
        except (ValueError, TypeError):
            return False

        # 4. 检查是否为垃圾/广告/错误提示行
        garbage_keywords = (
            "生成器异常", "订阅异常", "剩余流量", "到期时间", "距离下次重置",
            "官方网站", "官网地址", "TG频道", "TG群", "加入群组", "购买订阅",
            "套餐到期", "流量已用", "重置日", "发布页", "更新提示", "请更新"
        )
        if any(kw in name for kw in garbage_keywords) or any(kw in server for kw in garbage_keywords):
            return False

        # 5. 检查 server 不能包含协议头或中文
        if "://" in server or any('\u4e00' <= char <= '\u9fff' for char in server):
            return False

        return True

    def parse_subscription_text(self, text: str, sub_name: str = "Sub") -> List[dict]:
        """解析订阅文本（支持 Clash YAML、Base64 或按行 URI），并自动过滤垃圾节点。"""
        text = text.strip()
        nodes = []

        # 1. 尝试直接按 YAML 解析 (Clash/Mihomo 配置)
        if "proxies:" in text or "Proxy:" in text:
            try:
                import yaml
                data = yaml.safe_load(text)
                proxies = data.get("proxies") or data.get("Proxy") or []
                if isinstance(proxies, list) and proxies:
                    valid_proxies = [p for p in proxies if self._is_valid_node(p)]
                    if valid_proxies:
                        return valid_proxies
            except Exception:
                pass

        # 2. 尝试 Base64 解码
        try:
            padded = text + "=" * ((4 - len(text) % 4) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore").strip()
            if decoded and ("\n" in decoded or "://" in decoded):
                text = decoded
        except Exception:
            pass

        # 3. 按行解析分享链接 (vmess://, ss://, trojan://, vless://, hysteria2://)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for idx, line in enumerate(lines):
            node = self._parse_node_link(line, idx + 1)
            if node and self._is_valid_node(node):
                nodes.append(node)

        return nodes

    def _parse_node_link(self, link: str, index: int) -> Optional[dict]:
        """解析单节点 URI 链接为 Mihomo 代理字典对象。"""
        try:
            if link.startswith("ss://"):
                # ss://base64(method:password@server:port)#name
                u = urllib.parse.urlparse(link)
                name = urllib.parse.unquote(u.fragment) if u.fragment else f"SS-Node-{index}"
                userinfo = u.netloc.split("@")[0] if "@" in u.netloc else u.username
                hostport = u.netloc.split("@")[1] if "@" in u.netloc else f"{u.hostname}:{u.port}"
                if ":" not in userinfo:
                    try:
                        userinfo = base64.b64decode(userinfo + "==").decode()
                    except Exception:
                        pass
                cipher, password = userinfo.split(":", 1)
                server, port = hostport.split(":")
                return {
                    "name": name,
                    "type": "ss",
                    "server": server,
                    "port": int(port),
                    "cipher": cipher,
                    "password": password
                }

            elif link.startswith("vmess://"):
                b64 = link[8:]
                raw = base64.b64decode(b64 + "==").decode("utf-8", errors="ignore")
                j = json.loads(raw)
                return {
                    "name": j.get("ps", f"VMess-{index}"),
                    "type": "vmess",
                    "server": j.get("add"),
                    "port": int(j.get("port", 443)),
                    "uuid": j.get("id"),
                    "alterId": int(j.get("aid", 0)),
                    "cipher": j.get("scy", "auto"),
                    "tls": j.get("tls") == "tls",
                    "network": j.get("net", "tcp"),
                }

            elif link.startswith("trojan://"):
                u = urllib.parse.urlparse(link)
                name = urllib.parse.unquote(u.fragment) if u.fragment else f"Trojan-{index}"
                return {
                    "name": name,
                    "type": "trojan",
                    "server": u.hostname,
                    "port": int(u.port or 443),
                    "password": u.username,
                    "sni": u.hostname
                }

            elif link.startswith("vless://"):
                u = urllib.parse.urlparse(link)
                name = urllib.parse.unquote(u.fragment) if u.fragment else f"VLESS-{index}"
                query = urllib.parse.parse_qs(u.query)
                return {
                    "name": name,
                    "type": "vless",
                    "server": u.hostname,
                    "port": int(u.port or 443),
                    "uuid": u.username,
                    "tls": query.get("security", [""])[0] in ("tls", "reality"),
                    "flow": query.get("flow", [""])[0],
                }

        except Exception as e:
            logger.debug("[Mihomo] Error parsing node link %s: %s", link[:30], e)
        return None

    async def add_subscription(self, url: str, name: str = "") -> dict:
        """添加并拉取订阅。"""
        sub_id = f"sub_{int(time.time())}_{len(self.subscriptions) + 1}"
        sub_name = name.strip() or f"订阅 {len(self.subscriptions) + 1}"

        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(url, headers={"User-Agent": "ClashMeta/v1.19.0"})
            if resp.status_code != 200:
                raise RuntimeError(f"拉取订阅失败: HTTP {resp.status_code}")
            text = resp.text

        nodes = self.parse_subscription_text(text, sub_name)
        if not nodes:
            raise RuntimeError("未能从订阅中解析出任何有效节点")

        sub_obj = {
            "id": sub_id,
            "name": sub_name,
            "url": url,
            "node_count": len(nodes),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "nodes": nodes
        }

        # 替换已有同 URL 订阅或追加
        self.subscriptions = [s for s in self.subscriptions if s.get("url") != url]
        self.subscriptions.append(sub_obj)
        self.save_subscriptions()
        return sub_obj

    def remove_subscription(self, sub_id: str):
        """删除订阅并释放节点绑定。"""
        self.subscriptions = [s for s in self.subscriptions if s.get("id") != sub_id]
        self.garbage_collect_ports()
        self.save_subscriptions()

    def get_all_nodes(self) -> List[dict]:
        """获取所有订阅下的全部节点。"""
        all_nodes = []
        seen = set()
        for sub in self.subscriptions:
            for n in sub.get("nodes", []):
                name = n.get("name")
                if name and name not in seen:
                    seen.add(name)
                    health = self.node_health.get(name, {})
                    all_nodes.append({
                        "name": name,
                        "type": n.get("type"),
                        "server": n.get("server"),
                        "port": n.get("port"),
                        "local_port": self.port_map.get(name),
                        "delay_ms": health.get("delay_ms", 0),
                        "ok": health.get("ok", True),
                        "sub_name": sub.get("name"),
                        "raw": n
                    })
        return all_nodes

    def garbage_collect_ports(self):
        """清理悬空未引用的端口映射。"""
        all_node_names = {n["name"] for n in self.get_all_nodes()}
        self.port_map = {k: v for k, v in self.port_map.items() if k in all_node_names}
        self.node_health = {k: v for k, v in self.node_health.items() if k in all_node_names}

    def allocate_port_for_node(self, node_name: str) -> int:
        """为节点分配或复用本地 SOCKS5 端口。"""
        if node_name in self.port_map:
            return self.port_map[node_name]

        used_ports = set(self.port_map.values())
        curr = self.base_port
        while curr in used_ports:
            curr += 1

        self.port_map[node_name] = curr
        self.save_subscriptions()
        return curr

    def generate_runtime_yaml(self) -> str:
        """生成 Mihomo runtime.yaml 配置文件。"""
        nodes = self.get_all_nodes()
        nodes_dict = {n["name"]: n["raw"] for n in nodes}

        # 仅为已分配端口的活跃节点配置 listeners
        listeners_yaml = []
        active_proxies = []
        active_proxy_names = set()

        for node_name, port in self.port_map.items():
            if node_name in nodes_dict:
                active_proxy_names.add(node_name)
                active_proxies.append(nodes_dict[node_name])
                listeners_yaml.append(
                    f"  - name: socks-{port}\n"
                    f"    type: socks\n"
                    f"    port: {port}\n"
                    f"    proxy: \"{node_name}\""
                )

        import yaml
        proxies_str = yaml.dump(active_proxies, allow_unicode=True, default_flow_style=False) if active_proxies else "[]"
        proxies_indented = "\n".join("  " + line for line in proxies_str.splitlines()) if active_proxies else "  []"

        config_text = f"""port: 0
socks-port: 0
allow-lan: false
mode: direct
log-level: info
external-controller: 127.0.0.1:{self.api_port}

listeners:
{chr(10).join(listeners_yaml) if listeners_yaml else '  []'}

proxies:
{proxies_indented}

rules:
  - MATCH,DIRECT
"""
        return config_text

    async def apply_and_restart(self):
        """写入 runtime.yaml 并重启 Mihomo 进程。"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        config_content = self.generate_runtime_yaml()
        with open(RUNTIME_YAML, "w", encoding="utf-8") as f:
            f.write(config_content)

        self.stop()
        bin_path = self.detect_binary()
        if not bin_path:
            logger.warning("[Mihomo] Binary not found, skipping process start")
            return False

        try:
            log_file = open(RUNTIME_LOG, "a", encoding="utf-8")
            self.process = subprocess.Popen(
                [bin_path, "-d", str(DATA_DIR.resolve()), "-f", str(RUNTIME_YAML.resolve())],
                stdout=log_file,
                stderr=log_file,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            logger.info("[Mihomo] Started sub-process PID %d on ports %s", self.process.pid, list(self.port_map.values()))
            return True
        except Exception as e:
            logger.error("[Mihomo] Failed to start process: %s", e)
            return False

    def stop(self):
        """停止 Mihomo 进程。"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    async def test_node_delay(self, node_name: str, target_url: str = "https://www.google.com/generate_204", timeout_s: float = 7.0) -> dict:
        """经节点本地 SOCKS5 端口测试真实网络延迟（带 1 次失败重试，避免 ECH/TLS 握手延迟误判）。"""
        port = self.allocate_port_for_node(node_name)
        proxy_url = f"socks5://127.0.0.1:{port}"

        ok = False
        delay_ms = 9999

        for attempt in range(2):
            t0 = time.time()
            try:
                async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout_s, trust_env=False) as client:
                    resp = await client.get(target_url)
                    if resp.status_code in (200, 204):
                        ok = True
                        delay_ms = int((time.time() - t0) * 1000)
                        break
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                else:
                    ok = False
                    delay_ms = 9999

        result = {
            "delay_ms": delay_ms,
            "ok": ok,
            "tested_at": time.strftime("%H:%M:%S")
        }
        self.node_health[node_name] = result
        self.save_subscriptions()
        return result

    async def test_all_nodes(self, concurrency: int = 15) -> dict:
        """批量测试全部节点延迟。"""
        nodes = self.get_all_nodes()
        if not nodes:
            return {}

        # 确保全部节点都分配了端口并启动了 Mihomo
        for n in nodes:
            self.allocate_port_for_node(n["name"])
        await self.apply_and_restart()
        await asyncio.sleep(3.0)  # 预留 3s 供 Mihomo 进程与全部 SOCKS5 监听端口就绪

        sem = asyncio.Semaphore(concurrency)

        async def worker(node_name):
            async with sem:
                return node_name, await self.test_node_delay(node_name)

        tasks = [worker(n["name"]) for n in nodes]
        results = await asyncio.gather(*tasks)
        return dict(results)

    def assign_nodes_to_accounts(self, accounts: list) -> list:
        """一键按延迟升序为全部账号分配独占节点（一人一号一 IP）。"""
        nodes = self.get_all_nodes()
        if not nodes:
            return accounts

        # 过滤并按延迟排序
        healthy_nodes = [n for n in nodes if n.get("ok", True) and n.get("delay_ms", 9999) < 8000]
        sorted_nodes = sorted(healthy_nodes or nodes, key=lambda x: x.get("delay_ms", 9999))

        assigned_count = 0
        for i, acc in enumerate(accounts):
            if not getattr(acc, "enabled", True):
                continue
            node = sorted_nodes[i % len(sorted_nodes)]
            port = self.allocate_port_for_node(node["name"])
            acc.mihomo_node = node["name"]
            acc.proxy = f"socks5://127.0.0.1:{port}"
            assigned_count += 1

        self.save_subscriptions()
        return accounts


mihomo_manager = MihomoManager()

"""Build shareable subscription links (vless://, vmess://, trojan://, ss://)
from an x-ui inbound + client pair.

The logic mirrors 3x-ui's front-end generator closely but stays conservative:
it reads stream settings, reality / tls / ws / grpc / tcp parameters and
assembles a single-link URI suitable for v2rayN / Nekoray / v2rayNG / Streisand.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode


def _j(value: Any) -> Dict[str, Any]:
    """Safe JSON-load helper: accepts dict or JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}


def _pick_host(inbound: Dict[str, Any], fallback: str) -> str:
    listen = (inbound.get("listen") or "").strip()
    if listen and listen not in {"0.0.0.0", "::"}:
        return listen
    return fallback


def _extract_stream_params(stream: Dict[str, Any]) -> Dict[str, str]:
    """Extract v2rayN-style query parameters from stream settings."""
    params: Dict[str, str] = {}
    network = stream.get("network") or "tcp"
    params["type"] = network
    security = stream.get("security") or "none"
    params["security"] = security

    if network == "tcp":
        header = (stream.get("tcpSettings") or {}).get("header") or {}
        if header.get("type"):
            params["headerType"] = header["type"]
            if header["type"] == "http":
                req = header.get("request") or {}
                host = (req.get("headers") or {}).get("Host")
                if isinstance(host, list) and host:
                    params["host"] = host[0]
                elif isinstance(host, str):
                    params["host"] = host
                paths = req.get("path")
                if isinstance(paths, list) and paths:
                    params["path"] = paths[0]
                elif isinstance(paths, str):
                    params["path"] = paths
    elif network == "ws":
        ws = stream.get("wsSettings") or {}
        if ws.get("path"):
            params["path"] = ws["path"]
        headers = ws.get("headers") or {}
        if headers.get("Host"):
            params["host"] = headers["Host"]
        elif ws.get("host"):
            params["host"] = ws["host"]
    elif network == "grpc":
        grpc = stream.get("grpcSettings") or {}
        if grpc.get("serviceName"):
            params["serviceName"] = grpc["serviceName"]
        if grpc.get("multiMode"):
            params["mode"] = "multi"
    elif network == "http" or network == "h2":
        http_s = stream.get("httpSettings") or {}
        hosts = http_s.get("host") or []
        if hosts:
            params["host"] = ",".join(hosts)
        if http_s.get("path"):
            params["path"] = http_s["path"]
    elif network == "quic":
        quic = stream.get("quicSettings") or {}
        if quic.get("security"):
            params["quicSecurity"] = quic["security"]
        if quic.get("key"):
            params["key"] = quic["key"]
        header = (quic.get("header") or {}).get("type")
        if header:
            params["headerType"] = header
    elif network == "kcp":
        kcp = stream.get("kcpSettings") or {}
        header = (kcp.get("header") or {}).get("type")
        if header:
            params["headerType"] = header
        if kcp.get("seed"):
            params["seed"] = kcp["seed"]

    if security == "tls":
        tls = stream.get("tlsSettings") or {}
        if tls.get("serverName"):
            params["sni"] = tls["serverName"]
        alpn = tls.get("alpn")
        if isinstance(alpn, list) and alpn:
            params["alpn"] = ",".join(alpn)
        fp = (tls.get("settings") or {}).get("fingerprint")
        if fp:
            params["fp"] = fp
        if tls.get("allowInsecure"):
            params["allowInsecure"] = "1"
    elif security == "reality":
        reality = stream.get("realitySettings") or {}
        settings = reality.get("settings") or {}
        if reality.get("serverNames"):
            params["sni"] = reality["serverNames"][0]
        elif reality.get("serverName"):
            params["sni"] = reality["serverName"]
        if reality.get("publicKey"):
            params["pbk"] = reality["publicKey"]
        elif settings.get("publicKey"):
            params["pbk"] = settings["publicKey"]
        short_ids = reality.get("shortIds") or []
        if short_ids:
            params["sid"] = short_ids[0]
        if settings.get("fingerprint"):
            params["fp"] = settings["fingerprint"]
        spider = settings.get("spiderX") or reality.get("spiderX")
        if spider:
            params["spx"] = spider

    return {k: v for k, v in params.items() if v not in (None, "")}


def build_link(
    inbound: Dict[str, Any],
    client_obj: Dict[str, Any],
    server_host: str,
    remark: Optional[str] = None,
) -> str:
    protocol = (inbound.get("protocol") or "").lower()
    port = inbound.get("port")
    stream = _j(inbound.get("streamSettings"))
    host = _pick_host(inbound, server_host)
    label = remark or client_obj.get("email") or inbound.get("remark") or "config"

    if protocol == "vless":
        params = _extract_stream_params(stream)
        if client_obj.get("flow"):
            params["flow"] = client_obj["flow"]
        query = urlencode(params, safe=":/,")
        uuid = client_obj.get("id")
        return f"vless://{uuid}@{host}:{port}?{query}#{quote(label)}"

    if protocol == "trojan":
        params = _extract_stream_params(stream)
        query = urlencode(params, safe=":/,")
        pwd = client_obj.get("password") or client_obj.get("id")
        return f"trojan://{pwd}@{host}:{port}?{query}#{quote(label)}"

    if protocol == "vmess":
        net = stream.get("network") or "tcp"
        tls = stream.get("security") or "none"
        path = ""
        ws_host = ""
        if net == "ws":
            ws = stream.get("wsSettings") or {}
            path = ws.get("path") or ""
            ws_host = (ws.get("headers") or {}).get("Host") or ws.get("host") or ""
        elif net == "grpc":
            grpc = stream.get("grpcSettings") or {}
            path = grpc.get("serviceName") or ""
        vmess_obj = {
            "v": "2",
            "ps": label,
            "add": host,
            "port": str(port),
            "id": client_obj.get("id"),
            "aid": "0",
            "scy": "auto",
            "net": net,
            "type": "none",
            "host": ws_host,
            "path": path,
            "tls": tls if tls != "none" else "",
            "sni": (stream.get("tlsSettings") or {}).get("serverName", "") if tls == "tls" else "",
            "alpn": "",
            "fp": (stream.get("tlsSettings") or {}).get("settings", {}).get("fingerprint", "") if tls == "tls" else "",
        }
        encoded = base64.b64encode(
            json.dumps(vmess_obj, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        return "vmess://" + encoded

    if protocol == "shadowsocks":
        settings = _j(inbound.get("settings"))
        method = client_obj.get("method") or settings.get("method") or "chacha20-ietf-poly1305"
        password = client_obj.get("password")
        raw = f"{method}:{password}"
        userinfo = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
        return f"ss://{userinfo}@{host}:{port}#{quote(label)}"

    return f"# unsupported protocol: {protocol}"

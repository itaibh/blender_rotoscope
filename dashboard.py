# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generic Live System & Performance Monitor Web/IPC Server for AI Workers.
Supports dual-mode TCP connections:
  - JSON IPC socket requests (line-delimited) for client IPC (e.g. Blender).
  - HTTP GET requests for browser performance monitoring (auto-refreshing UI).
"""

from __future__ import annotations

import json
import os
import platform
import socket
import sys
from pathlib import Path
from typing import Callable, Any


def get_system_hardware_info() -> dict:
    cpu_model = platform.processor() or "Generic CPU"
    try:
        if Path("/proc/cpuinfo").is_file():
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if "model name" in line:
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    has_intel_dri = Path("/dev/dri/renderD128").exists() or Path("/dev/dri/card0").exists()

    info = {
        "python": sys.version.split()[0],
        "torch": "Not Loaded",
        "torchvision": "Not Loaded",
        "opencv": "Not Loaded",
        "pillow": "Not Loaded",
        "sam2": "Installed",
        "cpu_model": cpu_model,
        "cpu_count": os.cpu_count() or 1,
        "torch_threads": 1,
        "cuda": False,
        "xpu": False,
        "gpu_name": "None",
        "has_intel_gpu": has_intel_dri,
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
    }
    try:
        import torch
        info["torch"] = torch.__version__
        info["torch_threads"] = torch.get_num_threads()
        info["cuda"] = torch.cuda.is_available()
        info["xpu"] = hasattr(torch, "xpu") and torch.xpu.is_available()
        if info["cuda"]:
            info["gpu_name"] = torch.cuda.get_device_name(0)
        elif info["xpu"]:
            info["gpu_name"] = "Intel GPU (XPU)"
        elif has_intel_dri:
            info["gpu_name"] = "Intel Graphics (Integrated / Iris Xe)"
    except Exception:
        pass
    try:
        import torchvision
        info["torchvision"] = torchvision.__version__
    except Exception:
        pass
    try:
        import cv2
        info["opencv"] = cv2.__version__
    except Exception:
        pass
    try:
        import PIL
        info["pillow"] = PIL.__version__
    except Exception:
        pass
    return info


class GenericDashboardServer:
    def __init__(self, title: str = "AI Worker System & Performance Monitor"):
        self.title = title
        self.status_cards = {}
        self.libraries = {}
        self.perf_stats = {}
        self.log_lines = []
        self.advisory_html = ""
        self.total_requests = 0

    def set_status_card(self, key: str, value: Any, color: str | None = None):
        self.status_cards[key] = {"value": str(value), "color": color}

    def set_libraries(self, lib_dict: dict):
        self.libraries.update(lib_dict)

    def set_advisory(self, html_snippet: str):
        self.advisory_html = html_snippet

    def update_perf_stats(self, perf_dict: dict):
        self.perf_stats = perf_dict.copy()

    def add_log(self, message: str):
        self.log_lines.append(message)
        if len(self.log_lines) > 200:
            self.log_lines = self.log_lines[-100:]

    def generate_html(self) -> str:
        hw = get_system_hardware_info()
        total_time = sum(self.perf_stats.values()) if self.perf_stats else 0.0

        # Build Status Grid Cards
        cards_html = ""
        for key, info in self.status_cards.items():
            val = info["value"]
            col_style = f"color:{info['color']};" if info.get("color") else ""
            cards_html += f"""
            <div class="card">
                <div class="card-label">{key}</div>
                <div class="card-val" style="{col_style}">{val}</div>
            </div>
            """

        # Build Software Stack Badges
        libs_html = ""
        libs_data = {
            "Python": hw["python"],
            "PyTorch": hw["torch"],
            "Torchvision": hw["torchvision"],
            "OpenCV": hw["opencv"],
            "Pillow": hw["pillow"],
        }
        libs_data.update(self.libraries)
        for lib_name, lib_ver in libs_data.items():
            libs_html += f'<span class="lib-tag">{lib_name} {lib_ver}</span>'

        # Build Performance Table Rows
        perf_rows = ""
        if self.perf_stats:
            for name, dur in self.perf_stats.items():
                pct = (dur / total_time * 100.0) if total_time > 0 else 0.0
                bar_color = "#00e5ff" if "Inference" in name else ("#ff9800" if "init_state" in name or "Extraction" in name else "#4caf50")
                perf_rows += f"""
                <tr>
                    <td style="padding:10px;border-bottom:1px solid #2a2d3d;">{name}</td>
                    <td style="padding:10px;border-bottom:1px solid #2a2d3d;font-weight:bold;">{dur:.3f}s</td>
                    <td style="padding:10px;border-bottom:1px solid #2a2d3d;">
                        <div style="background:#2a2d3d;border-radius:4px;overflow:hidden;height:12px;width:100%;max-width:200px;display:inline-block;vertical-align:middle;margin-right:8px;">
                            <div style="background:{bar_color};width:{pct:.1f}%;height:100%;"></div>
                        </div>
                        <span>{pct:.1f}%</span>
                    </td>
                </tr>
                """
        else:
            perf_rows = "<tr><td colspan='3' style='padding:20px;text-align:center;color:#888;'>No preview requests processed yet. Run a job to benchmark performance!</td></tr>"

        log_str = "\n".join(self.log_lines[-30:]) if self.log_lines else "Daemon started. Listening for requests..."

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{self.title}</title>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #0f1015; color: #e0e0e0; margin: 0; padding: 24px; }}
        .header {{ display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #2a2d3d; padding-bottom: 16px; margin-bottom: 24px; }}
        .badge {{ background: #00e5ff1a; color: #00e5ff; border: 1px solid #00e5ff80; padding: 4px 12px; border-radius: 16px; font-size: 14px; font-weight: 600; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #181922; border: 1px solid #2a2d3d; border-radius: 8px; padding: 16px; }}
        .card-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 6px; }}
        .card-val {{ font-size: 17px; font-weight: bold; color: #fff; word-break: break-all; }}
        .section-title {{ font-size: 18px; font-weight: 600; color: #fff; margin-top: 16px; margin-bottom: 12px; }}
        table {{ width: 100%; border-collapse: collapse; background: #181922; border: 1px solid #2a2d3d; border-radius: 8px; overflow: hidden; margin-bottom: 24px; }}
        th {{ background: #222433; text-align: left; padding: 12px; font-size: 13px; text-transform: uppercase; color: #aaa; border-bottom: 1px solid #2a2d3d; }}
        .lib-tag {{ display: inline-block; background: #222433; border: 1px solid #3b3e54; padding: 6px 12px; border-radius: 6px; font-size: 13px; margin-right: 8px; margin-bottom: 8px; font-family: monospace; color: #00e5ff; }}
        .log-box {{ background: #08080c; border: 1px solid #2a2d3d; border-radius: 8px; padding: 16px; font-family: monospace; font-size: 12px; line-height: 1.5; color: #00e5ff; white-space: pre-wrap; max-height: 250px; overflow-y: auto; }}
    </style>
    <script>
        var isPaused = false;
        function pauseRefresh() {{ isPaused = true; }}
        function resumeRefresh() {{ isPaused = false; }}

        setInterval(function() {{
            if (!isPaused && !document.getSelection().toString()) {{
                window.location.reload();
            }}
        }}, 2500);

        function copyCmd() {{
            var el = document.getElementById('cmdCode');
            if (!el) return;
            var txt = el.innerText;
            navigator.clipboard.writeText(txt).then(function() {{
                var btn = document.getElementById('copyBtn');
                if (btn) {{
                    btn.innerText = '✓ Copied!';
                    btn.style.background = '#4caf501a';
                    btn.style.color = '#4caf50';
                    btn.style.borderColor = '#4caf5080';
                    setTimeout(function() {{
                        btn.innerText = '📋 Copy Command';
                        btn.style.background = '#00e5ff1a';
                        btn.style.color = '#00e5ff';
                        btn.style.borderColor = '#00e5ff80';
                    }}, 2500);
                }}
            }});
        }}
    </script>
</head>
<body>
    <div class="header">
        <h2 style="margin:0;color:#fff;">⚡ {self.title}</h2>
        <span class="badge">🟢 DAEMON ACTIVE</span>
    </div>

    {self.advisory_html}

    <div class="section-title">💻 Hardware & Execution Environment</div>
    <div class="grid">
        <div class="card">
            <div class="card-label">CPU Hardware</div>
            <div class="card-val">{hw['cpu_model']} ({hw['cpu_count']} Cores)</div>
        </div>
        <div class="card">
            <div class="card-label">Detected GPU</div>
            <div class="card-val">{hw['gpu_name']}</div>
        </div>
        <div class="card">
            <div class="card-label">OS & Architecture</div>
            <div class="card-val">{hw['os']} ({hw['arch']})</div>
        </div>
        <div class="card">
            <div class="card-label">Total Requests</div>
            <div class="card-val" style="color:#4caf50;">{self.total_requests} Requests</div>
        </div>
    </div>

    <div class="section-title">📚 Libraries & Software Stack</div>
    <div style="margin-bottom: 24px;">
        {libs_html}
    </div>

    <div class="section-title">🎛️ Active Runtime State</div>
    <div class="grid">
        {cards_html}
    </div>

    <div class="section-title">📊 Microsecond Performance Breakdown</div>
    <table>
        <thead>
            <tr>
                <th>Execution Phase</th>
                <th>Duration</th>
                <th>Percentage of Total</th>
            </tr>
        </thead>
        <tbody>
            {perf_rows}
        </tbody>
    </table>

    <div class="section-title">📜 Real-Time Worker Log Output</div>
    <div class="log-box">{log_str}</div>
</body>
</html>"""


def start_daemon_server(
    host: str,
    port: int,
    request_handler: Callable[[dict], dict],
    dashboard_server: GenericDashboardServer,
):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_sock.bind((host, port))
        server_sock.listen(5)
    except OSError as err:
        if err.errno == 98 or "address" in str(err).lower():
            print(f"🟢 Daemon is ALREADY active and listening on {host}:{port}.", flush=True)
            return
        raise

    print(f"🟢 Daemon active and listening on {host}:{port}", flush=True)

    while True:
        try:
            conn, addr = server_sock.accept()
            with conn:
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in chunk:
                        break
                decoded = data.decode("utf-8", errors="ignore").strip()
                if decoded.startswith("GET ") or decoded.startswith("POST "):
                    dash_body = dashboard_server.generate_html()
                    html = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n"
                        "Connection: close\r\n\r\n" + dash_body
                    )
                    conn.sendall(html.encode("utf-8"))
                    continue

                req = json.loads(decoded)
                res = request_handler(req)
                dashboard_server.total_requests += 1
                conn.sendall(json.dumps(res).encode("utf-8") + b"\n")
        except Exception as exc:
            print(f"Daemon Request Error: {exc}", flush=True)

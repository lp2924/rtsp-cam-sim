"""端到端自检：启动 RTSP 服务 -> 挂载流 -> 用 ffprobe / ffmpeg 以真实客户端身份验证。

用法：.venv\\Scripts\\python.exe tools\\selftest.py
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.engine import Engine                                    # noqa: E402
from app.media.pipeline import find_ffmpeg                      # noqa: E402
from app.models import AppConfig, Route, StreamSpec             # noqa: E402

PORT = int(os.environ.get("SELFTEST_PORT", "8555"))
SAMPLE = os.path.join(ROOT, "samples", "demo_720p.mp4")
SHORT = os.path.join(ROOT, "samples", "short_2s.mp4")

RESULTS: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    return ok


def run_cmd(args: list[str], timeout: float = 30.0):
    """执行外部命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=0x08000000)
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -9, "", "timeout"
    except OSError as exc:
        return -1, "", str(exc)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class _QuietServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass                    # 客户端中途断开是正常现象，不打印堆栈


def start_http_server(directory: str, port: int):
    """起一个本地 HTTP 服务，用来模拟网络视频源（点播文件 + m3u8）。"""
    handler = functools.partial(_QuietHandler, directory=directory)
    httpd = _QuietServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def ensure_hls(ffmpeg: str, sample: str, out_dir: str) -> str:
    """把示例视频切成本地 HLS，用于验证 m3u8 拉流。"""
    m3u8 = os.path.join(out_dir, "index.m3u8")
    if os.path.isfile(m3u8):
        return m3u8
    os.makedirs(out_dir, exist_ok=True)
    rc, _out, _err = run_cmd([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", sample,
        "-c", "copy", "-f", "hls", "-hls_time", "2", "-hls_list_size", "0", m3u8,
    ], timeout=90)
    return m3u8 if rc == 0 and os.path.isfile(m3u8) else ""


def ensure_short(ffmpeg: str) -> str:
    """生成 2 秒短片段：用来验证「播完即停」和「播完自动重播」两种策略。"""
    if os.path.isfile(SHORT):
        return SHORT
    os.makedirs(os.path.dirname(SHORT), exist_ok=True)
    rc, _out, _err = run_cmd([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", SHORT,
    ], timeout=60)
    return SHORT if rc == 0 and os.path.isfile(SHORT) else ""


def wait_state(source, targets, timeout: float) -> str:
    """等到 source 进入目标状态之一，返回最终状态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if source and source.state in targets:
            return source.state
        time.sleep(0.2)
    return source.state if source else "none"


def main() -> int:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("找不到 ffmpeg，自检无法进行")
        return 2
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
    if not os.path.isfile(ffprobe):
        print(f"找不到 ffprobe：{ffprobe}")
        return 2
    if not os.path.isfile(SAMPLE):
        print(f"缺少示例视频：{SAMPLE}")
        return 2

    logs: list[str] = []
    engine = Engine(AppConfig(host="127.0.0.1", port=PORT, advertise_ip="127.0.0.1"),
                    log=lambda level, msg: logs.append(f"[{level}] {msg}"))
    engine.start_server()
    report("RTSP 服务启动", engine.server_running, f"127.0.0.1:{PORT}")

    spec = StreamSpec(
        name="自检流", source=SAMPLE, codec="h264",
        width=1280, height=720, fps=25.0, kbps=1500,
        routes=[
            Route("/Streaming/Channels/101", "hikvision"),
            Route("/cam/realmonitor?channel=1&subtype=0", "dahua"),
            Route("/stream1", "generic"),
        ],
    )
    engine.start_stream(spec)

    source = engine.sources.get(spec.id)
    deadline = time.time() + 10
    while time.time() < deadline and (not source or source.state != "running"):
        time.sleep(0.2)
    report("编码器出帧（参数集就绪）", bool(source and source.state == "running"),
           f"state={source.state if source else 'none'}")

    urls = {
        "hikvision": f"rtsp://127.0.0.1:{PORT}/Streaming/Channels/101",
        "dahua": f"rtsp://127.0.0.1:{PORT}/cam/realmonitor?channel=1&subtype=0",
        "generic": f"rtsp://127.0.0.1:{PORT}/stream1",
    }

    # --- 1. ffprobe + 真实解码（TCP 交织模式）---
    for transport in ("tcp", "udp"):
        rc, out, err = run_cmd([
            ffprobe, "-v", "error", "-rtsp_transport", transport,
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
            "-of", "json", urls["hikvision"],
        ], timeout=25)
        ok = rc == 0 and '"codec_name": "h264"' in out
        detail = ""
        if ok:
            info = json.loads(out)["streams"][0]
            detail = f"{info['codec_name']} {info['width']}x{info['height']} {info['avg_frame_rate']}"
        else:
            detail = (err or out).strip().splitlines()[-1] if (err or out) else f"rc={rc}"
        report(f"ffprobe 识别 {transport.upper()} 流", ok, detail)

    # --- 2. 大华带查询串路径 ---
    rc, out, err = run_cmd([ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                            "-show_entries", "stream=codec_name", "-of", "json",
                            urls["dahua"]], timeout=25)
    report("大华 ?channel=1&subtype=0 路径", rc == 0 and '"h264"' in out,
           (err or "").strip().splitlines()[-1] if rc != 0 else "")

    # --- 3. 连续解码 4 秒，确认无任何解码告警（NAL 打包错误会在这里暴露）---
    rc, out, err = run_cmd([ffmpeg, "-hide_banner", "-v", "warning", "-rtsp_transport", "tcp",
                            "-i", urls["generic"], "-t", "4", "-f", "null", "-"],
                           timeout=40)
    report("连续解码 4 秒无告警", rc == 0 and not err.strip(), err.strip()[:160])

    # --- 4. 两个客户端并发接收 ---
    outcomes: list[tuple[int, str]] = []

    def client(tag: str) -> None:
        code, _o, e = run_cmd([ffmpeg, "-hide_banner", "-v", "error", "-rtsp_transport", "tcp",
                               "-i", urls["hikvision"], "-t", "3", "-f", "null", "-"], timeout=35)
        outcomes.append((code, f"{tag}:rc={code} {e.strip()[:80]}"))

    threads = [threading.Thread(target=client, args=(f"c{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    report("两个客户端并发接收", all(code == 0 for code, _ in outcomes) and len(outcomes) == 2,
           " | ".join(msg for _, msg in outcomes))

    # --- 4. H.265 分支（FU 分片走的是另一套 NAL 头，必须单独验证）---
    spec265 = StreamSpec(
        name="自检流-H265", source=SAMPLE, codec="h265",
        width=1280, height=720, fps=25.0, kbps=1200,
        routes=[Route("/video1", "sony")],
    )
    engine.start_stream(spec265)
    src265 = engine.sources.get(spec265.id)
    deadline = time.time() + 30
    while time.time() < deadline and (not src265 or src265.state != "running"):
        time.sleep(0.3)
    report("H.265 编码器就绪", bool(src265 and src265.state == "running"),
           f"state={src265.state if src265 else 'none'}")

    url265 = f"rtsp://127.0.0.1:{PORT}/video1"
    rc, out, err = run_cmd([ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                            "-show_entries", "stream=codec_name,width,height", "-of", "json",
                            url265], timeout=25)
    report("ffprobe 识别 H.265 流", rc == 0 and '"codec_name": "hevc"' in out,
           (err or "").strip().splitlines()[-1] if rc != 0 else "")

    rc, out, err = run_cmd([ffmpeg, "-hide_banner", "-v", "warning", "-rtsp_transport", "tcp",
                            "-i", url265, "-t", "3", "-f", "null", "-"], timeout=40)
    report("H.265 连续解码 3 秒无告警", rc == 0 and not err.strip(), err.strip()[:160])
    engine.stop_stream(spec265.id)

    # --- 4.7 网络视频源：HTTP 点播 + HLS 切片 ---
    httpd = start_http_server(os.path.join(ROOT, "samples"), 8099)
    time.sleep(0.3)
    try:
        net_spec = StreamSpec(
            name="网络源-HTTP", source="http://127.0.0.1:8099/demo_720p.mp4", codec="h264",
            width=1280, height=720, fps=25.0, kbps=1500,
            routes=[Route("/live/http", "generic")],
        )
        engine.start_stream(net_spec)
        net_src = engine.sources.get(net_spec.id)
        deadline = time.time() + 25
        while time.time() < deadline and (not net_src or net_src.state != "running"):
            time.sleep(0.3)
        report("网络源(HTTP) 拉流出帧", bool(net_src and net_src.state == "running"),
               f"state={net_src.state if net_src else 'none'}")

        net_url = f"rtsp://127.0.0.1:{PORT}/live/http"
        rc, out, err = run_cmd([ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                                "-show_entries", "stream=codec_name,width,height", "-of", "json",
                                net_url], timeout=25)
        report("网络源(HTTP) 转出的 RTSP 可被识别", rc == 0 and '"h264"' in out,
               (err or "").strip().splitlines()[-1] if rc != 0 else "")

        rc, out, err = run_cmd([ffmpeg, "-hide_banner", "-v", "warning", "-rtsp_transport", "tcp",
                                "-i", net_url, "-t", "3", "-f", "null", "-"], timeout=40)
        report("网络源(HTTP) 连续解码 3 秒无告警", rc == 0 and not err.strip(), err.strip()[:160])
        engine.stop_stream(net_spec.id)

        hls_path = ensure_hls(ffmpeg, SAMPLE, os.path.join(ROOT, "samples", "hls"))
        if not hls_path:
            report("网络源(HLS m3u8) 拉流并转出", False, "HLS 切片生成失败")
        else:
            hls_spec = StreamSpec(
                name="网络源-HLS", source="http://127.0.0.1:8099/hls/index.m3u8", codec="h264",
                width=1280, height=720, fps=25.0, kbps=1500,
                routes=[Route("/live/hls", "generic")],
            )
            engine.start_stream(hls_spec)
            hls_src = engine.sources.get(hls_spec.id)
            deadline = time.time() + 30
            while time.time() < deadline and (not hls_src or hls_src.state != "running"):
                time.sleep(0.3)
            hls_url = f"rtsp://127.0.0.1:{PORT}/live/hls"
            rc, out, err = run_cmd([ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                                    "-show_entries", "stream=codec_name,width,height", "-of", "json",
                                    hls_url], timeout=25)
            report("网络源(HLS m3u8) 拉流并转出", rc == 0 and '"h264"' in out,
                   (err or "").strip().splitlines()[-1] if rc != 0 else "")
            engine.stop_stream(hls_spec.id)
    finally:
        httpd.shutdown()
        httpd.server_close()

    # --- 4.8 循环策略：播完即停 / 播完自动重播 ---
    short = ensure_short(ffmpeg)
    if not short:
        report("循环关闭：播完自动停止", False, "短片段生成失败")
        report("循环开启：播完继续出流", False, "短片段生成失败")
        report("循环切换处解码无告警", False, "短片段生成失败")
    else:
        # (a) loop=False：2 秒片段放完 → 编码进程结束，状态变 stopped
        stop_spec = StreamSpec(
            name="播完即停", source=short, codec="h264", width=640, height=360,
            fps=25.0, kbps=800, loop=False, routes=[Route("/test/noloop", "generic")],
        )
        engine.start_stream(stop_spec)
        stop_src = engine.sources.get(stop_spec.id)
        wait_state(stop_src, ("running",), 8)
        state = wait_state(stop_src, ("stopped", "error"), 15)
        report("循环关闭：播完自动停止", state == "stopped" and bool(stop_src and stop_src.frame_count > 0),
               f"state={state} frames={stop_src.frame_count if stop_src else 0}")

        # 播完之后客户端不该拿到「还活着」的假象，应当被明确拒绝
        stop_url = f"rtsp://127.0.0.1:{PORT}/test/noloop"
        rc, _out, err = run_cmd([ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                                 "-show_entries", "stream=codec_name", "-of", "json",
                                 stop_url], timeout=15)
        report("播完停止后客户端被明确拒绝", rc != 0 and "503" in err,
               (err.strip().splitlines()[-1] if err.strip() else f"rc={rc}")[:120])
        engine.stop_stream(stop_spec.id)

        # (b) loop=True：同样 2 秒片段，出帧数应远超 2 秒（说明真的重播了）
        loop_spec = StreamSpec(
            name="自动重播", source=short, codec="h264", width=640, height=360,
            fps=25.0, kbps=800, loop=True, routes=[Route("/test/loop", "generic")],
        )
        engine.start_stream(loop_spec)
        loop_src = engine.sources.get(loop_spec.id)
        wait_state(loop_src, ("running",), 8)
        deadline = time.time() + 15
        while time.time() < deadline and not (loop_src and loop_src.frame_count >= 70):
            time.sleep(0.2)
        frames = loop_src.frame_count if loop_src else 0
        report("循环开启：播完继续出流", frames >= 70 and loop_src.state in ("running", "starting"),
               f"frames={frames}（2 秒片段约 50 帧，>70 说明已进入第二遍） state={loop_src.state if loop_src else 'none'}")

        # (c) 循环接缝处用真实客户端解码：时间戳回退会在这里暴露成解码告警
        loop_url = f"rtsp://127.0.0.1:{PORT}/test/loop"
        rc, _out, err = run_cmd([ffmpeg, "-hide_banner", "-v", "warning", "-rtsp_transport", "tcp",
                                 "-i", loop_url, "-t", "4", "-f", "null", "-"], timeout=40)
        report("循环切换处解码无告警", rc == 0 and not err.strip(), err.strip()[:160])
        engine.stop_stream(loop_spec.id)

    # --- 4.9 主子码流：同一路流起两个编码器，路径各自拿不同的分辨率 ---
    dual_spec = StreamSpec(
        name="自检流-主子码流", source=SAMPLE, codec="h264",
        width=1920, height=1080, fps=25.0, kbps=4096,          # 出厂默认主码流
        routes=[
            Route("/Streaming/Channels/101", "hikvision"),
            Route("/Streaming/Channels/102", "hikvision", sub=True),
        ],
        sub_enabled=True, sub_width=704, sub_height=576,       # 出厂默认辅流 D1@512
        sub_fps=25.0, sub_kbps=512,
    )
    engine.start_stream(dual_spec)
    main_src = engine.sources.get(dual_spec.id)
    sub_src = engine.sub_sources.get(dual_spec.id)
    deadline = time.time() + 30
    while time.time() < deadline and (
            not (main_src and main_src.state == "running" and sub_src and sub_src.state == "running")):
        time.sleep(0.3)
    report("主子码流双编码器均已就绪",
           bool(main_src and main_src.state == "running" and sub_src and sub_src.state == "running"),
           f"main={main_src.state if main_src else 'none'} "
           f"sub={sub_src.state if sub_src else 'none'}")

    sizes: dict[str, tuple[int, int]] = {}
    expected = {"主码流": (1920, 1080), "子码流": (704, 576)}
    for tag, path in (("主码流", "/Streaming/Channels/101"), ("子码流", "/Streaming/Channels/102")):
        url = f"rtsp://127.0.0.1:{PORT}{path}"
        rc, out, err = run_cmd([ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                                "-show_entries", "stream=codec_name,width,height", "-of", "json",
                                url], timeout=25)
        if rc == 0 and out.strip():
            info = json.loads(out)["streams"][0]
            sizes[tag] = (int(info["width"]), int(info["height"]))
        else:
            sizes[tag] = (0, 0)
        report(f"{tag}路径分辨率正确", sizes[tag] == expected[tag],
               f"{sizes[tag][0]}×{sizes[tag][1]}")

    for tag, path in (("主码流", "/Streaming/Channels/101"), ("子码流", "/Streaming/Channels/102")):
        rc, _out, err = run_cmd([ffmpeg, "-hide_banner", "-v", "warning", "-rtsp_transport", "tcp",
                                 "-i", f"rtsp://127.0.0.1:{PORT}{path}", "-t", "3", "-f", "null", "-"],
                                timeout=40)
        report(f"{tag}连续解码 3 秒无告警", rc == 0 and not err.strip(), err.strip()[:160])

    st = engine.stats().get(dual_spec.id, {})
    report("聚合状态含主/子两路", st.get("state") == "running" and st.get("sub_state") == "running",
           f"state={st.get('state')} main={st.get('main_state')} sub={st.get('sub_state')}")
    engine.stop_stream(dual_spec.id)

    # --- 5. 停止后客户端应拿不到流 ---
    engine.stop_stream(spec.id)
    time.sleep(0.5)
    rc, _out, _err = run_cmd([ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                              "-show_entries", "stream=codec_name", "-of", "json",
                              urls["hikvision"]], timeout=15)
    report("停止后路由已摘除（返回错误）", rc != 0)

    engine.stop_server()
    report("服务正常关闭", not engine.server_running)

    failures = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 60)
    print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failures)} 项，失败 {len(failures)} 项")
    if failures:
        print("\n最近日志：")
        for line in logs[-25:]:
            print("  " + line)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

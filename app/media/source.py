"""单路流的核心：ffmpeg 编码进程 + 帧节奏控制 + 向所有 RTSP 会话分发 RTP。"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from ..models import StreamSpec, is_network_source
from ..rtsp.rtp import nalu_type, packetize
from ..rtsp.sdp import split_param_sets
from . import bilibili
from .pipeline import CREATE_NO_WINDOW, TsDemuxer, build_ffmpeg_cmd

H264_KEY_TYPES = {5}                       # IDR
H265_KEY_TYPES = {16, 17, 19, 20, 21}      # BLA / IDR / CRA


class StreamSource:
    """一路「虚拟摄像机」：一个 ffmpeg 编码进程 + N 个 RTSP 客户端会话。"""

    def __init__(self, spec: StreamSpec, ffmpeg: str, *, preset: str = "veryfast",
                 mtu: int = 1400, rtsp_input_transport: str = "tcp",
                 bilibili_cookie: str = "", bilibili_require_fhd: bool = True, log=None):
        self.spec = spec
        self.ffmpeg = ffmpeg
        self.preset = preset
        self.mtu = mtu
        self.rtsp_input_transport = rtsp_input_transport
        self.bilibili_cookie = bilibili_cookie
        self.bilibili_require_fhd = bilibili_require_fhd
        self.is_network = is_network_source(spec.source)
        self.loop = bool(getattr(spec, "loop", True))     # 播完是否自动循环
        self._log = log or (lambda level, msg: None)
        self._bili_info: Optional[dict] = None
        self.source_note = ""            # 源侧补充信息（如哔哩哔哩解析到的原始画质）

        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._stopping = False
        self._channels: List[object] = []
        self._lock = threading.Lock()
        self._err_lines: Deque[str] = deque(maxlen=30)

        self.state = "idle"                 # idle / starting / running / error / stopped
        self.last_error = ""
        self.frame_count = 0
        self.byte_count = 0
        self._last_clean_eof = False        # 上一次 ffmpeg 是否是「正常播完」
        self._clock_base = 0                # 跨循环累计的 90kHz 时钟，避免 RTP 时间戳回退
        self._params_event = threading.Event()
        self._param_sets: Dict[str, bytes] = {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.is_network and not os.path.isfile(self.spec.source):
            self.state = "error"
            self.last_error = f"视频文件不存在：{self.spec.source}"
            self._log("error", f"[{self.spec.name}] {self.last_error}")
            return
        if not self.spec.source.strip():
            self.state = "error"
            self.last_error = "未设置视频源"
            self._log("error", f"[{self.spec.name}] {self.last_error}")
            return
        self._stopping = False
        self.state = "starting"
        self.last_error = ""
        self._err_lines.clear()
        self._thread = threading.Thread(target=self._run, name=f"stream-{self.spec.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=4)
        self._thread = None
        self.state = "stopped"
        with self._lock:
            for ch in list(self._channels):
                try:
                    ch.close()
                except Exception:
                    pass
            self._channels.clear()
        self._log("info", f"[{self.spec.name}] 已停止")

    @property
    def running(self) -> bool:
        return self.state in ("starting", "running")

    # ------------------------------------------------------------------ #
    # 参数集 / SDP 支持
    # ------------------------------------------------------------------ #
    def wait_params(self, timeout: float = 3.0) -> bool:
        return self._params_event.wait(timeout)

    @property
    def param_sets(self) -> Dict[str, bytes]:
        return dict(self._param_sets)

    # ------------------------------------------------------------------ #
    # 客户端管理
    # ------------------------------------------------------------------ #
    def add_channel(self, channel) -> None:
        with self._lock:
            if channel not in self._channels:
                self._channels.append(channel)

    def remove_channel(self, channel) -> None:
        with self._lock:
            if channel in self._channels:
                self._channels.remove(channel)
        try:
            channel.close()
        except Exception:
            pass

    @property
    def client_count(self) -> int:
        with self._lock:
            return sum(1 for ch in self._channels if getattr(ch, "active", False))

    def _snapshot(self) -> List[object]:
        with self._lock:
            return [ch for ch in self._channels if getattr(ch, "active", False)]

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        """决定「播完之后怎么办」：循环重播 / 停止；网络源出错则退避重连。

        · 本地文件 loop=True：ffmpeg 用 -stream_loop 自己循环，这里一般不会被触发；
          若流水线被迫中断，按出错处理直接结束，避免无意义地反复重启。
        · 本地文件 loop=False：播到文件尾 → state=stopped，进程结束、路径摘除由上层决定。
        · 网络源正常播完（点播地址、有限切片列表到头）→ 由 loop 决定重播还是停止；
          网络源出错（超时/断开/解析失败）→ 退避重连，与 loop 无关，属于连接容错。
        """
        attempt = 0
        loops = 0
        while not self._stopping:
            self._run_once()
            if self._stopping:
                break

            if self._last_clean_eof:
                attempt = 0
                if not self.loop:
                    self.state = "stopped"
                    self._log("info", f"[{self.spec.name}] 播放结束，已停止（未开启循环）")
                    break
                loops += 1
                self.state = "starting"
                self._log("info", f"[{self.spec.name}] 播放结束，第 {loops} 次循环重播")
                self._pause(0.2)
                continue

            if not self.is_network:
                break
            attempt += 1
            delay = min(30, 3 * attempt)          # 3s、6s…最多 30s 退避
            self.state = "starting"
            self._log("warn", f"[{self.spec.name}] 视频源中断，{delay} 秒后第 {attempt} 次重连")
            self._pause(delay)

    def _pause(self, seconds: float) -> None:
        """可被 stop() 打断的等待。"""
        end = time.monotonic() + seconds
        while not self._stopping:
            left = end - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(0.2, left))

    def _resolve_source(self) -> Tuple[str, List[str]]:
        """把哔哩哔哩视频页换成直链（每次都重新解析，直链签名会过期）。

        其他源原样返回，第二个返回值是额外的 ffmpeg 输入参数。
        """
        source = self.spec.source
        if not bilibili.is_bilibili_url(source):
            return source, []
        info = bilibili.resolve(
            source, cookie=self.bilibili_cookie,
            min_height=1080 if self.bilibili_require_fhd else 0,
        )
        self._bili_info = info
        self.source_note = (f"{info['title'][:24]}｜{info['width']}×{info['height']} "
                            f"{info['quality_name']} {info['codecs']}")
        self._log("info", f"[{self.spec.name}] 已解析哔哩哔哩：{self.source_note}"
                           f"（第 {info['page']}/{info['pages']} P，时长 {info['duration']:.0f} 秒）")
        return info["url"], bilibili.ffmpeg_input_args(info)

    def _run_once(self) -> None:
        spec = self.spec
        frames0 = self.frame_count
        self._last_clean_eof = False
        try:
            source, input_args = self._resolve_source()
        except bilibili.ResolveError as exc:
            self.state = "error"
            self.last_error = f"哔哩哔哩解析失败：{exc}"
            self._log("error", f"[{spec.name}] {self.last_error}")
            return
        cmd = build_ffmpeg_cmd(
            self.ffmpeg, source, codec=spec.codec, width=spec.width, height=spec.height,
            fps=spec.fps, kbps=spec.kbps, preset=self.preset,
            rtsp_input_transport=self.rtsp_input_transport, network=self.is_network,
            loop=self.loop, input_args=input_args,
        )
        log_text = " ".join(cmd)
        if self._bili_info:                      # 别把 SESSDATA 写进日志
            log_text = bilibili.mask_secret(log_text, self._bili_info)
        self._log("info", f"[{spec.name}] 编码器：{log_text}")
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.state = "error"
            self.last_error = f"无法启动 ffmpeg：{exc}"
            self._log("error", f"[{spec.name}] {self.last_error}")
            return

        self._proc = proc
        threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()

        demux = TsDemuxer(spec.codec)
        codec = spec.codec
        key_types = H265_KEY_TYPES if codec == "h265" else H264_KEY_TYPES
        prev_pts: Optional[int] = None
        rt_clock = self._clock_base             # 跨循环连续计时，RTP 时间戳不会回退
        t_start: Optional[float] = None
        frame_delta = int(90000 / max(spec.fps, 1.0))

        try:
            while not self._stopping:
                data = proc.stdout.read(188 * 40)
                if not data:
                    break

                for pts, nalus in demux.feed(data):
                    # --- 时间戳：用 PTS 差值累计，可抵抗文件循环导致的回绕 ---
                    if prev_pts is None:
                        delta = 0
                    else:
                        delta = pts - prev_pts
                        if delta <= 0 or delta > 90000 * 5:
                            delta = frame_delta
                    prev_pts = pts
                    rt_clock += delta

                    # --- 参数集（SPS/PPS/VPS）缓存，供 SDP 使用 ---
                    vps, sps, pps = split_param_sets(codec, nalus)
                    if sps and pps:
                        if codec == "h265" and vps:
                            self._param_sets["vps"] = vps
                        self._param_sets["sps"] = sps
                        self._param_sets["pps"] = pps
                        if not self._params_event.is_set():
                            self._params_event.set()
                            self._log("info", f"[{spec.name}] 已获取参数集，SDP 就绪")

                    is_key = any(nalu_type(codec, n) in key_types for n in nalus)

                    # --- 节奏：以真实时间匀速推送（模拟恒定帧率的摄像机）---
                    now = time.monotonic()
                    if t_start is None:
                        # rt_clock 可能已带着上一轮的累计值，换算成能对齐当前时刻的绝对时间轴
                        t_start = now - rt_clock / 90000.0
                    target = t_start + rt_clock / 90000.0
                    delay = target - now
                    if delay > 0:
                        time.sleep(min(delay, 0.5))
                    elif delay < -1.5:
                        t_start -= delay          # 落后过多，重新对齐

                    payloads = packetize(nalus, codec, self.mtu)
                    if not payloads:
                        continue

                    self.frame_count += 1
                    self.byte_count += sum(len(p) + 12 for p in payloads)

                    if is_key and self.state == "starting" and self._params_event.is_set():
                        self.state = "running"

                    for channel in self._snapshot():
                        try:
                            if getattr(channel, "need_keyframe", False):
                                if not is_key:
                                    continue        # 客户端从 GOP 中间接入时先丢到下一帧 I 帧
                                channel.need_keyframe = False
                            channel.send_rtp_payloads(payloads, rt_clock & 0x1FFFFFFFF)
                        except Exception:
                            self.remove_channel(channel)

        except Exception as exc:                 # 读到进程被杀等异常
            if not self._stopping:
                self.state = "error"
                self.last_error = f"读取码流异常：{exc}"
                self._log("error", f"[{spec.name}] {self.last_error}")
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            self._clock_base = rt_clock + frame_delta     # 下一轮从这里接着走，时间戳不回退
            if not self._stopping:
                rc = proc.poll()
                if rc == 0 and self.frame_count > frames0:
                    # ffmpeg 正常退出且确实出过帧 = 播放结束，交给 _run 决定循环还是停止
                    self._last_clean_eof = True
                elif self.state != "error":
                    self.state = "error"
                    tail = "\n".join(list(self._err_lines)[-3:]) or "编码进程意外退出"
                    self.last_error = tail
                    self._log("error", f"[{spec.name}] 编码失败：{tail}")

    # ------------------------------------------------------------------ #
    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self._err_lines.append(line)
                if not self._stopping and self.state != "error":
                    self._log("warn", f"[{self.spec.name}] {line}")
        except Exception:
            pass

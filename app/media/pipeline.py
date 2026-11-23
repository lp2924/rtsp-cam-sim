"""视频源处理：ffmpeg 子进程 + MPEG-TS 解复用。

为什么不直接用 ffmpeg 的 RTP 输出？
  1. 需要按「帧」精确控制发送节奏（真实摄像机是恒定帧率推送的）；
  2. 需要拿到 PTS 换算成 90kHz 的 RTP 时间戳；
  3. 需要多个客户端共用一个编码源（多路 RTSP 会话共享同一份码流）。
因此采用 ffmpeg 输出 MPEG-TS 到 stdout 的方式：TS 里既有 PTS 又有完整 NAL，
解出来就是「一帧 = 一个 PES = 一组 NAL」。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional, Sequence, Tuple

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

FFMPEG_CANDIDATES = [
    r"D:\ffmpeg-6.0-essentials_build\bin\ffmpeg.exe",
    r"D:\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]


def find_ffmpeg(explicit: str | None = None) -> Optional[str]:
    """按「显式配置 -> 打包内置 -> PATH -> 常见安装目录」的顺序查找 ffmpeg。

    打包后（``sys.frozen`` 为真）优先使用随 exe 内置的 ffmpeg，候选位置：
      · ``sys._MEIPASS\\bin\\ffmpeg.exe``（onefile 解压到 %TEMP% 的临时目录内）
      · exe 所在目录\\bin\\ffmpeg.exe
      · exe 所在目录\\ffmpeg.exe
    源码运行时没有 ``sys._MEIPASS``，这些候选直接跳过，行为与原先一致。
    """
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        sibling = os.path.join(explicit, "ffmpeg.exe")
        if os.path.isfile(sibling):
            return sibling

    # --- PyInstaller 打包内置候选（仅 frozen 状态下参与查找）---
    if getattr(sys, "frozen", False):
        packaged: List[str] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            packaged.append(os.path.join(meipass, "bin", "ffmpeg.exe"))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        packaged.append(os.path.join(exe_dir, "bin", "ffmpeg.exe"))
        packaged.append(os.path.join(exe_dir, "ffmpeg.exe"))
        for path in packaged:
            if os.path.isfile(path):
                return path

    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in FFMPEG_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def build_ffmpeg_cmd(ffmpeg: str, source: str, *, codec: str, width: int, height: int,
                     fps: float, kbps: int, preset: str = "veryfast",
                     rtsp_input_transport: str = "tcp",
                     network: bool | None = None, loop: bool = True,
                     input_args: Sequence[str] = ()) -> List[str]:
    """构造拉流 + 转码命令。

    source 可以是本地文件，也可以是网络地址：
      · 本地文件 + loop=True：-stream_loop -1 循环播放，等价于「永不结束的摄像机」
      · 本地文件 + loop=False：不加 loop，播到文件尾 ffmpeg 正常退出，由上层收尾
      · 网络源：加超时与自动重连；不加 -stream_loop（直播流本身无限），
        点播类地址（如有限切片列表）播完后由上层按 loop 决定重播还是停止
    input_args 用来透传额外的输入参数（例如哔哩哔哩直链需要的 -user_agent / -headers），
    必须排在 -i 之前。
    """
    from ..models import is_network_source

    if network is None:
        network = is_network_source(source)

    gop = max(1, int(round(fps)))              # 1 秒一个关键帧，接近摄像机默认 I 帧间隔
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},format=yuv420p"
    )

    if codec == "h265":
        encoder = [
            "-c:v", "libx265", "-preset", preset, "-tune", "zerolatency",
            "-x265-params",
            f"aud=1:keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:"
            f"repeat-headers=1:log-level=error",
        ]
    else:
        encoder = [
            "-c:v", "libx264", "-preset", preset, "-tune", "zerolatency",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-x264-params",
            f"aud=1:keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:repeat-headers=1",
        ]

    input_opts: List[str] = ["-fflags", "+genpts"]
    if network:
        scheme = source.split("://", 1)[0].strip().lower()
        if scheme in ("rtsp", "rtsps"):
            input_opts += ["-rtsp_transport", rtsp_input_transport]
        if scheme in ("http", "https", "rtmp", "rtmps"):
            input_opts += [
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            ]
        input_opts += ["-rw_timeout", "20000000"]      # 20 秒无数据即失败，交给上层重连
    elif loop:
        input_opts += ["-stream_loop", "-1"]           # 懒循环：播完立刻从头再来，PTS 连续递增

    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
        *input_opts, *input_args,
        "-i", source,
        "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", vf,
        *encoder,
        "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k",
        "-g", str(gop), "-bf", "0",
        "-bsf:v", "dump_extra",                    # 每个关键帧前重复 SPS/PPS
        "-muxdelay", "0", "-muxpreload", "0", "-flush_packets", "1",
        "-f", "mpegts", "pipe:1",
    ]


def split_annexb(data: bytes) -> List[bytes]:
    """把 Annex-B 字节流切成 NAL 单元（去掉起始码和尾部补零）。"""
    out: List[bytes] = []
    n = len(data)
    idx = data.find(b"\x00\x00\x01")
    starts = []
    while idx != -1:
        starts.append(idx)
        idx = data.find(b"\x00\x00\x01", idx + 3)
    for i, start in enumerate(starts):
        begin = start + 3
        while begin < n and data[begin] == 0x00:   # 兼容 4 字节起始码 00 00 00 01
            begin += 1
        end = starts[i + 1] if i + 1 < len(starts) else n
        while end > begin and data[end - 1] == 0x00:
            end -= 1
        if end > begin:
            out.append(data[begin:end])
    return out


def strip_aud(codec: str, nalus: Sequence[bytes]) -> List[bytes]:
    """去掉访问单元分隔符（AUD），与真实摄像机的 RTP 输出保持一致。"""
    aud_type = 35 if codec == "h265" else 9
    result = []
    for nal in nalus:
        if not nal:
            continue
        if codec == "h265":
            t = (nal[0] >> 1) & 0x3F
        else:
            t = nal[0] & 0x1F
        if t == aud_type:
            continue
        result.append(nal)
    return result


class TsDemuxer:
    """极简 MPEG-TS 解复用器：只取视频 PID，按 PES 边界输出「一帧」。"""

    TS_SIZE = 188

    def __init__(self, codec: str = "h264"):
        self.codec = codec
        self._buf = bytearray()
        self._video_pid: Optional[int] = None
        self._pes = bytearray()

    # ------------------------------------------------------------------ #
    def feed(self, chunk: bytes) -> List[Tuple[int, List[bytes]]]:
        """输入一段 TS 数据，返回 [(pts_90k, [nal...]), ...]。"""
        self._buf += chunk
        buf = self._buf
        out: List[Tuple[int, List[bytes]]] = []
        pos = 0
        n = len(buf)

        while n - pos >= self.TS_SIZE:
            if buf[pos] != 0x47:
                new_pos = self._resync(buf, pos)
                if new_pos < 0:
                    pos = n
                    break
                pos = new_pos
                continue

            pid = ((buf[pos + 1] & 0x1F) << 8) | buf[pos + 2]
            pusi = bool(buf[pos + 1] & 0x40)
            afc = (buf[pos + 3] >> 4) & 0x03
            off = pos + 4

            if afc & 0x02:
                af_len = buf[off]
                off = off + 1 + af_len
            if not (afc & 0x01) or off >= pos + self.TS_SIZE:
                pos += self.TS_SIZE
                continue

            payload = bytes(buf[off:pos + self.TS_SIZE])
            pos += self.TS_SIZE

            if pusi and payload[:3] == b"\x00\x00\x01":
                sid = payload[3]
                if not (0xE0 <= sid <= 0xEF):       # 只认视频流
                    continue
                if self._video_pid is None:
                    self._video_pid = pid
                if pid != self._video_pid:
                    continue
                frame = self._close_pes()
                if frame is not None:
                    out.append(frame)
                self._pes = bytearray(payload)
                continue

            if pid == self._video_pid and self._pes:
                self._pes += payload

        self._buf = buf[pos:]
        return out

    # ------------------------------------------------------------------ #
    def _close_pes(self) -> Optional[Tuple[int, List[bytes]]]:
        data = bytes(self._pes)
        self._pes = bytearray()
        if len(data) < 9 or data[:3] != b"\x00\x00\x01":
            return None

        pes_len = int.from_bytes(data[4:6], "big")
        end = len(data) if pes_len == 0 else min(len(data), 6 + pes_len)
        hdr_len = data[8]
        flags = data[7]
        payload_start = 9 + hdr_len
        if end <= payload_start:
            return None

        pts = 0
        if (flags & 0x80) and hdr_len >= 5:
            p = data[9:14]
            pts = (
                ((p[0] & 0x0E) << 29)
                | ((p[1] & 0xFF) << 22)
                | ((p[2] & 0xFE) << 14)
                | ((p[3] & 0xFF) << 7)
                | ((p[4] & 0xFE) >> 1)
            ) & 0x1FFFFFFFF

        nalus = strip_aud(self.codec, split_annexb(data[payload_start:end]))
        if not nalus:
            return None
        return pts, nalus

    # ------------------------------------------------------------------ #
    def _resync(self, buf: bytearray, pos: int) -> int:
        n = len(buf)
        for p in range(pos + 1, n - self.TS_SIZE):
            if buf[p] != 0x47:
                continue
            if p + self.TS_SIZE >= n or buf[p + self.TS_SIZE] == 0x47:
                return p
        return -1

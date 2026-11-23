"""SDP 生成。

不同厂商的 SDP 差异主要体现在：
  * 媒体控制串（a=control:trackID=1 / trackID=0 / track1 ...）
  * 会话名 s= / 参数集描述方式
本模块把这些差异参数化，由 vendors.py 里的预设提供。
"""

from __future__ import annotations

import base64
from typing import Optional, Sequence

from .rtp import h264_profile_level_id


def _b64(nal: bytes) -> str:
    return base64.b64encode(nal).decode("ascii")


def build_sdp(
    *,
    codec: str,
    server_ip: str,
    session_id: int,
    label: str = "RTSP Stream",
    payload_type: int = 96,
    sps: bytes,
    pps: bytes,
    vps: Optional[bytes] = None,
    control: str = "trackID=1",
    framerate: Optional[float] = None,
) -> str:
    """生成单视频轨道的 SDP 文本（CRLF 换行）。"""
    lines = [
        "v=0",
        f"o=- {session_id} 1 IN IP4 {server_ip}",
        f"s={label}",
        f"i={label}",
        "c=IN IP4 0.0.0.0",
        "t=0 0",
        "a=control:*",
        "a=range:npt=0-",
        "m=video 0 RTP/AVP %d" % payload_type,
    ]

    if codec == "h265":
        lines.append("a=rtpmap:%d H265/90000" % payload_type)
        fmtp = "sprop-vps=%s;sprop-sps=%s;sprop-pps=%s" % (
            _b64(vps or b""), _b64(sps), _b64(pps),
        )
    else:
        lines.append("a=rtpmap:%d H264/90000" % payload_type)
        fmtp = "packetization-mode=1;profile-level-id=%s;sprop-parameter-sets=%s,%s" % (
            h264_profile_level_id(sps), _b64(sps), _b64(pps),
        )

    if framerate:
        lines.append("a=framerate:%.3f" % float(framerate))

    lines.append("a=fmtp:%d %s" % (payload_type, fmtp))
    lines.append("a=control:%s" % control)
    return "\r\n".join(lines) + "\r\n"


def split_param_sets(codec: str, nalus: Sequence[bytes]):
    """从帧内的 NAL 列表中提取 (vps, sps, pps)。"""
    vps = sps = pps = None
    for nal in nalus:
        if not nal:
            continue
        if codec == "h265":
            t = (nal[0] >> 1) & 0x3F
            if t == 32:
                vps = nal
            elif t == 33:
                sps = nal
            elif t == 34:
                pps = nal
        else:
            t = nal[0] & 0x1F
            if t == 7:
                sps = nal
            elif t == 8:
                pps = nal
    return vps, sps, pps

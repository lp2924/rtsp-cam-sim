"""RTP 打包工具：H.264（RFC 6184）/ H.265（RFC 7798）。

设计要点：
1. 只做「载荷打包」和「RTP 头封装」两件事，序号 / SSRC / 时间戳由调用方（每路客户端）决定；
2. 一个 Access Unit（一帧）打包成若干个 RTP 载荷，最后一个载荷打 Marker 位；
3. 超过 MTU 的 NAL 用分片模式：H.264 -> FU-A，H.265 -> FU。
"""

from __future__ import annotations

import struct
from typing import List, Sequence

DEFAULT_PAYLOAD_TYPE = 96


def rtp_packet(payload_type: int, seq: int, timestamp: int, ssrc: int,
               marker: bool, payload: bytes) -> bytes:
    """封装一个 12 字节标准 RTP 头 + 载荷（无 CSRC / 无扩展头）。"""
    head = struct.pack(
        "!BBHII",
        0x80,                                   # V=2, P=0, X=0, CC=0
        (0x80 if marker else 0x00) | (payload_type & 0x7F),
        seq & 0xFFFF,
        timestamp & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )
    return head + payload


def h264_payloads(nalus: Sequence[bytes], mtu: int) -> List[bytes]:
    """把一帧的若干个 NAL 单元打包成 RTP 载荷序列（不含 RTP 头）。"""
    out: List[bytes] = []
    for nal in nalus:
        if not nal:
            continue
        if len(nal) <= mtu:
            out.append(nal)                      # 单 NAL 单元模式
            continue
        indicator = (nal[0] & 0xE0) | 28         # 保留 F/NRI，类型置为 28 (FU-A)
        nal_type = nal[0] & 0x1F                 # H.264 NAL 头只有 1 字节
        body = nal[1:]
        chunk_size = mtu - 2
        offset = 0
        first = True
        while offset < len(body):
            chunk = body[offset:offset + chunk_size]
            offset += len(chunk)
            is_last = offset >= len(body)
            fu_header = (0x80 if first else 0x00) | (0x40 if is_last else 0x00) | nal_type
            out.append(bytes((indicator, fu_header)) + chunk)
            first = False
    return out


def h265_payloads(nalus: Sequence[bytes], mtu: int) -> List[bytes]:
    """H.265（HEVC）的分片打包，分片类型为 49 (FU)。"""
    out: List[bytes] = []
    for nal in nalus:
        if not nal:
            continue
        if len(nal) <= mtu:
            out.append(nal)
            continue
        if len(nal) < 3:
            out.append(nal)
            continue
        nal_type = (nal[0] >> 1) & 0x3F
        # 16 bit NAL 头：F(1) Type(6) LayerId(6) TID(3)，分片时 Type 换成 49
        b0 = (nal[0] & 0x81) | (49 << 1)
        b1 = nal[1]
        body = nal[2:]
        chunk_size = mtu - 3
        offset = 0
        first = True
        while offset < len(body):
            chunk = body[offset:offset + chunk_size]
            offset += len(chunk)
            is_last = offset >= len(body)
            fu_header = (0x80 if first else 0x00) | (0x40 if is_last else 0x00) | nal_type
            out.append(bytes((b0, b1, fu_header)) + chunk)
            first = False
    return out


def packetize(nalus: Sequence[bytes], codec: str, mtu: int = 1400) -> List[bytes]:
    """按编解码器选择打包方式。"""
    if codec == "h265":
        return h265_payloads(nalus, mtu)
    return h264_payloads(nalus, mtu)


# --------------------------------------------------------------------------- #
# 参数集解析（用于生成 SDP 里的 sprop-parameter-sets / profile-level-id）
# --------------------------------------------------------------------------- #

def h264_profile_level_id(sps: bytes) -> str:
    """SPS 的字节 1..3 即 profile_idc / constraint flags / level_idc。"""
    if len(sps) >= 4:
        return bytes(sps[1:4]).hex().upper()
    return "42E01E"


def nalu_type(codec: str, nal: bytes) -> int:
    if not nal:
        return -1
    if codec == "h265":
        return (nal[0] >> 1) & 0x3F
    return nal[0] & 0x1F

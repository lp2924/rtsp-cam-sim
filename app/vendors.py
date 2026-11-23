"""厂商预设：RTSP 路径、SDP 细节、默认编码参数。

路径来自各厂商公开的接入文档 / 客户端抓包惯例，属于「典型值」。
同一厂商不同固件、不同设备型号可能略有差异，界面上支持自定义路径覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class VendorPreset:
    key: str
    label: str
    server_header: str                 # RTSP 响应里的 Server 头
    realm: str                         # 预留：需要鉴权时的 realm
    control: str                       # SDP 媒体级 a=control 值
    main_path: str
    sub_path: Optional[str] = None
    main_size: Tuple[int, int, float, int] = (1920, 1080, 25.0, 4096)   # w,h,fps,kbps
    sub_size: Tuple[int, int, float, int] = (704, 576, 25.0, 512)

    def default_size(self, stream_type: str) -> Tuple[int, int, float, int]:
        return self.main_size if stream_type != "sub" else self.sub_size


VENDORS: Dict[str, VendorPreset] = {
    "hikvision": VendorPreset(
        key="hikvision", label="海康威视 Hikvision",
        server_header="Hikvision", realm="Hikvision", control="trackID=1",
        main_path="/Streaming/Channels/101", sub_path="/Streaming/Channels/102",
    ),
    "dahua": VendorPreset(
        key="dahua", label="大华 Dahua",
        server_header="Dahua", realm="Dahua", control="trackID=0",
        main_path="/cam/realmonitor?channel=1&subtype=0",
        sub_path="/cam/realmonitor?channel=1&subtype=1",
        main_size=(1920, 1080, 25.0, 4096), sub_size=(640, 480, 25.0, 512),
    ),
    "uniview": VendorPreset(
        key="uniview", label="宇视 Uniview",
        server_header="Uniview", realm="Uniview", control="trackID=1",
        main_path="/media/video1", sub_path="/media/video2",
    ),
    "axis": VendorPreset(
        key="axis", label="安讯士 Axis",
        server_header="Axis", realm="Axis", control="track1",
        main_path="/axis-media/media.amp",
        sub_path="/axis-media/media.amp?resolution=640x480",
        main_size=(1920, 1080, 30.0, 4096), sub_size=(640, 480, 30.0, 512),
    ),
    "hanwha": VendorPreset(
        key="hanwha", label="韩华/三星 Hanwha",
        server_header="Hanwha", realm="Hanwha", control="trackID=1",
        main_path="/profile2/media.smp", sub_path="/profile5/media.smp",
        main_size=(1920, 1080, 30.0, 4096), sub_size=(640, 480, 30.0, 512),
    ),
    "bosch": VendorPreset(
        key="bosch", label="博世 Bosch",
        server_header="Bosch", realm="Bosch", control="trackID=1",
        main_path="/rtsp_tunnel", sub_path=None,
    ),
    "panasonic": VendorPreset(
        key="panasonic", label="松下 Panasonic",
        server_header="Panasonic", realm="Panasonic", control="trackID=1",
        main_path="/MediaInput/h264", sub_path="/MediaInput/h264_1",
        main_size=(1920, 1080, 25.0, 4096), sub_size=(640, 480, 25.0, 512),
    ),
    "vivotek": VendorPreset(
        key="vivotek", label="晶睿 Vivotek",
        server_header="Vivotek", realm="Vivotek", control="trackID=1",
        main_path="/live.sdp", sub_path="/live2.sdp",
        main_size=(1920, 1080, 25.0, 4096), sub_size=(640, 480, 25.0, 512),
    ),
    "sony": VendorPreset(
        key="sony", label="索尼 Sony",
        server_header="Sony", realm="Sony", control="trackID=1",
        main_path="/video1", sub_path="/video2",
        main_size=(1920, 1080, 30.0, 4096), sub_size=(640, 480, 30.0, 512),
    ),
    "generic": VendorPreset(
        key="generic", label="通用 / ONVIF（万能测试）",
        server_header="RTSP Server", realm="RTSP", control="streamid=0",
        main_path="/stream1", sub_path="/stream2",
        main_size=(1920, 1080, 25.0, 4096), sub_size=(704, 576, 25.0, 512),
    ),
}

DEFAULT_VENDOR = "generic"


def get_vendor(key: str) -> VendorPreset:
    return VENDORS.get(key) or VENDORS[DEFAULT_VENDOR]


def all_route_options() -> List[Tuple[str, str, str, str]]:
    """返回所有可选路径：(vendor_key, 显示名, stream_type, path)。"""
    options: List[Tuple[str, str, str, str]] = []
    for vendor in VENDORS.values():
        options.append((vendor.key, vendor.label, "main", vendor.main_path))
        if vendor.sub_path:
            options.append((vendor.key, vendor.label, "sub", vendor.sub_path))
    return options


def route_label(path: str) -> str:
    """把路径翻译成人类可读的描述，用于界面展示。"""
    for vendor in VENDORS.values():
        if path == vendor.main_path:
            return f"{vendor.label} · 主码流"
        if path == vendor.sub_path:
            return f"{vendor.label} · 子码流"
    return "自定义路径"

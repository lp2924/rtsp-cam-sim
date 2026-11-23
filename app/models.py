"""数据模型与配置持久化。"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)

VALID_THEMES = ("dark", "light")
DEFAULT_THEME = "dark"


def _resolve_config_path() -> str:
    """解析配置文件路径（兼容 PyInstaller 冻结打包）。

    * 源码运行：项目根目录 ``config.json``（保持现状）。
    * 冻结打包（``sys.frozen``）：优先 exe 同目录 ``config.json``；
      目录不可写（或不存在可写权限）时退回 ``%APPDATA%\\RtspCamSim\\config.json``，
      目录不存在时自动创建。
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        bundled = os.path.join(exe_dir, "config.json")
        # 已存在的配置文件直接沿用（只读目录也能正常加载）
        if os.path.isfile(bundled):
            return bundled
        try:
            os.makedirs(exe_dir, exist_ok=True)
            probe = os.path.join(exe_dir, ".rtsp_camsim_write_probe")
            with open(probe, "w", encoding="utf-8"):
                pass
            os.remove(probe)
            return bundled
        except OSError:
            appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
            fallback_dir = os.path.join(appdata, "RtspCamSim")
            os.makedirs(fallback_dir, exist_ok=True)
            return os.path.join(fallback_dir, "config.json")
    return os.path.join(PROJECT_ROOT, "config.json")


CONFIG_PATH = _resolve_config_path()

# 网络视频源的协议前缀（http 拉文件、m3u8 切片、rtsp 摄像机、rtmp 推流地址、udp 组播等）
NETWORK_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def is_network_source(value: str) -> bool:
    """判断是网络地址还是本地文件。"""
    return bool(value) and bool(NETWORK_SCHEME_RE.match(value.strip()))


def source_kind(value: str) -> str:
    """给界面展示用的源类型描述。"""
    value = (value or "").strip()
    if not value:
        return "未设置"
    if not is_network_source(value):
        return "本地文件"
    if "bilibili.com/" in value.lower() or "b23.tv/" in value.lower():
        return "网络流（哔哩哔哩视频页，自动解析直链）"
    scheme = urlsplit(value).scheme.lower()
    mapping = {
        "http": "网络流（HTTP，可直连 mp4/mkv 或 m3u8）",
        "https": "网络流（HTTPS）",
        "rtsp": "网络流（RTSP 摄像机/平台）",
        "rtsps": "网络流（RTSPS）",
        "rtmp": "网络流（RTMP）",
        "rtmps": "网络流（RTMPS）",
        "udp": "网络流（UDP 组播/单播，通常是裸 TS）",
        "rtp": "网络流（RTP）",
        "srt": "网络流（SRT）",
        "ftp": "网络流（FTP）",
    }
    return mapping.get(scheme, f"网络流（{scheme}）")


def describe_source(value: str) -> str:
    """紧凑显示视频源：本地只显示文件名，网络显示 协议://主机/路径。"""
    value = (value or "").strip()
    if not value:
        return "（未设置）"
    if not is_network_source(value):
        return os.path.basename(value) or value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    path = parts.path
    if len(path) > 60:
        path = "…" + path[-58:]
    return f"{parts.scheme.lower()}://{parts.netloc}{path}"


def default_stream_name(value: str) -> str:
    """根据视频源推导一个默认流名称。"""
    value = (value or "").strip()
    if not value:
        return "未命名流"
    lower = value.lower()
    if "bilibili.com/" in lower or "b23.tv/" in lower:
        from .media.bilibili import parse_video_id
        bvid, aid, _page = parse_video_id(value)
        return f"哔哩哔哩 {bvid or ('av' + (aid or ''))}".strip()
    if is_network_source(value):
        parts = urlsplit(value)
        stem = os.path.basename(parts.path.rstrip("/"))
        return os.path.splitext(stem)[0] or parts.netloc or "网络流"
    return os.path.splitext(os.path.basename(value))[0] or "流"


@dataclass
class Route:
    """对外暴露的一条 RTSP 路径（可同时挂多个厂商路径到同一路流上）。"""
    path: str
    vendor: str = "generic"
    sub: bool = False                  # 是否走子码流参数（102 / subtype=1 / video2 这类路径）

    def to_dict(self) -> dict:
        return {"path": self.path, "vendor": self.vendor, "sub": self.sub}

    @staticmethod
    def from_dict(data: dict) -> "Route":
        return Route(path=data.get("path", "/stream1"), vendor=data.get("vendor", "generic"),
                     sub=bool(data.get("sub", False)))


def is_sub_route(route: Route) -> bool:
    """判断这条路径是否属于子码流。

    旧配置里没有 sub 字段，这里按厂商预设的 sub_path 兜底推断（例如海康 102）。
    """
    if getattr(route, "sub", False):
        return True
    from .vendors import get_vendor
    vendor = get_vendor(route.vendor)
    return bool(vendor.sub_path) and route.path == vendor.sub_path


@dataclass
class StreamSpec:
    """一路流的完整描述（不含运行态）。

    source 既可以是本地文件路径，也可以是网络地址：
    网络源由 ffmpeg 主动拉流后再转成 RTSP 对外发布。
    """
    name: str
    source: str = ""
    codec: str = "h264"                 # h264 / h265
    width: int = 1920
    height: int = 1080
    fps: float = 25.0
    kbps: int = 4096                   # 1080P 主流出厂默认 4096kbps
    routes: List[Route] = field(default_factory=list)
    loop: bool = True                   # 播放结束后是否自动循环（False = 播完即停）
    # 子码流：102 / subtype=1 / video2 这类路径单独用一套编码参数（内部起第二个编码器）
    sub_enabled: bool = False
    sub_width: int = 704               # 辅流出厂默认 D1 704×576 @ 25fps 512kbps
    sub_height: int = 576
    sub_fps: float = 25.0
    sub_kbps: int = 512
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def is_network(self) -> bool:
        return is_network_source(self.source)

    @property
    def loop_text(self) -> str:
        """给界面展示用的循环策略描述。"""
        return "播放结束后自动循环" if self.loop else "播放结束后停止"

    def split_routes(self) -> Tuple[List[Route], List[Route]]:
        """(主码流路径, 子码流路径)。"""
        main, sub = [], []
        for route in self.routes:
            (sub if is_sub_route(route) else main).append(route)
        return main, sub

    @property
    def sub_active(self) -> bool:
        """是否真的会启用第二编码器（勾了开关且确实有子流路径）。"""
        _main, sub = self.split_routes()
        return bool(self.sub_enabled and sub)

    @property
    def sub_text(self) -> str:
        """给界面展示用的子码流描述。"""
        if not self.sub_active:
            return "未启用"
        return f"{self.sub_width}×{self.sub_height} @ {self.sub_fps:g}fps {self.sub_kbps}kbps"

    def route_paths(self) -> List[str]:
        return [r.path for r in self.routes]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["routes"] = [r.to_dict() for r in self.routes]
        return data

    @staticmethod
    def from_dict(data: dict) -> "StreamSpec":
        return StreamSpec(
            name=data.get("name", "未命名"),
            # 兼容旧配置：早期版本字段名是 file
            source=data.get("source") or data.get("file", ""),
            codec=data.get("codec", "h264"),
            width=int(data.get("width", 1920)),
            height=int(data.get("height", 1080)),
            fps=float(data.get("fps", 25.0)),
            kbps=int(data.get("kbps", 2048)),
            routes=[Route.from_dict(r) for r in data.get("routes", [])],
            # 兼容旧配置：早期没有 loop 字段，默认为循环（保持原行为）
            loop=bool(data.get("loop", True)),
            sub_enabled=bool(data.get("sub_enabled", False)),
            sub_width=int(data.get("sub_width", 1280)),
            sub_height=int(data.get("sub_height", 720)),
            sub_fps=float(data.get("sub_fps", 25.0)),
            sub_kbps=int(data.get("sub_kbps", 1024)),
            id=data.get("id") or uuid.uuid4().hex[:8],
        )


@dataclass
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 8554
    advertise_ip: str = ""              # 生成 URL 时对外展示的 IP
    ffmpeg: str = ""
    preset: str = "veryfast"
    mtu: int = 1400
    rtsp_input_transport: str = "tcp"    # 拉 rtsp:// 源时使用的传输方式：tcp / udp
    bilibili_cookie: str = ""            # 可选：哔哩哔哩 SESSDATA，用于取 1080P 及以上清晰度
    bilibili_require_fhd: bool = True    # 哔哩哔哩源要求 ≥1080P，拿不到就报错而不是静默降级
    auto_start: bool = False
    theme: str = DEFAULT_THEME           # 界面主题：dark / light（旧配置缺字段默认 dark）
    streams: List[StreamSpec] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        data = asdict(self)
        data["streams"] = [s.to_dict() for s in self.streams]
        return data

    @staticmethod
    def from_dict(data: dict) -> "AppConfig":
        theme = data.get("theme", DEFAULT_THEME)
        if theme not in VALID_THEMES:
            theme = DEFAULT_THEME
        return AppConfig(
            host=data.get("host", "0.0.0.0"),
            port=int(data.get("port", 8554)),
            advertise_ip=data.get("advertise_ip", ""),
            ffmpeg=data.get("ffmpeg", ""),
            preset=data.get("preset", "veryfast"),
            mtu=int(data.get("mtu", 1400)),
            rtsp_input_transport=data.get("rtsp_input_transport", "tcp"),
            bilibili_cookie=data.get("bilibili_cookie", ""),
            bilibili_require_fhd=bool(data.get("bilibili_require_fhd", True)),
            auto_start=bool(data.get("auto_start", False)),
            theme=theme,
            streams=[StreamSpec.from_dict(s) for s in data.get("streams", [])],
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def load(path: str = CONFIG_PATH) -> "AppConfig":
        if not os.path.isfile(path):
            return AppConfig()
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return AppConfig.from_dict(json.load(fp))
        except Exception:
            return AppConfig()

    def save(self, path: str = CONFIG_PATH) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(self.to_dict(), fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    # ------------------------------------------------------------------ #
    def stream_by_id(self, stream_id: str) -> Optional[StreamSpec]:
        for spec in self.streams:
            if spec.id == stream_id:
                return spec
        return None

    def used_paths(self) -> Dict[str, str]:
        """路径 -> 所属流名称，用于冲突检测。"""
        result: Dict[str, str] = {}
        for spec in self.streams:
            for route in spec.routes:
                result[route.path] = spec.name
        return result

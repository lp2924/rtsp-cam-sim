"""引擎层：把「流配置」翻译成「RTSP 服务 + 编码进程」，供 GUI 调用。

一条流最多对应两个编码器：
  · 主码流：101 / subtype=0 / video1 这类路径
  · 子码流：102 / subtype=1 / video2 这类路径（只有显式启用且有子流路径时才起）
两个编码器共用同一个视频源，各自独立编码，对外仍是同一路「摄像机」。
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Callable, Dict, List, Optional

from .media.pipeline import find_ffmpeg
from .media.source import StreamSource
from .models import AppConfig, StreamSpec
from .rtsp.server import RtspServer

LogFn = Callable[[str, str], None]


class Engine:
    def __init__(self, config: AppConfig, log: Optional[LogFn] = None):
        self.config = config
        self._log: LogFn = log or (lambda level, msg: None)
        self.server: Optional[RtspServer] = None
        self.sources: Dict[str, StreamSource] = {}       # 主码流，键是 spec.id
        self.sub_sources: Dict[str, StreamSource] = {}   # 子码流，键也是 spec.id
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def log(self, level: str, msg: str) -> None:
        if self._log:
            self._log(level, msg)

    @property
    def ffmpeg_path(self) -> Optional[str]:
        return find_ffmpeg(self.config.ffmpeg)

    @property
    def server_running(self) -> bool:
        return bool(self.server and self.server.running)

    # ------------------------------------------------------------------ #
    # RTSP 服务
    # ------------------------------------------------------------------ #
    def start_server(self) -> None:
        if self.server_running:
            return
        ffmpeg = self.ffmpeg_path
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，请在「设置」里指定 ffmpeg.exe 路径")
        self.server = RtspServer(
            host=self.config.host, port=self.config.port, log=self.log,
            advertise_ip=self.config.advertise_ip,
        )
        self.server.start()
        with self._lock:
            for source in self._all_sources():
                if source.running:
                    self._mount(source)

    def stop_server(self) -> None:
        self.stop_all()
        if self.server:
            self.server.stop()
            self.server = None

    # ------------------------------------------------------------------ #
    # 流
    # ------------------------------------------------------------------ #
    def _mount(self, source: StreamSource) -> None:
        if not self.server:
            return
        for route in source.spec.routes:
            self.server.add_route(source, route.path, route.vendor)

    def _unmount(self, source: StreamSource) -> None:
        if not self.server:
            return
        for route in source.spec.routes:
            self.server.remove_route(route.path)

    def start_stream(self, spec: StreamSpec) -> None:
        with self._lock:
            if self.is_stream_running(spec.id):
                return
            if not spec.routes:
                raise ValueError(f"「{spec.name}」没有配置任何 RTSP 路径")
            if not self.ffmpeg_path:
                raise RuntimeError("未找到 ffmpeg，请先在「设置」里指定 ffmpeg.exe 路径")
            if not self.server_running:
                raise RuntimeError("请先启动 RTSP 服务")

            main_routes, sub_routes = spec.split_routes()
            use_sub = spec.sub_enabled and bool(sub_routes)
            self._discard_sources(spec.id)

            # 主码流编码器（没有主路径且启用了子码流时可以不建）
            if main_routes or not use_sub:
                main_spec = replace(spec, routes=main_routes if use_sub else list(spec.routes))
                if not main_spec.routes:                 # 兜底：只有子流路径但没开子码流
                    main_spec = replace(spec, routes=list(spec.routes))
                source = self._make_source(main_spec)
                self.sources[spec.id] = source
                self._mount(source)
                source.start()

            # 子码流编码器（第二份轻量编码，只服务子流路径）
            if use_sub:
                sub_spec = replace(
                    spec, name=f"{spec.name}·子码流",
                    width=spec.sub_width, height=spec.sub_height,
                    fps=spec.sub_fps, kbps=spec.sub_kbps,
                    routes=sub_routes, id=f"{spec.id}-sub",
                )
                source = self._make_source(sub_spec)
                self.sub_sources[spec.id] = source
                self._mount(source)
                source.start()
                self.log("info", f"[{spec.name}] 子码流编码器：{spec.sub_width}×{spec.sub_height}"
                                 f" @ {spec.sub_fps:g}fps {spec.sub_kbps}kbps"
                                 f"（{len(sub_routes)} 条子流路径）")

    def _make_source(self, spec: StreamSpec) -> StreamSource:
        ffmpeg = self.ffmpeg_path
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，请先在「设置」里指定 ffmpeg.exe 路径")
        return StreamSource(
            spec, ffmpeg, preset=self.config.preset, mtu=self.config.mtu,
            rtsp_input_transport=self.config.rtsp_input_transport,
            bilibili_cookie=self.config.bilibili_cookie,
            bilibili_require_fhd=self.config.bilibili_require_fhd,
            log=self.log,
        )

    def _all_sources(self) -> List[StreamSource]:
        return [*self.sources.values(), *self.sub_sources.values()]

    def _sources_of(self, spec_id: str) -> List[StreamSource]:
        return [s for s in (self.sources.get(spec_id), self.sub_sources.get(spec_id)) if s]

    def is_stream_running(self, spec_id: str) -> bool:
        return any(source.running for source in self._sources_of(spec_id))

    def running_spec_ids(self) -> List[str]:
        with self._lock:
            return [spec_id for spec_id in set(self.sources) | set(self.sub_sources)
                    if self.is_stream_running(spec_id)]

    def _discard_sources(self, spec_id: str) -> None:
        """把这一路残留的编码器（主/子）先停掉并摘除路由。"""
        for registry in (self.sources, self.sub_sources):
            source = registry.pop(spec_id, None)
            if source:
                self._unmount(source)
                source.stop()

    def stop_stream(self, spec_id: str) -> None:
        with self._lock:
            self._discard_sources(spec_id)

    def stop_all(self) -> None:
        for spec_id in list(set(self.sources) | set(self.sub_sources)):
            self.stop_stream(spec_id)

    def start_all(self) -> None:
        for spec in self.config.streams:
            try:
                self.start_stream(spec)
            except Exception as exc:
                self.log("error", f"[{spec.name}] 启动失败：{exc}")

    def update_stream(self, old_id: str, new_spec: StreamSpec) -> None:
        """编辑后应用：若原先在运行则重启，让新参数生效。"""
        was_running = self.is_stream_running(old_id)
        if was_running:
            self.stop_stream(old_id)
        if new_spec.id != old_id:
            self._discard_sources(old_id)
        if was_running:
            try:
                self.start_stream(new_spec)
            except Exception as exc:
                self.log("error", f"[{new_spec.name}] 重启失败：{exc}")

    def remove_stream(self, spec_id: str) -> None:
        self.stop_stream(spec_id)

    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, dict]:
        """按 spec.id 聚合主/子两路编码器的运行状态。"""
        with self._lock:
            result: Dict[str, dict] = {}
            for spec_id in set(self.sources) | set(self.sub_sources):
                main = self.sources.get(spec_id)
                sub = self.sub_sources.get(spec_id)
                parts = [s for s in (main, sub) if s]
                if not parts:
                    continue
                states = [s.state for s in parts]
                state = next(
                    (candidate for candidate in ("running", "starting", "error", "stopped")
                     if candidate in states),
                    states[0],
                )
                result[spec_id] = {
                    "state": state,
                    "clients": sum(s.client_count for s in parts),
                    "frames": sum(s.frame_count for s in parts),
                    "error": next((s.last_error for s in parts if s.last_error), ""),
                    "note": next((getattr(s, "source_note", "") for s in parts
                                  if getattr(s, "source_note", "")), ""),
                    "main_state": main.state if main else "stopped",
                    "sub_state": sub.state if sub else "",
                    "sub_clients": sub.client_count if sub else 0,
                }
            return result

    @property
    def client_total(self) -> int:
        return self.server.client_total if self.server else 0

    # ------------------------------------------------------------------ #
    def url_for(self, route_path: str) -> str:
        ip = self.config.advertise_ip or "127.0.0.1"
        return f"rtsp://{ip}:{self.config.port}{route_path}"

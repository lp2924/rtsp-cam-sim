"""RTSP 服务端（RFC 2326 子集，单视频轨道）。

支持客户端行为：
  * OPTIONS / DESCRIBE / SETUP / PLAY / PAUSE / TEARDOWN / GET_PARAMETER / SET_PARAMETER
  * RTP over UDP（server_port 协商）与 RTP over TCP（interleaved 交织帧）两种传输
  * 周期发送 RTCP SR，便于部分客户端做时基同步
路由匹配支持带查询串的厂商路径（如 /cam/realmonitor?channel=1&subtype=0），
并能容忍客户端在 URL 后面拼上 /trackID=1 这类控制后缀。
"""

from __future__ import annotations

import queue
import random
import re
import socket
import struct
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit
from typing import Dict, List, Optional, Tuple

from ..vendors import VendorPreset, get_vendor
from .rtp import DEFAULT_PAYLOAD_TYPE, rtp_packet
from .sdp import build_sdp

CONTROL_SUFFIX_RE = re.compile(r"/+(?:trackid|track|streamid|stream)=\w+/?$", re.IGNORECASE)

REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    451: "Parameter Not Understood",
    461: "Unsupported Transport",
    500: "Internal Server Error",
    501: "Not Implemented",
    503: "Service Unavailable",
}

RTSP_TIMEOUT = 60.0


def normalize_key(path: str, query: str) -> str:
    """把 path+query 归一化成稳定的路由键（查询参数排序）。"""
    if query:
        pairs = sorted(parse_qsl(query, keep_blank_values=True))
        return path + "?" + urlencode(pairs)
    return path


class _Route:
    __slots__ = ("path", "key", "vendor", "stream")

    def __init__(self, path: str, vendor: VendorPreset, stream):
        self.path = path
        self.vendor = vendor
        self.stream = stream
        path_only, _, query = path.partition("?")
        self.key = normalize_key(path_only, query)

    @property
    def path_only(self) -> str:
        return self.path.partition("?")[0]


# --------------------------------------------------------------------------- #
# RTP 通道
# --------------------------------------------------------------------------- #
class RtspChannel:
    """一个会话里的一条 RTP 通道（本程序只有视频一条）。"""

    def __init__(self, conn: "RtspConnection", stream, transport: dict):
        self.conn = conn
        self.stream = stream
        self.mode = transport.get("mode", "udp")
        self.client_ip = transport.get("client_ip", "")
        self.client_rtp_port = transport.get("client_rtp_port", 0)
        self.client_rtcp_port = transport.get("client_rtcp_port", 0)
        self.interleaved = transport.get("interleaved", (0, 1))

        self.ssrc = random.getrandbits(32)
        self.seq = random.getrandbits(16)
        self.timestamp = 0
        self.packet_count = 0
        self.octet_count = 0
        self.active = False
        self.need_keyframe = True                   # 刚开始接收时先等一个关键帧，避免参考帧缺失
        self._closed = False

        self.server_rtp_port = 0
        self.server_rtcp_port = 0
        self._rtp_sock: Optional[socket.socket] = None
        self._rtcp_sock: Optional[socket.socket] = None
        self._queue: Optional[queue.Queue] = None
        self._writer: Optional[threading.Thread] = None

        if self.mode == "udp":
            self._rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rtp_sock.bind(("0.0.0.0", 0))
            self.server_rtp_port = self._rtp_sock.getsockname()[1]
            try:
                self._rtcp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._rtcp_sock.bind(("0.0.0.0", self.server_rtp_port + 1))
            except OSError:
                self._rtcp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._rtcp_sock.bind(("0.0.0.0", 0))
            self.server_rtcp_port = self._rtcp_sock.getsockname()[1]
        else:
            self._queue = queue.Queue(maxsize=300)
            self._writer = threading.Thread(
                target=self._tcp_writer, name=f"rtp-tcp-{self.ssrc}", daemon=True)
            self._writer.start()

    # ------------------------------------------------------------------ #
    def transport_reply(self) -> str:
        ssrc = format(self.ssrc, "08X")
        if self.mode == "udp":
            return (f"RTP/AVP/UDP;unicast;client_port={self.client_rtp_port}-"
                    f"{self.client_rtcp_port};server_port={self.server_rtp_port}-"
                    f"{self.server_rtcp_port};ssrc={ssrc}")
        low, high = self.interleaved
        return f"RTP/AVP/TCP;unicast;interleaved={low}-{high};ssrc={ssrc}"

    # ------------------------------------------------------------------ #
    def send_rtp_payloads(self, payloads: List[bytes], timestamp: int) -> None:
        if self._closed:
            return
        self.timestamp = timestamp & 0xFFFFFFFF
        last = len(payloads) - 1
        for index, payload in enumerate(payloads):
            packet = rtp_packet(
                DEFAULT_PAYLOAD_TYPE, self.seq, self.timestamp, self.ssrc,
                index == last, payload,
            )
            self.seq = (self.seq + 1) & 0xFFFF
            self.packet_count += 1
            self.octet_count += len(payload)
            self._send(packet, 0)

    def send_rtcp_sr(self) -> None:
        if self._closed or not self.packet_count:
            return
        ntp = time.time() + 2208988800             # 1900-01-01 起算
        ntp_int = int(ntp)
        ntp_frac = int((ntp - ntp_int) * (1 << 32))
        packet = struct.pack(
            "!BBHIIIIII",
            0x80, 200, 6, self.ssrc,
            ntp_int & 0xFFFFFFFF, ntp_frac & 0xFFFFFFFF,
            self.timestamp, self.packet_count & 0xFFFFFFFF,
            self.octet_count & 0xFFFFFFFF,
        )
        self._send(packet, 1)

    # ------------------------------------------------------------------ #
    def _send(self, packet: bytes, channel_offset: int) -> None:
        if self.mode == "udp":
            sock = self._rtp_sock if channel_offset == 0 else self._rtcp_sock
            port = self.client_rtp_port if channel_offset == 0 else self.client_rtcp_port
            if not sock or not port:
                return
            try:
                sock.sendto(packet, (self.client_ip, port))
            except OSError:
                pass
        else:
            index = self.interleaved[0] + channel_offset
            try:
                self._queue.put_nowait((index, packet))
            except queue.Full:
                pass                                # 客户端读不过来就丢帧，绝不阻塞整个流

    def _tcp_writer(self) -> None:
        while not self._closed:
            item = self._queue.get()
            if item is None:
                break
            try:
                self.conn.write_interleaved(item[0], item[1])
            except Exception:
                break

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        for sock in (self._rtp_sock, self._rtcp_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._rtp_sock = self._rtcp_sock = None


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class RtspSession:
    def __init__(self, session_id: str, route: _Route):
        self.id = session_id
        self.route = route
        self.channel: Optional[RtspChannel] = None
        self.created = time.time()

    @property
    def stream(self):
        return self.route.stream

    def start(self) -> None:
        if self.channel and not self.channel.active:
            self.channel.active = True
            self.channel.need_keyframe = True       # 从下一个关键帧开始发，客户端才不会缺参考帧
            self.stream.add_channel(self.channel)

    def pause(self) -> None:
        if self.channel and self.channel.active:
            self.channel.active = False
            self.stream.remove_channel(self.channel)

    def close(self) -> None:
        if self.channel:
            self.channel.active = False
            self.stream.remove_channel(self.channel)
            self.channel = None


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
class RtspConnection(threading.Thread):
    def __init__(self, server: "RtspServer", sock: socket.socket, addr):
        super().__init__(name=f"rtsp-conn-{addr[1]}", daemon=True)
        self.server = server
        self.sock = sock
        self.addr = addr
        self.sessions: Dict[str, RtspSession] = {}
        self._write_lock = threading.Lock()
        self._closed = False
        self.last_activity = time.time()

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        sock = self.sock
        sock.settimeout(0.5)
        buffer = b""
        try:
            while not self._closed and not self.server.stopping:
                try:
                    data = sock.recv(8192)
                except socket.timeout:
                    if time.time() - self.last_activity > RTSP_TIMEOUT:
                        break
                    continue
                except OSError:
                    break
                if not data:
                    break
                self.last_activity = time.time()
                buffer += data

                while buffer:
                    if buffer[:1] == b"$":          # 客户端发来的交织帧，忽略
                        if len(buffer) < 4:
                            break
                        length = int.from_bytes(buffer[2:4], "big")
                        if len(buffer) < 4 + length:
                            break
                        buffer = buffer[4 + length:]
                        continue
                    end = buffer.find(b"\r\n\r\n")
                    if end == -1:
                        if len(buffer) > 65536:
                            buffer = b""
                        break
                    head = buffer[:end]
                    buffer = buffer[end + 4:]
                    self._dispatch(head)
        finally:
            self._cleanup()

    # ------------------------------------------------------------------ #
    def _cleanup(self) -> None:
        self._closed = True
        for session in list(self.sessions.values()):
            session.close()
        self.sessions.clear()
        try:
            self.sock.close()
        except Exception:
            pass
        self.server._forget_connection(self)

    # ------------------------------------------------------------------ #
    def write_interleaved(self, channel: int, data: bytes) -> None:
        frame = b"$" + bytes((channel & 0xFF,)) + len(data).to_bytes(2, "big") + data
        self._write_raw(frame)

    def _write_raw(self, data: bytes) -> None:
        if self._closed:
            return
        with self._write_lock:
            try:
                self.sock.sendall(data)
            except OSError:
                self._closed = True

    # ------------------------------------------------------------------ #
    def _respond(self, cseq: str, code: int, headers: Optional[Dict[str, str]] = None,
                 body: bytes = b"", server_name: str = "") -> None:
        lines = [f"RTSP/1.0 {code} {REASONS.get(code, 'OK')}"]
        if cseq:
            lines.append(f"CSeq: {cseq}")
        if server_name:
            lines.append(f"Server: {server_name}")
        if body:
            lines.append(f"Content-Length: {len(body)}")
        for key, value in (headers or {}).items():
            lines.append(f"{key}: {value}")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body
        self._write_raw(raw)

    # ------------------------------------------------------------------ #
    def _dispatch(self, head: bytes) -> None:
        headers: Dict[str, str] = {}
        cseq = ""
        try:
            text = head.decode("utf-8", "replace")
            lines = text.split("\r\n")
            request_line = lines[0].strip()
            parts = request_line.split()
            if len(parts) < 2:
                return
            method = parts[0].upper()
            uri = parts[1]

            headers = {}
            for line in lines[1:]:
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

            cseq = headers.get("cseq", "")
            self.server._log("debug", f"<- {method} {uri}")

            handler = getattr(self, "_m_" + method.lower(), None)
            if handler is None:
                self._respond(cseq, 501, {}, server_name=self.server.default_server)
                return
            handler(cseq, uri, headers)
        except Exception as exc:                    # 任何解析异常都不能弄死连接线程
            self.server._log("error", f"处理请求失败：{exc}")
            try:
                self._respond(cseq, 500)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # RTSP 方法
    # ------------------------------------------------------------------ #
    @staticmethod
    def _stream_alive(stream) -> bool:
        """该路流当前是否在出流（starting 也算，参数集就绪后就会 running）。"""
        return getattr(stream, "state", "running") in ("starting", "running")

    def _m_options(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        route = self.server.resolve(uri)
        name = route.vendor.server_header if route else self.server.default_server
        self._respond(cseq, 200, {
            "Public": "OPTIONS, DESCRIBE, SETUP, PLAY, PAUSE, TEARDOWN, "
                      "GET_PARAMETER, SET_PARAMETER",
        }, server_name=name)

    def _m_describe(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        route = self.server.resolve(uri)
        if route is None:
            self._respond(cseq, 404)
            return

        stream = route.stream
        if not self._stream_alive(stream):
            # 该路流没在出流（未启动 / 已播完停止 / 出错），不能拿缓存的旧参数集糊弄客户端
            self._respond(cseq, 503, {"Retry-After": "1"})
            return
        timeout = 5.0 if getattr(stream, "is_network", False) else 3.0
        if not stream.wait_params(timeout):
            self._respond(cseq, 503, {"Retry-After": "1"})
            return

        params = stream.param_sets
        sps = params.get("sps", b"")
        pps = params.get("pps", b"")
        sdp = build_sdp(
            codec=stream.spec.codec,
            server_ip=self.server.advertise_ip or self.server.host,
            session_id=random.getrandbits(31),
            label=route.vendor.server_header,
            payload_type=DEFAULT_PAYLOAD_TYPE,
            sps=sps, pps=pps, vps=params.get("vps"),
            control=route.vendor.control,
        )
        body = sdp.encode("utf-8")
        self._respond(cseq, 200, {
            "Content-Type": "application/sdp",
            "Content-Base": self.server.absolute_url(route.path),
        }, body=body, server_name=route.vendor.server_header)
        self.server._log("info", f"DESCRIBE {route.path} -> 200 ({stream.spec.name})")

    def _m_setup(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        route = self.server.resolve(uri)
        if route is None:
            self._respond(cseq, 404)
            return
        if not self._stream_alive(route.stream):
            self._respond(cseq, 503, {"Retry-After": "1"})
            return

        transport = self._parse_transport(headers.get("transport", ""))
        if transport is None:
            self._respond(cseq, 461)
            return

        session_id = headers.get("session", "").split(";")[0].strip()
        session = self.sessions.get(session_id) if session_id else None
        if session is None:
            session_id = format(random.getrandbits(32), "08X")
            session = RtspSession(session_id, route)
            self.sessions[session_id] = session
        else:
            session.close()

        transport["client_ip"] = self.addr[0]
        channel = RtspChannel(self, route.stream, transport)
        session.channel = channel
        session.route = route

        self._respond(cseq, 200, {
            "Transport": channel.transport_reply(),
            "Session": f"{session_id};timeout={int(RTSP_TIMEOUT)}",
        }, server_name=route.vendor.server_header)
        self.server._log(
            "info",
            f"SETUP {route.path} ({channel.mode.upper()}) -> 200 session={session_id}",
        )

    def _m_play(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        session = self._session_from(headers)
        if session is None or session.channel is None:
            self._respond(cseq, 400)
            return

        session.start()
        channel = session.channel
        info = (f"url={self.server.absolute_url(session.route.path)}/{session.route.vendor.control};"
                f"seq={channel.seq};rtptime={channel.timestamp}")
        self._respond(cseq, 200, {
            "Session": session.id,
            "Range": "npt=0.000-",
            "RTP-Info": info,
        }, server_name=session.route.vendor.server_header)
        self.server._log(
            "info",
            f"PLAY {session.route.path} -> 200，客户端 {self.addr[0]} 开始接收",
        )

    def _m_pause(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        session = self._session_from(headers)
        if session is None:
            self._respond(cseq, 400)
            return
        session.pause()
        self._respond(cseq, 200, {"Session": session.id},
                      server_name=session.route.vendor.server_header)

    def _m_teardown(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        session = self._session_from(headers)
        if session is None:
            self._respond(cseq, 400)
            return
        name = session.route.vendor.server_header
        session_id = session.id
        session.close()
        self.sessions.pop(session_id, None)
        self._respond(cseq, 200, {"Session": session_id}, server_name=name)
        self.server._log("info", f"TEARDOWN {session.route.path}，会话 {session_id} 关闭")

    def _m_get_parameter(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        route = self.server.resolve(uri)
        name = route.vendor.server_header if route else self.server.default_server
        self._respond(cseq, 200, {}, server_name=name)

    def _m_set_parameter(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        self._m_get_parameter(cseq, uri, headers)

    def _m_announce(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        self._respond(cseq, 405)                    # 本程序只做服务端推流

    def _m_record(self, cseq: str, uri: str, headers: Dict[str, str]) -> None:
        self._respond(cseq, 405)

    # ------------------------------------------------------------------ #
    def _session_from(self, headers: Dict[str, str]) -> Optional[RtspSession]:
        session_id = headers.get("session", "").split(";")[0].strip()
        return self.sessions.get(session_id) if session_id else None

    @staticmethod
    def _parse_transport(value: str) -> Optional[dict]:
        if not value:
            return {"mode": "udp", "client_rtp_port": 0, "client_rtcp_port": 0,
                    "interleaved": (0, 1)}
        parts = [part.strip() for part in value.split(";") if part.strip()]
        spec = parts[0].upper()
        params = {}
        for part in parts[1:]:
            if "=" in part:
                key, _, val = part.partition("=")
                params[key.strip().lower()] = val.strip()

        if "TCP" in spec:
            low, high = 0, 1
            if "interleaved" in params:
                pair = params["interleaved"].split("-")
                try:
                    low = int(pair[0])
                    high = int(pair[1]) if len(pair) > 1 else low + 1
                except ValueError:
                    pass
            return {"mode": "tcp", "interleaved": (low, high)}

        if "UDP" in spec or spec.startswith("RTP/AVP"):
            ports = params.get("client_port", "")
            if not ports:                           # 客户端不给端口，退回 TCP 交织
                return {"mode": "tcp", "interleaved": (0, 1)}
            try:
                pair = ports.split("-")
                rtp_port = int(pair[0])
                rtcp_port = int(pair[1]) if len(pair) > 1 else rtp_port + 1
            except ValueError:
                return None
            return {"mode": "udp", "client_rtp_port": rtp_port, "client_rtcp_port": rtcp_port}
        return None


# --------------------------------------------------------------------------- #
# 服务端
# --------------------------------------------------------------------------- #
class RtspServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8554, log=None,
                 advertise_ip: str = ""):
        self.host = host
        self.port = port
        self.advertise_ip = advertise_ip or host
        self._log = log or (lambda level, msg: None)
        self.default_server = "RTSP Server"

        self._routes: Dict[str, _Route] = {}
        self._route_lock = threading.Lock()
        self._connections: List[RtspConnection] = []
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._rtcp_thread: Optional[threading.Thread] = None
        self.stopping = True

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def add_route(self, stream, path: str, vendor_key: str = "generic") -> str:
        route = _Route(path, get_vendor(vendor_key), stream)
        with self._route_lock:
            self._routes[route.key] = route
        return route.key

    def remove_route(self, path: str) -> None:
        path_only, _, query = path.partition("?")
        key = normalize_key(path_only, query)
        with self._route_lock:
            self._routes.pop(key, None)

    def clear_routes(self) -> None:
        with self._route_lock:
            self._routes.clear()

    def resolve(self, uri: str) -> Optional[_Route]:
        cleaned = CONTROL_SUFFIX_RE.sub("", uri.strip())
        parts = urlsplit(cleaned)
        if parts.scheme in ("rtsp", "rtsps"):
            path, query = parts.path, parts.query
        else:
            path, _, query = cleaned.partition("?")
        path = path.rstrip("/") or "/"

        key = normalize_key(path, query)
        with self._route_lock:
            route = self._routes.get(key)
            if route is not None:
                return route
            # 容错：客户端漏了查询串 / 多了查询串
            for candidate in self._routes.values():
                if candidate.path_only == path:
                    return candidate
        return None

    def absolute_url(self, path: str) -> str:
        return f"rtsp://{self.advertise_ip}:{self.port}{path}"

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if not self.stopping:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError as exc:
            sock.close()
            raise RuntimeError(f"端口 {self.host}:{self.port} 绑定失败：{exc}") from exc
        sock.listen(32)
        self._sock = sock
        self.stopping = False
        self._thread = threading.Thread(target=self._accept_loop, name="rtsp-accept", daemon=True)
        self._thread.start()
        self._rtcp_thread = threading.Thread(target=self._rtcp_loop, name="rtcp", daemon=True)
        self._rtcp_thread.start()
        self._log("info", f"RTSP 服务已启动：{self.host}:{self.port}")

    def stop(self) -> None:
        self.stopping = True
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        for conn in list(self._connections):
            conn._cleanup()
        self._connections.clear()
        self._thread = None
        self._log("info", "RTSP 服务已停止")

    @property
    def running(self) -> bool:
        return not self.stopping

    # ------------------------------------------------------------------ #
    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self.stopping:
            try:
                client, addr = self._sock.accept()
            except OSError:
                break
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn = RtspConnection(self, client, addr)
            self._connections.append(conn)
            conn.start()

    def _forget_connection(self, conn: RtspConnection) -> None:
        if conn in self._connections:
            self._connections.remove(conn)

    def _rtcp_loop(self) -> None:
        while not self.stopping:
            time.sleep(5.0)
            for conn in list(self._connections):
                for session in list(conn.sessions.values()):
                    channel = session.channel
                    if channel and channel.active:
                        try:
                            channel.send_rtcp_sr()
                        except Exception:
                            pass

    # ------------------------------------------------------------------ #
    @property
    def client_total(self) -> int:
        return sum(1 for conn in self._connections for s in conn.sessions.values()
                   if s.channel and s.channel.active)

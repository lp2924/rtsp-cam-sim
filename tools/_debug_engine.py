"""调试用：起一个只跑单路流的引擎实例，方便手工用 ffprobe/VLC 连上来定位问题。"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.engine import Engine                       # noqa: E402
from app.models import AppConfig, Route, StreamSpec  # noqa: E402

PORT = int(os.environ.get("DBG_PORT", "8557"))

engine = Engine(
    AppConfig(host="127.0.0.1", port=PORT, advertise_ip="127.0.0.1"),
    log=lambda level, msg: print(f"[{level}] {msg}", flush=True),
)
engine.start_server()

spec = StreamSpec(
    name="dbg", source=os.path.join(ROOT, "samples", "demo_720p.mp4"), codec="h264",
    width=1280, height=720, fps=25.0, kbps=1500,
    routes=[
        Route("/Streaming/Channels/101", "hikvision"),
        Route("/cam/realmonitor?channel=1&subtype=0", "dahua"),
        Route("/stream1", "generic"),
    ],
)
engine.start_stream(spec)
src = engine.sources.get(spec.id)
for _ in range(60):
    if src and src.state == "running":
        break
    time.sleep(0.2)
print(f"state={src.state if src else 'none'} params={bool(src and src.param_sets)}", flush=True)

try:
    time.sleep(float(os.environ.get("DBG_SECONDS", "120")))
finally:
    engine.stop_server()

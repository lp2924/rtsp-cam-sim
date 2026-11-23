"""生成仓内自带的示例视频 samples/demo_720p.mp4。

要点：加 `-movflags +faststart`，把 moov 放到文件头。
否则 HTTP 直连播放时，ffmpeg 需要做尾部 seek，遇到不支持 Range 的简易 HTTP 服务器就会拉流失败。
用法：.venv\\Scripts\\python.exe tools\\make_sample.py [ffmpeg 路径]
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.media.pipeline import find_ffmpeg   # noqa: E402

OUTPUT = os.path.join(ROOT, "samples", "demo_720p.mp4")


def main() -> int:
    ffmpeg = sys.argv[1] if len(sys.argv) > 1 else find_ffmpeg()
    if not ffmpeg:
        print("找不到 ffmpeg")
        return 2

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "12",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        "-movflags", "+faststart",
        OUTPUT,
    ]
    print("生成示例视频：", OUTPUT)
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("生成失败")
        return 1
    print("完成，大小 %.1f MB" % (os.path.getsize(OUTPUT) / 1024 / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())

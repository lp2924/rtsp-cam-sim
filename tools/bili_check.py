"""哔哩哔哩解析自检：把视频页解析成直链，并用 ffprobe 验证直链真的可读。

用法：
    .venv\\Scripts\\python.exe tools\\bili_check.py https://www.bilibili.com/video/BV1hh4y1N7k2/
    .venv\\Scripts\\python.exe tools\\bili_check.py <url> --cookie "SESSDATA=xxxx"
    .venv\\Scripts\\python.exe tools\\bili_check.py <url> --min-height 1080
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.media import bilibili                     # noqa: E402
from app.media.pipeline import find_ffmpeg         # noqa: E402

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    url = args[0]
    cookie = ""
    min_height = 0
    for index, arg in enumerate(sys.argv[1:]):
        if arg == "--cookie":
            cookie = sys.argv[index + 2]
        elif arg == "--min-height":
            min_height = int(sys.argv[index + 2])
        elif arg == "--no-fhd":
            min_height = 0

    if not bilibili.is_bilibili_url(url):
        print("这不是哔哩哔哩视频页地址：", url)
        return 2

    print("解析中…（匿名默认带 try_look，普通视频可拿到 1080P）")
    try:
        info = bilibili.resolve(url, cookie=cookie, min_height=min_height)
    except bilibili.ResolveError as exc:
        print("解析失败：", exc)
        return 1

    print(f"标题    ：{info['title']}")
    print(f"分P     ：第 {info['page']}/{info['pages']} P  {info['part']}")
    print(f"分辨率  ：{info['width']}×{info['height']}（{info['quality_name']}，id={info['quality']}）")
    print(f"编码    ：{info['codecs']}")
    print(f"时长    ：{info['duration']:.0f} 秒")
    print(f"直链    ：{info['url'][:110]}…")
    print(f"备用数  ：{len(info['backup_urls'])}")

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("\n找不到 ffmpeg，跳过直链验证")
        return 0
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
    extra = bilibili.ffmpeg_input_args(info)
    probe = subprocess.run([ffprobe, "-v", "error", *extra,
                            "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,codec_name,avg_frame_rate",
                            "-of", "default=nw=1", info["url"]],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
    print("\nffprobe 直链结果：")
    print(probe.stdout.decode("utf-8", "replace").strip() or probe.stderr.decode("utf-8", "replace").strip())
    return 0 if probe.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

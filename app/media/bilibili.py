"""哔哩哔哩视频页解析：把 www.bilibili.com/video/BVxxx 变成可直接喂给 ffmpeg 的直链。

为什么必须解析：ffmpeg 拿网页地址只会得到 HTML；而 B 站 CDN 又对缺少 Referer 的请求返回 403，
所以这里做三件事：
  1. view 接口取 cid / 标题 / 时长（分 P 也支持，`?p=2`）；
  2. playurl 接口取 DASH 直链，只取视频轨（本程序本来就不转发音频）；
  3. 返回直链 + 必要请求头（User-Agent / Referer / 可选 Cookie）。

清晰度说明：B 站匿名（不带 Cookie）只给到 480P；想要 720P/1080P 需要在设置里填 SESSDATA。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"/video/av(\d+)")
PAGE_RE = re.compile(r"[?&]p=(\d+)")
SHORT_RE = re.compile(r"https?://(?:b23\.tv|bili2233\.cn)/([0-9A-Za-z]+)")

# 清晰度代号（只用于日志展示）
QUALITY_NAMES = {
    127: "8K", 126: "杜比视界", 125: "HDR", 120: "4K", 116: "1080P60",
    112: "1080P+", 80: "1080P", 74: "720P60", 64: "720P", 32: "480P", 16: "360P", 6: "240P",
}


class ResolveError(Exception):
    """解析失败（给界面展示的中文原因）。"""


def is_bilibili_url(value: str) -> bool:
    """判断是不是哔哩哔哩的网页地址（不是直链）。"""
    text = (value or "").strip().lower()
    if not text:
        return False
    return ("bilibili.com/" in text) or ("b23.tv/" in text) or ("bili2233.cn/" in text)


def _headers(cookie: str = "") -> Dict[str, str]:
    result = {"User-Agent": UA, "Referer": REFERER, "Accept": "application/json"}
    if cookie.strip():
        value = cookie.strip()
        # 允许直接粘贴整段 cookie，也允许只粘 SESSDATA 的值
        result["Cookie"] = value if "=" in value else f"SESSDATA={value}"
    return result


def _http_json(url: str, headers: Dict[str, str], timeout: float = 12.0) -> dict:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ResolveError(f"接口请求失败 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ResolveError(f"网络不可用：{exc.reason}") from exc
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ResolveError("接口返回的不是 JSON（可能被风控或网络劫持）") from exc


def _expand_short(url: str) -> str:
    """b23.tv 短链 → 真实视频页地址。"""
    if not SHORT_RE.search(url):
        return url
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.geturl()
    except Exception as exc:                                  # noqa: BLE001
        raise ResolveError(f"短链展开失败：{exc}") from exc


def parse_video_id(url: str) -> Tuple[Optional[str], Optional[str], int]:
    """从地址里取 (bvid, aid, 分P页码)。"""
    page_match = PAGE_RE.search(url)
    page = int(page_match.group(1)) if page_match else 1
    bv_match = BV_RE.search(url)
    av_match = AV_RE.search(url)
    return (bv_match.group(1) if bv_match else None,
            av_match.group(1) if av_match else None,
            max(1, page))


def _pick_track(videos: List[dict], max_height: int) -> dict:
    """挑一条视频轨：优先 H.264，其次分辨率最高、码率最高。"""
    usable = [v for v in videos if v.get("baseUrl")]
    if not usable:
        raise ResolveError("没有拿到任何视频直链（可能需要登录）")
    avc = [v for v in usable if str(v.get("codecs", "")).startswith("avc")]
    pool = avc or usable
    capped = [v for v in pool if int(v.get("height") or 0) <= max_height]
    pool = capped or pool
    pool.sort(key=lambda v: (int(v.get("height") or 0), int(v.get("bandwidth") or 0)))
    return pool[-1]


def resolve(url: str, *, cookie: str = "", max_height: int = 1080,
            min_height: int = 0) -> dict:
    """把哔哩哔哩视频页解析成「ffmpeg 能直接读的直链 + 请求头」。

    返回 dict：
        url / backup_urls / headers / title / duration / width / height /
        codecs / quality / quality_name / cid / page / pages
    失败抛 ResolveError，消息可以直接显示给用户。

    min_height > 0 时，若该视频拿不到这么高的清晰度就直接报错（而不是静默降级）。
    """
    page_url = _expand_short(url.strip())
    if "/bangumi/" in page_url or "/cheese/" in page_url:
        raise ResolveError("暂不支持番剧 / 课堂链接，请粘贴普通视频页（.../video/BVxxx）")

    bvid, aid, page = parse_video_id(page_url)
    if not bvid and not aid:
        raise ResolveError("没识别出 BV 号，请粘贴形如 https://www.bilibili.com/video/BV1xx411c7mD 的地址")

    headers = _headers(cookie)
    params = {"bvid": bvid} if bvid else {"aid": aid}
    view = _http_json(f"{VIEW_API}?{urllib.parse.urlencode(params)}", headers)
    if view.get("code") != 0:
        code, msg = view.get("code"), view.get("message", "")
        raise ResolveError(f"取视频信息失败（{code}）：{msg}")
    data = view.get("data") or {}
    pages = data.get("pages") or []
    if pages:
        page = min(page, len(pages))
        entry = pages[page - 1]
        cid = int(entry.get("cid") or data.get("cid") or 0)
        part = entry.get("part") or data.get("title", "")
        duration = float(entry.get("duration") or data.get("duration") or 0)
    else:
        page = 1
        cid = int(data.get("cid") or 0)
        part = data.get("title", "")
        duration = float(data.get("duration") or 0)
    if not cid:
        raise ResolveError("视频信息里没有 cid（可能是付费/地区限制视频）")

    query = {
        "cid": cid,
        "qn": 120,                    # 请求尽可能高，服务端按登录态降级
        "fnval": 4048,                # DASH + 全部特性
        "fourk": 1,
        "try_look": 1,                # 关键：带它匿名也能拿到 1080P（否则最高只给 480P）
    }
    # 注意：不要带 platform=html5，那个值会让接口只回 durl（单片 mp4）而不回 DASH
    query.update({"bvid": bvid} if bvid else {"aid": aid})
    play = _http_json(f"{PLAYURL_API}?{urllib.parse.urlencode(query)}", headers)
    if play.get("code") != 0:
        code, msg = play.get("code"), play.get("message", "")
        hint = "（需要登录态，可在「设置」里填 SESSDATA）" if code in (-403, -10403, -101) else ""
        raise ResolveError(f"取播放地址失败（{code}）：{msg}{hint}")
    play_data = play.get("data") or {}
    dash = play_data.get("dash") or {}
    videos = dash.get("video") or []
    if not videos:
        # 少数视频只回 durl（单片 mp4）：能用，但清晰度通常只有 480P
        durls = play_data.get("durl") or []
        if durls and durls[0].get("url"):
            quality = int(play_data.get("quality") or 0)
            return {
                "url": durls[0]["url"],
                "backup_urls": [u for u in (durls[0].get("backup_url") or []) if u],
                "headers": headers,
                "title": data.get("title", ""),
                "part": part,
                "duration": float(durls[0].get("length") or duration) / (
                    1000.0 if int(durls[0].get("length") or 0) > 10000 else 1.0),
                "width": 0,
                "height": 0,
                "codecs": "mp4(durl)",
                "quality": quality,
                "quality_name": QUALITY_NAMES.get(quality, str(quality)),
                "cid": cid,
                "page": page,
                "pages": len(pages) or 1,
            }
        raise ResolveError("该视频没有 DASH 视频轨（可能是互动视频/付费视频），暂不支持")

    track = _pick_track(videos, max_height)
    best_height = max(int(v.get("height") or 0) for v in videos)
    if min_height and int(track.get("height") or 0) < min_height:
        hint = "" if cookie.strip() else "；可在「设置」里填 SESSDATA 用登录态再试（1080P60/高码率有时需要登录或大会员）"
        raise ResolveError(
            f"该视频最高只能拿到 {best_height}P，达不到 {min_height}P{hint}"
        )
    backup = [u for u in (track.get("backupUrl") or []) if u]
    quality = int(track.get("id") or 0)
    return {
        "url": track["baseUrl"],
        "backup_urls": backup,
        "headers": headers,
        "title": data.get("title", ""),
        "part": part,
        "duration": duration,
        "width": int(track.get("width") or 0),
        "height": int(track.get("height") or 0),
        "codecs": track.get("codecs", ""),
        "quality": quality,
        "quality_name": QUALITY_NAMES.get(quality, str(quality)),
        "cid": cid,
        "page": page,
        "pages": len(pages) or 1,
    }


def ffmpeg_input_args(info: dict) -> List[str]:
    """把请求头转成 ffmpeg 输入参数（必须放在 -i 之前）。

    Cookie 会进 -headers，所以日志里要对它做掩码，见 mask_secret()。
    """
    headers = info.get("headers") or {}
    args: List[str] = ["-user_agent", headers.get("User-Agent", UA)]
    lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k != "User-Agent")
    if lines:
        args += ["-headers", lines]
    return args


def mask_secret(text: str, info: dict) -> str:
    """把 Cookie 值从命令行文本里抹掉，避免日志泄露登录态。"""
    cookie = (info.get("headers") or {}).get("Cookie", "")
    if not cookie:
        return text
    value = cookie.split("=", 1)[-1] if "SESSDATA=" in cookie else cookie
    if len(value) >= 8:
        text = text.replace(value, "******")
    return text

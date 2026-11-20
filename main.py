"""Furry convention utilities: countdown, calendar, fortune, group stats.

All answers are one image card with no extra chat. Triggered by slash commands
(``/下次展``) or by calling the bot by name (``阿灰 下次展``). Card files are
cached per day and per query, so repeated asks reuse the same PNG.

Data: FEC 兽展日历 (furrycons.cn). The site is Next.js and embeds the whole
event list in ``__NEXT_DATA__``; we parse that and cache for a few hours.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter


def _detect_root() -> Path:
    """Locate the data root without hard-coding any machine-specific path.

    Priority:
      1. ``QQNTBOT_ROOT`` environment variable (explicit override).
      2. Walk up from this file looking for an ``apps/AstrBot/data`` layout
         (the original QQNTBOT project layout).
      3. Fall back to this plugin's own directory, so a cloned plugin works
         out of the box with data written beside it.

    Override example (PowerShell):
        $env:QQNTBOT_ROOT = "D:\\QQNTBOT"
    """
    override = os.environ.get("QQNTBOT_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "apps" / "AstrBot" / "data").is_dir():
            return parent
    return here.parent


ROOT = _detect_root()
CACHE_FILE = ROOT / "data" / "furcons_cache.json"
CARD_DIR = ROOT / "data" / "furcons_cards"
WATCH_FILE = ROOT / "data" / "furcon_watch.json"
ENABLE_FILE = ROOT / "data" / "furcon_enabled.json"

# AstrBot's database always sits two directories above plugins/, regardless of
# whether the host uses the original <root>/apps/AstrBot layout or a plain
# AstrBot install. Override with FURCON_BOT_DB if that does not hold.
_db_override = os.environ.get("FURCON_BOT_DB", "").strip()
BOT_DB = (
    Path(_db_override).expanduser()
    if _db_override
    else Path(__file__).resolve().parent.parent.parent / "data_v4.db"
)
SOURCE_URL = "https://www.furrycons.cn/"
CACHE_TTL = 6 * 3600
CST = timezone(timedelta(hours=8))

SCALE_TEXT = {
    "mini": "迷你",
    "small": "小型",
    "medium": "中型",
    "large": "大型",
    "xlarge": "特大型",
}
TYPE_TEXT = {
    "hotel": "酒店",
    "venue": "场馆",
    "comic-market": "综合展会",
    "special": "专项聚会",
    "travel": "旅游聚会",
}

# Bundled open-source CJK font (Noto Sans SC, SIL OFL 1.1 — see
# fonts/OFL.txt). Shipped with the plugin so cards render identically on any
# OS without relying on proprietary system fonts. FURCON_FONT lets a user
# override it with an absolute path to any font file they prefer.
BUNDLED_FONT = Path(__file__).resolve().parent / "fonts" / "NotoSansSC-Bold.ttf"
_font_override = os.environ.get("FURCON_FONT", "").strip()
FONT_CANDIDATES = [p.strip() for p in _font_override.split(os.pathsep) if p.strip()] if _font_override else []

FONT_CANDIDATES += [
    str(BUNDLED_FONT),
    # Fallbacks for anyone who deleted the bundled file.
    # Windows
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    # Linux (Debian/Ubuntu: fonts-noto-cjk / fonts-wqy-microhei)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]

# Bot names that trigger "call by name" mode (env-overridable).
NAME_CALL = os.environ.get("FURCON_NAME_CALL", r"(阿灰|小灰|灰狼)")

# 权限：优先用 access 统一判定，单拎时自动降级为本地名单（也能用）
sys.path.insert(0, str(ROOT / "apps" / "AstrBot" / "data" / "plugins" / "astrbot_plugin_access"))
try:
    from unified_perm import admin_qqs as _admin_qqs
    from unified_perm import is_admin as _unified_is_admin
except ImportError:
    # 单拎模式：环境变量或默认名单
    _admins_raw = os.environ.get("FURCON_ADMINS", "1154487120")
    _STANDALONE_ADMINS = {x.strip() for x in re.split(r"[,\s]+", _admins_raw) if x.strip()}
    def _unified_is_admin(event):  # noqa: F811
        uid = str(event.get_sender_id() or "")
        return uid in _STANDALONE_ADMINS or getattr(event, "role", "") == "admin"
    def _admin_qqs():  # noqa: F811
        return sorted(_STANDALONE_ADMINS)

# 本插件自声明指令（单拎时帮助/权限表自动读这里）
DECLARED_COMMANDS = {
    "/下次展 /近期展 /<城市>下次展 /<城市>近期展": ("兽展倒计时/列表卡", "公开"),
    "/兽聚help /兽聚指令 /兽聚功能 /兽聚用法": ("兽聚帮助", "公开"),
    "/运势 /人品 /jrrp /jrys": ("今日人品卡", "公开"),
    "/群统计 /谁最活跃 /水群榜": ("近 7 天发言榜", "公开"),
    "/启用 /启用功能 /开启功能 /打开功能": ("开启本群兽聚功能（对群）", "管理"),
    "/关闭 /关闭功能 /停用功能 /禁用功能": ("关闭本群兽聚功能（对群）", "管理"),
    "/关心 <展会名> [HH:MM]": ("设特别关心播报", "管理"),
    "/取消关心 /不关心了 /取消关注": ("关掉播报", "管理"),
}

# Commands with their own syntax are checked before the compositional event
# matcher.  Keeping them separate stops e.g. "/关心 苏州小聚" (which contains
# the kind word 聚) from being mistaken for an event query.
COMMANDS: list[tuple[str, re.Pattern]] = [
    ("help", re.compile(r"^(兽聚help|兽聚指令|兽聚功能|兽聚用法)$", re.I)),
    ("enable_on", re.compile(r"^(启用功能|开启功能|打开功能|启用)$")),
    ("enable_off", re.compile(r"^(关闭功能|停用功能|禁用功能|关闭)$")),
    ("watch_off", re.compile(r"(取消关心|不关心了|取消关注)")),
    ("watch", re.compile(r"^(关心|特别关心|关注)\s*(.*)$")),
    ("fortune", re.compile(r"(运势|运气|抽个签|求签|吉凶|今日份|转运|人品|jrrp|jrys)")),
    ("stats", re.compile(r"(群统计|发言统计|谁最活跃|水群榜|活跃度|话最多|谁话多)")),
]

# Event queries are matched compositionally instead of by enumerating every
# permutation.  A query is read as: [时序] + [范围] + [地名] + [类型],
# in any order, e.g. 下次展 / 下一次苏州兽聚 / 江苏近期聚 / 苏州上次展.
# The first time word (by position) decides 过去/下次/近期; if there is no
# time word but a kind word is present the query is a plain city listing.
# Longer words must come first so "上一次" is not shadowed by "上次".
EVENT_TIME_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("past", ("上一次", "上次", "上回", "上届", "办过", "过去")),
    ("next", ("下一次", "下一场", "下一届", "下次", "下场", "下回",
              "接下来", "将来", "还有几天", "倒计时")),
    ("recent", ("近期", "最近", "前瞻", "展会一览")),
]
# 展 and 聚 (and their compounds) are treated as the same kind: the FEC feed
# has no separate 展/聚 field, so "下次展" and "下次聚" must resolve alike.
EVENT_KIND_WORDS = ("兽聚", "兽展", "展会", "漫展", "展", "聚")
# Old standalone trigger phrases that contain neither 展 nor 聚; keep them so
# "/还有几天", "/最近有什么", "/前瞻" etc. still work after the rewrite.
EVENT_EXTRA_MARKERS = ("最近有什么", "最近有哪些", "近期活动", "有什么活动",
                       "前瞻", "下一场", "下一届", "还有几天", "倒计时")

PROVINCE_CITIES = {
    "江苏": ["南京", "苏州", "无锡", "常州", "扬州", "徐州", "南通", "镇江", "泰州", "盐城"],
    "浙江": ["杭州", "宁波", "温州", "嘉兴", "绍兴", "金华", "湖州", "台州", "舟山"],
    "广东": ["广州", "深圳", "佛山", "东莞", "珠海", "中山", "惠州", "汕头", "江门"],
    "四川": ["成都", "绵阳", "乐山", "德阳", "宜宾"],
    "山东": ["济南", "青岛", "烟台", "潍坊", "临沂", "淄博"],
    "湖北": ["武汉", "宜昌", "襄阳"],
    "湖南": ["长沙", "株洲", "衡阳"],
    "河南": ["郑州", "洛阳", "开封"],
    "河北": ["石家庄", "唐山", "保定"],
    "福建": ["厦门", "福州", "泉州"],
    "安徽": ["合肥", "芜湖", "蚌埠"],
    "江西": ["南昌", "赣州", "九江"],
    "山西": ["太原", "大同"],
    "陕西": ["西安", "咸阳"],
    "辽宁": ["沈阳", "大连", "鞍山"],
    "吉林": ["长春", "吉林"],
    "黑龙江": ["哈尔滨", "大庆", "齐齐哈尔"],
    "云南": ["昆明", "大理", "丽江"],
    "贵州": ["贵阳", "遵义"],
    "广西": ["南宁", "桂林", "柳州"],
    "甘肃": ["兰州"],
    "内蒙古": ["呼和浩特", "包头"],
    "海南": ["海口", "三亚"],
    "新疆": ["乌鲁木齐"],
    "宁夏": ["银川"],
    "青海": ["西宁"],
    "西藏": ["拉萨"],
    "重庆": ["重庆"],
    "天津": ["天津"],
    "北京": ["北京"],
    "上海": ["上海"],
    "香港": ["香港"],
    "澳门": ["澳门"],
    "台湾": ["台北", "高雄"],
}


def expand_place(token: str) -> list[str]:
    """Expand a city or province into the list of city names to match."""
    if not token:
        return []
    if token in PROVINCE_CITIES:
        return [token] + PROVINCE_CITIES[token]
    return [token]


PROVINCES = [
    "江苏", "浙江", "广东", "四川", "山东", "河南", "湖北", "湖南", "河北", "福建",
    "安徽", "江西", "山西", "陕西", "辽宁", "吉林", "黑龙江", "云南", "贵州", "广西",
    "甘肃", "青海", "宁夏", "新疆", "西藏", "内蒙古", "海南", "台湾", "香港", "澳门",
]

CITY_NAMES = [
    "北京", "上海", "广州", "深圳", "成都", "重庆", "杭州", "南京", "武汉", "西安",
    "天津", "苏州", "长沙", "郑州", "青岛", "厦门", "福州", "济南", "合肥", "昆明",
    "大连", "沈阳", "哈尔滨", "长春", "石家庄", "太原", "南昌", "南宁", "贵阳",
    "海口", "兰州", "银川", "西宁", "乌鲁木齐", "拉萨", "呼和浩特", "宁波", "温州",
    "无锡", "佛山", "东莞", "珠海", "中山", "嘉兴", "绍兴", "金华", "洛阳",
]

FORTUNE_LEVELS = [
    ("大吉", (255, 96, 96), "今天说什么都对，大胆点"),
    ("中吉", (255, 150, 80), "稳着来，好事在后面"),
    ("小吉", (255, 200, 90), "小确幸会来，别急"),
    ("平", (150, 190, 150), "不好不坏，正常过"),
    ("小凶", (120, 160, 210), "少说两句，能避就避"),
    ("凶", (130, 130, 160), "别折腾，早点睡"),
]
FORTUNE_TIPS = [
    "适合主动找人说句话", "别在群里赌气", "今天适合摸鱼", "有事直接问，别憋着",
    "买东西前先看余额", "多喝水", "别熬夜，真的", "适合补番", "别接奇怪的活",
    "把想说的话说清楚",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Load a CJK-capable font, falling back to the default bitmap font."""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _today() -> str:
    """Return today's date string used in cache keys."""
    return datetime.now(CST).strftime("%Y%m%d")


def _card_path(key: str) -> Path:
    """Return the per-day cache path for a query key.

    中日文会被过滤成下划线，不同城市会撞名，所以补一段短哈希做区分。
    """
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", key)[:20].strip("_") or "x"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:8]
    return CARD_DIR / f"{_today()}_{safe}_{digest}.png"


def _draw_banner(img: Image.Image, draw: ImageDraw.ImageDraw, url: str,
                 width: int, height: int) -> None:
    """Paste a promo image as the top banner, or fill a placeholder color."""
    banner = None
    if url:
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            banner = Image.open(io.BytesIO(resp.content)).convert("RGB")
            scale = max(width / banner.width, height / banner.height)
            banner = banner.resize(
                (int(banner.width * scale) + 1, int(banner.height * scale) + 1)
            )
            left = (banner.width - width) // 2
            top = (banner.height - height) // 2
            banner = banner.crop((left, top, left + width, top + height))
        except Exception:  # noqa: BLE001
            banner = None
    if banner:
        img.paste(banner, (0, 0))
    else:
        draw.rectangle((0, 0, width, height), fill=(38, 42, 52))


def _place(value) -> str:
    """Normalize a place field that may be a string or a {localName} dict."""
    if isinstance(value, dict):
        for key in ("localName", "name", "fullName"):
            if value.get(key):
                return str(value[key])[:24]
        return ""
    return str(value or "")[:24]


async def fetch_events(force: bool = False) -> list[dict]:
    """Return all events sorted by start time, refreshing the cache when stale."""
    if not force and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - cached.get("fetched", 0) < CACHE_TTL:
                return cached.get("events", [])
        except (json.JSONDecodeError, OSError):
            pass

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[furcon] fetch failed: {exc}")
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text(encoding="utf-8")).get("events", [])
            except (json.JSONDecodeError, OSError):
                pass
        return []

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not match:
        logger.warning("[furcon] __NEXT_DATA__ not found")
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    raw_events = data.get("props", {}).get("pageProps", {}).get("events", []) or []
    events = []
    for item in raw_events:
        try:
            start = datetime.fromisoformat(item["startAt"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(item["endAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        name = str(item.get("name", "")).strip()
        if not name or name.isdigit():
            continue

        raw_org = item.get("organization") or item.get("organizations") or ""
        if isinstance(raw_org, dict):
            org = str(raw_org.get("name", "")).strip()
        elif isinstance(raw_org, list):
            names = []
            for one in raw_org:
                if isinstance(one, dict):
                    names.append(str(one.get("name", "")).strip())
                elif isinstance(one, str):
                    names.append(one.strip())
            org = "、".join(n for n in names if n)
        else:
            org = str(raw_org).strip()

        thumb = str(item.get("thumbnail") or "").strip()
        if thumb and not thumb.startswith("http"):
            thumb = f"https://images.furrycons.cn/{thumb.lstrip('/')}"

        events.append(
            {
                "name": name,
                "thumb": thumb,
                "start": start.astimezone(CST).strftime("%Y-%m-%d"),
                "end": end.astimezone(CST).strftime("%Y-%m-%d"),
                "start_ts": start.timestamp(),
                "end_ts": end.timestamp(),
                "city": _place(item.get("placeName")) or _place(item.get("region")),
                "region": _place(item.get("region")),
                "org": org,
                "scale": SCALE_TEXT.get(str(item.get("scale")), ""),
                "type": TYPE_TEXT.get(str(item.get("locationType")), ""),
            }
        )

    events.sort(key=lambda e: e["start_ts"])
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"fetched": time.time(), "events": events}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass
    return events


def pick_next(events: list[dict]) -> dict | None:
    """Return the nearest event that has not finished yet."""
    now = time.time()
    upcoming = [e for e in events if e["end_ts"] >= now - 86400]
    return upcoming[0] if upcoming else None


def _days_until(event: dict) -> int:
    """Whole days between today and the event start."""
    return max(
        0,
        (
            datetime.strptime(event["start"], "%Y-%m-%d").date()
            - datetime.now(CST).date()
        ).days,
    )


def render_next(event: dict, following: list[dict],
                updated: str = "", title: str = "下一次兽聚") -> Path:
    """Draw the countdown card for one convention."""
    started = time.perf_counter()
    path = _card_path(f"next_{title}")
    if path.exists():
        return path

    width, banner_h = 900, 380
    shown = following[:3]
    height = banner_h + 700 + max(1, len(shown)) * 64 + 150

    img = Image.new("RGB", (width, height), (24, 26, 32))
    draw = ImageDraw.Draw(img)
    _draw_banner(img, draw, event.get("thumb") or "", width, banner_h)

    title_font, big_font = _font(52), _font(150)
    body_font, small_font = _font(34), _font(26)
    days = _days_until(event)

    y = banner_h + 40
    draw.text((60, y), title[:16], font=small_font, fill=(140, 146, 160))
    y += 42
    draw.text((60, y), _full_name(event)[:18], font=title_font, fill=(240, 240, 245))
    y += 100
    draw.text((60, y), "今天" if days == 0 else "还有", font=body_font,
              fill=(140, 146, 160))
    y += 40
    draw.text((60, y), f"{days} 天", font=big_font, fill=(255, 190, 90))
    y += 220
    draw.text((60, y), f"{event['start']} ~ {event['end']}", font=body_font,
              fill=(220, 220, 228))
    y += 60
    line2 = " · ".join(x for x in (event["city"], event["scale"], event["type"]) if x)
    if line2:
        draw.text((60, y), line2, font=body_font, fill=(180, 186, 198))
        y += 60
    if event.get("org"):
        draw.text((60, y), f"主办：{event['org']}", font=small_font,
                  fill=(150, 156, 170))
        y += 50

    y += 20
    draw.line((60, y, width - 60, y), fill=(60, 64, 74), width=2)
    y += 30
    draw.text((60, y), "之后还有", font=small_font, fill=(140, 146, 160))
    y += 50
    for nxt in shown:
        draw.text((60, y), f"{nxt['start']}  {nxt['name'][:14]}", font=body_font,
                  fill=(210, 214, 222))
        y += 64

    stamp = datetime.now(CST).strftime("%m-%d %H:%M")
    footer = "数据：FEC 兽展日历 furrycons.cn"
    if updated:
        footer += f" · 更新于 {updated}"
    draw.text((60, height - 92), footer, font=small_font, fill=(120, 124, 136))
    draw.text((60, height - 50),
              f"生成于 {stamp} · {int((time.perf_counter() - started) * 1000)} ms",
              font=small_font, fill=(90, 94, 106))

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def _cover(url: str, width: int, height: int) -> Image.Image | None:
    """Download and center-crop a thumbnail to the requested size."""
    if not url:
        return None
    try:
        resp = httpx.get(url, timeout=12, follow_redirects=True)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        scale = max(width / img.width, height / img.height)
        img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
        left = (img.width - width) // 2
        top = (img.height - height) // 2
        return img.crop((left, top, left + width, top + height))
    except Exception:  # noqa: BLE001
        return None


def _tile(event: dict, width: int, height: int, days: int | None) -> Image.Image:
    """Render one event tile: cover image with a dark caption overlay."""
    tile = Image.new("RGB", (width, height), (34, 38, 48))
    cover = _cover(event.get("thumb") or "", width, height)
    if cover:
        tile.paste(cover, (0, 0))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    bar_top = int(height * 0.52)
    for i in range(bar_top, height):
        alpha = int(215 * (i - bar_top) / max(1, height - bar_top))
        od.rectangle((0, i, width, i + 1), fill=(10, 12, 16, alpha))
    tile = Image.alpha_composite(tile.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(tile)
    name_font = _font(30)
    info_font = _font(20)
    tiny_font = _font(18)

    draw.text((16, bar_top + 8), event["name"][:12], font=name_font,
              fill=(255, 255, 255))
    date_line = f"{event['start']} ~ {event['end']}"
    draw.text((16, bar_top + 46), date_line, font=info_font, fill=(220, 224, 232))
    place = " · ".join(
        x for x in (str(event.get("city", ""))[:12], event.get("scale", "")) if x
    )
    if place:
        draw.text((16, bar_top + 72), place, font=tiny_font, fill=(190, 196, 208))

    if days is not None:
        label = "今天" if days == 0 else f"{days} 天"
        box_w = 16 + len(label) * 20
        draw.rectangle((width - box_w - 16, 16, width - 16, 58), fill=(255, 176, 60))
        draw.text((width - box_w - 4, 22), label, font=info_font, fill=(24, 26, 32))
    return tile


CARD_BG = (20, 22, 28)


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    """Round the corners of an image using a mask."""
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1),
                                           radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def _full_name(event: dict) -> str:
    """Combine organizer and event name, avoiding duplication.

    Args:
        event: Normalized event dict.

    Returns:
        Display name such as ``绒萌世界双澜遗珠``.
    """
    name = str(event.get("name", "")).strip()
    org = str(event.get("org", "")).strip()
    if org and org not in name:
        return f"{org}{name}"
    return name


def _recent_row(event: dict, width: int, height: int, label: str,
                days: int, upcoming: bool) -> Image.Image:
    """Render one rounded row: colour cover, soft text shadow, no heavy overlay."""
    card = Image.new("RGB", (width, height), CARD_BG)
    cover = _cover(event.get("thumb") or "", width, height)
    if cover:
        if upcoming:
            # 未开始：保留原色，只压一点点亮度，避免文字被亮部吃掉
            dimmed = Image.blend(cover, Image.new("RGB", cover.size, (0, 0, 0)), 0.18)
        else:
            # 已结束：整张转灰
            dimmed = cover.convert("L").convert("RGB")
            dimmed = Image.blend(dimmed, Image.new("RGB", cover.size, CARD_BG), 0.45)
        card.paste(dimmed, (0, 0))

    # 底部轻过渡到卡片底色，收尾自然
    fade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    tail = int(height * 0.55)
    for i in range(tail, height):
        ratio = (i - tail) / max(1, height - tail)
        fd.rectangle((0, i, width, i + 1), fill=(*CARD_BG, int(190 * (ratio ** 1.6))))
    card = Image.alpha_composite(card.convert("RGBA"), fade).convert("RGB")

    num_font = _font(66)
    unit_font = _font(24)
    name_font = _font(36)
    meta_font = _font(22)

    color = (255, 186, 80) if upcoming else (150, 154, 166)
    name_color = (255, 255, 255) if upcoming else (184, 188, 198)
    meta_color = (226, 230, 238) if upcoming else (152, 156, 168)

    num_text = str(days)
    tmp = ImageDraw.Draw(card)
    num_w = tmp.textlength(num_text, font=num_font)

    text_x = 176
    items = [
        (26, 150, num_text, num_font, color),
        (26 + num_w + 6, 192, "天" if upcoming else "天前", unit_font, color),
        (text_x, 140, _full_name(event)[:17], name_font, name_color),
        (text_x, 186, f"{event['start']} ~ {event['end']}", meta_font, meta_color),
    ]
    place = " · ".join(
        x for x in (str(event.get("region", ""))[:10], str(event.get("city", ""))[:20]) if x
    )
    if place:
        items.append((text_x, 216, place[:32], meta_font, meta_color))
    if label:
        items.append((width - 26 - tmp.textlength(label, font=meta_font), 216,
                      label, meta_font, (255, 186, 80)))

    # 柔光阴影：文字铺一层模糊黑，再画正文
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for x, y, text, font, _c in items:
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 3)):
            sd.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 225))
    shadow = shadow.filter(ImageFilter.GaussianBlur(2))
    card = Image.alpha_composite(card.convert("RGBA"), shadow).convert("RGB")

    draw = ImageDraw.Draw(card)
    for x, y, text, font, fill in items:
        draw.text((x, y), text, font=font, fill=fill)

    return _rounded(card, 22)


def render_recent(events: list[dict]) -> Path:
    """Draw finished events first, then upcoming, as rounded cards with gaps."""
    started = time.perf_counter()
    path = _card_path("recent")
    if path.exists():
        return path

    now = time.time()
    past = [e for e in events if e["end_ts"] < now][-2:][::-1]
    upcoming = [e for e in events if e["end_ts"] >= now][:10]

    width = 900
    margin = 24
    card_w = width - margin * 2
    card_h = 300
    gap = 20
    header_h = 150
    section_h = 58
    total_cards = len(past) + len(upcoming)
    height = (
        120
        + header_h
        + section_h * (1 if past else 0)
        + section_h * (1 if upcoming else 0)
        + total_cards * (card_h + gap)
        + 70
    )
    img = Image.new("RGB", (width, height), CARD_BG)
    draw = ImageDraw.Draw(img)
    big, small = _font(52), _font(24)

    draw.text((margin, 42), "近期展会", font=big, fill=(240, 240, 245))
    draw.text((margin, 112), f"已结束 {len(past)} 场 · 即将举办 {len(upcoming)} 场",
              font=small, fill=(140, 146, 160))

    y = 164
    if past:
        draw.text((margin, y), "已结束", font=small, fill=(150, 154, 166))
        y += section_h
        for ev in past:
            over = max(0, int((now - ev["end_ts"]) // 86400))
            row = _recent_row(ev, card_w, card_h, "", over, False)
            img.paste(row, (margin, y), row)
            y += card_h + gap

    if upcoming:
        draw.text((margin, y), "即将举办", font=small, fill=(255, 186, 80))
        y += section_h
        for ev in upcoming:
            row = _recent_row(ev, card_w, card_h, ev.get("scale", ""),
                              _days_until(ev), True)
            img.paste(row, (margin, y), row)
            y += card_h + gap

    stamp = datetime.now(CST).strftime("%m-%d %H:%M")
    draw.text(
        (margin, height - 46),
        f"数据：FEC 兽展日历 · 生成于 {stamp} · "
        f"{int((time.perf_counter() - started) * 1000)} ms",
        font=small,
        fill=(96, 100, 112),
    )

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def render_city(city: str, events: list[dict], mode: str) -> Path:
    """Draw one city's events as banner cards (upcoming or finished)."""
    started = time.perf_counter()
    path = _card_path(f"city_{city}_{mode}")
    if path.exists():
        return path

    now = time.time()
    tokens = expand_place(city)
    hits = [
        e
        for e in events
        if any(t in (str(e.get("city", "")) + str(e.get("region", ""))) for t in tokens)
    ]
    if mode == "past":
        hits = [e for e in hits if e["end_ts"] < now][-6:][::-1]
        subtitle = "已举办"
    else:
        hits = [e for e in hits if e["end_ts"] >= now][:6]
        subtitle = "即将举办"

    width = 900
    margin = 24
    card_w = width - margin * 2
    card_h = 300
    gap = 20
    height = 190 + max(1, len(hits)) * (card_h + gap) + 70
    img = Image.new("RGB", (width, height), CARD_BG)
    draw = ImageDraw.Draw(img)
    big, small = _font(50), _font(24)

    draw.text((margin, 44), city, font=big, fill=(240, 240, 245))
    draw.text((margin, 112), f"{subtitle} {len(hits)} 场", font=small,
              fill=(140, 146, 160))

    y = 170
    if not hits:
        draw.text((margin, y), "没有查到记录", font=_font(32), fill=(160, 166, 178))
    for ev in hits:
        if mode == "past":
            over = max(0, int((now - ev["end_ts"]) // 86400))
            row = _recent_row(ev, card_w, card_h, "", over, False)
        else:
            row = _recent_row(ev, card_w, card_h, ev.get("scale", ""),
                              _days_until(ev), True)
        img.paste(row, (margin, y), row)
        y += card_h + gap

    stamp = datetime.now(CST).strftime("%m-%d %H:%M")
    draw.text(
        (margin, height - 46),
        f"数据：FEC 兽展日历 · 生成于 {stamp} · "
        f"{int((time.perf_counter() - started) * 1000)} ms",
        font=small,
        fill=(96, 100, 112),
    )

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def fortune_of(uid: str, day: str) -> tuple[int, str, tuple[int, int, int], str]:
    """Derive today's fixed fortune for one QQ number.

    Args:
        uid: QQ number.
        day: Date string (YYYYMMDD).

    Returns:
        Tuple of (jrrp score 0-100, level name, level color, tip).
    """
    digest = hashlib.sha256(f"{uid}|{day}".encode()).hexdigest()
    jrrp = int(digest[:8], 16) % 101
    if jrrp >= 90:
        level, color = "大吉", (255, 96, 96)
    elif jrrp >= 75:
        level, color = "中吉", (255, 150, 80)
    elif jrrp >= 60:
        level, color = "小吉", (255, 200, 90)
    elif jrrp >= 40:
        level, color = "平", (150, 190, 150)
    elif jrrp >= 20:
        level, color = "小凶", (120, 160, 210)
    else:
        level, color = "凶", (130, 130, 160)
    tip = FORTUNE_TIPS[int(digest[8:12], 16) % len(FORTUNE_TIPS)]
    return jrrp, level, color, tip


def render_fortune(uid: str, name: str) -> Path:
    """Draw today's jrrp card; same QQ and date always yields the same result."""
    started = time.perf_counter()
    path = _card_path(f"fortune_{uid}_{name}")
    if path.exists():
        return path

    day = _today()
    jrrp, level, color, tip = fortune_of(uid, day)

    width, height = 900, 740
    img = Image.new("RGB", (width, height), (24, 26, 32))
    draw = ImageDraw.Draw(img)
    big, huge, mid, small = _font(48), _font(140), _font(32), _font(24)

    draw.text((60, 46), "今日人品 · jrrp", font=big, fill=(200, 204, 214))
    draw.text((60, 112), f"{name[:12]} · {day}", font=small, fill=(140, 146, 160))
    draw.text((60, 166), str(jrrp), font=huge, fill=color)
    offset = 60 + int(len(str(jrrp)) * 92)
    draw.text((offset, 246), "分", font=mid, fill=color)
    draw.text((60, 340), level, font=big, fill=color)

    draw.rectangle((60, 430, 820, 462), outline=(70, 74, 84), width=2)
    draw.rectangle((60, 430, 60 + int(760 * jrrp / 100), 462), fill=color)
    draw.text((60, 496), f"今日提示：{tip}", font=mid, fill=(210, 214, 222))

    draw.text((60, height - 48),
              f"{day} · 仅供娱乐 · {int((time.perf_counter() - started) * 1000)} ms",
              font=small, fill=(90, 94, 106))

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def group_stats(group_id: str) -> tuple[list[tuple[str, int]], int, Counter]:
    """Collect message counts for one group over the last 7 days."""
    if not BOT_DB.exists():
        return [], 0, Counter()
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    con = sqlite3.connect(f"file:{BOT_DB}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(
            "SELECT sender_name, sender_id, created_at FROM platform_message_history "
            "WHERE user_id LIKE ? AND created_at >= ?",
            (f"%:{group_id}", since),
        ).fetchall()
    except sqlite3.Error:
        return [], 0, Counter()
    finally:
        con.close()

    counter: Counter = Counter()
    hours: Counter = Counter()
    names: dict[str, str] = {}
    total = 0
    for name, sid, created in rows:
        sid = str(sid or "")
        if sid == "1474388967":
            continue
        total += 1
        counter[sid] += 1
        names[sid] = str(name or sid)[:10]
        if created and len(created) >= 19:
            try:
                utc = datetime.strptime(str(created)[:19], "%Y-%m-%d %H:%M:%S")
                hours[str((utc + timedelta(hours=8)).hour).zfill(2)] += 1
            except ValueError:
                pass
    top = [(names.get(sid, sid), cnt) for sid, cnt in counter.most_common(8)]
    return top, total, hours


def render_stats(group_id: str) -> Path:
    """Draw a group activity card."""
    started = time.perf_counter()
    path = _card_path(f"stats_{group_id}")
    if path.exists():
        return path

    top, total, hours = group_stats(group_id)
    width, height = 900, 1020
    img = Image.new("RGB", (width, height), (24, 26, 32))
    draw = ImageDraw.Draw(img)
    big, mid, small = _font(46), _font(32), _font(24)

    draw.text((60, 50), "群活跃统计", font=big, fill=(240, 240, 245))
    draw.text((60, 112), f"近 7 天 · 共 {total} 条消息", font=small,
              fill=(140, 146, 160))

    y = 180
    draw.text((60, y), "发言榜", font=mid, fill=(200, 204, 214))
    y += 56
    peak = top[0][1] if top else 1
    for idx, (name, cnt) in enumerate(top, 1):
        draw.text((60, y), str(idx), font=small, fill=(110, 116, 128))
        draw.text((100, y - 4), name, font=mid, fill=(230, 230, 236))
        bar_w = int(400 * cnt / max(1, peak))
        draw.rectangle((360, y + 4, 360 + bar_w, y + 32), fill=(90, 130, 200))
        draw.text((790, y), str(cnt), font=mid, fill=(200, 204, 214))
        y += 56

    y += 40
    draw.text((60, y), "活跃时段", font=mid, fill=(200, 204, 214))
    base_y = y + 210
    peak_h = max(hours.values()) if hours else 1
    for h in range(24):
        cnt = hours.get(f"{h:02d}", 0)
        bar_h = int(180 * cnt / max(1, peak_h))
        x = 60 + h * 33
        draw.rectangle((x, base_y - bar_h, x + 22, base_y), fill=(255, 170, 90))
        if h % 4 == 0:
            draw.text((x - 4, base_y + 8), str(h), font=small, fill=(130, 136, 148))

    stamp = datetime.now(CST).strftime("%m-%d %H:%M")
    draw.text((60, height - 50),
              f"生成于 {stamp} · {int((time.perf_counter() - started) * 1000)} ms",
              font=small, fill=(90, 94, 106))

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


HELP_TEXT = """可用指令（所有人可用，需本群已启用）

▍展会（可自由组合：时间 + 地名 + 展/聚）
时间：上次·上一次 / 下次·下一次 / 近期·最近
类型：展·聚·兽聚·兽展·展会（写法随意，结果一样）
/下次展 /下次聚      -最近那一场（单场卡）
/江苏下次展 /江苏下次聚 -该城市/省份最近那一场（单场卡）
/近期展 /最近有什么    -近期 10 场 + 刚结束 2 场
/江苏近期展           -该城市/省份的展会列表（最多 6 场）
/江苏上次展           -该城市/省份办过的展会（灰色卡片）

▍其他
/运势-今日人品（也支持 /jrrp /jrys /人品）
/群统计-本群近 7 天活跃
/兽聚help-这张说明

▍管理员
/启用功能-开启本群功能
/关闭功能-关闭本群功能
/关心 <展会名> [HH:MM]-为特别关心，每天定时播报倒计时
/取消关心-关掉播报

也可以直接喊「阿灰 下次展」「小灰 运势」"""

# 一图流帮助卡（惰性导入，坏了降级纯文字）
def _render_help_card(title: str, lines: list[str], footer: str = ""):
    try:
        from card_render import render_text_card
        return render_text_card(title, lines, footer=footer)
    except Exception:  # noqa: BLE001
        return None


def group_enabled(group_id: str) -> bool:
    """Return whether the feature set is enabled for one group."""
    if not ENABLE_FILE.exists():
        return False
    try:
        data = json.loads(ENABLE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    groups = data.get("groups") or {}
    return bool(groups.get(str(group_id), False))


def set_group_enabled(group_id: str, enabled: bool) -> None:
    """Enable or disable the feature set for one group."""
    data = {"groups": {}}
    if ENABLE_FILE.exists():
        try:
            data = json.loads(ENABLE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {"groups": {}}
    data.setdefault("groups", {})[str(group_id)] = bool(enabled)
    ENABLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENABLE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def match_command(text: str, slash: bool = False) -> tuple[str, str]:
    """Match a message to a command key and an optional city.

    Event queries are parsed compositionally: the first time word decides
    过去 / 下次 / 近期, the first matching place name becomes the city, and
    any of 展/聚/兽聚/兽展/展会 simply marks it as an event query.  A message
    that carries no recognised command is returned as ``("", "")`` and left
    to other plugins instead of being swallowed.
    """
    body = re.sub(r"^\[[^\]]*\]\s*", "", text).strip()
    body = re.sub(rf"@{NAME_CALL}\s*", "", body)
    called = bool(re.search(NAME_CALL, body))
    body_clean = re.sub(r"^\s*[/／]\s*", "", body).strip()
    if not (slash or called):
        return "", ""

    # 1. Commands with fixed syntax (help / admin / fortune / stats).
    for key, pattern in COMMANDS:
        if pattern.search(body_clean):
            return key, ""

    # 2. Compositional event query. Pick the earliest place name so that
    #    "苏州南京" style input is deterministic.
    city = ""
    city_pos = len(body_clean)
    for name in CITY_NAMES + PROVINCES:
        pos = body_clean.find(name)
        if 0 <= pos < city_pos:
            city, city_pos = name, pos

    # Find the first time word by position, not by list order, so mixed
    # phrases like "上次…下次…" follow what the user wrote first.
    time_key = ""
    time_pos = len(body_clean)
    for key, words in EVENT_TIME_WORDS:
        for word in words:
            pos = body_clean.find(word)
            if 0 <= pos < time_pos:
                time_key, time_pos = key, pos

    has_kind = any(word in body_clean for word in EVENT_KIND_WORDS)
    has_marker = any(word in body_clean for word in EVENT_EXTRA_MARKERS)
    if not (has_kind or has_marker):
        # No recognised command. Do not stop the event: other plugins may
        # still have their own slash command with the same name.
        return "", ""

    if time_key == "past":
        return "city_past", city
    if time_key == "next":
        return ("city_next" if city else "next"), city
    if time_key == "recent":
        return ("city_recent" if city else "recent"), city
    return "city_list", city


class Main(star.Star):
    """Serve convention cards, fortune and group stats on demand."""

    def __init__(self, context: star.Context) -> None:
        self.context = context
        self._busy: set[str] = set()
        logger.info("[furcon] utilities ready")

    async def _send_card(self, event: AstrMessageEvent, path: Path, tag: str) -> None:
        """Send one image and stop further processing."""
        await event.send(event.image_result(str(path)))
        event.stop_event()
        logger.info(f"[furcon] sent {tag}: {path.name}")

    # 优先级由 astrbot_plugin_access 统一分档（指令档 > 内置主动回复），
    # 不再自报 maxsize 力大砖飞。此前必须抢在内置群聊上下文/主动回复
    # （maxsize-1 / maxsize-2）之前跑完，否则「阿灰下次兽聚」这类喊名字的
    # 消息会被主动回复抢先另起一次 LLM 请求，stop_event() 来不及，就会
    # 一句话 + 一张卡。本 handler 不匹配时立即 return，不会影响正常聊天。
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle(self, event: AstrMessageEvent) -> None:
        """Dispatch slash or name triggers to the matching card."""
        text = (event.message_str or "").strip()
        if not text:
            return

        raw = ""
        msg_obj = getattr(event, "message_obj", None)
        raw_msg = getattr(msg_obj, "raw_message", None)
        if isinstance(raw_msg, dict):
            raw = str(raw_msg.get("raw_message") or "")
        elif isinstance(raw_msg, str):
            raw = raw_msg
        slash = raw.lstrip().startswith(("/", "／")) or text.lstrip().startswith(
            ("/", "／")
        )

        key, city = match_command(text, slash=slash)
        if not key:
            # 非指令消息（绝大多数）：静默返回。此前这里每条消息都打一条
            # INFO 日志，纯属日志 I/O 浪费；匹配本身在 match_command 内部
            # 已有「非斜线且非喊名字 → 早退」的廉价路径，无需再省。
            return
        logger.info(f"[furcon] recv slash={slash} key={key} text={text[:24]}")

        group_id = str(event.get_group_id() or "")
        sender = str(event.get_sender_id() or "")

        # 管理员开关命令（私聊/群聊都能用）——兽聚功能开关，按群生效
        if key in ("enable_on", "enable_off"):
            if not _unified_is_admin(event):
                await event.send(event.plain_result("只有管理员能改开关"))
                event.stop_event()
                return
            if not group_id:
                await event.send(event.plain_result("请在要开启的群里发这条指令"))
                event.stop_event()
                return
            set_group_enabled(group_id, key == "enable_on")
            state = "已启用" if key == "enable_on" else "已关闭"
            await event.send(event.plain_result(f"本群功能{state}"))
            event.stop_event()
            return

        # 未启用的群：只回一句提示，不做任何事
        if group_id and not group_enabled(group_id):
            if key == "help":
                pass
            else:
                names = "/".join(_admin_qqs()) or "管理员"
                await event.send(
                    event.plain_result(
                        f"未启用请联系本机管理员 {names} owo"
                    )
                )
                event.stop_event()
                return

        umo = event.unified_msg_origin
        if umo in self._busy:
            return
        self._busy.add(umo)
        try:
            if key in ("watch", "watch_off"):
                if not _unified_is_admin(event):
                    await event.send(event.plain_result("这个只有管理员能设"))
                    event.stop_event()
                    return
                if key == "watch_off":
                    WATCH_FILE.write_text(json.dumps({"enabled": False},
                                                     ensure_ascii=False), encoding="utf-8")
                    await event.send(event.plain_result("已取消特别关心"))
                    event.stop_event()
                    return
                rest = re.sub(r"^\s*[/／]?\s*(关心|特别关心|关注)\s*", "", text).strip()
                if not rest:
                    await event.send(event.plain_result("用法：/关心 展会名 [HH:MM]"))
                    event.stop_event()
                    return
                bits = rest.split()
                name = bits[0]
                at = bits[1] if len(bits) > 1 else "09:00"
                WATCH_FILE.write_text(json.dumps({
                    "enabled": True,
                    "event": name,
                    "time": at,
                    "targets": [event.unified_msg_origin],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                await event.send(event.plain_result(f"已关心「{name}」，每天 {at} 播报"))
                event.stop_event()
                return

            if key == "help":
                card_path = _render_help_card(
                    "兽聚指令帮助",
                    HELP_TEXT.split("\n"),
                    footer="也可喊「阿灰 下次展」「小灰 运势」",
                )
                if card_path is not None:
                    await event.send(event.image_result(str(card_path)))
                else:
                    # 宁可报错也不出文字版
                    await event.send(event.plain_result("⚠ 帮助图卡生成失败，稍后再试"))
                event.stop_event()
                return

            if key == "fortune":
                card = render_fortune(event.get_sender_id() or "0",
                                      event.get_sender_name() or "群友")
                await self._send_card(event, card, key)
                return

            if key == "stats":
                group = str(event.get_group_id() or "")
                if not group:
                    await event.send(event.plain_result("这个只能在群里用"))
                    event.stop_event()
                    return
                await self._send_card(event, render_stats(group), key)
                return

            events = await fetch_events()
            if not events:
                await event.send(event.plain_result("展会数据暂时拿不到，等会儿再试"))
                event.stop_event()
                return

            if key == "next":
                nxt = pick_next(events)
                if not nxt:
                    await event.send(event.plain_result("近期没有查到展会"))
                    event.stop_event()
                    return
                following = [e for e in events if e["start_ts"] >= nxt["start_ts"]][1:]
                await self._send_card(event, render_next(nxt, following), key)
                return

            if key == "recent":
                await self._send_card(event, render_recent(events), key)
                return

            if key in ("city_recent", "city_list", "city_all"):
                if city:
                    await self._send_card(event, render_city(city, events, "next"),
                                          f"city_recent_{city}")
                else:
                    await self._send_card(event, render_recent(events), key)
                return

            if key == "city_past":
                target = city
                if not target:
                    await event.send(event.plain_result("说个城市名，比如 /上海上次展"))
                    event.stop_event()
                    return
                await self._send_card(event, render_city(target, events, "past"),
                                      f"city_past_{target}")
                return

            if key == "city_next" and not city:
                nxt = pick_next(events)
                if not nxt:
                    await event.send(event.plain_result("近期没有查到展会"))
                    event.stop_event()
                    return
                following = [e for e in events if e["start_ts"] >= nxt["start_ts"]][1:]
                await self._send_card(event, render_next(nxt, following), "next")
                return

            if key == "city_next":
                target = city
                tokens = expand_place(target)
                now = time.time()
                hits = [
                    e
                    for e in events
                    if e["end_ts"] >= now
                    and any(
                        t in (str(e.get("city", "")) + str(e.get("region", "")))
                        for t in tokens
                    )
                ]
                if not hits:
                    await event.send(
                        event.plain_result(f"{target}近期没有查到展会")
                    )
                    event.stop_event()
                    return
                # "之后还有"也只看该城市，避免混进外地展
                following = [e for e in hits[1:3]]
                card = render_next(hits[0], following, title=f"{target} · 下一次兽聚")
                await self._send_card(event, card, f"city_next_{target}")
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[furcon] handler failed: {exc}")
        finally:
            self._busy.discard(umo)

    @filter.on_astrbot_loaded()
    async def start_watch(self) -> None:
        """Start the daily countdown broadcaster."""
        asyncio.create_task(self._watch_loop())

    async def _watch_loop(self) -> None:
        """Every minute, check whether the watched countdown should be sent."""
        while True:
            await asyncio.sleep(60)
            try:
                await self._maybe_broadcast()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[furcon] watch error: {exc}")

    async def _maybe_broadcast(self) -> None:
        """Send the watched countdown once per day at the configured time."""
        if not WATCH_FILE.exists():
            return
        try:
            cfg = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not cfg.get("enabled"):
            return
        now = datetime.now(CST)
        if now.strftime("%H:%M") != str(cfg.get("time", "09:00")):
            return
        today = now.strftime("%Y%m%d")
        if cfg.get("last_sent") == today:
            return

        name = str(cfg.get("event", "")).strip()
        targets = [t for t in cfg.get("targets", []) if isinstance(t, str)]
        if not name or not targets:
            return

        events = await fetch_events()
        target = next((e for e in events if name in e["name"]), None)
        if not target:
            return
        card = render_next(target, [])
        for umo in targets:
            try:
                from astrbot.api.message_components import Image as ImageComp
                from astrbot.core.message.message_event_result import MessageChain

                chain = MessageChain()
                chain.chain.append(ImageComp.fromFileSystem(str(card)))
                await self.context.send_message(umo, chain)
                logger.info(f"[furcon] broadcast to {umo}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[furcon] broadcast failed for {umo}: {exc}")

        cfg["last_sent"] = today
        WATCH_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                              encoding="utf-8")

"""
AI매출업 가맹점 분석 시스템 V2
- 네이버 플레이스 (영수증/블로그 리뷰) Playwright 수집
- 네이버 블로그 수집
- 인스타그램 수집
- 유튜브 수집
- 일별/월별 집계
- Claude API 자체분석 리포트
- PDF 다운로드
"""

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen
from uuid import uuid4

import anthropic
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr

# 엑셀 생성기
try:
    from excel_export import make_raw_excel, make_daily_excel, make_monthly_excel
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 경로 설정 ──
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_DIR   = DATA_DIR / "db"
OUT_DIR  = DATA_DIR / "outputs"
LOG_DIR  = DATA_DIR / "logs"
for d in [DATA_DIR, DB_DIR, OUT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI매출업 리포트 API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer()

# ── 환경변수 ──
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "zetarise@gmail.com").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "4858").strip()
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ai-report-secret-2026").strip()
JWT_ALGORITHM  = "HS256"
JWT_EXPIRE_MINUTES = 720
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CRAWL_TIMEOUT      = int(os.getenv("CRAWL_TIMEOUT_SECONDS", "480"))

# ── 인메모리 저장소 (SQLite 없이 운영, 재시작시 초기화) ──
REPORTS: dict[str, dict] = {}
CRAWL_JOBS: dict[str, dict] = {}

# ── 가맹점 목록 (고정 데이터 → 추후 DB 연동) ──
MERCHANTS = [
    {
        "id": "bae_po_cha",
        "name": "배포차",
        "region": "서울 강남구",
        "address": "서울 강남구 도산대로1길 16 지상1, 2층",
        "naver_place_url": "https://naver.me/xv6tlDW3",
        "blog_keywords": ["신사역 배포차", "신사동 배포차"],
        "instagram_hashtags": ["배포차"],
        "instagram_channel": "bae_po_cha",
        "youtube_keywords": ["신사역 배포차", "신사동 배포차"],
    },
    {
        "id": "soyo_ilsan",
        "name": "소요",
        "region": "경기 고양시",
        "address": "경기 고양시 일산동구 월드고양로 21 상가동 1동 1층 309호, 310호",
        "naver_place_url": "https://naver.me/F0AHoPtm",
        "blog_keywords": ["일산 소요", "고양시 소요", "일산동구 소요", "장항동 소요"],
        "instagram_hashtags": ["일산소요", "고양시소요"],
        "instagram_channel": "soyo_izakaya",
        "youtube_keywords": ["일산 소요", "고양시 소요"],
    },
    {
        "id": "soon_jamae",
        "name": "순자매감자탕",
        "region": "경기 화성시",
        "address": "경기 화성시 동탄구 동탄기흥로257번가길 24-11 1층",
        "naver_place_url": "https://naver.me/GNRzS59C",
        "blog_keywords": ["순자매감자탕"],
        "instagram_hashtags": ["순자매감자탕"],
        "instagram_channel": "",
        "youtube_keywords": ["순자매감자탕"],
    },
    {
        "id": "yeontan_kim",
        "name": "연탄김평선",
        "region": "서울 강남구",
        "address": "서울 강남구 선릉로90길 64 지상1층",
        "naver_place_url": "https://naver.me/xNLZbjfI",
        "blog_keywords": ["연탄김평선"],
        "instagram_hashtags": ["연탄김평선"],
        "instagram_channel": "yeon_tan_pyeongseon_kim",
        "youtube_keywords": ["연탄김평선"],
    },
    {
        "id": "liveball_yeoksam",
        "name": "라이브볼",
        "region": "서울 강남구",
        "address": "서울 강남구 테헤란로 147 지하 1층 3호",
        "naver_place_url": "https://naver.me/5bVsye2y",
        "blog_keywords": ["라이브볼 역삼점", "라이브볼 역삼역", "라이브볼 역삼동"],
        "instagram_hashtags": ["라이브볼역삼점", "라이브볼역삼역"],
        "instagram_channel": "",
        "youtube_keywords": ["라이브볼 역삼점", "라이브볼 역삼역"],
    },
]


# ══════════════════════════════════════════
# 인증
# ══════════════════════════════════════════
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class CrawlJobCreate(BaseModel):
    merchant_id: str
    period: str = "최근 6개월"
    start_date: Optional[str] = None
    end_date: Optional[str] = None

def create_token(email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": email, "exp": expire}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="인증 실패")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="토큰 오류")


# ══════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════
def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat()

def fetch_text(url: str, timeout: int = 15) -> str:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def strip_html(v: str) -> str:
    return unescape(re.sub(r"<.*?>", "", v or "")).strip()

def parse_naver_date(text: str) -> Optional[str]:
    """네이버 날짜 텍스트 → YYYY-MM-DD"""
    if not text:
        return None
    s = strip_html(text).replace(" ", "")
    s = s.replace("년", ".").replace("월", ".").replace("일", "")
    s = re.sub(r"[월화수목금토일]$", "", s).strip(".")

    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"(2[0-9])[.](\d{1,2})[.](\d{1,2})", s)
    if m:
        y, mo, d = 2000+int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    # "3일 전"
    m = re.match(r"(\d+)일전", s)
    if m:
        dt = datetime.now() - timedelta(days=int(m.group(1)))
        return dt.strftime("%Y-%m-%d")
    m = re.match(r"(\d+)주전", s)
    if m:
        dt = datetime.now() - timedelta(weeks=int(m.group(1)))
        return dt.strftime("%Y-%m-%d")
    m = re.match(r"(\d+)개월전", s)
    if m:
        dt = datetime.now() - timedelta(days=int(m.group(1))*30)
        return dt.strftime("%Y-%m-%d")
    return None

def date_to_month_key(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    m = re.search(r"(20\d{2})-(\d{2})", d)
    return f"{m.group(1)}-{m.group(2)}" if m else None

def build_month_keys(month_count: int) -> list[str]:
    today = datetime.now()
    keys = []
    for offset in range(month_count - 1, -1, -1):
        y, mo = today.year, today.month - offset
        while mo <= 0:
            mo += 12; y -= 1
        keys.append(f"{y}-{mo:02d}")
    return keys

def get_month_count(period: str) -> int:
    mapping = {"최근 1개월": 1, "최근 3개월": 3, "최근 6개월": 6, "최근 1년": 12}
    return mapping.get(period, 6)

def month_key_label(k: str) -> str:
    y, m = k.split("-")
    return f"{y}년 {int(m)}월"

def resolve_final_url(url: str) -> str:
    try:
        opener = build_opener(HTTPRedirectHandler)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return opener.open(req, timeout=12).geturl()
    except Exception:
        return url

def extract_place_id(place_url: str) -> Optional[str]:
    final = resolve_final_url(place_url)
    for pat in [r"/place/(\d+)", r"/restaurant/(\d+)", r"placeId=(\d+)", r"id=(\d+)"]:
        m = re.search(pat, final)
        if m:
            return m.group(1)
    return None


# ══════════════════════════════════════════
# 네이버 플레이스 수집 (Playwright)
# ══════════════════════════════════════════
def crawl_naver_place(place_id: str) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "error", "reason": "Playwright 미설치", "receipt_items": [], "blog_items": [], "receipt_count": None, "blog_count": None}

    receipt_items, blog_items = [], []
    receipt_count = blog_count = None

    def get_blob(page) -> str:
        parts = []
        try: parts.append(page.inner_text("body", timeout=8000))
        except: pass
        for frame in page.frames:
            try:
                if frame == page.main_frame: continue
                t = frame.locator("body").inner_text(timeout=3000)
                if t and len(t) > 100: parts.append(t)
            except: continue
        return "\n".join(parts)

    def parse_dates(blob: str, source: str) -> list[dict]:
        items = []
        pats = [
            r"20\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}",
            r"2[0-9]\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}",
            r"(?<!\d)(?:1[0-2]|[1-9])\s*\.\s*(?:3[01]|[12][0-9]|[1-9])\s*\.?(?:[월화수목금토일])?",
        ]
        for pat in pats:
            for m in re.finditer(pat, blob):
                d = parse_naver_date(m.group(0))
                mk = date_to_month_key(d)
                if d and mk:
                    items.append({"date": d, "month_key": mk, "source": source})
        return items

    def scroll_and_collect(page, source: str, target: Optional[int], max_rounds: int = 120) -> list[dict]:
        best = []
        stable = 0
        last_len = 0
        for _ in range(max_rounds):
            # 더보기 버튼 클릭
            for sel in ["button:has-text('펼쳐서 더보기')", "button:has-text('더보기')", "[role=button]:has-text('더보기')"]:
                try:
                    loc = page.locator(sel)
                    for i in range(min(loc.count(), 20)):
                        try:
                            it = loc.nth(i)
                            if it.is_visible():
                                it.scroll_into_view_if_needed(timeout=1000)
                                it.click(timeout=1200, force=True)
                                page.wait_for_timeout(400)
                        except: pass
                except: pass

            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(600)

            blob = get_blob(page)
            items = parse_dates(blob, source)

            if len(items) > len(best):
                best = items

            if target and len(items) >= int(target * 0.9):
                return best[:target] if target else best

            if len(blob) <= last_len + 100 and len(items) <= len(best):
                stable += 1
            else:
                stable = 0
                last_len = max(last_len, len(blob))

            if stable >= 18:
                break

        return best[:target] if target else best

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            ctx = browser.new_context(
                viewport={"width": 430, "height": 1500},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
            page = ctx.new_page()

            # 영수증 리뷰 수집
            for url in [
                f"https://m.place.naver.com/restaurant/{place_id}/review/visitor?reviewSort=recent",
                f"https://m.place.naver.com/place/{place_id}/review/visitor?reviewSort=recent",
            ]:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2500)
                    blob = get_blob(page)
                    # 총 건수 파싱
                    for pat in [r"방문자\s*리뷰\s*([0-9,]+)", r"영수증\s*리뷰\s*([0-9,]+)"]:
                        mm = re.search(pat, blob)
                        if mm:
                            receipt_count = int(mm.group(1).replace(",", ""))
                            break
                    receipt_items = scroll_and_collect(page, "receipt", receipt_count, max_rounds=100)
                    if receipt_items:
                        break
                except: continue

            # 블로그 리뷰 수집
            for url in [
                f"https://m.place.naver.com/restaurant/{place_id}/review/ugc",
                f"https://m.place.naver.com/place/{place_id}/review/ugc",
            ]:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2500)
                    blob = get_blob(page)
                    for pat in [r"블로그\s*리뷰\s*([0-9,]+)"]:
                        mm = re.search(pat, blob)
                        if mm:
                            blog_count = int(mm.group(1).replace(",", ""))
                            break
                    blog_items = scroll_and_collect(page, "blog", blog_count, max_rounds=60)
                    if blog_items:
                        break
                except: continue

            ctx.close()
            browser.close()

        return {
            "status": "ok",
            "receipt_count": receipt_count or len(receipt_items),
            "blog_count": blog_count or len(blog_items),
            "receipt_items": receipt_items,
            "blog_items": blog_items,
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)[:200], "receipt_items": [], "blog_items": [], "receipt_count": None, "blog_count": None}


# ══════════════════════════════════════════
# 네이버 블로그 수집
# ══════════════════════════════════════════
def crawl_naver_blog(keywords: list[str], max_results: int = 50) -> dict:
    posts = {}
    undated = 0

    for keyword in keywords:
        try:
            url = f"https://search.naver.com/search.naver?where=blog&query={quote_plus(keyword)}"
            html = fetch_text(url)

            for link_m in re.finditer(r'href="(https://blog\.naver\.com/[^"]+)"', html):
                link = link_m.group(1)
                if link in posts:
                    continue

                chunk_s = max(0, link_m.start() - 2500)
                chunk_e = min(len(html), link_m.end() + 3500)
                chunk = html[chunk_s:chunk_e]

                title = ""
                for pat in [r'class="[^"]*title_link[^"]*"[^>]*>(.*?)</a>', r'class="[^"]*api_txt_lines[^"]*"[^>]*>(.*?)</span>']:
                    mm = re.search(pat, chunk, re.S)
                    if mm:
                        title = strip_html(mm.group(1))
                        break

                date_raw = ""
                for pat in [r"(\d{4}\.\d{1,2}\.\d{1,2}\.?)", r"(2[0-9]\.\d{1,2}\.\d{1,2})"]:
                    mm = re.search(pat, chunk)
                    if mm:
                        date_raw = mm.group(1)
                        break

                d = parse_naver_date(date_raw)
                mk = date_to_month_key(d)
                if not mk:
                    undated += 1

                posts[link] = {
                    "url": link,
                    "title": title or f"{keyword} 블로그",
                    "date": d,
                    "month_key": mk,
                    "keyword": keyword,
                }

                if len(posts) >= max_results:
                    break

        except Exception as e:
            logger.warning(f"[Blog] 키워드 수집 실패 '{keyword}': {e}")
            continue

    items = list(posts.values())
    return {
        "status": "ok",
        "count": len(items),
        "undated_count": undated,
        "items": items,
    }


# ══════════════════════════════════════════
# 유튜브 수집 (HTML 비공식)
# ══════════════════════════════════════════
def parse_youtube_published(label: str) -> Optional[str]:
    if not label:
        return None
    text = label.strip().lower()
    now = datetime.now()
    m = re.search(r"(\d+)\s*(초|분|시간|일|주|개월|달|년)", text)
    if m:
        v, u = int(m.group(1)), m.group(2)
        if u in ("일",): dt = now - timedelta(days=v)
        elif u in ("주",): dt = now - timedelta(weeks=v)
        elif u in ("개월", "달"):
            mo = now.month - v; yr = now.year
            while mo <= 0: mo += 12; yr -= 1
            dt = now.replace(year=yr, month=mo)
        elif u in ("년",): dt = now.replace(year=now.year - v)
        else: dt = now
        return f"{dt.year}-{dt.month:02d}"
    m = re.search(r"(\d+)\s*(day|week|month|year)s?\s+ago", text)
    if m:
        v, u = int(m.group(1)), m.group(2)
        if u == "day": dt = now - timedelta(days=v)
        elif u == "week": dt = now - timedelta(weeks=v)
        elif u == "month":
            mo = now.month - v; yr = now.year
            while mo <= 0: mo += 12; yr -= 1
            dt = now.replace(year=yr, month=mo)
        elif u == "year": dt = now.replace(year=now.year - v)
        else: dt = now
        return f"{dt.year}-{dt.month:02d}"
    return None

def crawl_youtube(merchant: dict, max_results: int = 12) -> dict:
    keywords = merchant.get("youtube_keywords", []) or [merchant["name"]]
    name_norm = re.sub(r"[^0-9a-zA-Z가-힣]", "", merchant["name"]).lower()
    videos = {}

    try:
        for keyword in keywords:
            url = f"https://www.youtube.com/results?search_query={quote_plus(keyword)}"
            html = fetch_text(url, timeout=20)

            for vm in re.finditer(r'"videoId":"([^"]{8,20})"', html):
                vid = vm.group(1)
                if vid in videos:
                    continue

                s = max(0, vm.start() - 3500)
                e = min(len(html), vm.end() + 6500)
                chunk = html[s:e]

                def ex(pats):
                    for p in pats:
                        mm = re.search(p, chunk, re.S)
                        if mm:
                            return strip_html(mm.group(1))
                    return ""

                title = ex([r'"title":\{"runs":\[\{"text":"(.*?)"', r'"title":\{"simpleText":"(.*?)"'])
                channel = ex([r'"ownerText":\{"runs":\[\{"text":"(.*?)"', r'"shortBylineText":\{"runs":\[\{"text":"(.*?)"'])
                view_text = ex([r'"viewCountText":\{"simpleText":"(.*?)"', r'"shortViewCountText":\{"simpleText":"(.*?)"'])
                published_label = ex([r'"publishedTimeText":\{"simpleText":"(.*?)"'])

                if not title:
                    continue

                title_norm = re.sub(r"[^0-9a-zA-Z가-힣]", "", title).lower()
                channel_norm = re.sub(r"[^0-9a-zA-Z가-힣]", "", channel).lower()
                combined = title_norm + channel_norm
                kw_norms = [re.sub(r"[^0-9a-zA-Z가-힣]", "", k).lower() for k in keywords]

                relevant = name_norm in combined or any(k in combined for k in kw_norms if k)
                if not relevant:
                    continue

                # 조회수 파싱
                views = 0
                vm2 = re.search(r"([\d.]+)(만|천|K|M)?", view_text.replace(",", ""), re.I)
                if vm2:
                    num = float(vm2.group(1))
                    u = (vm2.group(2) or "").lower()
                    if u == "만": num *= 10000
                    elif u == "천": num *= 1000
                    elif u == "k": num *= 1000
                    elif u == "m": num *= 1000000
                    views = int(num)

                videos[vid] = {
                    "video_id": vid,
                    "title": title,
                    "channel": channel or "YouTube",
                    "views": views,
                    "published_label": published_label,
                    "month_key": parse_youtube_published(published_label),
                    "url": f"https://www.youtube.com/watch?v={vid}",
                }

                if len(videos) >= max_results:
                    break

        monthly_counts: dict[str, int] = {}
        undated = 0
        for v in videos.values():
            k = v.get("month_key")
            if k:
                monthly_counts[k] = monthly_counts.get(k, 0) + 1
            else:
                undated += 1

        top = sorted(videos.values(), key=lambda x: x["views"], reverse=True)[:5]
        total_views = sum(v["views"] for v in videos.values())

        return {
            "status": "ok",
            "youtube_count": len(videos),
            "youtube_total_views": total_views,
            "monthly_counts": monthly_counts,
            "undated_count": undated,
            "top_videos": [{"title": v["title"], "channel": v["channel"], "views": v["views"], "published": v.get("published_label", "")} for v in top],
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)[:200], "youtube_count": 0, "youtube_total_views": 0, "monthly_counts": {}, "undated_count": 0, "top_videos": []}


# ══════════════════════════════════════════
# 리포트 생성 (실제 수집 데이터 통합)
# ══════════════════════════════════════════
def build_report(merchant: dict, period: str = "최근 6개월",
                 start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    month_count = get_month_count(period)
    month_keys = build_month_keys(month_count)

    # ── 수집 실행 ──
    logger.info(f"[Collect] {merchant['name']} 수집 시작")

    # 1) 네이버 블로그
    blog_result = crawl_naver_blog(merchant.get("blog_keywords", []))
    logger.info(f"[Collect] 블로그: {blog_result['count']}건")

    # 2) 네이버 플레이스
    place_id = extract_place_id(merchant.get("naver_place_url", ""))
    place_result = {"status": "skip", "receipt_count": 0, "blog_count": 0, "receipt_items": [], "blog_items": []}
    if place_id:
        place_result = crawl_naver_place(place_id)
        logger.info(f"[Collect] 플레이스 영수증:{place_result.get('receipt_count')} 블로그:{place_result.get('blog_count')}")

    # 3) 유튜브
    yt_result = crawl_youtube(merchant)
    logger.info(f"[Collect] 유튜브: {yt_result['youtube_count']}건, 조회수:{yt_result['youtube_total_views']}")

    # ── 월별 집계 (작성일 기준) ──
    def dist_by_month(items: list[dict]) -> dict[str, int]:
        counts = defaultdict(int)
        for item in items:
            mk = item.get("month_key")
            if mk and mk in month_keys:
                counts[mk] += 1
        return dict(counts)

    blog_monthly    = dist_by_month(blog_result.get("items", []))
    receipt_monthly = dist_by_month(place_result.get("receipt_items", []))
    place_blog_monthly = dist_by_month(place_result.get("blog_items", []))
    yt_monthly      = dict(yt_result.get("monthly_counts", {}))

    # undated 유튜브는 최신월 배정
    yt_undated = int(yt_result.get("undated_count", 0))
    if yt_undated and month_keys:
        yt_monthly[month_keys[-1]] = yt_monthly.get(month_keys[-1], 0) + yt_undated

    # undated 블로그는 최신월 배정
    blog_total  = blog_result.get("count", 0) or 0
    blog_dated  = sum(blog_monthly.get(k, 0) for k in month_keys)
    blog_undated = max(0, blog_total - blog_dated)
    if blog_undated and month_keys:
        blog_monthly[month_keys[-1]] = blog_monthly.get(month_keys[-1], 0) + blog_undated

    # 월별 행 생성
    monthly = []
    for mk in month_keys:
        blog_c    = blog_monthly.get(mk, 0)
        insta_c   = 0  # 인스타 추후 구현
        receipt_c = receipt_monthly.get(mk, 0)
        pblog_c   = place_blog_monthly.get(mk, 0)
        yt_c      = yt_monthly.get(mk, 0)
        total     = blog_c + insta_c + receipt_c + pblog_c + yt_c

        monthly.append({
            "month":               month_key_label(mk),
            "month_key":           mk,
            "blog_count":          blog_c,
            "instagram_count":     insta_c,
            "place_receipt_count": receipt_c,
            "place_blog_count":    pblog_c,
            "youtube_count":       yt_c,
            "total_count":         total,
        })

    # 일별 집계
    daily_raw: dict[str, dict] = defaultdict(lambda: {"blog": 0, "instagram": 0, "place_receipt": 0, "place_blog": 0, "youtube": 0})
    for item in blog_result.get("items", []):
        if item.get("date"):
            daily_raw[item["date"]]["blog"] += 1
    for item in place_result.get("receipt_items", []):
        if item.get("date"):
            daily_raw[item["date"]]["place_receipt"] += 1
    for item in place_result.get("blog_items", []):
        if item.get("date"):
            daily_raw[item["date"]]["place_blog"] += 1

    cumulative = 0
    daily = []
    for d in sorted(daily_raw.keys()):
        row = daily_raw[d]
        total = sum(row.values())
        cumulative += total
        daily.append({
            "date": d,
            "blog": row["blog"],
            "instagram": row["instagram"],
            "place_receipt": row["place_receipt"],
            "place_blog": row["place_blog"],
            "youtube": row["youtube"],
            "total": total,
            "cumulative": cumulative,
        })

    # 요약
    summary = {
        "naver_blog_count":    blog_total,
        "instagram_count":     0,
        "place_receipt_count": place_result.get("receipt_count") or sum(r["place_receipt_count"] for r in monthly),
        "place_blog_count":    place_result.get("blog_count") or sum(r["place_blog_count"] for r in monthly),
        "youtube_count":       yt_result["youtube_count"],
        "youtube_total_views": yt_result["youtube_total_views"],
    }
    summary["total_mentions"] = (
        summary["naver_blog_count"] + summary["instagram_count"] +
        summary["place_receipt_count"] + summary["place_blog_count"] +
        summary["youtube_count"]
    )

    # 채널 비중
    channel_share = [
        {"name": "네이버 블로그", "value": summary["naver_blog_count"]},
        {"name": "인스타그램",    "value": summary["instagram_count"]},
        {"name": "영수증 리뷰",   "value": summary["place_receipt_count"]},
        {"name": "플레이스 블로그 리뷰", "value": summary["place_blog_count"]},
        {"name": "유튜브",        "value": summary["youtube_count"]},
    ]

    # ChatGPT용 JSON payload
    chatgpt_payload = {
        "merchant_id":   merchant["id"],
        "merchant_name": merchant["name"],
        "period":        period,
        "platform_summary": summary,
        "monthly_stats": monthly,
        "daily_stats":   daily,
        "top_videos":    yt_result.get("top_videos", []),
        "auto_prompt":   (
            f"'{merchant['name']}' {period} 멀티플랫폼 데이터입니다. "
            "①플랫폼별 노출 추이 ②방문자 리뷰 키워드 감성 분석 ③월별 성장률 ④개선 제안 3가지 ⑤다음달 예측을 포함한 월간 리포트를 작성해주세요."
        ),
    }

    report = {
        "merchant_id":     merchant["id"],
        "merchant_name":   merchant["name"],
        "region":          merchant.get("region", ""),
        "generated_at":    now_text(),
        "period":          period,
        "period_label":    period,
        "summary":         summary,
        "monthly_summary": monthly,
        "daily_summary":   daily,
        "channel_share":   channel_share,
        "top_videos":      yt_result.get("top_videos", []),
        "chatgpt_payload": chatgpt_payload,
        # 엑셀 원시 데이터용
        "_raw_blog_items":       blog_result.get("items", []),
        "_raw_place_items":      place_result.get("receipt_items", []),
        "_raw_place_blog_items": place_result.get("blog_items", []),
        "source_status": {
            "naver_blog":   {"status": blog_result["status"], "count": blog_result["count"]},
            "naver_place":  {"status": place_result["status"], "place_id": place_id},
            "youtube":      {"status": yt_result["status"], "count": yt_result["youtube_count"]},
            "instagram":    {"status": "pending", "note": "다음 버전 구현"},
        },
    }
    return report


# ══════════════════════════════════════════
# Claude API 자체분석 리포트
# ══════════════════════════════════════════
def generate_claude_report(report: dict) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"status": "error", "reason": "ANTHROPIC_API_KEY 미설정", "html": "", "analysis": {}}

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        merchant_name = report["merchant_name"]
        period = report["period"]
        summary = report["summary"]
        monthly = report["monthly_summary"]

        prompt = f"""당신은 외식업 마케팅 전문 분석가입니다.
아래 데이터를 기반으로 '{merchant_name}'의 {period} 온라인 마케팅 분석 리포트를 작성해주세요.

## 데이터
- 네이버 블로그 게시 수: {summary.get('naver_blog_count', 0)}건
- 인스타그램 게시 수: {summary.get('instagram_count', 0)}건
- 플레이스 영수증 리뷰: {summary.get('place_receipt_count', 0)}건
- 플레이스 블로그 리뷰: {summary.get('place_blog_count', 0)}건
- 유튜브 영상 수: {summary.get('youtube_count', 0)}건 (총 조회수: {summary.get('youtube_total_views', 0):,})
- 총 언급 수: {summary.get('total_mentions', 0)}건

## 월별 추이
{json.dumps(monthly, ensure_ascii=False, indent=2)}

## 작성 요구사항
다음 6개 섹션으로 구성하여 **한국어**로 작성하세요. JSON 형식으로만 응답하세요.

{{
  "overall_score": 75,
  "overall_comment": "전반적인 한줄 평가 (50자 이내)",
  "sections": [
    {{
      "title": "플랫폼별 노출 현황",
      "content": "상세 분석 내용 (200자 이상)",
      "highlight": "핵심 인사이트 1문장"
    }},
    {{
      "title": "월별 성장 트렌드",
      "content": "월별 증감 분석",
      "highlight": "핵심 인사이트"
    }},
    {{
      "title": "방문자 리뷰 분석",
      "content": "영수증/블로그 리뷰 품질 분석",
      "highlight": "핵심 인사이트"
    }},
    {{
      "title": "채널별 효과 분석",
      "content": "각 채널의 기여도와 효율성",
      "highlight": "핵심 인사이트"
    }},
    {{
      "title": "개선 제안 사항",
      "content": "구체적인 액션 아이템 3가지",
      "highlight": "최우선 실행 과제"
    }},
    {{
      "title": "다음 달 예측 및 목표",
      "content": "예측 근거와 달성 목표",
      "highlight": "목표 수치"
    }}
  ],
  "action_items": ["즉시 실행 과제 1", "즉시 실행 과제 2", "즉시 실행 과제 3"],
  "risk_flags": ["주의 사항 1", "주의 사항 2"]
}}"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        analysis = json.loads(raw)

        # HTML 리포트 렌더링
        html = _render_claude_report_html(merchant_name, period, summary, analysis, report)

        return {"status": "ok", "html": html, "analysis": analysis}

    except Exception as e:
        logger.error(f"[Claude] 분석 실패: {e}")
        return {"status": "error", "reason": str(e)[:300], "html": "", "analysis": {}}


def _render_claude_report_html(merchant_name: str, period: str, summary: dict, analysis: dict, report: dict) -> str:
    score = analysis.get("overall_score", 0)
    score_color = "#00d4ff" if score >= 70 else "#ffd166" if score >= 50 else "#f87171"
    sections = analysis.get("sections", [])
    action_items = analysis.get("action_items", [])
    risk_flags = analysis.get("risk_flags", [])
    overall_comment = analysis.get("overall_comment", "")

    monthly = report.get("monthly_summary", [])
    max_total = max((r["total_count"] for r in monthly), default=1) or 1

    monthly_bars = ""
    for row in monthly:
        pct = int(row["total_count"] / max_total * 100)
        monthly_bars += f"""
        <div class="bar-row">
          <div class="bar-label">{row['month']}</div>
          <div class="bar-wrap"><div class="bar-fill" style="width:{pct}%"></div></div>
          <div class="bar-val">{row['total_count']}건</div>
        </div>"""

    sections_html = ""
    for i, sec in enumerate(sections):
        color = ["#00d4ff", "#00e5a0", "#ffd166", "#ff6b35", "#7c3aed", "#f472b6"][i % 6]
        sections_html += f"""
        <div class="section-card" style="border-left: 3px solid {color}">
          <div class="section-title" style="color:{color}">{sec.get('title', '')}</div>
          <div class="section-content">{sec.get('content', '')}</div>
          <div class="highlight-box">💡 {sec.get('highlight', '')}</div>
        </div>"""

    action_html = "".join(f'<div class="action-item">✅ {a}</div>' for a in action_items)
    risk_html   = "".join(f'<div class="risk-item">⚠️ {r}</div>' for r in risk_flags)

    channel_rows = ""
    for ch in report.get("channel_share", []):
        channel_rows += f"<tr><td>{ch['name']}</td><td class='num'>{ch['value']:,}건</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{merchant_name} 분석 리포트 - AI매출업</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Noto Sans KR',sans-serif; background:#0a0c10; color:#e8eaf0; font-size:14px; line-height:1.7; }}
  .page {{ max-width:900px; margin:0 auto; padding:40px 24px; }}
  .header {{ text-align:center; padding:48px 0 36px; border-bottom:1px solid #1e2330; margin-bottom:40px; }}
  .brand {{ font-size:11px; letter-spacing:3px; color:#00d4ff; margin-bottom:12px; }}
  .merchant-name {{ font-size:32px; font-weight:900; letter-spacing:-1px; margin-bottom:8px; }}
  .period-badge {{ display:inline-block; background:#1e2330; border:1px solid #00d4ff; color:#00d4ff; padding:4px 16px; border-radius:20px; font-size:12px; }}
  .generated {{ font-size:11px; color:#5a6278; margin-top:12px; }}
  .score-block {{ display:flex; align-items:center; justify-content:center; gap:32px; margin:40px 0; }}
  .score-circle {{ width:120px; height:120px; border-radius:50%; border:4px solid {score_color}; display:flex; flex-direction:column; align-items:center; justify-content:center; }}
  .score-num {{ font-size:42px; font-weight:900; color:{score_color}; line-height:1; }}
  .score-label {{ font-size:10px; color:#5a6278; letter-spacing:1px; }}
  .score-comment {{ max-width:400px; font-size:15px; color:#c9d1e0; line-height:1.6; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:32px 0; }}
  .kpi-card {{ background:#111318; border:1px solid #1e2330; border-radius:8px; padding:20px; text-align:center; }}
  .kpi-label {{ font-size:11px; color:#5a6278; margin-bottom:8px; }}
  .kpi-val {{ font-size:28px; font-weight:700; color:#00d4ff; }}
  .kpi-unit {{ font-size:12px; color:#5a6278; }}
  .section-title-main {{ font-size:13px; letter-spacing:2px; color:#5a6278; margin:40px 0 16px; padding-bottom:8px; border-bottom:1px solid #1e2330; }}
  .section-card {{ background:#111318; border:1px solid #1e2330; border-left:3px solid #00d4ff; border-radius:8px; padding:20px; margin-bottom:12px; }}
  .section-title {{ font-size:14px; font-weight:700; margin-bottom:10px; }}
  .section-content {{ font-size:13px; color:#9ba3b4; line-height:1.8; margin-bottom:12px; }}
  .highlight-box {{ background:#0a0c10; border:1px solid #1e2330; border-radius:6px; padding:10px 14px; font-size:12px; color:#ffd166; }}
  .bar-row {{ display:flex; align-items:center; gap:12px; margin-bottom:8px; }}
  .bar-label {{ width:100px; font-size:12px; color:#5a6278; text-align:right; flex-shrink:0; }}
  .bar-wrap {{ flex:1; background:#1e2330; border-radius:4px; height:18px; overflow:hidden; }}
  .bar-fill {{ height:100%; background:linear-gradient(90deg,#00d4ff,#7c3aed); border-radius:4px; transition:width 0.5s; }}
  .bar-val {{ width:60px; font-size:12px; color:#00d4ff; text-align:right; }}
  .channel-table {{ width:100%; border-collapse:collapse; background:#111318; border-radius:8px; overflow:hidden; border:1px solid #1e2330; }}
  .channel-table th {{ background:#181c24; color:#5a6278; font-size:10px; letter-spacing:1px; padding:10px 16px; text-align:left; }}
  .channel-table td {{ padding:10px 16px; border-bottom:1px solid #1e2330; font-size:13px; }}
  .channel-table td.num {{ color:#00d4ff; font-weight:700; text-align:right; }}
  .channel-table tr:last-child td {{ border-bottom:none; }}
  .action-item {{ background:#111318; border:1px solid #00e5a0; border-radius:6px; padding:10px 16px; margin-bottom:8px; font-size:13px; color:#00e5a0; }}
  .risk-item {{ background:#111318; border:1px solid #ffd166; border-radius:6px; padding:10px 16px; margin-bottom:8px; font-size:13px; color:#ffd166; }}
  .footer {{ text-align:center; padding:40px 0 24px; border-top:1px solid #1e2330; margin-top:48px; }}
  .footer-brand {{ font-size:13px; color:#5a6278; }}
  @media print {{
    body {{ background:#fff; color:#111; }}
    .kpi-card, .section-card {{ background:#f8f9fa; border-color:#ddd; }}
    .bar-wrap {{ background:#eee; }}
  }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="brand">AI매출업 · 가맹점 분석 리포트</div>
    <div class="merchant-name">{merchant_name}</div>
    <div class="period-badge">{period}</div>
    <div class="generated">생성일: {now_text()}</div>
  </div>

  <div class="score-block">
    <div class="score-circle">
      <div class="score-num">{score}</div>
      <div class="score-label">종합 점수</div>
    </div>
    <div class="score-comment">{overall_comment}</div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">총 언급 수</div>
      <div class="kpi-val">{summary.get('total_mentions', 0):,}</div>
      <div class="kpi-unit">건</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">영수증 리뷰</div>
      <div class="kpi-val">{summary.get('place_receipt_count', 0):,}</div>
      <div class="kpi-unit">건</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">유튜브 조회수</div>
      <div class="kpi-val">{summary.get('youtube_total_views', 0):,}</div>
      <div class="kpi-unit">회</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">네이버 블로그</div>
      <div class="kpi-val">{summary.get('naver_blog_count', 0):,}</div>
      <div class="kpi-unit">건</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">인스타그램</div>
      <div class="kpi-val">{summary.get('instagram_count', 0):,}</div>
      <div class="kpi-unit">건</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">플레이스 블로그</div>
      <div class="kpi-val">{summary.get('place_blog_count', 0):,}</div>
      <div class="kpi-unit">건</div>
    </div>
  </div>

  <div class="section-title-main">월별 추이</div>
  {monthly_bars}

  <div class="section-title-main">채널별 현황</div>
  <table class="channel-table">
    <thead><tr><th>채널</th><th style="text-align:right">건수</th></tr></thead>
    <tbody>{channel_rows}</tbody>
  </table>

  <div class="section-title-main">AI 분석 리포트</div>
  {sections_html}

  <div class="section-title-main">즉시 실행 과제</div>
  {action_html}

  <div class="section-title-main">주의 사항</div>
  {risk_html}

  <div class="footer">
    <div class="footer-brand">먼키 · AI매출업 · 가맹점 마케팅 분석 리포트</div>
  </div>
</div>
</body>
</html>"""


# ══════════════════════════════════════════
# PDF 변환 (Playwright)
# ══════════════════════════════════════════
def html_to_pdf(html_content: str, output_path: str) -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.set_content(html_content, wait_until="networkidle", timeout=30000)
            page.pdf(
                path=output_path,
                format="A4",
                print_background=True,
                margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
            )
            browser.close()
        return True
    except Exception as e:
        logger.error(f"[PDF] 변환 실패: {e}")
        return False


# ══════════════════════════════════════════
# 수집 Job 실행 (백그라운드)
# ══════════════════════════════════════════
def run_crawl_job(job_id: str, merchant: dict, period: str):
    started = time.time()
    try:
        CRAWL_JOBS[job_id]["status"] = "running"
        CRAWL_JOBS[job_id]["message"] = "데이터 수집 중..."

        def work():
            return build_report(merchant, period=period)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(work)
            report = future.result(timeout=CRAWL_TIMEOUT)

        REPORTS[merchant["id"]] = report
        elapsed = int(time.time() - started)

        # Claude 분석
        CRAWL_JOBS[job_id]["message"] = "AI 분석 리포트 생성 중..."
        claude_result = generate_claude_report(report)
        REPORTS[merchant["id"]]["claude_report"] = claude_result

        # PDF 저장
        if claude_result["status"] == "ok" and claude_result["html"]:
            pdf_path = str(OUT_DIR / f"{merchant['id']}_{job_id[:8]}.pdf")
            if html_to_pdf(claude_result["html"], pdf_path):
                REPORTS[merchant["id"]]["pdf_path"] = pdf_path
                REPORTS[merchant["id"]]["pdf_job_id"] = job_id[:8]

        CRAWL_JOBS[job_id].update({
            "status": "success",
            "message": f"수집 완료 ({elapsed}초)",
            "finished_at": now_iso(),
            "elapsed_seconds": elapsed,
        })

    except concurrent.futures.TimeoutError:
        CRAWL_JOBS[job_id].update({
            "status": "timeout",
            "message": f"시간 초과 ({CRAWL_TIMEOUT}초)",
            "finished_at": now_iso(),
            "elapsed_seconds": int(time.time() - started),
        })
    except Exception as e:
        CRAWL_JOBS[job_id].update({
            "status": "error",
            "message": f"오류: {str(e)[:200]}",
            "finished_at": now_iso(),
            "elapsed_seconds": int(time.time() - started),
        })


# ══════════════════════════════════════════
# API 엔드포인트
# ══════════════════════════════════════════
@app.get("/")
def root():
    return {"status": "ok", "service": "AI매출업 리포트 API", "version": "2.0.0"}

@app.get("/api/health")
def health():
    return {"status": "ok", "time": now_text()}

@app.post("/api/auth/login")
def login(payload: LoginRequest):
    if payload.email.strip() != ADMIN_EMAIL or payload.password.strip() != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    return {
        "access_token": create_token(payload.email),
        "token_type": "bearer",
        "expires_in_minutes": JWT_EXPIRE_MINUTES,
    }

@app.get("/api/merchants")
def list_merchants(admin: str = Depends(verify_token)):
    return MERCHANTS

@app.get("/api/reports/{merchant_id}")
def get_report(merchant_id: str, period: str = "최근 6개월", admin: str = Depends(verify_token)):
    merchant = next((m for m in MERCHANTS if m["id"] == merchant_id), None)
    if not merchant:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")
    report = REPORTS.get(merchant_id)
    if not report:
        # 샘플 데이터 반환 (수집 전)
        report = _sample_report(merchant, period)
        REPORTS[merchant_id] = report
    return report

@app.post("/api/crawl-jobs")
def create_crawl_job(payload: CrawlJobCreate, background_tasks: BackgroundTasks, email: str = Depends(verify_token)):
    merchant = next((m for m in MERCHANTS if m["id"] == payload.merchant_id), None)
    if not merchant:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")

    job_id = str(uuid4())
    CRAWL_JOBS[job_id] = {
        "job_id": job_id,
        "merchant_id": payload.merchant_id,
        "merchant_name": merchant["name"],
        "period": payload.period,
        "status": "queued",
        "message": "수집 대기 중...",
        "started_at": now_iso(),
        "finished_at": None,
        "elapsed_seconds": 0,
    }

    background_tasks.add_task(run_crawl_job, job_id, merchant, payload.period)
    return CRAWL_JOBS[job_id]

@app.get("/api/crawl-jobs/{job_id}")
def get_crawl_job(job_id: str, email: str = Depends(verify_token)):
    job = CRAWL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job

@app.get("/api/reports/{merchant_id}/pdf")
def download_pdf(merchant_id: str, admin: str = Depends(verify_token)):
    report = REPORTS.get(merchant_id)
    if not report:
        raise HTTPException(status_code=404, detail="리포트가 없습니다. 먼저 수집을 실행해주세요.")
    pdf_path = report.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF 파일이 없습니다. 수집 완료 후 다시 시도하세요.")
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    merchant_name = report.get("merchant_name", merchant_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={merchant_name}_report.pdf"}
    )

@app.get("/api/reports/{merchant_id}/chatgpt-json")
def download_chatgpt_json(merchant_id: str, admin: str = Depends(verify_token)):
    report = REPORTS.get(merchant_id)
    if not report:
        raise HTTPException(status_code=404, detail="리포트가 없습니다.")
    payload = report.get("chatgpt_payload", {})
    merchant_name = report.get("merchant_name", merchant_id)
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={merchant_name}_chatgpt.json"}
    )

@app.get("/api/reports/{merchant_id}/claude-report")
def get_claude_report(merchant_id: str, admin: str = Depends(verify_token)):
    report = REPORTS.get(merchant_id)
    if not report or not report.get("claude_report"):
        raise HTTPException(status_code=404, detail="Claude 분석 리포트가 없습니다.")
    cr = report["claude_report"]
    return Response(content=cr.get("html", ""), media_type="text/html; charset=utf-8")


# ══════════════════════════════════════════
# 샘플 데이터 (수집 전 초기 화면용)
# ══════════════════════════════════════════
def _sample_report(merchant: dict, period: str) -> dict:
    month_count = get_month_count(period)
    month_keys = build_month_keys(month_count)
    monthly = []
    for i, mk in enumerate(month_keys):
        base = 10 + i * 2
        monthly.append({
            "month": month_key_label(mk), "month_key": mk,
            "blog_count": base, "instagram_count": base*2,
            "place_receipt_count": base//2, "place_blog_count": base//3,
            "youtube_count": 1, "total_count": base*4,
        })
    summary = {
        "total_mentions": sum(r["total_count"] for r in monthly),
        "naver_blog_count": sum(r["blog_count"] for r in monthly),
        "instagram_count": sum(r["instagram_count"] for r in monthly),
        "place_receipt_count": sum(r["place_receipt_count"] for r in monthly),
        "place_blog_count": sum(r["place_blog_count"] for r in monthly),
        "youtube_count": len(monthly),
        "youtube_total_views": 50000,
    }
    return {
        "merchant_id": merchant["id"],
        "merchant_name": merchant["name"],
        "region": merchant.get("region", ""),
        "generated_at": now_text(),
        "period": period, "period_label": period,
        "is_sample": True,
        "summary": summary,
        "monthly_summary": monthly,
        "daily_summary": [],
        "channel_share": [
            {"name": "네이버 블로그", "value": summary["naver_blog_count"]},
            {"name": "인스타그램", "value": summary["instagram_count"]},
            {"name": "영수증 리뷰", "value": summary["place_receipt_count"]},
            {"name": "플레이스 블로그 리뷰", "value": summary["place_blog_count"]},
            {"name": "유튜브", "value": summary["youtube_count"]},
        ],
        "top_videos": [],
        "source_status": {"note": "수집 전 샘플 데이터입니다. [수집 시작] 버튼을 눌러주세요."},
    }


# ══════════════════════════════════════════
# 가맹점 CRUD API
# ══════════════════════════════════════════
class MerchantCreate(BaseModel):
    name: str
    region: str = ""
    address: str = ""
    naver_place_url: str = ""
    blog_keywords: list[str] = []
    instagram_hashtags: list[str] = []
    instagram_channel: str = ""
    youtube_keywords: list[str] = []

class MerchantUpdate(MerchantCreate):
    pass


@app.post("/api/merchants")
def add_merchant(payload: MerchantCreate, admin: str = Depends(verify_token)):
    """가맹점 추가"""
    new_id = re.sub(r"[^a-z0-9_]", "_", payload.name.lower().strip())
    new_id = f"{new_id}_{len(MERCHANTS)+1}"

    if any(m["id"] == new_id or m["name"] == payload.name for m in MERCHANTS):
        raise HTTPException(status_code=409, detail="이미 등록된 가맹점명입니다.")

    merchant = {
        "id": new_id,
        **payload.model_dump(),
    }
    MERCHANTS.append(merchant)
    return merchant


@app.put("/api/merchants/{merchant_id}")
def update_merchant(merchant_id: str, payload: MerchantUpdate, admin: str = Depends(verify_token)):
    """가맹점 정보 수정"""
    for i, m in enumerate(MERCHANTS):
        if m["id"] == merchant_id:
            MERCHANTS[i] = {"id": merchant_id, **payload.model_dump()}
            # 기존 리포트 캐시 삭제 (재수집 필요)
            REPORTS.pop(merchant_id, None)
            return MERCHANTS[i]
    raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")


@app.delete("/api/merchants/{merchant_id}")
def delete_merchant(merchant_id: str, admin: str = Depends(verify_token)):
    """가맹점 삭제"""
    global MERCHANTS
    original = len(MERCHANTS)
    MERCHANTS = [m for m in MERCHANTS if m["id"] != merchant_id]
    if len(MERCHANTS) == original:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")
    REPORTS.pop(merchant_id, None)
    return {"deleted": merchant_id}


# ══════════════════════════════════════════
# 엑셀 다운로드 3종
# ══════════════════════════════════════════
def _get_report_or_404(merchant_id: str) -> dict:
    report = REPORTS.get(merchant_id)
    if not report or report.get("is_sample"):
        raise HTTPException(
            status_code=404,
            detail="수집된 데이터가 없습니다. 먼저 [수집 시작]을 실행해주세요."
        )
    return report


@app.get("/api/reports/{merchant_id}/excel/raw")
def download_excel_raw(merchant_id: str, admin: str = Depends(verify_token)):
    """① 원시 데이터 엑셀"""
    if not EXCEL_AVAILABLE:
        raise HTTPException(status_code=500, detail="엑셀 라이브러리가 설치되지 않았습니다.")
    report = _get_report_or_404(merchant_id)
    xlsx_bytes = make_raw_excel(report)
    name = report.get("merchant_name", merchant_id)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}_원시데이터.xlsx"},
    )


@app.get("/api/reports/{merchant_id}/excel/daily")
def download_excel_daily(merchant_id: str, admin: str = Depends(verify_token)):
    """② 일별 집계 엑셀"""
    if not EXCEL_AVAILABLE:
        raise HTTPException(status_code=500, detail="엑셀 라이브러리가 설치되지 않았습니다.")
    report = _get_report_or_404(merchant_id)
    xlsx_bytes = make_daily_excel(report)
    name = report.get("merchant_name", merchant_id)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}_일별집계.xlsx"},
    )


@app.get("/api/reports/{merchant_id}/excel/monthly")
def download_excel_monthly(merchant_id: str, admin: str = Depends(verify_token)):
    """③ 월별 집계 엑셀"""
    if not EXCEL_AVAILABLE:
        raise HTTPException(status_code=500, detail="엑셀 라이브러리가 설치되지 않았습니다.")
    report = _get_report_or_404(merchant_id)
    xlsx_bytes = make_monthly_excel(report)
    name = report.get("merchant_name", merchant_id)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}_월별집계.xlsx"},
    )

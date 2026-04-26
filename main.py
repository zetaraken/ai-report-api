import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr

app = FastAPI(title="AI매출업 리포트 API", version="1.3.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "zetarise@gmail.com").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "4858").strip()
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ai-report-secret-2026").strip()
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 720


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CrawlJobCreate(BaseModel):
    merchant_id: str
    period: str = "최근 6개월"
    start_date: str | None = None
    end_date: str | None = None


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_token(email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": email, "role": "admin", "exp": expire},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="인증 실패")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="토큰 오류")


MERCHANTS = [
    {
        "id": "bae_po_cha",
        "name": "배포차",
        "region": "서울 강남구",
        "address": "서울 강남구 도산대로1길 16 지상1, 2층",
        "naver_place_url": "https://naver.me/xv6tlDW3",
        "blog_keywords": "신사역 배포차, 신사동 배포차",
        "instagram_hashtags": "배포차",
        "instagram_channel": "https://www.instagram.com/bae_po_cha",
        "youtube_keywords": "신사역 배포차, 신사동 배포차",
    },
    {
        "id": "soyo_ilsan",
        "name": "소요",
        "region": "경기 고양시",
        "address": "경기 고양시 일산동구 월드고양로 21 상가동 1동 1층 309호, 310호",
        "naver_place_url": "https://naver.me/F0AHoPtm",
        "blog_keywords": "일산 소요, 고양시 소요, 일산동구 소요, 장항동 소요",
        "instagram_hashtags": "일산 소요, 고양시 소요, 일산동구 소요, 장항동 소요",
        "instagram_channel": "https://www.instagram.com/soyo_izakaya",
        "youtube_keywords": "일산 소요, 고양시 소요, 일산동구 소요, 장항동 소요",
    },
    {
        "id": "soon_jamae_gamjatang",
        "name": "순자매감자탕",
        "region": "경기 화성시",
        "address": "경기 화성시 동탄구 동탄기흥로257번가길 24-11 1층",
        "naver_place_url": "https://naver.me/GNRzS59C",
        "blog_keywords": "순자매감자탕",
        "instagram_hashtags": "순자매감자탕",
        "instagram_channel": "",
        "youtube_keywords": "순자매감자탕",
    },
    {
        "id": "yeontan_kim_pyeongseon",
        "name": "연탄김평선",
        "region": "서울 강남구",
        "address": "서울 강남구 선릉로90길 64 지상1층",
        "naver_place_url": "https://naver.me/xNLZbjfI",
        "blog_keywords": "연탄김평선",
        "instagram_hashtags": "연탄김평선",
        "instagram_channel": "https://www.instagram.com/yeon_tan_pyeongseon_kim",
        "youtube_keywords": "연탄김평선",
    },
    {
        "id": "liveball_yeoksam",
        "name": "라이브볼",
        "region": "서울 강남구",
        "address": "서울 강남구 테헤란로 147 지하 1층 3호 라이브볼",
        "naver_place_url": "https://naver.me/5bVsye2y",
        "blog_keywords": "라이브볼 역삼점, 라이브볼 역삼역, 라이브볼 역삼동",
        "instagram_hashtags": "라이브볼 역삼점, 라이브볼 역삼역, 라이브볼 역삼동",
        "instagram_channel": "",
        "youtube_keywords": "라이브볼 역삼점, 라이브볼 역삼역, 라이브볼 역삼동",
    },
]

REPORTS: dict[str, dict[str, Any]] = {}


def get_period_label(period: str = "최근 6개월", start_date: str | None = None, end_date: str | None = None) -> str:
    if period in ("custom", "기간 설정"):
        return f"{start_date} ~ {end_date}" if start_date and end_date else "기간 설정"
    return period


def get_month_count(period: str) -> int:
    if period == "최근 1개월":
        return 1
    if period == "최근 3개월":
        return 3
    if period == "최근 1년":
        return 12
    return 6


def split_keywords(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def fetch_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    with urlopen(req, timeout=15) as response:
        return response.read().decode("utf-8", errors="ignore")


def parse_compact_view_count(text: str) -> int:
    if not text:
        return 0
    raw = text.replace("조회수", "").replace("views", "").replace(",", "").replace(" ", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)(만|천|K|M)?", raw, re.IGNORECASE)
    if not m:
        return 0
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "만":
        num *= 10000
    elif unit == "천":
        num *= 1000
    elif unit == "k":
        num *= 1000
    elif unit == "m":
        num *= 1000000
    return int(num)


def build_recent_month_keys(month_count: int) -> list[str]:
    today = datetime.now()
    months: list[str] = []
    year = today.year
    month = today.month

    for offset in range(month_count - 1, -1, -1):
        y = year
        m = month - offset
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y}-{m:02d}")

    return months


def month_key_to_label(month_key: str) -> str:
    year, month = month_key.split("-")
    return f"{year}년 {int(month)}월"


def estimate_published_month_key(label: str) -> str | None:
    """
    YouTube 검색 HTML의 publishedTimeText를 기준으로 업로드 월을 추정합니다.
    예: '3개월 전', '2 weeks ago', '1 year ago'
    """
    if not label:
        return None

    text = label.strip().lower()
    now = datetime.now()

    m = re.search(r"(\d+)\s*(초|분|시간|일|주|개월|달|년)", text)
    if m:
        value = int(m.group(1))
        unit = m.group(2)
        if unit in ("초", "분", "시간"):
            dt = now
        elif unit == "일":
            dt = now - timedelta(days=value)
        elif unit == "주":
            dt = now - timedelta(weeks=value)
        elif unit in ("개월", "달"):
            month = now.month - value
            year = now.year
            while month <= 0:
                month += 12
                year -= 1
            dt = now.replace(year=year, month=month)
        elif unit == "년":
            dt = now.replace(year=now.year - value)
        else:
            dt = now
        return f"{dt.year}-{dt.month:02d}"

    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago", text)
    if m:
        value = int(m.group(1))
        unit = m.group(2)
        if unit in ("second", "minute", "hour"):
            dt = now
        elif unit == "day":
            dt = now - timedelta(days=value)
        elif unit == "week":
            dt = now - timedelta(weeks=value)
        elif unit == "month":
            month = now.month - value
            year = now.year
            while month <= 0:
                month += 12
                year -= 1
            dt = now.replace(year=year, month=month)
        elif unit == "year":
            dt = now.replace(year=now.year - value)
        else:
            dt = now
        return f"{dt.year}-{dt.month:02d}"

    return None


def normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", value or "").lower()


def is_relevant_youtube_video(merchant: dict[str, Any], title: str, channel: str) -> bool:
    merchant_name = merchant["name"]
    title_n = normalize_text(title)
    channel_n = normalize_text(channel)
    target_n = normalize_text(merchant_name)
    combined = title_n + channel_n

    if target_n and target_n in combined:
        return True

    keywords = split_keywords(merchant.get("youtube_keywords"))
    for keyword in keywords:
        keyword_n = normalize_text(keyword)
        if keyword_n and keyword_n in combined:
            return True

    return False


def collect_youtube_search_scrape(merchant: dict[str, Any], max_results: int = 12) -> dict[str, Any]:
    """
    YouTube 검색결과 HTML 비공식 수집.
    v3: 업로드 시점 publishedTimeText를 파싱해 월별 유튜브 건수를 배분합니다.
    """
    keywords = split_keywords(merchant.get("youtube_keywords")) or [merchant["name"]]
    videos: dict[str, dict[str, Any]] = {}
    raw_candidates = 0
    filtered_out = 0

    try:
        for keyword in keywords:
            url = f"https://www.youtube.com/results?search_query={quote_plus(keyword)}"
            html = fetch_text(url)

            video_ids = re.findall(r'"videoId":"([^"]{8,20})"', html)
            titles = re.findall(r'"title":\{"runs":\[\{"text":"(.*?)"\}\]', html)
            channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"(.*?)"', html)
            views = re.findall(r'"viewCountText":\{"simpleText":"(.*?)"\}', html)
            published_times = re.findall(r'"publishedTimeText":\{"simpleText":"(.*?)"\}', html)

            for idx, video_id in enumerate(video_ids):
                raw_candidates += 1

                if video_id in videos:
                    continue

                title = unescape(titles[idx]) if idx < len(titles) else f"{keyword} 관련 영상"
                channel = unescape(channels[idx]) if idx < len(channels) else "YouTube"
                view_text = unescape(views[idx]) if idx < len(views) else ""
                published_label = unescape(published_times[idx]) if idx < len(published_times) else ""
                published_month_key = estimate_published_month_key(published_label)

                if not is_relevant_youtube_video(merchant, title, channel):
                    filtered_out += 1
                    continue

                videos[video_id] = {
                    "video_id": video_id,
                    "title": title,
                    "channel": channel,
                    "views": parse_compact_view_count(view_text),
                    "published_label": published_label,
                    "published_month_key": published_month_key,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                }

                if len(videos) >= max_results:
                    break

            if len(videos) >= max_results:
                break

        monthly_counts: dict[str, int] = {}
        for video in videos.values():
            key = video.get("published_month_key")
            if key:
                monthly_counts[key] = monthly_counts.get(key, 0) + 1

        top_videos = sorted(videos.values(), key=lambda x: x["views"], reverse=True)[:5]

        if not videos:
            return {
                "status": "ok",
                "reason": f"정확 매칭 영상 없음. 후보 {raw_candidates}건 중 {filtered_out}건 제외",
                "youtube_count": 0,
                "youtube_total_views": 0,
                "monthly_youtube_counts": {},
                "top_videos": [],
            }

        return {
            "status": "ok",
            "reason": f"정확 매칭+업로드월 파싱 적용. 후보 {raw_candidates}건 중 {filtered_out}건 제외",
            "youtube_count": len(videos),
            "youtube_total_views": sum(v["views"] for v in videos.values()),
            "monthly_youtube_counts": monthly_counts,
            "top_videos": [
                {
                    "title": v["title"],
                    "channel": v["channel"],
                    "views": v["views"],
                    "published": v.get("published_label", ""),
                }
                for v in top_videos
            ],
        }
    except Exception as e:
        return {
            "status": "fallback",
            "reason": f"YouTube 비공식 크롤링 오류: {str(e)[:140]}",
            "youtube_count": None,
            "youtube_total_views": None,
            "monthly_youtube_counts": {},
            "top_videos": [],
        }


def resolve_final_url(url: str) -> str:
    try:
        opener = build_opener(HTTPRedirectHandler)
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        response = opener.open(req, timeout=12)
        return response.geturl()
    except Exception:
        return url


def extract_naver_place_id(naver_place_url: str) -> str | None:
    final_url = resolve_final_url(naver_place_url)

    patterns = [
        r"/place/(\d+)",
        r"/restaurant/(\d+)",
        r"placeId=(\d+)",
        r"id=(\d+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, final_url)
        if m:
            return m.group(1)

    return None


def parse_naver_count(text: str) -> int:
    if not text:
        return 0

    cleaned = text.replace(",", "").replace(" ", "")
    m = re.search(r"(\d+)", cleaned)
    return int(m.group(1)) if m else 0


def collect_naver_blog_search(merchant: dict[str, Any], max_results: int = 30) -> dict[str, Any]:
    """
    네이버 블로그 검색 HTML 기반 비공식 수집.
    검색결과 HTML 구조 변경 시 fallback 처리됩니다.
    """
    keywords = split_keywords(merchant.get("blog_keywords")) or [merchant["name"]]
    posts: dict[str, dict[str, Any]] = {}
    raw_candidates = 0
    filtered_out = 0

    try:
        for keyword in keywords:
            url = f"https://search.naver.com/search.naver?where=blog&query={quote_plus(keyword)}"
            html = fetch_text(url)

            # 네이버 검색결과의 블로그 링크 후보
            links = re.findall(r'href="(https://blog\.naver\.com/[^"]+)"', html)
            titles = re.findall(r'<a[^>]+class="[^"]*(?:title_link|api_txt_lines)[^"]*"[^>]*>(.*?)</a>', html, re.S)

            for i, link in enumerate(links):
                raw_candidates += 1
                if link in posts:
                    continue

                title_html = titles[i] if i < len(titles) else ""
                title = re.sub(r"<.*?>", "", title_html)
                title = unescape(title).strip()

                combined = normalize_text(title + " " + link)
                merchant_name = normalize_text(merchant["name"])

                # 매장명 또는 등록 검색어 전체 문구가 포함되어야 인정
                relevant = merchant_name in combined if merchant_name else False
                if not relevant:
                    for keyword_check in keywords:
                        if normalize_text(keyword_check) in combined:
                            relevant = True
                            break

                if not relevant:
                    filtered_out += 1
                    continue

                posts[link] = {
                    "title": title or merchant["name"] + " 관련 블로그",
                    "url": link,
                    "source": "naver_blog",
                }

                if len(posts) >= max_results:
                    break

            if len(posts) >= max_results:
                break

        return {
            "status": "ok",
            "reason": f"네이버 블로그 검색 수집. 후보 {raw_candidates}건 중 {filtered_out}건 제외",
            "count": len(posts),
            "items": list(posts.values())[:10],
        }
    except Exception as e:
        return {
            "status": "fallback",
            "reason": f"네이버 블로그 수집 오류: {str(e)[:140]}",
            "count": None,
            "items": [],
        }


def collect_naver_place_review_counts(merchant: dict[str, Any]) -> dict[str, Any]:
    """
    네이버 플레이스 모바일 페이지에서 방문자 리뷰/블로그 리뷰 카운트를 추출하는 비공식 수집.
    naver.me 단축 URL은 최종 URL로 resolve 후 place id를 추정합니다.
    """
    place_url = merchant.get("naver_place_url", "")
    place_id = extract_naver_place_id(place_url)

    if not place_id:
        return {
            "status": "fallback",
            "reason": "네이버 플레이스 ID 추출 실패",
            "place_id": None,
            "place_receipt_count": None,
            "place_blog_count": None,
        }

    candidate_urls = [
        f"https://m.place.naver.com/restaurant/{place_id}/home",
        f"https://m.place.naver.com/place/{place_id}/home",
        f"https://m.place.naver.com/restaurant/{place_id}/review/visitor",
        f"https://m.place.naver.com/place/{place_id}/review/visitor",
    ]

    try:
        merged = ""
        for url in candidate_urls:
            try:
                merged += "\n" + fetch_text(url)
            except Exception:
                continue

        if not merged.strip():
            return {
                "status": "fallback",
                "reason": "네이버 플레이스 페이지 접근 실패",
                "place_id": place_id,
                "place_receipt_count": None,
                "place_blog_count": None,
            }

        receipt_count = None
        blog_count = None

        receipt_patterns = [
            r"방문자\s*리뷰\s*([0-9,]+)",
            r"방문자리뷰\s*([0-9,]+)",
            r'"visitorReviewCount"\s*:\s*([0-9]+)',
            r'"reviewCount"\s*:\s*([0-9]+)',
        ]
        blog_patterns = [
            r"블로그\s*리뷰\s*([0-9,]+)",
            r"블로그리뷰\s*([0-9,]+)",
            r'"blogReviewCount"\s*:\s*([0-9]+)',
        ]

        for pattern in receipt_patterns:
            m = re.search(pattern, merged)
            if m:
                receipt_count = parse_naver_count(m.group(1))
                break

        for pattern in blog_patterns:
            m = re.search(pattern, merged)
            if m:
                blog_count = parse_naver_count(m.group(1))
                break

        status_value = "ok" if receipt_count is not None or blog_count is not None else "fallback"
        reason = "네이버 플레이스 리뷰 수집 완료" if status_value == "ok" else "리뷰 카운트 파싱 실패"

        return {
            "status": status_value,
            "reason": reason,
            "place_id": place_id,
            "place_receipt_count": receipt_count,
            "place_blog_count": blog_count,
        }
    except Exception as e:
        return {
            "status": "fallback",
            "reason": f"네이버 플레이스 리뷰 수집 오류: {str(e)[:140]}",
            "place_id": place_id,
            "place_receipt_count": None,
            "place_blog_count": None,
        }


def distribute_total_to_recent_months(total: int, month_count: int) -> list[int]:
    """
    실제 월별 게시일을 안정적으로 확보하지 못하는 채널의 임시 배분 로직.
    총량은 실제 수집값을 쓰고, 월별은 최근월 가중치로 분배합니다.
    """
    if month_count <= 0:
        return []

    if total <= 0:
        return [0] * month_count

    weights = list(range(1, month_count + 1))
    weight_sum = sum(weights)
    values = [int(total * w / weight_sum) for w in weights]
    diff = total - sum(values)
    values[-1] += diff
    return values


def make_base_sample_data(name: str) -> dict[str, Any]:
    sample_data = {
        "배포차": {
            "monthly": [(18, 42, 9, 5, 6), (22, 57, 11, 7, 7), (29, 66, 13, 8, 10), (34, 88, 17, 10, 11)],
            "summary": {"total_mentions": 470, "naver_blog_count": 103, "instagram_count": 253, "place_receipt_count": 50, "place_blog_count": 30, "youtube_total_views": 264000, "ad_ratio": 62, "self_ratio": 23},
            "videos": [("신사역 배포차 방문 후기", "맛집탐방러", 128000), ("가로수길 술집 추천 배포차", "서울먹방일기", 84600)],
        },
        "소요": {
            "monthly": [(9, 18, 4, 3, 2), (13, 22, 5, 4, 2), (16, 27, 7, 5, 3), (21, 34, 8, 6, 3)],
            "summary": {"total_mentions": 192, "naver_blog_count": 59, "instagram_count": 101, "place_receipt_count": 24, "place_blog_count": 18, "youtube_total_views": 72000, "ad_ratio": 38, "self_ratio": 31},
            "videos": [("일산 소요 이자카야 방문 후기", "일산맛집노트", 31800), ("장항동 술집 소요 추천", "고양먹방채널", 24700)],
        },
        "순자매감자탕": {
            "monthly": [(6, 8, 5, 2, 1), (8, 11, 6, 2, 1), (10, 14, 8, 3, 2), (13, 18, 9, 4, 2)],
            "summary": {"total_mentions": 133, "naver_blog_count": 37, "instagram_count": 51, "place_receipt_count": 28, "place_blog_count": 11, "youtube_total_views": 38000, "ad_ratio": 24, "self_ratio": 42},
            "videos": [("순자매감자탕 동탄 방문 후기", "동탄맛집리뷰", 18600), ("화성 감자탕 맛집 추천", "경기맛집지도", 12400)],
        },
        "연탄김평선": {
            "monthly": [(7, 12, 4, 2, 1), (9, 16, 5, 3, 2), (12, 22, 6, 3, 2), (15, 28, 7, 4, 3)],
            "summary": {"total_mentions": 146, "naver_blog_count": 43, "instagram_count": 78, "place_receipt_count": 22, "place_blog_count": 12, "youtube_total_views": 56000, "ad_ratio": 29, "self_ratio": 36},
            "videos": [("연탄김평선 선릉 맛집 후기", "강남맛집기록", 22100), ("선릉 고기집 연탄김평선", "퇴근후한끼", 19300)],
        },
        "라이브볼": {
            "monthly": [(5, 9, 3, 1, 1), (7, 12, 4, 2, 1), (9, 15, 5, 2, 2), (11, 19, 6, 3, 2)],
            "summary": {"total_mentions": 104, "naver_blog_count": 32, "instagram_count": 55, "place_receipt_count": 18, "place_blog_count": 8, "youtube_total_views": 41000, "ad_ratio": 33, "self_ratio": 28},
            "videos": [("라이브볼 역삼점 방문 후기", "역삼맛집로그", 17500), ("역삼역 라이브볼 분위기 리뷰", "강남데이트코스", 14200)],
        },
    }
    return sample_data.get(name, sample_data["배포차"])


def expand_monthly(base_rows: list[tuple[int, int, int, int, int]], months: int) -> list[tuple[int, int, int, int, int]]:
    if months <= len(base_rows):
        return base_rows[-months:]

    rows = list(base_rows)
    while len(rows) < months:
        last = rows[-1]
        growth = 1.06 + (len(rows) % 3) * 0.02
        rows.append(tuple(max(1, int(v * growth)) for v in last))
    return rows[-months:]


def make_sample_report(
    merchant: dict[str, Any],
    period: str = "최근 6개월",
    start_date: str | None = None,
    end_date: str | None = None,
    use_live_youtube: bool = False,
    use_live_naver: bool = False,
) -> dict[str, Any]:
    name = merchant["name"]
    data = make_base_sample_data(name)

    month_count = get_month_count(period)
    if period in ("custom", "기간 설정"):
        month_count = 6

    month_keys = build_recent_month_keys(month_count)
    monthly_rows = expand_monthly(data["monthly"], month_count)
    monthly = []
    for i, row in enumerate(monthly_rows):
        blog, insta, receipt, place_blog, youtube = row
        monthly.append({
            "month": month_key_to_label(month_keys[i]),
            "month_key": month_keys[i],
            "blog_count": blog,
            "instagram_count": insta,
            "place_receipt_count": receipt,
            "place_blog_count": place_blog,
            "youtube_count": youtube,
            "total_count": blog + insta + receipt + place_blog + youtube,
        })

    summary = {
        "naver_blog_count": sum(r["blog_count"] for r in monthly),
        "instagram_count": sum(r["instagram_count"] for r in monthly),
        "place_receipt_count": sum(r["place_receipt_count"] for r in monthly),
        "place_blog_count": sum(r["place_blog_count"] for r in monthly),
        "youtube_total_views": data["summary"]["youtube_total_views"],
        "ad_ratio": data["summary"]["ad_ratio"],
        "self_ratio": data["summary"]["self_ratio"],
    }

    youtube_count = sum(r["youtube_count"] for r in monthly)
    top_videos = [
        {"title": title, "channel": channel, "views": views}
        for title, channel, views in data["videos"]
    ]
    source_status = {
        "youtube": {
            "status": "sample",
            "reason": "수집 전 샘플 데이터",
        },
        "naver_blog": {
            "status": "sample",
            "reason": "다음 단계 구현 예정",
        },
        "instagram": {
            "status": "sample",
            "reason": "다음 단계 구현 예정",
        },
    }

    if use_live_youtube:
        # 실제 수집 모드에서는 샘플 유튜브 월별값을 먼저 전부 0으로 초기화합니다.
        # 그래야 정확 매칭 영상이 없을 때 월별 유튜브가 2,2,2처럼 남지 않습니다.
        for row in monthly:
            row["youtube_count"] = 0
            row["total_count"] = (
                row["blog_count"]
                + row["instagram_count"]
                + row["place_receipt_count"]
                + row["place_blog_count"]
            )

        youtube_count = 0
        summary["youtube_total_views"] = 0
        top_videos = []

        youtube_result = collect_youtube_search_scrape(merchant)
        source_status["youtube"] = {
            "status": youtube_result["status"],
            "reason": youtube_result["reason"],
        }

        if youtube_result["status"] == "ok":
            summary["youtube_total_views"] = youtube_result["youtube_total_views"] or 0
            monthly_youtube_counts = youtube_result.get("monthly_youtube_counts", {})

            for row in monthly:
                matched_count = int(monthly_youtube_counts.get(row["month_key"], 0))
                row["youtube_count"] = matched_count
                row["total_count"] = (
                    row["blog_count"]
                    + row["instagram_count"]
                    + row["place_receipt_count"]
                    + row["place_blog_count"]
                    + row["youtube_count"]
                )
                youtube_count += matched_count

            if youtube_result["top_videos"]:
                top_videos = youtube_result["top_videos"]

    if use_live_naver:
        naver_blog_result = collect_naver_blog_search(merchant)
        place_review_result = collect_naver_place_review_counts(merchant)

        source_status["naver_blog"] = {
            "status": naver_blog_result["status"],
            "reason": naver_blog_result["reason"],
            "items": naver_blog_result.get("items", []),
        }
        source_status["naver_place"] = {
            "status": place_review_result["status"],
            "reason": place_review_result["reason"],
            "place_id": place_review_result.get("place_id"),
        }

        if naver_blog_result["status"] == "ok" and naver_blog_result["count"] is not None:
            blog_total = int(naver_blog_result["count"])
            summary["naver_blog_count"] = blog_total
            blog_monthly = distribute_total_to_recent_months(blog_total, len(monthly))
            for i, row in enumerate(monthly):
                row["blog_count"] = blog_monthly[i]

        if place_review_result["status"] == "ok":
            if place_review_result.get("place_receipt_count") is not None:
                receipt_total = int(place_review_result["place_receipt_count"])
                summary["place_receipt_count"] = receipt_total
                receipt_monthly = distribute_total_to_recent_months(receipt_total, len(monthly))
                for i, row in enumerate(monthly):
                    row["place_receipt_count"] = receipt_monthly[i]

            if place_review_result.get("place_blog_count") is not None:
                place_blog_total = int(place_review_result["place_blog_count"])
                summary["place_blog_count"] = place_blog_total
                place_blog_monthly = distribute_total_to_recent_months(place_blog_total, len(monthly))
                for i, row in enumerate(monthly):
                    row["place_blog_count"] = place_blog_monthly[i]

        for row in monthly:
            row["total_count"] = (
                row["blog_count"]
                + row["instagram_count"]
                + row["place_receipt_count"]
                + row["place_blog_count"]
                + row["youtube_count"]
            )

    summary["total_mentions"] = (
        summary["naver_blog_count"]
        + summary["instagram_count"]
        + summary["place_receipt_count"]
        + summary["place_blog_count"]
        + youtube_count
    )

    return {
        "merchant_name": merchant["name"],
        "region": merchant.get("region", ""),
        "generated_at": now_text(),
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "period_label": get_period_label(period, start_date, end_date),
        "source_status": source_status,
        "summary": summary,
        "monthly_summary": monthly,
        "channel_share": [
            {"name": "네이버 블로그", "value": summary["naver_blog_count"]},
            {"name": "인스타그램", "value": summary["instagram_count"]},
            {"name": "영수증 리뷰", "value": summary["place_receipt_count"]},
            {"name": "플레이스 블로그 리뷰", "value": summary["place_blog_count"]},
            {"name": "유튜브", "value": youtube_count},
        ],
        "top_videos": top_videos,
        "insights": [
            f"{merchant['name']}의 온라인 언급량은 채널별로 차이가 있습니다.",
            "네이버 블로그와 인스타그램은 주요 노출 채널로 확인됩니다.",
            "네이버 플레이스 리뷰는 실제 방문 반응을 확인하는 핵심 지표입니다.",
            "유튜브 콘텐츠는 조회수 기반 인지도 확산 여부를 함께 봐야 합니다.",
        ],
    }


@app.get("/")
def root():
    return {"status": "ok", "service": "AI매출업 리포트 API", "version": "1.3.1"}


@app.get("/api/health")
def health():
    return {"status": "ok", "time": now_text()}


@app.get("/api/debug-login-config")
def debug_login_config():
    return {
        "admin_email": ADMIN_EMAIL,
        "admin_password_length": len(ADMIN_PASSWORD),
        "admin_password_preview": ADMIN_PASSWORD[:1] + "***" + ADMIN_PASSWORD[-1:] if ADMIN_PASSWORD else "",
        "jwt_secret_set": bool(JWT_SECRET_KEY),
    }


@app.get("/api/debug-source-config")
def debug_source_config():
    return {
        "youtube_collection_mode": "html_scrape_strict_with_published_month",
        "naver_blog_collection_mode": "html_scrape",
        "naver_place_review_collection_mode": "mobile_html_scrape",
        "youtube_api_key_required": False,
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    input_email = payload.email.strip()
    input_password = payload.password.strip()

    if input_email != ADMIN_EMAIL or input_password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    return {
        "access_token": create_token(input_email),
        "token_type": "bearer",
        "expires_in_minutes": JWT_EXPIRE_MINUTES,
        "admin_email": input_email,
    }


@app.get("/api/merchants")
def list_merchants(admin: str = Depends(verify_token)):
    return MERCHANTS


@app.get("/api/reports/{merchant_id}")
def get_report(
    merchant_id: str,
    period: str = "최근 6개월",
    start_date: str | None = None,
    end_date: str | None = None,
    admin: str = Depends(verify_token)
):
    merchant = next((m for m in MERCHANTS if m["id"] == merchant_id), None)

    if not merchant:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")

    report = REPORTS.get(merchant_id)
    if not report:
        report = make_sample_report(
            merchant,
            period=period,
            start_date=start_date,
            end_date=end_date,
            use_live_youtube=False,
        )
        REPORTS[merchant_id] = report

    report["period"] = period
    report["start_date"] = start_date
    report["end_date"] = end_date
    report["period_label"] = get_period_label(period, start_date, end_date)

    return report


@app.post("/api/crawl-jobs")
def create_crawl_job(payload: CrawlJobCreate, admin: str = Depends(verify_token)):
    merchant = next((m for m in MERCHANTS if m["id"] == payload.merchant_id), None)

    if not merchant:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")

    REPORTS[payload.merchant_id] = make_sample_report(
        merchant,
        period=payload.period,
        start_date=payload.start_date,
        end_date=payload.end_date,
        use_live_youtube=True,
        use_live_naver=True,
    )

    return {
        "id": f"job_{uuid4().hex[:10]}",
        "merchant_id": payload.merchant_id,
        "period": payload.period,
        "start_date": payload.start_date,
        "end_date": payload.end_date,
        "status": "completed",
        "source_status": REPORTS[payload.merchant_id].get("source_status", {}),
        "created_at": now_text()
    }


@app.get("/api/reports/{merchant_id}/pdf")
def get_pdf(merchant_id: str, period: str = "최근 6개월"):
    return {"message": "PDF 기능은 다음 단계에서 연결됩니다.", "merchant_id": merchant_id, "period": period}

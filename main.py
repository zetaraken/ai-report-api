import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr

app = FastAPI(title="AI매출업 리포트 API", version="1.1.0")

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

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()


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
    if period == "custom":
        return f"{start_date} ~ {end_date}" if start_date and end_date else "기간 설정"
    if period == "기간 설정":
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


def http_get_json(url: str) -> dict[str, Any]:
    req = Request(url, headers={"User-Agent": "ai-report-api/1.1"})
    with urlopen(req, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_youtube_data(merchant: dict[str, Any], max_results: int = 5) -> dict[str, Any]:
    """
    YouTube Data API v3 기반 실제 수집.
    YOUTUBE_API_KEY가 없거나 API 오류가 나면 status가 fallback으로 반환됩니다.
    """
    if not YOUTUBE_API_KEY:
        return {
            "status": "fallback",
            "reason": "YOUTUBE_API_KEY 미설정",
            "youtube_count": None,
            "youtube_total_views": None,
            "top_videos": [],
        }

    keywords = split_keywords(merchant.get("youtube_keywords")) or [merchant["name"]]
    video_map: dict[str, dict[str, Any]] = {}

    try:
        for keyword in keywords:
            search_params = urlencode({
                "part": "snippet",
                "q": keyword,
                "type": "video",
                "maxResults": max_results,
                "order": "relevance",
                "regionCode": "KR",
                "key": YOUTUBE_API_KEY,
            })
            search_url = f"https://www.googleapis.com/youtube/v3/search?{search_params}"
            search_data = http_get_json(search_url)

            for item in search_data.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                snippet = item.get("snippet", {})
                if not video_id:
                    continue
                video_map[video_id] = {
                    "video_id": video_id,
                    "title": snippet.get("title", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "published_at": snippet.get("publishedAt", ""),
                    "views": 0,
                }

        if not video_map:
            return {
                "status": "ok",
                "reason": "검색 결과 없음",
                "youtube_count": 0,
                "youtube_total_views": 0,
                "top_videos": [],
            }

        ids = ",".join(video_map.keys())
        videos_params = urlencode({
            "part": "statistics,snippet",
            "id": ids,
            "key": YOUTUBE_API_KEY,
        })
        videos_url = f"https://www.googleapis.com/youtube/v3/videos?{videos_params}"
        videos_data = http_get_json(videos_url)

        for item in videos_data.get("items", []):
            video_id = item.get("id")
            stats = item.get("statistics", {})
            if video_id in video_map:
                video_map[video_id]["views"] = int(stats.get("viewCount", 0))

        top_videos = sorted(video_map.values(), key=lambda x: x["views"], reverse=True)[:5]
        return {
            "status": "ok",
            "reason": "",
            "youtube_count": len(video_map),
            "youtube_total_views": sum(v["views"] for v in video_map.values()),
            "top_videos": [
                {"title": v["title"], "channel": v["channel"], "views": v["views"]}
                for v in top_videos
            ],
        }
    except Exception as e:
        return {
            "status": "fallback",
            "reason": f"YouTube API 오류: {str(e)[:120]}",
            "youtube_count": None,
            "youtube_total_views": None,
            "top_videos": [],
        }


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
) -> dict[str, Any]:
    name = merchant["name"]
    data = make_base_sample_data(name)

    month_count = get_month_count(period)
    if period in ("custom", "기간 설정"):
        month_count = 6

    monthly_rows = expand_monthly(data["monthly"], month_count)
    monthly = []
    for i, row in enumerate(monthly_rows, start=1):
        blog, insta, receipt, place_blog, youtube = row
        monthly.append({
            "month": f"{i}월",
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
        }
    }

    if use_live_youtube:
        youtube_result = collect_youtube_data(merchant)
        source_status["youtube"] = {
            "status": youtube_result["status"],
            "reason": youtube_result["reason"],
        }
        if youtube_result["status"] == "ok":
            youtube_count = youtube_result["youtube_count"] or 0
            summary["youtube_total_views"] = youtube_result["youtube_total_views"] or 0
            if youtube_result["top_videos"]:
                top_videos = youtube_result["top_videos"]

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
    return {"status": "ok", "service": "AI매출업 리포트 API", "version": "1.1.0"}


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
        "youtube_api_key_set": bool(YOUTUBE_API_KEY),
        "youtube_api_key_length": len(YOUTUBE_API_KEY),
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

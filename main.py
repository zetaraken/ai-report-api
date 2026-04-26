from __future__ import annotations
from datetime import datetime, timedelta

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr

app = FastAPI(title="AI매출업 리포트 API", version="1.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# Railway 환경변수가 안 먹어도 로그인되도록 기본값을 실제 테스트 계정으로 고정
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
    if period == "기간 설정":
        if start_date and end_date:
            return f"{start_date} ~ {end_date}"
        return "기간 설정"

    return period

def make_sample_report(
    merchant: dict[str, Any],
    period: str = "최근 6개월",
    start_date: str | None = None,
    end_date: str | None = None
) -> dict[str, Any]:
    name = merchant["name"]

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

    data = sample_data.get(name, sample_data["배포차"])
    monthly = []
    for i, row in enumerate(data["monthly"], start=1):
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

    summary = data["summary"]
    top_videos = [
        {"title": title, "channel": channel, "views": views}
        for title, channel, views in data["videos"]
    ]

    return {
    "merchant_name": merchant["name"],
    "region": merchant.get("region", ""),
    "generated_at": now_text(),

    "period": period,
    "start_date": start_date,
    "end_date": end_date,
    "period_label": get_period_label(period, start_date, end_date),

    "summary": summary,
        "monthly_summary": monthly,
        "channel_share": [
            {"name": "네이버 블로그", "value": summary["naver_blog_count"]},
            {"name": "인스타그램", "value": summary["instagram_count"]},
            {"name": "영수증 리뷰", "value": summary["place_receipt_count"]},
            {"name": "플레이스 블로그 리뷰", "value": summary["place_blog_count"]},
            {"name": "유튜브", "value": sum(r["youtube_count"] for r in monthly)},
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
    return {"status": "ok", "service": "AI매출업 리포트 API"}


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


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    input_email = payload.email.strip()
    input_password = payload.password.strip()

    if input_email != ADMIN_EMAIL or input_password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"이메일 또는 비밀번호가 올바르지 않습니다. server_email={ADMIN_EMAIL}, password_length={len(ADMIN_PASSWORD)}",
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
    if merchant_id not in REPORTS:
        merchant = next((m for m in MERCHANTS if m["id"] == merchant_id), None)

        if not merchant:
            raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")

        REPORTS[merchant_id] = make_sample_report(
            merchant,
            period=period,
            start_date=start_date,
            end_date=end_date
        )

    report = REPORTS[merchant_id]
    report["period"] = period
    report["start_date"] = start_date
    report["end_date"] = end_date
    report["period_label"] = get_period_label(period, start_date, end_date)

    return report
    
    if merchant_id not in REPORTS:
        merchant = next((m for m in MERCHANTS if m["id"] == merchant_id), None)
        if not merchant:
            raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")
        REPORTS[merchant_id] = make_sample_report(merchant)
    return REPORTS[merchant_id]


@app.post("/api/crawl-jobs")
def create_crawl_job(payload: CrawlJobCreate, admin: str = Depends(verify_token)):
    merchant = next((m for m in MERCHANTS if m["id"] == payload.merchant_id), None)
    if not merchant:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다.")
    REPORTS[payload.merchant_id] = make_sample_report(merchant)
    return {"id": f"job_{uuid4().hex[:10]}", "merchant_id": payload.merchant_id, "period": payload.period, "status": "completed", "created_at": now_text()}


@app.get("/api/reports/{merchant_id}/pdf")
def get_pdf(merchant_id: str, period: str = "최근 6개월"):
    return {"message": "PDF 기능은 다음 단계에서 연결됩니다.", "merchant_id": merchant_id}

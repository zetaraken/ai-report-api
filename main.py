from __future__ import annotations

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
        "id": "m_test_001",
        "name": "배포차",
        "region": "서울 강남구",
        "naver_place_url": "https://naver.me/xv6tlDW3",
        "blog_keywords": "배포차, 신사역 배포차",
        "instagram_hashtags": "배포차,신사역배포차",
        "youtube_keywords": "배포차, 신사역 배포차",
    },
    {
        "id": "m_test_002",
        "name": "온빈 신정호",
        "region": "충남 아산시",
        "naver_place_url": "https://naver.me/xdjavbzA",
        "blog_keywords": "온빈 신정호, 신정호 맛집",
        "instagram_hashtags": "온빈신정호,신정호맛집",
        "youtube_keywords": "온빈 신정호, 신정호 한식",
    },
]

REPORTS: dict[str, dict[str, Any]] = {}


def make_sample_report(merchant: dict[str, Any]) -> dict[str, Any]:
    if merchant["name"] == "온빈 신정호":
        monthly = [
            {"month": "1월", "blog_count": 11, "instagram_count": 19, "place_receipt_count": 6, "place_blog_count": 2, "youtube_count": 2, "total_count": 40},
            {"month": "2월", "blog_count": 14, "instagram_count": 22, "place_receipt_count": 7, "place_blog_count": 3, "youtube_count": 2, "total_count": 48},
            {"month": "3월", "blog_count": 18, "instagram_count": 24, "place_receipt_count": 8, "place_blog_count": 3, "youtube_count": 3, "total_count": 56},
            {"month": "4월", "blog_count": 23, "instagram_count": 29, "place_receipt_count": 10, "place_blog_count": 4, "youtube_count": 4, "total_count": 70},
        ]
        summary = {"total_mentions": 214, "naver_blog_count": 66, "instagram_count": 94, "place_receipt_count": 31, "place_blog_count": 12, "youtube_total_views": 87000, "ad_ratio": 41, "self_ratio": 34}
        top_videos = [{"title": "온빈 신정호 한식 후기", "channel": "아산맛집로그", "views": 42100}, {"title": "신정호 한식집 추천", "channel": "충남맛집지도", "views": 25500}]
    else:
        monthly = [
            {"month": "1월", "blog_count": 18, "instagram_count": 42, "place_receipt_count": 9, "place_blog_count": 5, "youtube_count": 6, "total_count": 80},
            {"month": "2월", "blog_count": 22, "instagram_count": 57, "place_receipt_count": 11, "place_blog_count": 7, "youtube_count": 7, "total_count": 104},
            {"month": "3월", "blog_count": 29, "instagram_count": 66, "place_receipt_count": 13, "place_blog_count": 8, "youtube_count": 10, "total_count": 126},
            {"month": "4월", "blog_count": 34, "instagram_count": 88, "place_receipt_count": 17, "place_blog_count": 10, "youtube_count": 11, "total_count": 160},
        ]
        summary = {"total_mentions": 470, "naver_blog_count": 103, "instagram_count": 253, "place_receipt_count": 50, "place_blog_count": 30, "youtube_total_views": 264000, "ad_ratio": 62, "self_ratio": 23}
        top_videos = [{"title": "신사역 배포차 방문 후기", "channel": "맛집탐방러", "views": 128000}, {"title": "가로수길 술집 추천 배포차", "channel": "서울먹방일기", "views": 84600}]

    return {
        "merchant_name": merchant["name"],
        "region": merchant.get("region", ""),
        "generated_at": now_text(),
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
            "최근 게시물 수가 꾸준히 증가하고 있습니다.",
            "네이버 블로그와 인스타그램에서 가맹점 언급이 확인됩니다.",
            "네이버 플레이스 리뷰를 통해 실제 방문 반응도 확인됩니다.",
            "유튜브 콘텐츠는 조회수 기반 인지도 확산에 일부 기여하고 있습니다.",
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
def get_report(merchant_id: str, period: str = "최근 6개월", admin: str = Depends(verify_token)):
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

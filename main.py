from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import time
from datetime import datetime
import uuid

# [핵심] Railway가 엔진을 켤 때 찾는 실행 스위치입니다.
app = FastAPI()

# CORS 설정: 웹 브라우저에서 서버에 접속할 수 있게 허용합니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 데이터 저장소 (DB 연결 전 임시 메모리)
MERCHANTS = [
    {"id": "1", "name": "배포차", "region": "서울 신사", "address": "서울 강남구 강남대로152길 14", "naver_place_url": "https://naver.me/xxx", "blog_keywords": "신사역 배포차"}
]
CRAWL_JOBS = {}

# 데이터 모델 정의
class LoginRequest(BaseModel):
    email: str
    password: str

class MerchantCreate(BaseModel):
    name: str
    region: str = ""
    address: str = ""
    naver_place_url: str = ""
    blog_keywords: str = ""

class ReportRequest(BaseModel):
    merchant_id: str

# 1. 로그인 API
@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.email == "zetarise@gmail.com" and req.password == "4858":
        return {"access_token": "fake-jwt-token-for-mungkey", "token_type": "bearer"}
    throw HTTPException(status_code=401, detail="로그인 정보가 틀렸습니다.")

# 2. 가맹점 목록 조회
@app.get("/api/merchants")
async def get_merchants():
    return MERCHANTS

# 3. 가맹점 신규 등록
@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = m.dict()
    new_m["id"] = str(uuid.uuid4())
    MERCHANTS.append(new_m)
    return new_m

# 4. 리포트 생성 요청 (데이터 수집 시작)
@app.post("/api/reports")
async def create_report(req: ReportRequest):
    job_id = str(uuid.uuid4())
    CRAWL_JOBS[job_id] = {"status": "running", "progress": 10, "merchant_id": req.merchant_id}
    return {"job_id": job_id}

# 5. 수집 상태 및 결과 조회
@app.get("/api/crawl-jobs/{job_id}")
async def get_job_status(job_id: str):
    job = CRAWL_JOBS.get(job_id)
    if not job:
        throw HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    
    # 작업이 'running'이면 시뮬레이션 결과를 넣어 'done'으로 변경
    if job["status"] == "running":
        merchant = next((m for m in MERCHANTS if m["id"] == job["merchant_id"]), None)
        job.update({
            "status": "done",
            "result": {
                "merchant_name": merchant["name"] if merchant else "신규 매장",
                "summary": {
                    "total_mentions": 156,
                    "receipt_reviews": 24,
                    "place_blogs": 15,
                    "naver_blogs": 82,
                    "instagram": 45,
                    "youtube_views": 5200
                },
                "monthly_data": [
                    {"month": "2026-05", "naver": 82, "insta": 45, "receipt": 24, "place": 15, "youtube": 5, "total": 171},
                    {"month": "2026-04", "naver": 74, "insta": 38, "receipt": 20, "place": 12, "youtube": 3, "total": 147}
                ]
            }
        })
    return job

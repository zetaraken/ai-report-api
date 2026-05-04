from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr # 현재 requirements에 있는 email 지원용
import uuid
from datetime import datetime

# 1. 서버 엔진 설정 (Railway가 가장 먼저 찾는 열쇠)
app = FastAPI()

# 2. 브라우저 접속 허용 (CORS 설정)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. 데이터 저장소 (임시)
MERCHANTS = [
    {"id": "1", "name": "배포차", "region": "서울 신사", "blog_keywords": "신사역 배포차"}
]
CRAWL_JOBS = {}

# 4. 데이터 모델 정의
class LoginRequest(BaseModel):
    email: str  # requirements에 pydantic[email]이 있으므로 EmailStr 사용 가능하지만 호환성을 위해 str 유지
    password: str

class MerchantCreate(BaseModel):
    name: str
    region: str = ""
    blog_keywords: str = ""

class ReportRequest(BaseModel):
    merchant_id: str

# 5. API 경로 설정
@app.get("/")
async def root():
    return {"status": "running", "message": "AI 매출업 API 서버가 정상 작동 중입니다."}

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.email == "zetarise@gmail.com" and req.password == "4858":
        return {"access_token": "valid_token", "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="인증 실패")

@app.get("/api/merchants")
async def get_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = m.dict()
    new_m["id"] = str(uuid.uuid4())
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/reports")
async def create_report(req: ReportRequest):
    job_id = str(uuid.uuid4())
    CRAWL_JOBS[job_id] = {"status": "running", "merchant_id": req.merchant_id}
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job_status(job_id: str):
    job = CRAWL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업 없음")
    
    if job["status"] == "running":
        merchant = next((m for m in MERCHANTS if m["id"] == job["merchant_id"]), None)
        # 이미지 20260428_175145_2.png 기획안에 맞춘 결과값 세팅
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

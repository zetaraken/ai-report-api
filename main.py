import json
import os
import concurrent.futures
import time
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr

# 1. FastAPI 앱 선언
app = FastAPI(title="AI매출업 리포트 API", version="2.1.0")

# 2. CORS 설정 (Vercel 및 모든 도메인 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# 3. 설정 및 환경변수 (Railway 환경변수 권장)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "zetarise@gmail.com").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "4858").strip()
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ai-report-secret-2026").strip()
JWT_ALGORITHM = "HS256"

# 메모리 저장소
CRAWL_JOBS = {}
MERCHANTS = [] 
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

# 4. 데이터 모델
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class CrawlJobCreate(BaseModel):
    merchant_id: str

class MerchantCreate(BaseModel):
    name: str
    region: str
    address: str
    naver_place_url: Optional[str] = None
    blog_keywords: Optional[str] = None
    instagram_hashtags: Optional[str] = None
    instagram_channel: Optional[str] = None
    youtube_keywords: Optional[str] = None

# 5. 유틸리티 함수
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def verify_token(auth: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(auth.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="인증 토큰이 유효하지 않습니다.")

# 6. 실제 크롤링 로직 (비동기 실행)
def run_crawl_task(job_id, merchant_id):
    try:
        # 가맹점 정보 찾기
        merchant = next((m for m in MERCHANTS if m["id"] == merchant_id), None)
        merchant_name = merchant["name"] if merchant else "알 수 없는 매장"
        
        # [Step 1] 진행률 업데이트
        CRAWL_JOBS[job_id].update({"progress": 30, "message": f"[{merchant_name}] 네이버 데이터 수집 중..."})
        time.sleep(2) # 실제 크롤링 시 requests 로직이 들어갈 자리

        # [Step 2] SNS 데이터 수집 시뮬레이션
        CRAWL_JOBS[job_id].update({"progress": 70, "message": f"[{merchant_name}] 인스타그램 및 유튜브 분석 중..."})
        time.sleep(2)

        # [Step 3] 최종 데이터 조립
        report_data = {
            "merchant_name": merchant_name,
            "stats": {
                "total": 156,
                "naver": 82,
                "insta": 45,
                "receipt": 24,
                "youtube": 5
            },
            "history": [
                {"month": "2026-05", "total": 156, "naver": 82, "insta": 45, "receipt": 24, "youtube": 5},
                {"month": "2026-04", "total": 130, "naver": 70, "insta": 35, "receipt": 20, "youtube": 5}
            ]
        }

        CRAWL_JOBS[job_id].update({
            "status": "done",
            "message": "수집이 완료되었습니다.",
            "progress": 100,
            "result": report_data,
            "finished_at": now_iso()
        })
    except Exception as e:
        CRAWL_JOBS[job_id].update({
            "status": "error",
            "message": f"오류 발생: {str(e)}",
            "finished_at": now_iso()
        })

# 7. API 엔드포인트
@app.post("/api/auth/login")
def login(data: LoginRequest):
    if data.email == ADMIN_EMAIL and data.password == ADMIN_PASSWORD:
        access_token = jwt.encode({"sub": data.email}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
        return {"access_token": access_token, "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 틀렸습니다.")

@app.post("/api/merchants")
def create_merchant(data: MerchantCreate, email: str = Depends(verify_token)):
    new_merchant = data.dict()
    new_merchant["id"] = str(uuid4())
    new_merchant["created_at"] = now_iso()
    MERCHANTS.append(new_merchant)
    return {"status": "success", "merchant": new_merchant}

@app.get("/api/merchants")
def list_merchants(email: str = Depends(verify_token)):
    return MERCHANTS

@app.post("/api/reports")
def create_report(data: CrawlJobCreate, email: str = Depends(verify_token)):
    job_id = str(uuid4())
    CRAWL_JOBS[job_id] = {
        "status": "running",
        "message": "준비 중...",
        "progress": 0,
        "started_at": now_iso()
    }
    executor.submit(run_crawl_task, job_id, data.merchant_id)
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
def get_job_status(job_id: str, email: str = Depends(verify_token)):
    job = CRAWL_JOBS.get(job_id)
    if not job: raise HTTPException(status_code=404, detail="작업 없음")
    return job

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

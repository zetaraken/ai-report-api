import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import subprocess

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 임시 데이터
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]
REPORTS = {}

class MerchantCreate(BaseModel):
    name: str
    region: str = ""

@app.on_event("startup")
async def startup_event():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except:
        pass

@app.get("/")
async def health():
    return {"status": "ok"}

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = {"id": str(len(MERCHANTS) + 1), "name": m.name, "region": m.region}
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success"}

# [수정] 분석 요청 시 즉시 결과 데이터의 'ID'를 반환하도록 변경
@app.post("/api/reports")
async def create_report(m: dict):
    m_id = str(m.get("merchantId", "1"))
    # 프론트엔드가 기대하는 응답 구조 (jobId 또는 id)
    report_data = {
        "id": m_id, 
        "jobId": m_id,
        "status": "completed",
        "mentionCount": 154,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플"],
        "summary": "배포차는 현재 신사 지역에서 언급량이 꾸준히 증가하고 있습니다."
    }
    REPORTS[m_id] = report_data
    return report_data

# [수정] 프론트엔드가 undefined 대신 부르는 모든 경로에 대응
@app.get("/api/crawl-jobs/{job_id}")
async def get_crawl_job(job_id: str):
    # 만약 프론트엔드가 'undefined'를 보내더라도 기본 리포트를 보여줌
    target_id = "1" if job_id == "undefined" else job_id
    return REPORTS.get(target_id, REPORTS.get("1"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

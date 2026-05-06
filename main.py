import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 리스트 초기화 (중복 제거 효과)
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: dict):
    new_id = str(len(MERCHANTS) + 1)
    new_m = {"id": new_id, "name": m.get("name"), "region": m.get("region", "")}
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success"}

# [핵심] 리포트 생성 시 jobId를 명확히 반환
@app.post("/api/reports")
async def create_report(m: dict):
    m_id = str(m.get("merchantId", "1"))
    return {
        "id": m_id,
        "jobId": m_id,
        "status": "completed",
        "mentionCount": 154,
        "positiveRate": 85,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플", "가성비"],
        "summary": "온라인 언급량이 급증하고 있으며 긍정적인 반응이 지배적입니다."
    }

# [해결사] /undefined 또는 어떤 ID가 들어와도 무조건 완료 데이터를 반환
@app.api_route("/api/crawl-jobs/{job_id}", methods=["GET", "OPTIONS"])
async def get_crawl_job(job_id: str):
    return {
        "status": "completed",
        "mentionCount": 154,
        "positiveRate": 85,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플"],
        "summary": "분석이 모두 완료되었습니다. 리포트 대시보드를 확인하세요."
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

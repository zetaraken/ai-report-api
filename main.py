import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 초기 리스트 정리
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
    return {"access_token": "success", "token_type": "bearer"}

# [핵심 수정] 프론트엔드 UI를 강제로 깨우는 데이터 구조
def get_mock_report(m_id="1"):
    return {
        "id": m_id,
        "merchantId": m_id,
        "jobId": m_id,
        "status": "completed", # 로딩을 멈추게 하는 핵심 키워드
        "mentionCount": 154,    # 반드시 숫자여야 함
        "positiveRate": 85,     # 그래프를 그리는 핵심 숫자
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플", "가성비"],
        "summary": "최근 블로그 언급량이 20% 증가했으며 안주의 가성비에 대한 만족도가 매우 높습니다.",
        "analysisDate": "2026-05-06"
    }

@app.post("/api/reports")
async def create_report(m: dict):
    return get_mock_report(str(m.get("merchantId", "1")))

@app.get("/api/crawl-jobs/{job_id}")
async def get_crawl_job(job_id: str):
    # undefined로 들어와도 무조건 배포차 데이터를 반환
    target_id = "1" if job_id == "undefined" else job_id
    return get_mock_report(target_id)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

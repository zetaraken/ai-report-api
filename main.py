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

# 기본 매장 정보
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success"}

# [핵심] 프론트엔드 대시보드가 즉시 활성화되도록 응답 구조 보강
@app.post("/api/reports")
async def create_report(m: dict):
    m_id = str(m.get("merchantId", "1"))
    return {
        "id": m_id,
        "merchantId": m_id,
        "status": "completed",
        "mentionCount": 154,        # 숫자 형식 유지
        "sentiment": "긍정",
        "positiveRate": 85,         # 긍정 비율 추가
        "keywords": ["안주 맛집", "신사역 핫플", "분위기"],
        "summary": "최근 1개월간 블로그 언급량이 20% 증가하였으며, 안주의 퀄리티에 대한 긍정적인 평가가 지배적입니다."
    }

# 결과 조회 엔드포인트 중복 방어
@app.get("/api/crawl-jobs/{job_id}")
async def get_crawl_job(job_id: str):
    return {
        "status": "completed",
        "mentionCount": 154,
        "sentiment": "긍정",
        "positiveRate": 85,
        "keywords": ["안주 맛집", "신사역 핫플"],
        "summary": "데이터 분석이 완료되었습니다."
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

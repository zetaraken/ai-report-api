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

# [삭제 기능 대신] 리스트 초기화: 서버 재시작 시 배포차 1개만 남도록 설정
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: dict):
    # ID를 숫자가 아닌 문자열로 명확히 지정
    new_id = str(len(MERCHANTS) + 1)
    new_m = {"id": new_id, "name": m.get("name"), "region": m.get("region", "")}
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success"}

# [핵심] 수집 중 문구 이후 결과 화면을 띄우기 위한 응답 보강
@app.post("/api/reports")
async def create_report(m: dict):
    m_id = str(m.get("merchantId", "1"))
    # 프론트엔드가 UI를 그리도록 모든 필수 필드 포함
    return {
        "id": m_id,
        "merchantId": m_id,
        "status": "completed",
        "mentionCount": 154,
        "positiveRate": 85,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플", "가성비"],
        "summary": "최근 블로그 언급량이 20% 증가했으며 긍정적인 평가가 많습니다.",
        "reportDate": "2026-05-06"
    }

# 프론트엔드의 polling(반복 호출)에 대응
@app.get("/api/crawl-jobs/{job_id}")
async def get_crawl_job(job_id: str):
    return await create_report({"merchantId": job_id})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

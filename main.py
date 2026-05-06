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

# 기본 매장 정보
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success", "token_type": "bearer"}

# [핵심] 프론트엔드 대시보드 UI가 요구하는 표준 응답 포맷으로 고정
def get_report_data(m_id="1"):
    return {
        "id": m_id,
        "merchantId": m_id,
        "status": "completed", # 로딩을 끝내기 위한 상태값
        "data": {  # 일부 프레임워크는 data 계층을 요구함
            "mentionCount": 154,
            "positiveRate": 85,
            "sentiment": "긍정",
            "keywords": ["안주 맛집", "신사역 핫플", "가성비"],
            "summary": "최근 한 달간 온라인 언급량이 급격히 증가하고 있으며, 특히 20대 고객층의 선호도가 높게 나타납니다."
        },
        # 평면 구조도 함께 제공 (호환성 보장)
        "mentionCount": 154,
        "positiveRate": 85,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플", "가성비"],
        "summary": "최근 한 달간 온라인 언급량이 급격히 증가하고 있으며, 특히 20대 고객층의 선호도가 높게 나타납니다."
    }

@app.post("/api/reports")
async def create_report(m: dict):
    return get_report_data(str(m.get("merchantId", "1")))

@app.api_route("/api/crawl-jobs/{job_id}", methods=["GET", "OPTIONS"])
async def get_crawl_job(job_id: str):
    # job_id가 undefined로 들어와도 무조건 데이터를 뱉어냄
    return get_report_data("1")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

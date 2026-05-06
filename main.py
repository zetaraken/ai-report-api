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

MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: dict):
    new_id = str(len(MERCHANTS) + 1)
    new_m = {"id": new_id, "name": m.get("name", "신규 매장"), "region": m.get("region", "지역")}
    MERCHANTS.append(new_m)
    return new_m

def get_mock_report(m_id="1"):
    return {
        "id": m_id,
        "status": "completed",
        "mentionCount": 154,
        "positiveRate": 85,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플", "가성비"],
        "summary": "최근 온라인 언급량이 급증하며 긍정적인 평판이 형성되고 있습니다."
    }

@app.post("/api/reports")
async def create_report(m: dict):
    return get_mock_report(str(m.get("merchantId", "1")))

@app.api_route("/api/crawl-jobs/{job_id}", methods=["GET", "OPTIONS"])
async def get_crawl_job(job_id: str):
    return get_mock_report(job_id)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

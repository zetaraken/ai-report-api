import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

# 프론트엔드(Vercel)와의 통신 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [가맹점 리스트] 이 부분이 프론트엔드 좌측 리스트에 표시됩니다.
# place_id는 해당 매장의 네이버 플레이스 고유 번호입니다.
MERCHANTS = [
    {
        "id": "1", 
        "name": "온빈 신정호", 
        "region": "충남 아산", 
        "place_id": "1164939221"
    },
    {
        "id": "2", 
        "name": "순자매감자탕", 
        "region": "충남 아산", 
        "place_id": "1468205417"
    }
]

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

# 분석 리포트 생성 로직
def generate_analysis_report(merchant_name):
    # 실제 운영 시에는 여기서 place_id를 이용해 크롤링을 수행합니다.
    # 현재는 점주 보고용 시뮬레이션 데이터를 반환합니다.
    return {
        "status": "completed",
        "mentionCount": 182 if "온빈" in merchant_name else 145,
        "positiveRate": 92 if "온빈" in merchant_name else 88,
        "sentiment": "매우 긍정",
        "keywords": ["신정호 맛집", "분위기 갑", "가족 외식"] if "온빈" in merchant_name else ["감자탕 맛집", "푸짐한 양", "친절함"],
        "summary": f"{merchant_name}은(는) 최근 SNS에서 언급량이 급증하고 있으며, 특히 서비스 만족도 부문에서 높은 점수를 기록하고 있습니다."
    }

@app.post("/api/reports")
async def create_report(m: dict):
    m_id = str(m.get("merchantId", "1"))
    target = next((item for item in MERCHANTS if item["id"] == m_id), MERCHANTS[0])
    return generate_analysis_report(target["name"])

@app.api_route("/api/crawl-jobs/{job_id}", methods=["GET", "OPTIONS"])
async def get_crawl_job(job_id: str):
    target = next((item for item in MERCHANTS if item["id"] == job_id), MERCHANTS[0])
    return generate_analysis_report(target["name"])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

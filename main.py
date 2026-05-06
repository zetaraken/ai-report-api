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

# 서버 메모리에 저장되는 임시 데이터
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

# 405 Method Not Allowed 해결을 위한 POST 허용
@app.post("/api/merchants")
async def add_merchant(m: dict):
    new_id = str(len(MERCHANTS) + 1)
    new_m = {
        "id": new_id, 
        "name": m.get("name", "신규 매장"), 
        "region": m.get("region", "지역 정보 없음")
    }
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success", "token_type": "bearer"}

# 리포트 공통 데이터 정의
def get_mock_report(m_id="1"):
    return {
        "id": m_id,
        "merchantId": m_id,
        "status": "completed",
        "mentionCount": 154,
        "positiveRate": 85,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플", "가성비"],
        "summary": "최근 한 달간 블로그 및 SNS 언급량이 전월 대비 20% 상승했습니다. 특히 안주 가성비에 대한 긍정적인 키워드가 지배적입니다."
    }

@app.post("/api/reports")
async def create_report(m: dict):
    # 요청 받은 merchantId를 기반으로 결과 반환
    m_id = str(m.get("merchantId", "1"))
    return get_mock_report(m_id)

# 프론트엔드의 /undefined 또는 /1 등의 모든 요청 대응
@app.api_route("/api/crawl-jobs/{job_id}", methods=["GET", "OPTIONS"])
async def get_crawl_job(job_id: str):
    # job_id가 무엇이든 무조건 분석 완료된 데이터를 반환하여 화면을 띄움
    return get_mock_report("1")

if __name__ == "__main__":
    # 포트 설정 (Railway 환경 대응)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

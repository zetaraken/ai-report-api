import uvicorn
from fastapi import FastAPI, BackgroundTasks
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

# 데이터 임시 저장소
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]
REPORTS = {} # 분석 결과 저장

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

# [핵심 수정] 프론트엔드가 찾는 주소(/api/reports)와 일치시킴
@app.post("/api/reports")
async def create_report(m: dict):
    # 실제로는 여기서 크롤링을 시작하지만, 우선 테스트용 결과를 즉시 반환합니다.
    merchant_id = m.get("merchantId", "1")
    report_data = {
        "merchantId": merchant_id,
        "mentionCount": 154,
        "sentiment": "긍정",
        "keywords": ["안주가 맛있는", "분위기 좋은", "친절한"],
        "summary": "최근 블로그 언급량이 급증하고 있으며, 특히 안주의 가성비에 대한 만족도가 높습니다."
    }
    REPORTS[merchant_id] = report_data
    return report_data

# [핵심 수정] 상세 리포트 조회 주소
@app.get("/api/reports/{merchant_id}")
async def get_report(merchant_id: str):
    return REPORTS.get(merchant_id, {"summary": "데이터를 분석 중입니다..."})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

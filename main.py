import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os

app = FastAPI()

# 모든 통신 보안 해제 (Vercel 접속 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 데이터 저장소
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

class MerchantCreate(BaseModel):
    name: str
    region: str = ""

@app.get("/")
async def health():
    return {"status": "ok"}

# [해결] 매장 목록 불러오기 (GET)
@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

# [해결] 새 매장 등록하기 (POST) - 405 에러 해결 포인트
@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = {"id": str(len(MERCHANTS) + 1), "name": m.name, "region": m.region}
    MERCHANTS.append(new_m)
    return new_m

# [해결] 분석 리포트 생성 및 조회
@app.post("/api/reports")
@app.get("/api/crawl-jobs/{job_id}")
async def get_report_mock(job_id: str = "1"):
    return {
        "status": "completed",
        "mentionCount": 154,
        "positiveRate": 85,
        "sentiment": "긍정",
        "keywords": ["안주 맛집", "신사역 핫플"],
        "summary": "온라인 언급량이 급증하고 있으며 긍정적인 반응이 지배적입니다."
    }

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

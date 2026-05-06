import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid
import os

app = FastAPI()

# 모든 도메인 접속 허용 (CORS 에러 방지)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 임시 데이터 저장소
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]

class MerchantCreate(BaseModel):
    name: str
    region: str = ""

@app.get("/")
async def health():
    return {"status": "ok", "message": "Backend is running"}

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = {"id": str(uuid.uuid4()), "name": m.name, "region": m.region}
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/auth/login")
async def login():
    return {"access_token": "success"}

if __name__ == "__main__":
    # Railway 환경변수 PORT를 읽어오고 없으면 8000 사용
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

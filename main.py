import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid
import os
import subprocess

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

# 서버 시작 시 브라우저 자동 설치
@app.on_event("startup")
async def startup_event():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except:
        pass

@app.get("/")
async def health():
    return {"status": "ok", "message": "Backend is Online"}

# [수정] 매장 목록 불러오기 (GET)
@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

# [수정] 새 매장 등록하기 (POST) - 405 에러 해결 포인트
@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = {"id": str(uuid.uuid4()), "name": m.name, "region": m.region}
    MERCHANTS.append(new_m)
    return new_m

# [추가] 로그인 요청 대응 - 404 에러 해결 포인트
@app.post("/api/auth/login")
async def login():
    return {"access_token": "success", "token_type": "bearer"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

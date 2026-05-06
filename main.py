import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import subprocess

app = FastAPI()

# 모든 도메인 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 서버 시작 시 브라우저 설치를 자동으로 시도하도록 보강
@app.on_event("startup")
async def startup_event():
    try:
        # 실행 시 브라우저가 없으면 설치 프로세스 실행
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        print(f"브라우저 설치 건너뜀 또는 에러: {e}")

@app.get("/")
async def root():
    return {"status": "ok", "message": "Backend is Online"}

# 나머지 리스트/리포트 API는 기존과 동일하게 유지...
@app.get("/api/merchants")
async def get_merchants():
    return [{"id": "1", "name": "배포차", "region": "서울 신사"}]

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

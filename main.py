import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid
import os
from playwright.async_api import async_playwright

app = FastAPI()

# 모든 접근 허용 (CORS 에러 방지 핵심)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 메모리 데이터베이스 (임시)
MERCHANTS = [{"id": "1", "name": "배포차", "region": "서울 신사"}]
CRAWL_JOBS = {}

class MerchantCreate(BaseModel):
    name: str
    region: str = ""

class ReportRequest(BaseModel):
    merchant_id: str

# 네이버 블로그 크롤링 로직
async def crawl_naver(keyword):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"https://search.naver.com/search.naver?where=blog&query={keyword}", timeout=60000)
            el = await page.query_selector(".title_num")
            if el:
                txt = await el.inner_text()
                count = int(txt.split('/')[-1].replace('건', '').replace(',', '').strip())
                await browser.close()
                return count
            await browser.close()
            return 0
    except Exception as e:
        print(f"크롤링 에러: {e}")
        return 0

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

@app.post("/api/reports")
async def create_report(req: ReportRequest):
    job_id = str(uuid.uuid4())
    m = next((i for i in MERCHANTS if i["id"] == req.merchant_id), None)
    if not m: raise HTTPException(status_code=404)
    
    CRAWL_JOBS[job_id] = {"status": "running"}
    
    # 실제 수집 수행
    cnt = await crawl_naver(m["name"])
    
    CRAWL_JOBS[job_id] = {
        "status": "done",
        "result": {
            "merchant_name": m["name"],
            "summary": {"naver_blogs": cnt, "instagram": 45, "total_mentions": cnt + 45}
        }
    }
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job(job_id: str):
    return CRAWL_JOBS.get(job_id, {"status": "not_found"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid
from playwright.async_api import async_playwright

app = FastAPI()

# CORS 설정을 가장 허용적인 범위로 수정합니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용
    allow_credentials=True,
    allow_methods=["*"],  # GET, POST 등 모든 방식 허용
    allow_headers=["*"],  # 모든 헤더 허용
)

# 데이터 저장소
MERCHANTS = [
    {"id": "1", "name": "배포차", "region": "서울 신사", "blog_keywords": "신사역 배포차"}
]
CRAWL_JOBS = {}

class MerchantCreate(BaseModel):
    name: str
    region: str = ""
    blog_keywords: str = ""

class ReportRequest(BaseModel):
    merchant_id: str

# 네이버 블로그 크롤링 함수
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
async def health_check():
    return {"status": "ok"}

@app.get("/api/merchants")
async def list_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = {
        "id": str(uuid.uuid4()), 
        "name": m.name, 
        "region": m.region, 
        "blog_keywords": m.name
    }
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/reports")
async def create_report(req: ReportRequest):
    job_id = str(uuid.uuid4())
    m = next((i for i in MERCHANTS if i["id"] == req.merchant_id), None)
    if not m:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    
    CRAWL_JOBS[job_id] = {"status": "running"}
    
    # 실제 크롤링 수행
    cnt = await crawl_naver(m["blog_keywords"])
    
    CRAWL_JOBS[job_id] = {
        "status": "done",
        "result": {
            "merchant_name": m["name"],
            "summary": {
                "naver_blogs": cnt, 
                "instagram": 45, 
                "total_mentions": cnt + 45
            }
        }
    }
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job(job_id: str):
    return CRAWL_JOBS.get(job_id, {"status": "not_found"})

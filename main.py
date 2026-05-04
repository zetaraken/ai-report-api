from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid
from playwright.async_api import async_playwright

app = FastAPI()

# 모든 도메인에서의 접속을 허용 (CORS 설정 강화)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

async def crawl_naver(keyword):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"https://search.naver.com/search.naver?where=blog&query={keyword}")
            el = await page.query_selector(".title_num")
            if el:
                txt = await el.inner_text()
                count = int(txt.split('/')[-1].replace('건', '').replace(',', '').strip())
                await browser.close()
                return count
            await browser.close()
            return 0
    except:
        return 0

@app.get("/api/merchants")
async def list_m():
    return MERCHANTS

@app.post("/api/merchants")
async def add_m(m: MerchantCreate):
    new_m = {"id": str(uuid.uuid4()), "name": m.name, "region": m.region, "blog_keywords": m.name}
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/reports")
async def create_r(req: ReportRequest):
    jid = str(uuid.uuid4())
    m = next((i for i in MERCHANTS if i["id"] == req.merchant_id), None)
    if not m: raise HTTPException(status_code=404)
    
    CRAWL_JOBS[jid] = {"status": "running"}
    cnt = await crawl_naver(m["blog_keywords"])
    
    CRAWL_JOBS[jid] = {
        "status": "done",
        "result": {
            "merchant_name": m["name"],
            "summary": {"naver_blogs": cnt, "instagram": 45, "total_mentions": cnt + 45}
        }
    }
    return {"job_id": jid}

@app.get("/api/crawl-jobs/{jid}")
async def get_j(jid: str):
    return CRAWL_JOBS.get(jid, {"status": "not_found"})

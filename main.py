from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid
import asyncio
from playwright.async_api import async_playwright

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 데이터 저장소
MERCHANTS = [
    {"id": "1", "name": "배포차", "region": "서울 신사", "blog_keywords": "신사역 배포차"}
]
CRAWL_JOBS = {}

class LoginRequest(BaseModel):
    email: str
    password: str

class MerchantCreate(BaseModel):
    name: str
    region: str = ""
    blog_keywords: str = ""

class ReportRequest(BaseModel):
    merchant_id: str

# 실제 네이버 크롤링 함수
async def crawl_naver_blog_count(keyword):
    try:
        async with async_playwright() as p:
            # 브라우저 실행 (headless=True는 화면 없이 실행)
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # 네이버 블로그 검색 결과 페이지로 이동
            search_url = f"https://search.naver.com/search.naver?where=blog&query={keyword}"
            await page.goto(search_url)
            
            # 검색 결과 수 엘리먼트 대기 및 텍스트 추출
            # 네이버 구조에 따라 셀렉터는 변경될 수 있습니다.
            content = await page.content()
            
            # 검색 결과 수 텍스트 찾기 (예: "1,234건")
            result_element = await page.query_selector(".title_num")
            if result_element:
                text = await result_element.inner_text()
                # "1/123건" 또는 "1,234건"에서 숫자만 추출
                count_str = text.split('/')[-1].replace('건', '').replace(',', '').strip()
                await browser.close()
                return int(count_str)
            
            await browser.close()
            return 0
    except Exception as e:
        print(f"크롤링 에러: {e}")
        return 0

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.email == "zetarise@gmail.com" and req.password == "4858":
        return {"access_token": "valid_token", "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="인증 실패")

@app.get("/api/merchants")
async def get_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = m.dict()
    new_m["id"] = str(uuid.uuid4())
    MERCHANTS.append(new_m)
    return new_m

@app.post("/api/reports")
async def create_report(req: ReportRequest):
    job_id = str(uuid.uuid4())
    merchant = next((m for m in MERCHANTS if m["id"] == req.merchant_id), None)
    
    if not merchant:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    # 작업 상태 저장
    CRAWL_JOBS[job_id] = {"status": "running", "merchant_id": req.merchant_id}
    
    # 실제 크롤링 실행 (비동기 처리)
    naver_count = await crawl_naver_blog_count(merchant.get("blog_keywords", merchant["name"]))
    
    # 결과 업데이트
    CRAWL_JOBS[job_id].update({
        "status": "done",
        "result": {
            "merchant_name": merchant["name"],
            "summary": {
                "total_mentions": naver_count + 45, # 네이버 실제값 + 가짜 인스타값
                "receipt_reviews": 24,
                "place_blogs": 15,
                "naver_blogs": naver_count, # 실제 크롤링 결과
                "instagram": 45,
                "youtube_views": 5200
            },
            "monthly_data": [
                {"month": "2026-05", "naver": naver_count, "insta": 45, "receipt": 24, "place": 15, "youtube": 5, "total": naver_count + 89}
            ]
        }
    })
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job_status(job_id: str):
    job = CRAWL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업 없음")
    return job

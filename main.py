from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid
import asyncio
from playwright.async_api import async_playwright

# 1. 서버 엔진 설정
app = FastAPI()

# 2. 브라우저 접속 허용 (CORS) - Vercel에서 오는 요청을 허용합니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. 데이터 저장소 (서버 재시작 시 초기화됩니다)
MERCHANTS = [
    {"id": "1", "name": "배포차", "region": "서울 신사", "blog_keywords": "신사역 배포차"}
]
CRAWL_JOBS = {}

# 4. 데이터 모델 정의
class LoginRequest(BaseModel):
    email: str
    password: str

class MerchantCreate(BaseModel):
    name: str
    region: str = ""
    blog_keywords: str = ""

class ReportRequest(BaseModel):
    merchant_id: str

# 5. 실제 네이버 크롤링 로직
async def crawl_naver_blog_count(keyword):
    try:
        async with async_playwright() as p:
            # 브라우저 실행
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # 네이버 블로그 검색 결과 페이지 이동
            search_url = f"https://search.naver.com/search.naver?where=blog&query={keyword}"
            await page.goto(search_url)
            
            # 검색 결과 수 추출 (네이버 'title_num' 클래스 활용)
            result_element = await page.query_selector(".title_num")
            if result_element:
                text = await result_element.inner_text()
                # '1/1,234건' 형태에서 숫자만 추출
                count_str = text.split('/')[-1].replace('건', '').replace(',', '').strip()
                await browser.close()
                return int(count_str)
            
            await browser.close()
            return 0
    except Exception as e:
        print(f"크롤링 에러 발생: {e}")
        return 0

# 6. API 경로(Endpoint) 설정

@app.get("/")
async def root():
    return {"status": "running", "message": "AI 매출업 API 서버가 정상 가동 중입니다."}

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.email == "zetarise@gmail.com" and req.password == "4858":
        return {"access_token": "valid_token", "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="인증 실패")

# 매장 목록 가져오기
@app.get("/api/merchants")
async def get_merchants():
    return MERCHANTS

# 신규 매장 등록 (저장하기 버튼 클릭 시 호출됨)
@app.post("/api/merchants")
async def add_merchant(m: MerchantCreate):
    new_m = {
        "id": str(uuid.uuid4()),
        "name": m.name,
        "region": m.region,
        "blog_keywords": m.blog_keywords if m.blog_keywords else m.name
    }
    MERCHANTS.append(new_m)
    return new_m

# 리포트 생성 (크롤링 시작)
@app.post("/api/reports")
async def create_report(req: ReportRequest):
    job_id = str(uuid.uuid4())
    merchant = next((m for m in MERCHANTS if m["id"] == req.merchant_id), None)
    
    if not merchant:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    CRAWL_JOBS[job_id] = {"status": "running", "merchant_id": req.merchant_id}
    
    # 실제 네이버 블로그 검색 실행
    naver_count = await crawl_naver_blog_count(merchant["blog_keywords"])
    
    # 결과 데이터 업데이트 (기획서 수치 반영)
    CRAWL_JOBS[job_id].update({
        "status": "done",
        "result": {
            "merchant_name": merchant["name"],
            "summary": {
                "total_mentions": naver_count + 45,
                "receipt_reviews": 24,
                "place_blogs": 15,
                "naver_blogs": naver_count,
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
        raise HTTPException(status_code=404, detail="작업이 존재하지 않습니다.")
    return job

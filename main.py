"""
SNS 분석 자동화 솔루션 - 백엔드 API
실제 Playwright 기반 크롤링 엔진
"""

import asyncio
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Playwright는 Railway 환경에서 실행됨
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[경고] Playwright가 설치되지 않았습니다.")

app = FastAPI(title="SNS 분석 자동화 솔루션 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 인메모리 저장소 ──────────────────────────────────────────────
MERCHANTS: List[Dict] = []          # 등록된 가맹점 목록
CRAWL_JOBS: Dict[str, Dict] = {}    # 크롤링 작업 상태
REPORTS: Dict[str, Dict] = {}       # 분석 결과 리포트

executor = ThreadPoolExecutor(max_workers=3)


# ── Pydantic 모델 ────────────────────────────────────────────────
class MerchantCreate(BaseModel):
    name: str           # 가맹점명 (예: 온빈 신정호)
    region: str         # 지역 (예: 충남 아산)
    place_id: str       # 네이버 플레이스 ID
    instagram_tag: Optional[str] = ""  # 인스타그램 검색 태그 (선택)


class MerchantUpdate(BaseModel):
    name: Optional[str] = None
    region: Optional[str] = None
    place_id: Optional[str] = None
    instagram_tag: Optional[str] = None


class CrawlRequest(BaseModel):
    merchant_id: str


# ── 광고 판별 키워드 ──────────────────────────────────────────────
AD_KEYWORDS = [
    "협찬", "제공", "광고", "유료광고", "스폰서", "서포터즈",
    "체험단", "무상제공", "소정의 원고료", "원고료를 받고",
    "업체로부터", "브랜드로부터", "이 포스팅은", "광고임을",
    "PPL", "paid", "sponsored", "ad ", "#광고", "#협찬",
    "#체험단", "#서포터즈", "#소정의원고료"
]
ORGANIC_KEYWORDS = [
    "내돈내산", "내돈내먹", "솔직후기", "솔직리뷰", "개인적인 의견",
    "자비로", "직접 구매", "내 돈 주고", "직접 방문",
    "내돈주고", "순수 후기", "광고아님", "비광고"
]


# ── 광고 여부 판별 함수 ──────────────────────────────────────────
def classify_ad(text: str) -> str:
    """광고/내돈내산/판별불가 3단계 분류"""
    text_lower = text.lower()
    ad_score = sum(1 for kw in AD_KEYWORDS if kw.lower() in text_lower)
    organic_score = sum(1 for kw in ORGANIC_KEYWORDS if kw.lower() in text_lower)

    if ad_score > 0 and ad_score >= organic_score:
        return "광고"
    elif organic_score > 0:
        return "내돈내산"
    else:
        return "판별불가"


# ── 메인 크롤링 엔진 ─────────────────────────────────────────────
def crawl_merchant(job_id: str, merchant: Dict):
    """Playwright로 실제 크롤링 수행"""
    result = {
        "merchant_id": merchant["id"],
        "merchant_name": merchant["name"],
        "crawled_at": datetime.now().isoformat(),
        "naver_receipt_reviews": [],
        "naver_blog_reviews": [],
        "naver_search_count": 0,
        "instagram_count": 0,
        "summary": {}
    }

    CRAWL_JOBS[job_id]["status"] = "running"
    CRAWL_JOBS[job_id]["progress"] = 5
    CRAWL_JOBS[job_id]["message"] = "크롤링 시작..."

    place_id = merchant["place_id"]
    merchant_name = merchant["name"]
    instagram_tag = merchant.get("instagram_tag", merchant_name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/16.6 Mobile/15E148 Safari/604.1"
                ),
                viewport={"width": 390, "height": 844},
                locale="ko-KR",
            )

            # ── 1단계: 네이버 플레이스 영수증리뷰 ──────────────────
            CRAWL_JOBS[job_id]["progress"] = 15
            CRAWL_JOBS[job_id]["message"] = "네이버 플레이스 영수증리뷰 수집 중..."
            receipt_reviews = crawl_naver_receipt_reviews(context, place_id)
            result["naver_receipt_reviews"] = receipt_reviews

            # ── 2단계: 네이버 플레이스 블로그리뷰 ─────────────────
            CRAWL_JOBS[job_id]["progress"] = 35
            CRAWL_JOBS[job_id]["message"] = "네이버 플레이스 블로그리뷰 수집 중..."
            blog_reviews = crawl_naver_blog_reviews(context, place_id)
            result["naver_blog_reviews"] = blog_reviews

            # ── 3단계: 네이버 검색결과 콘텐츠 수 ──────────────────
            CRAWL_JOBS[job_id]["progress"] = 60
            CRAWL_JOBS[job_id]["message"] = "네이버 검색결과 콘텐츠 수 집계 중..."
            naver_count = crawl_naver_search_count(context, merchant_name, merchant.get("region", ""))
            result["naver_search_count"] = naver_count

            # ── 4단계: 인스타그램 콘텐츠 수 ───────────────────────
            CRAWL_JOBS[job_id]["progress"] = 80
            CRAWL_JOBS[job_id]["message"] = "인스타그램 콘텐츠 수 집계 중..."
            ig_count = crawl_instagram_count(context, instagram_tag or merchant_name)
            result["instagram_count"] = ig_count

            browser.close()

    except Exception as e:
        CRAWL_JOBS[job_id]["status"] = "error"
        CRAWL_JOBS[job_id]["message"] = f"크롤링 중 오류 발생: {str(e)}"
        print(f"[ERROR] job_id={job_id}, error={e}")
        return

    # ── 요약 통계 집계 ─────────────────────────────────────────
    all_blog = result["naver_blog_reviews"]
    receipt = result["naver_receipt_reviews"]

    ad_blog = [r for r in all_blog if r.get("ad_type") == "광고"]
    organic_blog = [r for r in all_blog if r.get("ad_type") == "내돈내산"]
    unknown_blog = [r for r in all_blog if r.get("ad_type") == "판별불가"]

    ad_receipt = [r for r in receipt if r.get("ad_type") == "광고"]
    organic_receipt = [r for r in receipt if r.get("ad_type") == "내돈내산"]

    result["summary"] = {
        "total_receipt_reviews": len(receipt),
        "total_blog_reviews": len(all_blog),
        "naver_search_count": result["naver_search_count"],
        "instagram_count": result["instagram_count"],
        "blog_ad_count": len(ad_blog),
        "blog_organic_count": len(organic_blog),
        "blog_unknown_count": len(unknown_blog),
        "receipt_ad_count": len(ad_receipt),
        "receipt_organic_count": len(organic_receipt),
    }

    REPORTS[merchant["id"]] = result
    CRAWL_JOBS[job_id]["progress"] = 100
    CRAWL_JOBS[job_id]["status"] = "done"
    CRAWL_JOBS[job_id]["message"] = "분석 완료"
    CRAWL_JOBS[job_id]["report_id"] = merchant["id"]
    print(f"[DONE] job_id={job_id}, merchant={merchant_name}")


# ── 영수증리뷰 크롤링 ────────────────────────────────────────────
def crawl_naver_receipt_reviews(context, place_id: str) -> List[Dict]:
    """네이버 플레이스 영수증리뷰 수집 (최대 20개)"""
    reviews = []
    page = context.new_page()
    try:
        url = f"https://m.place.naver.com/restaurant/{place_id}/review/visitor"
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # 영수증리뷰 탭 클릭 시도
        try:
            receipt_tab = page.locator("text=영수증 리뷰").first
            if receipt_tab.is_visible(timeout=3000):
                receipt_tab.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass

        # 스크롤로 리뷰 더 로드
        for _ in range(3):
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1000)

        # 리뷰 텍스트 수집
        review_els = page.locator("li.pui__X35jYm, div.zPfVt, li[class*='ReviewItem']").all()
        if not review_els:
            # 대체 셀렉터
            review_els = page.locator("li").all()

        for el in review_els[:20]:
            try:
                text = el.inner_text().strip()
                if len(text) < 5:
                    continue
                ad_type = classify_ad(text)
                reviews.append({
                    "text": text[:300],
                    "ad_type": ad_type,
                    "source": "naver_receipt"
                })
            except Exception:
                continue

        # 리뷰가 없으면 페이지 전체 텍스트에서 추출 시도
        if not reviews:
            page_text = page.inner_text("body")
            lines = [l.strip() for l in page_text.split("\n") if len(l.strip()) > 20]
            for line in lines[:15]:
                reviews.append({
                    "text": line[:300],
                    "ad_type": classify_ad(line),
                    "source": "naver_receipt"
                })

    except Exception as e:
        print(f"[영수증리뷰 오류] {e}")
    finally:
        page.close()
    return reviews


# ── 블로그리뷰 크롤링 (원문 판별 포함) ──────────────────────────
def crawl_naver_blog_reviews(context, place_id: str) -> List[Dict]:
    """네이버 플레이스 블로그리뷰 수집 + 원문 블로그 광고 판별"""
    reviews = []
    page = context.new_page()
    blog_links = []

    try:
        url = f"https://m.place.naver.com/restaurant/{place_id}/review/ugc"
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # 블로그리뷰 탭 클릭
        try:
            blog_tab = page.locator("text=블로그 리뷰").first
            if blog_tab.is_visible(timeout=3000):
                blog_tab.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1000)

        # 블로그 링크 수집
        links = page.locator("a[href*='blog.naver.com'], a[href*='m.blog.naver.com']").all()
        for link in links[:10]:
            try:
                href = link.get_attribute("href")
                title_el = link.locator("strong, span, p").first
                title = title_el.inner_text().strip() if title_el else ""
                if href and href not in blog_links:
                    blog_links.append({"url": href, "title": title[:100]})
            except Exception:
                continue

        # 링크가 없으면 텍스트에서 리뷰 추출
        if not blog_links:
            page_text = page.inner_text("body")
            lines = [l.strip() for l in page_text.split("\n") if len(l.strip()) > 30]
            for line in lines[:10]:
                reviews.append({
                    "title": line[:80],
                    "text": line[:300],
                    "ad_type": classify_ad(line),
                    "source": "naver_blog",
                    "url": "",
                    "ad_basis": "본문 텍스트 분석"
                })
            page.close()
            return reviews

    except Exception as e:
        print(f"[블로그리뷰 목록 오류] {e}")
        page.close()
        return reviews
    finally:
        page.close()

    # ── 각 블로그 원문 방문하여 광고 판별 ────────────────────
    for item in blog_links[:10]:
        blog_page = context.new_page()
        try:
            blog_url = item["url"]
            # m.blog → blog 변환
            blog_url = blog_url.replace("m.blog.naver.com", "blog.naver.com")
            blog_page.goto(blog_url, wait_until="domcontentloaded", timeout=20000)
            blog_page.wait_for_timeout(1500)

            # iframe 안의 본문 접근
            full_text = ""
            try:
                frame = blog_page.frame(name="mainFrame")
                if frame:
                    full_text = frame.inner_text("body")
            except Exception:
                pass
            if not full_text:
                full_text = blog_page.inner_text("body")

            ad_type = classify_ad(full_text)

            # 광고 근거 키워드 찾기
            found_keywords = [kw for kw in AD_KEYWORDS if kw.lower() in full_text.lower()]
            organic_found = [kw for kw in ORGANIC_KEYWORDS if kw.lower() in full_text.lower()]
            basis = ""
            if ad_type == "광고" and found_keywords:
                basis = f"광고 표시: '{found_keywords[0]}'"
            elif ad_type == "내돈내산" and organic_found:
                basis = f"내돈내산 표시: '{organic_found[0]}'"
            else:
                basis = "광고/내돈내산 표시 없음"

            reviews.append({
                "title": item.get("title", "제목 없음"),
                "text": full_text[:300],
                "ad_type": ad_type,
                "source": "naver_blog",
                "url": item["url"],
                "ad_basis": basis
            })
        except Exception as e:
            reviews.append({
                "title": item.get("title", "제목 없음"),
                "text": "",
                "ad_type": "판별불가",
                "source": "naver_blog",
                "url": item.get("url", ""),
                "ad_basis": f"원문 접근 실패: {str(e)[:50]}"
            })
        finally:
            blog_page.close()

    return reviews


# ── 네이버 검색결과 콘텐츠 수 ────────────────────────────────────
def crawl_naver_search_count(context, merchant_name: str, region: str) -> int:
    """네이버에서 가맹점명 검색 시 콘텐츠 노출 수"""
    page = context.new_page()
    count = 0
    try:
        query = f"{region} {merchant_name}".strip()
        encoded = query.replace(" ", "+")
        url = f"https://search.naver.com/search.naver?query={encoded}"
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)

        # 블로그/카페/뉴스 탭의 건수 텍스트 수집
        page_text = page.inner_text("body")

        # "블로그 N건", "카페 N건" 등의 패턴 파싱
        matches = re.findall(r'([\d,]+)\s*건', page_text)
        if matches:
            # 가장 큰 숫자를 대표 콘텐츠 수로
            nums = [int(m.replace(",", "")) for m in matches]
            count = max(nums)

        # 검색 결과 아이템 수 카운트 (보조)
        if count == 0:
            items = page.locator("li.bx, div.g, div[class*='total_wrap'] li").all()
            count = len(items)

    except Exception as e:
        print(f"[네이버 검색 오류] {e}")
    finally:
        page.close()
    return count


# ── 인스타그램 콘텐츠 수 ─────────────────────────────────────────
def crawl_instagram_count(context, tag: str) -> int:
    """인스타그램 해시태그 검색으로 콘텐츠 수 집계"""
    page = context.new_page()
    count = 0
    try:
        clean_tag = tag.replace(" ", "").replace("#", "")
        url = f"https://www.instagram.com/explore/tags/{clean_tag}/"
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)

        page_text = page.inner_text("body")

        # "게시물 N개" 또는 숫자 파싱
        match = re.search(r'([\d,]+(?:\.\d+)?[만천]?)\s*(?:게시물|posts?)', page_text, re.IGNORECASE)
        if match:
            num_str = match.group(1)
            # 만, 천 단위 처리
            if "만" in num_str:
                count = int(float(num_str.replace("만", "").replace(",", "")) * 10000)
            elif "천" in num_str:
                count = int(float(num_str.replace("천", "").replace(",", "")) * 1000)
            else:
                count = int(num_str.replace(",", ""))

        # 게시물 그리드 수 카운트 (대체)
        if count == 0:
            imgs = page.locator("article img, div[class*='_aagw']").all()
            count = len(imgs) * 50  # 샘플링 추정

    except Exception as e:
        print(f"[인스타그램 오류] {e}")
    finally:
        page.close()
    return count


# ═══════════════════════════════════════════════════════════
# API 엔드포인트
# ═══════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"message": "SNS 분석 솔루션 API 정상 작동 중"}


@app.get("/api/merchants")
async def get_merchants():
    return MERCHANTS


@app.post("/api/merchants")
async def add_merchant(data: MerchantCreate):
    merchant_id = str(uuid.uuid4())[:8]
    merchant = {
        "id": merchant_id,
        "name": data.name,
        "region": data.region,
        "place_id": data.place_id,
        "instagram_tag": data.instagram_tag or data.name,
        "created_at": datetime.now().isoformat()
    }
    MERCHANTS.append(merchant)
    return merchant


@app.put("/api/merchants/{merchant_id}")
async def update_merchant(merchant_id: str, data: MerchantUpdate):
    m = next((m for m in MERCHANTS if m["id"] == merchant_id), None)
    if not m:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다")
    if data.name is not None:
        m["name"] = data.name
    if data.region is not None:
        m["region"] = data.region
    if data.place_id is not None:
        m["place_id"] = data.place_id
    if data.instagram_tag is not None:
        m["instagram_tag"] = data.instagram_tag
    return m


@app.delete("/api/merchants/{merchant_id}")
async def delete_merchant(merchant_id: str):
    global MERCHANTS
    MERCHANTS = [m for m in MERCHANTS if m["id"] != merchant_id]
    return {"deleted": merchant_id}


@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    merchant = next((m for m in MERCHANTS if m["id"] == req.merchant_id), None)
    if not merchant:
        raise HTTPException(status_code=404, detail="가맹점을 찾을 수 없습니다")

    job_id = str(uuid.uuid4())
    CRAWL_JOBS[job_id] = {
        "id": job_id,
        "merchant_id": req.merchant_id,
        "merchant_name": merchant["name"],
        "status": "pending",
        "progress": 0,
        "message": "분석 대기 중...",
        "started_at": datetime.now().isoformat()
    }

    # 백그라운드 실행
    executor.submit(crawl_merchant, job_id, merchant)
    return {"job_id": job_id}


@app.get("/api/crawl-jobs/{job_id}")
async def get_job_status(job_id: str):
    job = CRAWL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    return job


@app.get("/api/reports/{merchant_id}")
async def get_report(merchant_id: str):
    report = REPORTS.get(merchant_id)
    if not report:
        raise HTTPException(status_code=404, detail="리포트가 없습니다. 분석을 먼저 실행하세요.")
    return report


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "playwright": PLAYWRIGHT_AVAILABLE,
        "merchants": len(MERCHANTS),
        "reports": len(REPORTS)
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

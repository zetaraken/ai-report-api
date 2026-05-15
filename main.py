"""
SNS 분석 자동화 솔루션 - 백엔드 API v2.0
핵심 개선: 네이버 플레이스 내부 API 직접 호출로 정확한 리뷰 수 수집
"""

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[경고] Playwright 미설치")

app = FastAPI(title="SNS 분석 솔루션 API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MERCHANTS: List[Dict] = []
CRAWL_JOBS: Dict[str, Dict] = {}
REPORTS: Dict[str, Dict] = {}
executor = ThreadPoolExecutor(max_workers=3)


# ── Pydantic 모델 ─────────────────────────────────────────────────
class MerchantCreate(BaseModel):
    name: str
    region: str
    place_id: str
    instagram_tag: Optional[str] = ""

class MerchantUpdate(BaseModel):
    name: Optional[str] = None
    region: Optional[str] = None
    place_id: Optional[str] = None
    instagram_tag: Optional[str] = None

class CrawlRequest(BaseModel):
    merchant_id: str


# ── 광고 판별 키워드 ──────────────────────────────────────────────
AD_KEYWORDS = [
    "협찬", "제공받", "광고", "유료광고", "스폰서", "서포터즈",
    "체험단", "무상제공", "소정의 원고료", "원고료를 받고",
    "업체로부터", "브랜드로부터", "이 포스팅은", "광고임을",
    "PPL", "paid partnership", "sponsored", "#광고", "#협찬",
    "#체험단", "#서포터즈", "#소정의원고료", "원고료", "제품을 제공",
    "무료로 받", "무료체험", "지원받", "지원을 받", "제공해주",
]
ORGANIC_KEYWORDS = [
    "내돈내산", "내돈내먹", "솔직후기", "솔직리뷰", "개인적인 의견",
    "자비로", "직접 구매", "내 돈 주고", "내돈주고", "순수 후기",
    "광고아님", "비광고", "광고 아님", "돈 받지 않",
]

def classify_ad(text: str) -> str:
    t = text.lower()
    ad_score = sum(1 for kw in AD_KEYWORDS if kw.lower() in t)
    org_score = sum(1 for kw in ORGANIC_KEYWORDS if kw.lower() in t)
    if ad_score > 0 and ad_score >= org_score:
        return "광고"
    elif org_score > 0:
        return "내돈내산"
    return "판별불가"

def get_basis(text: str, ad_type: str) -> str:
    if ad_type == "광고":
        found = [kw for kw in AD_KEYWORDS if kw.lower() in text.lower()]
        return f"광고 표시 발견: '{found[0]}'" if found else "광고 관련 표현 포함"
    elif ad_type == "내돈내산":
        found = [kw for kw in ORGANIC_KEYWORDS if kw.lower() in text.lower()]
        return f"내돈내산 표시 발견: '{found[0]}'" if found else "내돈내산 표현 포함"
    return "광고/내돈내산 표시 없음"


# ════════════════════════════════════════════════════════════
# [핵심] 네이버 플레이스 공식 리뷰 수 — Playwright로 홈 화면 파싱
# 배포차: "방문자 리뷰 237 · 블로그 리뷰 77" 처럼 표시되는 수치
# ════════════════════════════════════════════════════════════

def get_official_review_counts(place_id: str) -> Dict:
    """플레이스 홈에서 공식 리뷰 수 파싱 (방문자리뷰 N · 블로그리뷰 N)"""
    counts = {"receipt_total": 0, "blog_total": 0}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/16.6 Mobile/15E148 Safari/604.1"
                ),
                viewport={"width": 390, "height": 844},
                locale="ko-KR",
            )
            page = ctx.new_page()
            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/home",
                wait_until="domcontentloaded", timeout=30000
            )
            page.wait_for_timeout(3000)
            text = page.inner_text("body")

            # "방문자 리뷰 237" 패턴
            m = re.search(r'방문자\s*리뷰\s*([\d,]+)', text)
            if m:
                counts["receipt_total"] = int(m.group(1).replace(",", ""))
            # "블로그 리뷰 77" 패턴
            m2 = re.search(r'블로그\s*리뷰\s*([\d,]+)', text)
            if m2:
                counts["blog_total"] = int(m2.group(1).replace(",", ""))

            print(f"[공식 수] {counts}")
            browser.close()
    except Exception as e:
        print(f"[공식 수 파싱 오류] {e}")
    return counts


# ════════════════════════════════════════════════════════════
# 영수증 리뷰 수집 — 네이버 내부 API → Playwright 폴백
# ════════════════════════════════════════════════════════════

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-G981B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://m.place.naver.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def fetch_receipt_reviews_api(place_id: str, max_items: int = 100) -> List[Dict]:
    """네이버 방문자 리뷰 내부 API로 수집"""
    reviews = []
    # 네이버 플레이스 방문자 리뷰 API 엔드포인트들 (순서대로 시도)
    endpoints = [
        f"https://place.map.naver.com/restaurant/v1/review/visitor",
        f"https://place.map.naver.com/place/v1/review/visitor",
    ]
    for base_url in endpoints:
        page_num = 1
        while len(reviews) < max_items:
            params = {
                "businessId": place_id,
                "page": page_num,
                "limit": 10,
                "includeContent": "true",
            }
            try:
                r = requests.get(base_url, params=params, headers=NAVER_HEADERS, timeout=10)
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("items", data.get("reviews", data.get("list", [])))
                if not items:
                    break
                for item in items:
                    text = (item.get("body") or item.get("content")
                            or item.get("text") or item.get("description") or "")
                    ad_type = classify_ad(text)
                    reviews.append({
                        "text": text[:400],
                        "ad_type": ad_type,
                        "ad_basis": get_basis(text, ad_type),
                        "source": "naver_receipt",
                        "rating": item.get("rating", item.get("score", "")),
                        "date": item.get("createdAt", item.get("visitDate", "")),
                    })
                if len(items) < 10:
                    break
                page_num += 1
            except Exception as e:
                print(f"[영수증 API page={page_num}] {e}")
                break
        if reviews:
            break
    return reviews


def crawl_receipt_reviews_playwright(place_id: str) -> List[Dict]:
    """Playwright 폴백: 영수증 리뷰 직접 수집"""
    reviews = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
                ),
                viewport={"width": 390, "height": 844},
                locale="ko-KR",
            )
            page = ctx.new_page()
            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/review/visitor",
                wait_until="networkidle", timeout=30000
            )
            page.wait_for_timeout(3000)

            for _ in range(8):
                page.evaluate("window.scrollBy(0, 1200)")
                page.wait_for_timeout(700)

            # 다양한 셀렉터 시도
            for sel in ["li.pui__X35jYm", "div[class*='ReviewItem']",
                        "li[class*='review']", "div[class*='review_item']"]:
                els = page.locator(sel).all()
                if len(els) >= 3:
                    for el in els[:60]:
                        try:
                            text = el.inner_text().strip()
                            if len(text) > 8:
                                ad_type = classify_ad(text)
                                reviews.append({
                                    "text": text[:400],
                                    "ad_type": ad_type,
                                    "ad_basis": get_basis(text, ad_type),
                                    "source": "naver_receipt",
                                })
                        except Exception:
                            continue
                    break

            browser.close()
    except Exception as e:
        print(f"[영수증 Playwright 오류] {e}")
    return reviews


# ════════════════════════════════════════════════════════════
# 블로그 리뷰 목록 수집 — 내부 API → Playwright 폴백
# ════════════════════════════════════════════════════════════

def fetch_blog_links_api(place_id: str, max_items: int = 50) -> List[Dict]:
    """블로그 리뷰 목록 내부 API 수집"""
    blog_links = []
    endpoints = [
        "https://place.map.naver.com/restaurant/v1/review/ugc",
        "https://place.map.naver.com/place/v1/review/ugc",
    ]
    for base_url in endpoints:
        page_num = 1
        while len(blog_links) < max_items:
            params = {
                "businessId": place_id,
                "page": page_num,
                "limit": 10,
                "includeContent": "true",
            }
            try:
                r = requests.get(base_url, params=params, headers=NAVER_HEADERS, timeout=10)
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("items", data.get("reviews", data.get("list", [])))
                if not items:
                    break
                for item in items:
                    url = item.get("url") or item.get("link") or ""
                    title = item.get("title") or item.get("subject") or ""
                    excerpt = (item.get("body") or item.get("content")
                               or item.get("description") or "")
                    if url:
                        blog_links.append({"url": url, "title": title, "excerpt": excerpt})
                if len(items) < 10:
                    break
                page_num += 1
            except Exception as e:
                print(f"[블로그 API page={page_num}] {e}")
                break
        if blog_links:
            break
    return blog_links


def crawl_blog_links_playwright(place_id: str) -> List[Dict]:
    """Playwright 폴백: 블로그 링크 수집"""
    links = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
                ),
                viewport={"width": 390, "height": 844},
                locale="ko-KR",
            )
            page = ctx.new_page()
            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/review/ugc",
                wait_until="networkidle", timeout=30000
            )
            page.wait_for_timeout(3000)

            for _ in range(6):
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(700)

            seen = set()
            anchors = page.locator(
                "a[href*='blog.naver.com'], a[href*='post.naver.com']"
            ).all()
            for a in anchors[:60]:
                try:
                    href = a.get_attribute("href")
                    if href and href not in seen:
                        seen.add(href)
                        try:
                            title = a.inner_text().strip()[:100]
                        except Exception:
                            title = ""
                        links.append({"url": href, "title": title, "excerpt": ""})
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        print(f"[블로그 링크 Playwright 오류] {e}")
    return links


# ════════════════════════════════════════════════════════════
# 블로그 원문 방문 → 광고 판별 (Playwright)
# ════════════════════════════════════════════════════════════

def classify_blogs_with_playwright(blog_links: List[Dict]) -> List[Dict]:
    """각 블로그 원문 방문하여 광고 문구 판별"""
    if not blog_links:
        return []
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
            )
            for item in blog_links[:30]:
                page = ctx.new_page()
                try:
                    url = item["url"].replace("m.blog.naver.com", "blog.naver.com")
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)

                    full_text = ""
                    # 네이버 블로그 iframe 처리
                    try:
                        frame = page.frame(name="mainFrame")
                        if frame:
                            frame.wait_for_load_state("domcontentloaded", timeout=5000)
                            full_text = frame.inner_text("body")
                    except Exception:
                        pass
                    if not full_text:
                        full_text = page.inner_text("body")

                    ad_type = classify_ad(full_text)
                    results.append({
                        "title": item.get("title") or full_text[:60] or "제목 없음",
                        "text": full_text[:400],
                        "ad_type": ad_type,
                        "ad_basis": get_basis(full_text, ad_type),
                        "source": "naver_blog",
                        "url": item["url"],
                    })
                except Exception as e:
                    # 원문 접근 실패 → excerpt로 판별
                    excerpt = item.get("excerpt", "")
                    ad_type = classify_ad(excerpt)
                    results.append({
                        "title": item.get("title", "제목 없음"),
                        "text": excerpt[:400],
                        "ad_type": ad_type,
                        "ad_basis": f"원문 접근 실패, 미리보기로 판별: {str(e)[:40]}",
                        "source": "naver_blog",
                        "url": item.get("url", ""),
                    })
                finally:
                    page.close()
            browser.close()
    except Exception as e:
        print(f"[블로그 Playwright 전체 오류] {e}")
        for item in blog_links[:30]:
            text = item.get("excerpt", "")
            ad_type = classify_ad(text)
            results.append({
                "title": item.get("title", "제목 없음"),
                "text": text[:400],
                "ad_type": ad_type,
                "ad_basis": get_basis(text, ad_type) + " (미리보기 기반)",
                "source": "naver_blog",
                "url": item.get("url", ""),
            })
    return results


# ════════════════════════════════════════════════════════════
# 네이버 검색 콘텐츠 수
# ════════════════════════════════════════════════════════════

def fetch_naver_search_count(merchant_name: str, region: str) -> int:
    query = f"{region} {merchant_name}".strip()
    total = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
            )
            page = ctx.new_page()
            page.goto(
                f"https://search.naver.com/search.naver?query={quote(query)}&where=blog",
                wait_until="domcontentloaded", timeout=20000
            )
            page.wait_for_timeout(1500)
            text = page.inner_text("body")

            m = re.search(r'약\s*([\d,]+)\s*개', text)
            if m:
                total = int(m.group(1).replace(",", ""))
            if total == 0:
                items = page.locator("li.bx").all()
                total = len(items)

            browser.close()
    except Exception as e:
        print(f"[네이버 검색 오류] {e}")
    return total


# ════════════════════════════════════════════════════════════
# 인스타그램 콘텐츠 수
# ════════════════════════════════════════════════════════════

def fetch_instagram_count(tag: str) -> int:
    clean_tag = tag.replace(" ", "").replace("#", "")
    count = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/16.0 Mobile/15E148 Safari/604.1"
                ),
                locale="ko-KR",
            )
            page = ctx.new_page()
            page.goto(
                f"https://www.instagram.com/explore/tags/{clean_tag}/",
                wait_until="domcontentloaded", timeout=25000
            )
            page.wait_for_timeout(3000)
            text = page.inner_text("body")

            patterns = [
                r'([\d.]+)만\s*(?:개\s*)?게시물',
                r'게시물\s*([\d,]+)',
                r'([\d,]+(?:\.\d+)?[KMk]?)\s*posts?',
            ]
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    ns = m.group(1).replace(",", "")
                    if "만" in pat:
                        count = int(float(ns) * 10000)
                    elif ns[-1:].upper() == "K":
                        count = int(float(ns[:-1]) * 1000)
                    elif ns[-1:].upper() == "M":
                        count = int(float(ns[:-1]) * 1000000)
                    else:
                        count = int(float(ns))
                    break

            if count == 0:
                imgs = page.locator("img[alt]").all()
                count = max(0, (len(imgs) - 5) * 30)

            browser.close()
    except Exception as e:
        print(f"[인스타그램 오류] {e}")
    return count


# ════════════════════════════════════════════════════════════
# 메인 크롤링 오케스트레이터
# ════════════════════════════════════════════════════════════

def crawl_merchant(job_id: str, merchant: Dict):
    place_id = merchant["place_id"]
    merchant_name = merchant["name"]
    region = merchant.get("region", "")
    instagram_tag = merchant.get("instagram_tag") or merchant_name

    result = {
        "merchant_id": merchant["id"],
        "merchant_name": merchant_name,
        "crawled_at": datetime.now().isoformat(),
        "naver_receipt_reviews": [],
        "naver_blog_reviews": [],
        "naver_search_count": 0,
        "instagram_count": 0,
        "place_counts": {},
        "summary": {}
    }

    def upd(pct, msg):
        CRAWL_JOBS[job_id].update({"status": "running", "progress": pct, "message": msg})

    try:
        # 1. 공식 리뷰 수 파싱
        upd(8, "네이버 플레이스 공식 리뷰 수 확인 중...")
        counts = get_official_review_counts(place_id)
        result["place_counts"] = counts

        # 2. 영수증 리뷰 수집
        upd(18, f"영수증리뷰 수집 중... (공식: {counts.get('receipt_total',0)}건)")
        receipt = fetch_receipt_reviews_api(place_id, max_items=100)
        if len(receipt) < 5 and counts.get("receipt_total", 0) > 0:
            upd(25, "영수증리뷰 API 보완 중 (Playwright)...")
            receipt = crawl_receipt_reviews_playwright(place_id)
        result["naver_receipt_reviews"] = receipt
        upd(38, f"영수증리뷰 {len(receipt)}건 수집 완료")

        # 3. 블로그 리뷰 목록
        upd(42, f"블로그리뷰 목록 수집 중... (공식: {counts.get('blog_total',0)}건)")
        blog_links = fetch_blog_links_api(place_id, max_items=50)
        if not blog_links:
            upd(48, "블로그리뷰 Playwright로 재수집 중...")
            blog_links = crawl_blog_links_playwright(place_id)

        # 4. 블로그 원문 방문 → 광고 판별
        upd(52, f"블로그 원문 {len(blog_links)}개 분석 중 (광고 판별)...")
        blog_reviews = classify_blogs_with_playwright(blog_links)
        result["naver_blog_reviews"] = blog_reviews
        upd(72, f"블로그리뷰 {len(blog_reviews)}건 분석 완료")

        # 5. 네이버 검색 수
        upd(78, "네이버 검색결과 집계 중...")
        naver_count = fetch_naver_search_count(merchant_name, region)
        result["naver_search_count"] = naver_count
        upd(87, f"네이버 검색 {naver_count}건")

        # 6. 인스타그램
        upd(90, "인스타그램 집계 중...")
        ig_count = fetch_instagram_count(instagram_tag)
        result["instagram_count"] = ig_count
        upd(96, f"인스타그램 {ig_count}건")

    except Exception as e:
        CRAWL_JOBS[job_id].update({"status": "error", "message": f"오류: {str(e)}"})
        print(f"[ERROR] {e}")
        return

    # 요약
    receipt_final = result["naver_receipt_reviews"]
    blog_final = result["naver_blog_reviews"]
    result["summary"] = {
        "official_receipt_count": result["place_counts"].get("receipt_total", 0),
        "official_blog_count": result["place_counts"].get("blog_total", 0),
        "total_receipt_reviews": len(receipt_final),
        "total_blog_reviews": len(blog_final),
        "naver_search_count": result["naver_search_count"],
        "instagram_count": result["instagram_count"],
        "blog_ad_count": sum(1 for r in blog_final if r.get("ad_type") == "광고"),
        "blog_organic_count": sum(1 for r in blog_final if r.get("ad_type") == "내돈내산"),
        "blog_unknown_count": sum(1 for r in blog_final if r.get("ad_type") == "판별불가"),
        "receipt_ad_count": sum(1 for r in receipt_final if r.get("ad_type") == "광고"),
        "receipt_organic_count": sum(1 for r in receipt_final if r.get("ad_type") == "내돈내산"),
        "receipt_unknown_count": sum(1 for r in receipt_final if r.get("ad_type") == "판별불가"),
    }

    REPORTS[merchant["id"]] = result
    CRAWL_JOBS[job_id].update({
        "status": "done", "progress": 100,
        "message": "분석 완료", "report_id": merchant["id"]
    })
    print(f"[DONE] {merchant_name} / 영수증:{len(receipt_final)} 블로그:{len(blog_final)}")


# ════════════════════════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"message": "SNS 분석 솔루션 API v2.0 정상 작동"}

@app.get("/api/merchants")
async def get_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(data: MerchantCreate):
    m = {
        "id": str(uuid.uuid4())[:8],
        "name": data.name, "region": data.region,
        "place_id": data.place_id,
        "instagram_tag": data.instagram_tag or data.name,
        "created_at": datetime.now().isoformat()
    }
    MERCHANTS.append(m)
    return m

@app.put("/api/merchants/{merchant_id}")
async def update_merchant(merchant_id: str, data: MerchantUpdate):
    m = next((m for m in MERCHANTS if m["id"] == merchant_id), None)
    if not m:
        raise HTTPException(status_code=404, detail="가맹점 없음")
    for f in ["name", "region", "place_id", "instagram_tag"]:
        v = getattr(data, f)
        if v is not None:
            m[f] = v
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
        raise HTTPException(status_code=404, detail="가맹점 없음")
    job_id = str(uuid.uuid4())
    CRAWL_JOBS[job_id] = {
        "id": job_id, "merchant_id": req.merchant_id,
        "merchant_name": merchant["name"],
        "status": "pending", "progress": 0,
        "message": "분석 대기 중...",
        "started_at": datetime.now().isoformat()
    }
    executor.submit(crawl_merchant, job_id, merchant)
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job(job_id: str):
    j = CRAWL_JOBS.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="작업 없음")
    return j

@app.get("/api/reports/{merchant_id}")
async def get_report(merchant_id: str):
    r = REPORTS.get(merchant_id)
    if not r:
        raise HTTPException(status_code=404, detail="리포트 없음. 분석을 먼저 실행하세요.")
    return r

@app.get("/api/health")
async def health():
    return {"status": "ok", "playwright": PLAYWRIGHT_AVAILABLE,
            "merchants": len(MERCHANTS), "reports": len(REPORTS)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

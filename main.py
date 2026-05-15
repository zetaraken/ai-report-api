"""
SNS 분석 자동화 솔루션 - 백엔드 API v3.1
전략: 내부 API 의존 제거 → Playwright 실제 브라우저 스크롤 방식으로 전면 교체
- 영수증리뷰: 더보기 버튼 반복 클릭 + 스크롤로 전체 수집
- 블로그리뷰: 목록 전체 수집 후 원문 방문 광고 판별
- 공식 수치: 플레이스 홈에서 파싱 (표시용)
- 파일 기반 영속성: Railway 재배포 후에도 job/report 유지
"""

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

app = FastAPI(title="SNS 분석 솔루션 API", version="3.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 파일 기반 영속성 저장소 ───────────────────────────────────────
DATA_DIR = Path("/tmp/sns_analyzer_data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

MERCHANTS_FILE = DATA_DIR / "merchants.json"
JOBS_DIR       = DATA_DIR / "jobs"
REPORTS_DIR    = DATA_DIR / "reports"
JOBS_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[저장 오류] {path}: {e}")

# 인메모리 캐시 (파일에서 초기 로드)
MERCHANTS: List[Dict] = _load_json(MERCHANTS_FILE, [])
CRAWL_JOBS: Dict[str, Dict] = {}   # 실행 중인 job만 메모리 유지
REPORTS: Dict[str, Dict] = {}      # 캐시 (파일이 원본)

def save_merchants():
    _save_json(MERCHANTS_FILE, MERCHANTS)

def save_job(job: Dict):
    _save_json(JOBS_DIR / f"{job['id']}.json", job)

def load_job(job_id: str) -> Optional[Dict]:
    # 메모리 우선, 없으면 파일에서
    if job_id in CRAWL_JOBS:
        return CRAWL_JOBS[job_id]
    data = _load_json(JOBS_DIR / f"{job_id}.json", None)
    if data:
        CRAWL_JOBS[job_id] = data
    return data

def save_report(merchant_id: str, report: Dict):
    _save_json(REPORTS_DIR / f"{merchant_id}.json", report)

def load_report(merchant_id: str) -> Optional[Dict]:
    if merchant_id in REPORTS:
        return REPORTS[merchant_id]
    data = _load_json(REPORTS_DIR / f"{merchant_id}.json", None)
    if data:
        REPORTS[merchant_id] = data
    return data

executor = ThreadPoolExecutor(max_workers=2)

# ── Pydantic ──────────────────────────────────────────────────────
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


# ── 광고 판별 ──────────────────────────────────────────────────────
AD_KW = [
    "협찬", "제공받", "유료광고", "스폰서", "서포터즈", "체험단",
    "무상제공", "소정의 원고료", "원고료를 받고", "업체로부터",
    "브랜드로부터", "광고임을", "PPL", "paid partnership",
    "sponsored", "#광고", "#협찬", "#체험단", "#서포터즈",
    "#소정의원고료", "원고료", "제품을 제공", "무료로 받",
    "무료체험", "지원받", "지원을 받", "제공해주", "광고비",
]
# "광고" 단독은 오탐 많으므로 단어 경계 처리
AD_KW_WORD = ["광고"]

ORG_KW = [
    "내돈내산", "내돈내먹", "솔직후기", "솔직리뷰", "개인적인 의견",
    "자비로", "직접 구매", "내 돈 주고", "내돈주고", "순수 후기",
    "광고아님", "비광고", "광고 아님", "돈 받지 않",
]

def classify_ad(text: str) -> str:
    t = text.lower()
    ad = sum(1 for kw in AD_KW if kw.lower() in t)
    # "광고" 단어 단독 — 앞뒤 공백·문장 끝 등
    ad += sum(1 for kw in AD_KW_WORD
              if re.search(r'(?<![가-힣a-z])' + re.escape(kw) + r'(?![가-힣a-z])', t))
    org = sum(1 for kw in ORG_KW if kw.lower() in t)
    if ad > 0 and ad >= org:
        return "광고"
    elif org > 0:
        return "내돈내산"
    return "판별불가"

def get_basis(text: str, ad_type: str) -> str:
    if ad_type == "광고":
        found = [kw for kw in AD_KW + AD_KW_WORD if kw.lower() in text.lower()]
        return f"광고 표시 발견: '{found[0]}'" if found else "광고 관련 표현 포함"
    if ad_type == "내돈내산":
        found = [kw for kw in ORG_KW if kw.lower() in text.lower()]
        return f"내돈내산 표시 발견: '{found[0]}'" if found else "내돈내산 표현 포함"
    return "광고/내돈내산 표시 없음"


# ════════════════════════════════════════════════════════════
# 공통 브라우저 팩토리
# ════════════════════════════════════════════════════════════
def make_browser(p, mobile=True):
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]
    )
    if mobile:
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/16.6 Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
            locale="ko-KR",
        )
    else:
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )
    return browser, ctx


# ════════════════════════════════════════════════════════════
# STEP 0: 공식 리뷰 수 파싱 (표시용)
# ════════════════════════════════════════════════════════════
def get_official_counts(place_id: str) -> Dict:
    """플레이스 홈에서 공식 수치 파싱"""
    counts = {"receipt_total": 0, "blog_total": 0}
    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=True)
            page = ctx.new_page()
            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/home",
                wait_until="domcontentloaded", timeout=30000
            )
            page.wait_for_timeout(3000)
            text = page.inner_text("body")
            browser.close()

        m1 = re.search(r'방문자\s*리뷰\s*([\d,]+)', text)
        m2 = re.search(r'블로그\s*리뷰\s*([\d,]+)', text)
        if m1:
            counts["receipt_total"] = int(m1.group(1).replace(",", ""))
        if m2:
            counts["blog_total"] = int(m2.group(1).replace(",", ""))
        print(f"[공식 수] {counts}")
    except Exception as e:
        print(f"[공식 수 오류] {e}")
    return counts


# ════════════════════════════════════════════════════════════
# STEP 1: 영수증(방문자) 리뷰
# page.on("request") 로 POST body 캡처 (요청 차단 없음)
# ════════════════════════════════════════════════════════════
def crawl_receipt_reviews(place_id: str, target: int = 300) -> List[Dict]:
    reviews = []
    seen_texts = set()
    graphql_calls = {}   # url → {headers, body, method}

    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=True)
            page = ctx.new_page()

            # ── request 이벤트: POST body 저장 (차단 없음) ────────
            def on_request(request):
                url = request.url
                if "graphql" in url or any(k in url for k in [
                    "visitorReview", "visitor", "ugcReview", "ugc"
                ]):
                    try:
                        body = request.post_data or ""
                        graphql_calls[url] = {
                            "url": url,
                            "headers": dict(request.headers),
                            "body": body,
                            "method": request.method,
                        }
                        if body:
                            print(f"[req] {request.method} {url[:70]} body={body[:80]}")
                    except Exception as e:
                        print(f"[req 오류] {e}")

            # ── response 이벤트: 응답 데이터 저장 ─────────────────
            def on_response(response):
                url = response.url
                if "graphql" in url or any(k in url for k in [
                    "visitorReview", "visitor", "ugcReview", "ugc"
                ]):
                    try:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct:
                            data = response.json()
                            if url in graphql_calls:
                                graphql_calls[url]["data"] = data
                            _extract_review_texts(data, reviews, seen_texts)
                    except Exception:
                        pass

            page.on("request", on_request)
            page.on("response", on_response)

            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/review/visitor",
                wait_until="networkidle", timeout=30000
            )
            page.wait_for_timeout(2500)

            # 스크롤로 2~3페이지 GraphQL 트리거
            for _ in range(4):
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(1000)

            browser.close()

    except Exception as e:
        print(f"[영수증 가로채기 오류] {e}")

    calls = list(graphql_calls.values())
    print(f"[영수증] 가로채기: {len(calls)}개 요청, {len(reviews)}건 추출")
    for c in calls:
        body_preview = c.get('body', '')[:200]
        print(f"  URL: {c['url'][:80]}")
        print(f"  METHOD: {c['method']}")
        print(f"  BODY: {body_preview}")

    # ── GraphQL POST 페이지네이션 ───────────────────────────────
    if len(reviews) < target:
        import requests as req_lib

        gql_posts = [c for c in calls
                     if c["method"] == "POST"
                     and "graphql" in c["url"]
                     and c.get("body")]

        for gql in gql_posts:
            try:
                body_json = json.loads(gql["body"])
                variables = body_json.get("variables", {})
                print(f"[영수증] variables 키: {list(variables.keys())}")

                page_key = next(
                    (k for k in ["page", "after", "cursor", "offset", "start"]
                     if k in variables), None
                )

                if not page_key:
                    print(f"[영수증] page 키 없음, 다음 API 시도")
                    continue

                current_val = variables[page_key]
                next_val = (current_val + 1) if isinstance(current_val, int) else 2
                print(f"[영수증] 페이지네이션: {page_key}={current_val} → {next_val}부터 시작")

                while len(reviews) < target:
                    try:
                        variables[page_key] = next_val
                        body_json["variables"] = variables
                        r = req_lib.post(
                            gql["url"],
                            json=body_json,
                            headers=gql["headers"],
                            timeout=12
                        )
                        if r.status_code != 200:
                            print(f"[영수증] 페이지 {next_val} → HTTP {r.status_code}")
                            break
                        data = r.json()
                        prev = len(reviews)
                        _extract_review_texts(data, reviews, seen_texts)
                        if len(reviews) == prev:
                            print(f"[영수증] 페이지 {next_val}: 새 데이터 없음. 종료.")
                            break
                        print(f"[영수증] 페이지 {next_val}: 누적 {len(reviews)}건")
                        next_val += 1
                    except Exception as e:
                        print(f"[영수증] 페이지 {next_val} 오류: {e}")
                        break

                if len(reviews) >= target:
                    break

            except Exception as e:
                print(f"[영수증 페이지네이션 오류] {e}")

        if len(reviews) < 10:
            _paginate_get_api(calls, reviews, seen_texts, target)

    # ── Playwright 전체 스크롤 최후 폴백 ──────────────────────
    if len(reviews) < 5:
        print(f"[영수증] GraphQL 실패({len(reviews)}건) → Playwright 스크롤 폴백")
        reviews = _crawl_receipt_playwright_scroll(place_id, target, seen_texts)

    print(f"[영수증] 최종 수집: {len(reviews)}건")
    return reviews


def _paginate_get_api(api_calls: list, reviews: list,
                       seen_texts: set, target: int):
    """GET 파라미터 방식 페이지네이션"""
    import requests as req_lib
    for call in api_calls:
        if call["method"] != "GET":
            continue
        url = call["url"]
        for pat in [r'(page=)(\d+)', r'(start=)(\d+)']:
            m = re.search(pat, url)
            if m:
                page_num = int(m.group(2)) + 1
                while len(reviews) < target:
                    try:
                        next_url = re.sub(
                            pat, lambda x, n=page_num: x.group(1) + str(n), url
                        )
                        r = req_lib.get(next_url, headers=call["headers"], timeout=10)
                        if r.status_code != 200:
                            break
                        data = r.json()
                        prev = len(reviews)
                        _extract_review_texts(data, reviews, seen_texts)
                        if len(reviews) == prev:
                            break
                        page_num += 1
                        print(f"[영수증 GET] 페이지 {page_num}: 누적 {len(reviews)}건")
                    except Exception as e:
                        print(f"[영수증 GET page={page_num}] {e}")
                        break
                return


def _extract_review_texts(data, reviews: list, seen_texts: set):
    """JSON 응답에서 리뷰 텍스트 재귀 추출"""
    if isinstance(data, dict):
        # 리뷰 텍스트 필드
        for key in ["body", "content", "text", "description", "contents"]:
            val = data.get(key, "")
            if isinstance(val, str) and len(val) >= 10 and val not in seen_texts:
                # UI 버튼 텍스트 필터
                if not any(t in val for t in ("펼쳐서 더보기", "반응 남기기", "신고")):
                    seen_texts.add(val)
                    ad_type = classify_ad(val)
                    reviews.append({
                        "text": val[:500],
                        "ad_type": ad_type,
                        "ad_basis": get_basis(val, ad_type),
                        "source": "naver_receipt",
                        "rating": data.get("rating", data.get("score", "")),
                    })
        for v in data.values():
            if isinstance(v, (dict, list)):
                _extract_review_texts(v, reviews, seen_texts)
    elif isinstance(data, list):
        for item in data:
            _extract_review_texts(item, reviews, seen_texts)


def _crawl_receipt_playwright_scroll(place_id: str, target: int,
                                      seen_texts: set) -> list:
    """
    Playwright 전체 스크롤 폴백
    - 더보기(펼쳐서 더보기) 버튼 클릭으로 텍스트 펼치기
    - page.evaluate()로 JS에서 직접 DOM 텍스트 추출
    - 스크롤로 무한 로드
    """
    reviews = []
    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=True)
            page = ctx.new_page()
            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/review/visitor",
                wait_until="domcontentloaded", timeout=30000
            )
            page.wait_for_timeout(3000)

            no_new = 0
            for i in range(400):  # 최대 400회 스크롤 (239건 × 스크롤당 1건 기준)
                prev = len(reviews)

                # JS로 DOM에서 리뷰 텍스트 직접 추출
                texts = page.evaluate("""
                    () => {
                        const results = [];
                        // 방법1: 리뷰 li 아이템의 텍스트 span
                        const spans = document.querySelectorAll(
                            'li span.pui__Ic-pg, li .pui__vn15t2, li span[class*="body"]'
                        );
                        spans.forEach(el => {
                            const t = el.innerText.trim();
                            if (t.length >= 10) results.push(t);
                        });
                        // 방법2: 리뷰 li 전체
                        if (results.length === 0) {
                            const lis = document.querySelectorAll(
                                'li.pui__X35jYm, li[data-laim-exp-id]'
                            );
                            lis.forEach(el => {
                                const t = el.innerText.trim();
                                if (t.length >= 10) results.push(t);
                            });
                        }
                        // 방법3: 리뷰 섹션 내 p 태그
                        if (results.length === 0) {
                            const ps = document.querySelectorAll(
                                'div[class*="Review"] p, div[class*="review"] p'
                            );
                            ps.forEach(el => {
                                const t = el.innerText.trim();
                                if (t.length >= 10) results.push(t);
                            });
                        }
                        return results;
                    }
                """)

                SKIP = {"펼쳐서 더보기", "더보기", "접기", "반응 남기기",
                        "좋아요", "신고", "사진보기"}
                for text in (texts or []):
                    text = text.strip()
                    if (len(text) >= 10
                            and text not in seen_texts
                            and not any(s in text for s in SKIP)):
                        seen_texts.add(text)
                        ad_type = classify_ad(text)
                        reviews.append({
                            "text": text[:500],
                            "ad_type": ad_type,
                            "ad_basis": get_basis(text, ad_type),
                            "source": "naver_receipt",
                        })

                if len(reviews) >= target:
                    break

                # 더보기 버튼 클릭
                try:
                    btns = page.locator(
                        "button:has-text('더보기'), "
                        "a:has-text('더보기'), "
                        "span:has-text('더보기')"
                    ).all()
                    for btn in btns[:5]:
                        try:
                            if btn.is_visible(timeout=300):
                                btn.scroll_into_view_if_needed()
                                btn.click()
                                page.wait_for_timeout(150)
                        except Exception:
                            pass
                except Exception:
                    pass

                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(700)

                if len(reviews) == prev:
                    no_new += 1
                    if no_new >= 10:
                        print(f"[영수증 스크롤] {i}회 후 종료. 수집: {len(reviews)}건")
                        break
                else:
                    no_new = 0

            browser.close()
    except Exception as e:
        print(f"[영수증 스크롤 폴백 오류] {e}")
    return reviews


def _collect_receipt_texts(page, reviews: list, seen_texts: set):
    """현재 페이지에서 리뷰 텍스트 추출 (중복 제외, 텍스트 있는 것만)"""
    selectors = [
        "li.pui__X35jYm span.pui__Ic-pg",
        "li.pui__X35jYm",
        "li[data-laim-exp-id]",
        ".pui__vn15t2",
        "div[class*='ReviewItem']",
        "div[class*='review_item']",
    ]
    for sel in selectors:
        try:
            els = page.locator(sel).all()
            if len(els) >= 2:
                for el in els:
                    try:
                        text = el.inner_text().strip()
                        if (len(text) >= 10
                                and text not in seen_texts
                                and "펼쳐서 더보기" not in text
                                and "반응 남기기" not in text):
                            seen_texts.add(text)
                            ad_type = classify_ad(text)
                            reviews.append({
                                "text": text[:500],
                                "ad_type": ad_type,
                                "ad_basis": get_basis(text, ad_type),
                                "source": "naver_receipt",
                            })
                    except Exception:
                        continue
                break
        except Exception:
            continue


# ════════════════════════════════════════════════════════════
# STEP 2: 블로그 리뷰 목록 수집
# page.on("request") 로 POST body 캡처 (요청 차단 없음)
# ════════════════════════════════════════════════════════════
def crawl_blog_links(place_id: str, merchant_name: str = "",
                     target: int = 100, progress_cb=None) -> List[Dict]:
    links = []
    seen_urls = set()
    graphql_calls = {}

    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=True)
            page = ctx.new_page()

            def on_request(request):
                url = request.url
                if "graphql" in url or any(k in url for k in ["ugc", "blog", "review"]):
                    try:
                        body = request.post_data or ""
                        graphql_calls[url] = {
                            "url": url,
                            "headers": dict(request.headers),
                            "body": body,
                            "method": request.method,
                        }
                        if body:
                            print(f"[블로그 req] {request.method} {url[:70]} body={body[:80]}")
                    except Exception as e:
                        print(f"[블로그 req 오류] {e}")

            def on_response(response):
                url = response.url
                if "graphql" in url or any(k in url for k in ["ugc", "blog", "review"]):
                    try:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct:
                            data = response.json()
                            if url in graphql_calls:
                                graphql_calls[url]["data"] = data
                            _extract_links_from_json(data, links, seen_urls)
                    except Exception:
                        pass

            page.on("request", on_request)
            page.on("response", on_response)

            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/review/ugc",
                wait_until="networkidle", timeout=25000
            )
            page.wait_for_timeout(2000)

            if progress_cb:
                progress_cb(len(links), target)

            for _ in range(4):
                if len(links) >= target:
                    break
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(1000)
                if progress_cb:
                    progress_cb(len(links), target)

            _collect_blog_links(page, links, seen_urls)
            browser.close()

    except Exception as e:
        print(f"[블로그 가로채기 오류] {e}")

    calls = list(graphql_calls.values())
    print(f"[블로그] 가로채기: {len(calls)}개 요청, {len(links)}건 추출")

    # ── GraphQL POST 페이지네이션 ──────────────────────────────
    if len(links) < target:
        import requests as req_lib

        gql_posts = [c for c in calls
                     if c["method"] == "POST"
                     and "graphql" in c["url"]
                     and c.get("body")]

        for gql in gql_posts:
            try:
                body_json = json.loads(gql["body"])
                variables = body_json.get("variables", {})
                page_key = next(
                    (k for k in ["page", "after", "cursor", "offset", "start"]
                     if k in variables), None
                )
                if not page_key:
                    continue

                current_val = variables[page_key]
                next_val = (current_val + 1) if isinstance(current_val, int) else 2

                while len(links) < target:
                    try:
                        variables[page_key] = next_val
                        body_json["variables"] = variables
                        r = req_lib.post(
                            gql["url"],
                            json=body_json,
                            headers=gql["headers"],
                            timeout=12
                        )
                        if r.status_code != 200:
                            break
                        data = r.json()
                        prev = len(links)
                        _extract_links_from_json(data, links, seen_urls)
                        if len(links) == prev:
                            break
                        if progress_cb:
                            progress_cb(len(links), target)
                        print(f"[블로그] 페이지 {next_val}: 누적 {len(links)}건")
                        next_val += 1
                    except Exception as e:
                        print(f"[블로그] 페이지 {next_val} 오류: {e}")
                        break

                if len(links) >= target:
                    break

            except Exception as e:
                print(f"[블로그 페이지네이션 오류] {e}")

    # ── 폴백 ──────────────────────────────────────────────────
    if len(links) < 5 and merchant_name:
        print("[블로그] 수집 부족 → 네이버 블로그 검색 폴백")
        extra = _crawl_blog_links_via_search(merchant_name, target - len(links), seen_urls)
        links.extend(extra)

    if progress_cb:
        progress_cb(len(links), target)

    print(f"[블로그 목록] 최종 수집: {len(links)}건")
    return links


def _collect_blog_links(page, links: list, seen_urls: set):
    """DOM에서 블로그 카드 링크 추출"""
    # 네이버 플레이스 블로그리뷰 카드 앵커 셀렉터
    selectors = [
        "a[href*='blog.naver.com']",
        "a[href*='post.naver.com']",
        "a[href*='m.blog.naver.com']",
    ]
    for sel in selectors:
        anchors = page.locator(sel).all()
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                if not href or href in seen_urls:
                    continue
                # 목록·프로필·사진 페이지 제외
                if any(x in href for x in ["PostList", "photo", "media", "?tab=", "/profile", "CategoryList"]):
                    continue
                seen_urls.add(href)
                # 제목: 카드 내 strong 또는 링크 텍스트
                title = ""
                try:
                    parent = a.locator("xpath=ancestor::div[3]")
                    title_el = parent.locator("strong, em, .title, [class*='title']").first
                    title = title_el.inner_text().strip()[:120]
                except Exception:
                    pass
                if not title:
                    try:
                        title = a.inner_text().strip()[:120]
                    except Exception:
                        pass
                # 미리보기 텍스트
                excerpt = ""
                try:
                    parent = a.locator("xpath=ancestor::div[3]")
                    excerpt = parent.inner_text().strip()[:300]
                except Exception:
                    pass
                links.append({"url": href, "title": title, "excerpt": excerpt})
            except Exception:
                continue



def _extract_links_from_json(data, links: list, seen_urls: set):
    """캡처된 JSON 응답에서 블로그 URL 재귀 추출"""
    if isinstance(data, dict):
        for key in ["url", "link", "blogUrl", "postUrl", "permalink"]:
            val = data.get(key, "")
            if isinstance(val, str) and "blog.naver.com" in val and val not in seen_urls:
                seen_urls.add(val)
                title = data.get("title", data.get("subject", ""))
                excerpt = data.get("contents", data.get("body", data.get("description", "")))
                links.append({
                    "url": val,
                    "title": str(title)[:120],
                    "excerpt": str(excerpt)[:300],
                })
        for v in data.values():
            if isinstance(v, (dict, list)):
                _extract_links_from_json(v, links, seen_urls)
    elif isinstance(data, list):
        for item in data:
            _extract_links_from_json(item, links, seen_urls)


def _crawl_blog_links_via_search(merchant_name: str, target: int,
                                  seen_urls: set) -> List[Dict]:
    """네이버 블로그 검색으로 폴백 수집"""
    links = []
    try:
        import requests as req_lib
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        for start in range(1, min(target, 80) + 1, 10):
            if len(links) >= target:
                break
            try:
                r = req_lib.get(
                    f"https://search.naver.com/search.naver"
                    f"?query={quote(merchant_name)}&where=blog&start={start}",
                    headers=headers, timeout=8
                )
                html = r.text
                blog_urls = re.findall(
                    r'href="(https?://(?:blog\.naver\.com|post\.naver\.com)[^"]+)"', html
                )
                titles_raw = re.findall(
                    r'<a[^>]+class="[^"]*title[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL
                )
                clean_titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles_raw]
                for i, u in enumerate(blog_urls):
                    if u in seen_urls or len(links) >= target:
                        break
                    if any(x in u for x in ["PostList", "?tab=", "/profile"]):
                        continue
                    seen_urls.add(u)
                    title = clean_titles[i] if i < len(clean_titles) else ""
                    links.append({"url": u, "title": title[:120], "excerpt": ""})
            except Exception as e:
                print(f"[블로그 검색 폴백 page={start}] {e}")
                break
    except Exception as e:
        print(f"[블로그 검색 폴백 오류] {e}")
    return links


# ════════════════════════════════════════════════════════════
# STEP 3: 블로그 원문 방문 → 광고 판별
# ════════════════════════════════════════════════════════════
def classify_blog_originals(blog_links: List[Dict],
                             progress_cb=None) -> List[Dict]:
    """
    각 블로그 원문에 직접 방문하여 본문 전체 텍스트로 광고 판별
    - 타임아웃: 페이지 8초 / iframe 3초 (기존 20초→8초로 단축)
    - progress_cb(done, total): 건별 진행률 콜백
    - 원문 접근 실패 시 excerpt 텍스트로 즉시 판별 (블로킹 없음)
    """
    if not blog_links:
        return []

    results = []
    total = len(blog_links)

    def _visit_one(ctx, item, idx):
        """단일 블로그 원문 방문 및 판별"""
        page = ctx.new_page()
        try:
            url = item["url"].replace("m.blog.naver.com", "blog.naver.com")
            page.goto(url, wait_until="domcontentloaded", timeout=8000)
            page.wait_for_timeout(800)   # 최소 대기만

            full_text = ""

            # 네이버 블로그 iframe(mainFrame) 처리
            try:
                frame = page.frame(name="mainFrame")
                if frame:
                    frame.wait_for_load_state("domcontentloaded", timeout=3000)
                    for sel in [".se-main-container", "#postViewArea", ".post-view", "body"]:
                        try:
                            el = frame.locator(sel).first
                            if el.is_visible(timeout=500):
                                full_text = el.inner_text()
                                break
                        except Exception:
                            continue
            except Exception:
                pass

            if not full_text:
                try:
                    full_text = page.inner_text("body")
                except Exception:
                    full_text = ""

            ad_type = classify_ad(full_text)

            title = item.get("title", "")
            if not title:
                try:
                    title = page.title()[:120]
                except Exception:
                    pass

            return {
                "title": title or "제목 없음",
                "text": full_text[:500],
                "ad_type": ad_type,
                "ad_basis": get_basis(full_text, ad_type),
                "source": "naver_blog",
                "url": item["url"],
            }

        except Exception as e:
            excerpt = item.get("excerpt", "")
            ad_type = classify_ad(excerpt)
            return {
                "title": item.get("title", "제목 없음"),
                "text": excerpt[:500],
                "ad_type": ad_type,
                "ad_basis": f"원문 접근 실패, 미리보기로 판별",
                "source": "naver_blog",
                "url": item.get("url", ""),
            }
        finally:
            try:
                page.close()
            except Exception:
                pass

    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=False)

            for idx, item in enumerate(blog_links):
                result = _visit_one(ctx, item, idx)
                results.append(result)

                # 건별 진행률 콜백
                if progress_cb:
                    progress_cb(idx + 1, total)

                print(f"[블로그 원문] {idx+1}/{total} {result['ad_type']} - {result['title'][:30]}")

            browser.close()

    except Exception as e:
        print(f"[블로그 원문 전체 오류] {e}")
        # 브라우저 전체 실패 → 모든 나머지를 excerpt로 즉시 판별
        already_done = len(results)
        for item in blog_links[already_done:]:
            text = item.get("excerpt", "")
            ad_type = classify_ad(text)
            results.append({
                "title": item.get("title", "제목 없음"),
                "text": text[:500],
                "ad_type": ad_type,
                "ad_basis": get_basis(text, ad_type) + " (브라우저 오류, 미리보기 기반)",
                "source": "naver_blog",
                "url": item.get("url", ""),
            })

    print(f"[블로그 원문] 판별 완료: {len(results)}건")
    return results


# ════════════════════════════════════════════════════════════
# STEP 4: 네이버 검색 콘텐츠 수
# ════════════════════════════════════════════════════════════
def crawl_naver_search_count(merchant_name: str, region: str) -> int:
    """네이버 블로그 검색 결과 수"""
    query = f"{region} {merchant_name}".strip()
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=False)
            page = ctx.new_page()
            page.goto(
                f"https://search.naver.com/search.naver?query={quote(query)}&where=blog",
                wait_until="domcontentloaded", timeout=20000
            )
            page.wait_for_timeout(2000)
            text = page.inner_text("body")

            # "약 1,234개" 패턴
            m = re.search(r'약\s*([\d,]+)\s*개', text)
            if m:
                count = int(m.group(1).replace(",", ""))
            else:
                # 검색결과 li 개수
                items = page.locator("li.bx").all()
                count = len(items)

            browser.close()
    except Exception as e:
        print(f"[네이버 검색 오류] {e}")
    return count


# ════════════════════════════════════════════════════════════
# STEP 5: 인스타그램 콘텐츠 수
# ════════════════════════════════════════════════════════════
def crawl_instagram_count(tag: str) -> int:
    """인스타그램 해시태그 게시물 수"""
    clean = tag.replace(" ", "").replace("#", "")
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=True)
            page = ctx.new_page()
            page.goto(
                f"https://www.instagram.com/explore/tags/{clean}/",
                wait_until="domcontentloaded", timeout=25000
            )
            page.wait_for_timeout(3500)
            text = page.inner_text("body")

            patterns = [
                (r'([\d.]+)만\s*(?:개\s*)?게시물', "만"),
                (r'게시물\s*([\d,]+(?:\.\d+)?)\s*만', "만"),
                (r'게시물\s*([\d,]+)', ""),
                (r'([\d,]+(?:\.\d+)?[KMk만천]?)\s*posts?', ""),
            ]
            for pat, unit in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    ns = m.group(1).replace(",", "")
                    if unit == "만" or "만" in ns:
                        count = int(float(ns.replace("만", "")) * 10000)
                    elif "K" in ns.upper():
                        count = int(float(re.sub(r'[Kk]','',ns)) * 1000)
                    elif "M" in ns.upper():
                        count = int(float(re.sub(r'[Mm]','',ns)) * 1000000)
                    else:
                        try:
                            count = int(float(ns))
                        except Exception:
                            count = 0
                    break

            if count == 0:
                imgs = page.locator("img[alt]").all()
                count = max(0, (len(imgs) - 3) * 25)

            browser.close()
    except Exception as e:
        print(f"[인스타그램 오류] {e}")
    return count


# ════════════════════════════════════════════════════════════
# 메인 크롤링 오케스트레이터
# ════════════════════════════════════════════════════════════
def crawl_merchant(job_id: str, merchant: Dict):
    place_id    = merchant["place_id"]
    name        = merchant["name"]
    region      = merchant.get("region", "")
    ig_tag      = merchant.get("instagram_tag") or name

    result = {
        "merchant_id": merchant["id"],
        "merchant_name": name,
        "crawled_at": datetime.now().isoformat(),
        "naver_receipt_reviews": [],
        "naver_blog_reviews": [],
        "naver_search_count": 0,
        "instagram_count": 0,
        "place_counts": {},
        "summary": {}
    }

    def upd(pct, msg):
        job = CRAWL_JOBS.get(job_id, {})
        job.update({"status": "running", "progress": pct, "message": msg})
        CRAWL_JOBS[job_id] = job
        save_job(job)
        print(f"[{pct}%] {msg}")

    # ── 하트비트: 30초마다 현재 상태를 파일에 기록 (watchdog) ──
    import threading
    _stop_hb = threading.Event()
    def _heartbeat():
        while not _stop_hb.is_set():
            _stop_hb.wait(30)
            if not _stop_hb.is_set():
                job = CRAWL_JOBS.get(job_id, {})
                if job.get("status") == "running":
                    save_job(job)
                    print(f"[HB] job={job_id} {job.get('progress')}% alive")
    hb_thread = threading.Thread(target=_heartbeat, daemon=True)
    hb_thread.start()

    try:
        # 0. 공식 수치 파싱
        upd(5,  "플레이스 공식 리뷰 수 확인 중...")
        counts = get_official_counts(place_id)
        result["place_counts"] = counts

        # 1. 영수증(방문자) 리뷰 전체 수집
        official_r = counts.get("receipt_total", 0)
        upd(10, f"영수증리뷰 수집 중... (공식 {official_r}건)")
        # target = 공식 수치 그대로, 최대 300건
        receipt = crawl_receipt_reviews(place_id, target=min(official_r or 250, 300))
        result["naver_receipt_reviews"] = receipt
        upd(40, f"영수증리뷰 {len(receipt)}건 수집 완료")

        # 2. 블로그 리뷰 목록 수집 (43~56% 구간)
        official_b = counts.get("blog_total", 0)
        blog_target = min(official_b or 50, 100)  # 공식 수치 기준, 최대 100건

        def blog_list_progress(current, total):
            pct = 43 + int((min(current, total) / max(total, 1)) * 13)
            upd(pct, f"블로그리뷰 목록 수집 중... ({current}건 / 목표 {total}건)")

        upd(43, f"블로그리뷰 목록 수집 중... (공식 {official_b}건, 최대 {blog_target}건)")
        blog_links = crawl_blog_links(
            place_id=place_id,
            merchant_name=name,
            target=blog_target,
            progress_cb=blog_list_progress,
        )
        upd(57, f"블로그 링크 {len(blog_links)}건 확보, 원문 방문 시작...")

        # 3. 블로그 원문 방문 → 광고 판별 (57~75% 구간 — 건별 갱신)
        def blog_orig_progress(done, total):
            pct = 57 + int((done / max(total, 1)) * 18)
            upd(pct, f"블로그 원문 방문 중... ({done}/{total}건 완료)")

        blog_reviews = classify_blog_originals(blog_links, progress_cb=blog_orig_progress)
        result["naver_blog_reviews"] = blog_reviews
        upd(75, f"블로그리뷰 {len(blog_reviews)}건 분석 완료")

        # 4. 네이버 검색 수
        upd(80, "네이버 검색결과 집계 중...")
        naver_cnt = crawl_naver_search_count(name, region)
        result["naver_search_count"] = naver_cnt
        upd(88, f"네이버 검색 {naver_cnt}건")

        # 5. 인스타그램
        upd(91, "인스타그램 집계 중...")
        ig_cnt = crawl_instagram_count(ig_tag)
        result["instagram_count"] = ig_cnt
        upd(97, f"인스타그램 {ig_cnt}건")

    except Exception as e:
        _stop_hb.set()
        job = CRAWL_JOBS.get(job_id, {})
        job.update({"status": "error", "message": f"오류: {str(e)}"})
        CRAWL_JOBS[job_id] = job
        save_job(job)
        print(f"[ERROR job={job_id}] {e}")
        return

    # 집계
    receipt_list = result["naver_receipt_reviews"]
    blog_list    = result["naver_blog_reviews"]

    result["summary"] = {
        "official_receipt_count": result["place_counts"].get("receipt_total", 0),
        "official_blog_count":    result["place_counts"].get("blog_total", 0),
        "total_receipt_reviews":  len(receipt_list),
        "total_blog_reviews":     len(blog_list),
        "naver_search_count":     naver_cnt,
        "instagram_count":        ig_cnt,
        "blog_ad_count":      sum(1 for r in blog_list if r["ad_type"]=="광고"),
        "blog_organic_count": sum(1 for r in blog_list if r["ad_type"]=="내돈내산"),
        "blog_unknown_count": sum(1 for r in blog_list if r["ad_type"]=="판별불가"),
        "receipt_ad_count":      sum(1 for r in receipt_list if r["ad_type"]=="광고"),
        "receipt_organic_count": sum(1 for r in receipt_list if r["ad_type"]=="내돈내산"),
        "receipt_unknown_count": sum(1 for r in receipt_list if r["ad_type"]=="판별불가"),
    }

    # 하트비트 종료
    _stop_hb.set()

    # 리포트 파일 저장
    REPORTS[merchant["id"]] = result
    save_report(merchant["id"], result)

    # job 완료 파일 저장
    done_job = {**CRAWL_JOBS.get(job_id, {}),
                "status": "done", "progress": 100,
                "message": "분석 완료", "report_id": merchant["id"]}
    CRAWL_JOBS[job_id] = done_job
    save_job(done_job)
    print(f"[DONE] {name} / 영수증:{len(receipt_list)} 블로그:{len(blog_list)}")


# ════════════════════════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════════════════════════
@app.get("/")
async def root():
    return {"message": "SNS 분석 솔루션 API v3.0"}

@app.get("/api/merchants")
async def get_merchants():
    return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(data: MerchantCreate):
    m = {
        "id": str(uuid.uuid4())[:8],
        "name": data.name,
        "region": data.region,
        "place_id": data.place_id,
        "instagram_tag": data.instagram_tag or data.name,
        "created_at": datetime.now().isoformat()
    }
    MERCHANTS.append(m)
    save_merchants()   # ← 파일 저장
    return m

@app.put("/api/merchants/{mid}")
async def update_merchant(mid: str, data: MerchantUpdate):
    m = next((m for m in MERCHANTS if m["id"] == mid), None)
    if not m:
        raise HTTPException(404, "가맹점 없음")
    for f in ["name", "region", "place_id", "instagram_tag"]:
        v = getattr(data, f)
        if v is not None:
            m[f] = v
    save_merchants()
    return m

@app.delete("/api/merchants/{mid}")
async def delete_merchant(mid: str):
    global MERCHANTS
    MERCHANTS = [m for m in MERCHANTS if m["id"] != mid]
    save_merchants()
    return {"deleted": mid}

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    merchant = next((m for m in MERCHANTS if m["id"] == req.merchant_id), None)
    if not merchant:
        raise HTTPException(404, "가맹점 없음")
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "merchant_id": req.merchant_id,
        "merchant_name": merchant["name"],
        "status": "pending",
        "progress": 0,
        "message": "분석 대기 중...",
        "started_at": datetime.now().isoformat()
    }
    # ── 파일 먼저 저장 → 응답 → executor 실행 순서 보장 ──
    CRAWL_JOBS[job_id] = job
    save_job(job)                           # 폴링 전에 반드시 파일 존재해야 함
    executor.submit(crawl_merchant, job_id, merchant)
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job(job_id: str):
    j = load_job(job_id)   # ← 메모리 없으면 파일에서 복원
    if not j:
        raise HTTPException(404, "작업 없음")
    return j

@app.get("/api/reports/{mid}")
async def get_report(mid: str):
    r = load_report(mid)   # ← 메모리 없으면 파일에서 복원
    if not r:
        raise HTTPException(404, "리포트 없음. 분석을 먼저 실행하세요.")
    return r

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "playwright": PLAYWRIGHT_AVAILABLE,
        "merchants": len(MERCHANTS),
        "jobs_in_memory": len(CRAWL_JOBS),
        "reports_in_memory": len(REPORTS),
        "data_dir": str(DATA_DIR),
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

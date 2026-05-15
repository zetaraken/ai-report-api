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
# STEP 1: 영수증(방문자) 리뷰 — Playwright 스크롤+더보기 반복
# ════════════════════════════════════════════════════════════
def crawl_receipt_reviews(place_id: str, target: int = 100) -> List[Dict]:
    """
    네이버 플레이스 방문자(영수증) 리뷰 수집
    - 더보기 버튼 반복 클릭 + 스크롤로 최대 target건 수집
    - 텍스트가 있는 리뷰만 추출 (별점만 있는 리뷰 제외)
    """
    reviews = []
    seen = set()

    try:
        with sync_playwright() as p:
            browser, ctx = make_browser(p, mobile=True)
            page = ctx.new_page()

            # 방문자 리뷰 탭 직접 진입
            page.goto(
                f"https://m.place.naver.com/restaurant/{place_id}/review/visitor",
                wait_until="domcontentloaded", timeout=30000
            )
            page.wait_for_timeout(3000)

            # ── 더보기 반복 클릭으로 리뷰 로드 ──────────────────
            more_click_count = 0
            max_clicks = 30  # 최대 클릭 횟수 (리뷰 10건/클릭 × 30 = ~300건)
            no_new_count = 0

            while more_click_count < max_clicks:
                prev_count = len(seen)

                # 현재 페이지의 리뷰 텍스트 수집
                _collect_receipt_texts(page, reviews, seen)

                # 목표 건수 달성 시 종료
                if len(reviews) >= target:
                    break

                # 더보기 버튼 탐색 (다양한 셀렉터)
                clicked = False
                more_selectors = [
                    "a.place_bluelink:has-text('더보기')",
                    "button:has-text('더보기')",
                    "a:has-text('더보기')",
                    "span:has-text('더보기')",
                    "[class*='more']:has-text('더보기')",
                ]
                for sel in more_selectors:
                    try:
                        btn = page.locator(sel).last
                        if btn.is_visible(timeout=1500):
                            btn.scroll_into_view_if_needed()
                            btn.click()
                            page.wait_for_timeout(1800)
                            clicked = True
                            break
                    except Exception:
                        continue

                if not clicked:
                    # 스크롤로 추가 로드 시도
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)

                more_click_count += 1

                # 새로 추가된 리뷰가 없으면 카운트
                if len(seen) == prev_count:
                    no_new_count += 1
                    if no_new_count >= 3:
                        print(f"[영수증] 더 이상 새 리뷰 없음. 종료.")
                        break
                else:
                    no_new_count = 0

            # 마지막 한 번 더 수집
            _collect_receipt_texts(page, reviews, seen)
            browser.close()

    except Exception as e:
        print(f"[영수증 크롤링 오류] {e}")

    print(f"[영수증] 최종 수집: {len(reviews)}건")
    return reviews


def _collect_receipt_texts(page, reviews: list, seen: set):
    """현재 페이지에서 리뷰 텍스트 추출 (중복 제외)"""
    # 네이버 플레이스 리뷰 셀렉터 (여러 버전 대응)
    selectors = [
        "li.pui__X35jYm",           # 구버전
        "div.place_section_content li",
        "li[class*='ReviewItem']",
        "div[class*='ReviewItem']",
        "li[data-laim-exp-id]",
        ".pui__vn15t2",             # 2024년~
        "div[class*='review_item']",
    ]

    collected = []
    for sel in selectors:
        try:
            els = page.locator(sel).all()
            if len(els) >= 2:
                collected = els
                break
        except Exception:
            continue

    if not collected:
        # 최후 수단: 텍스트가 긴 span/p 태그
        try:
            collected = page.locator("span.pui__Ic-pg, p.pui__xtsQN").all()
        except Exception:
            pass

    for el in collected:
        try:
            text = el.inner_text().strip()
            # 너무 짧거나 이미 수집한 텍스트 제외
            if len(text) < 10 or text in seen:
                continue
            # UI 텍스트 필터링 (버튼명 등)
            if text in ("더보기", "접기", "좋아요", "신고"):
                continue
            seen.add(text)
            ad_type = classify_ad(text)
            reviews.append({
                "text": text[:500],
                "ad_type": ad_type,
                "ad_basis": get_basis(text, ad_type),
                "source": "naver_receipt",
            })
        except Exception:
            continue


# ════════════════════════════════════════════════════════════
# STEP 2: 블로그 리뷰 목록 수집 — 네이버 검색 API (requests)
# Playwright 방식은 봇 감지로 블로킹 → requests로 전환
# ════════════════════════════════════════════════════════════
def crawl_blog_links(place_id: str, merchant_name: str = "",
                     target: int = 30, progress_cb=None) -> List[Dict]:
    """
    네이버 블로그 검색 API로 블로그 링크 수집 (빠름, 봇 감지 없음)
    플레이스 블로그리뷰 탭 대신 네이버 검색에서 가맹점명으로 검색
    """
    import urllib.request
    links = []
    seen_urls = set()

    # 방법 1: 네이버 검색 HTML 파싱 (requests)
    try:
        import requests as req_lib
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": "https://search.naver.com/",
        }

        # 페이지별 수집 (10건/페이지)
        for start in range(1, target + 1, 10):
            if len(links) >= target:
                break
            try:
                url = (
                    f"https://search.naver.com/search.naver"
                    f"?query={quote(merchant_name)}&where=blog&start={start}"
                )
                r = req_lib.get(url, headers=headers, timeout=8)
                html = r.text

                # 블로그 링크 추출 (정규식)
                blog_urls = re.findall(
                    r'href="(https?://(?:blog\.naver\.com|m\.blog\.naver\.com|post\.naver\.com)[^"]+)"',
                    html
                )
                titles = re.findall(
                    r'<a[^>]+class="[^"]*title[^"]*"[^>]*>(.*?)</a>',
                    html, re.DOTALL
                )
                # HTML 태그 제거
                clean_titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles]

                for i, u in enumerate(blog_urls):
                    if u in seen_urls or len(links) >= target:
                        break
                    # 프로필·목록 페이지 제외
                    if any(x in u for x in ["PostList", "?tab=", "/profile"]):
                        continue
                    seen_urls.add(u)
                    title = clean_titles[i] if i < len(clean_titles) else ""
                    links.append({"url": u, "title": title[:120], "excerpt": ""})

                if progress_cb:
                    progress_cb(len(links), target)

            except Exception as e:
                print(f"[블로그 검색 page={start}] {e}")
                break

    except ImportError:
        pass

    # 방법 2: requests 실패 시 Playwright 폴백 (간소화)
    if not links:
        print("[블로그 목록] requests 실패 → Playwright 폴백")
        try:
            with sync_playwright() as p:
                browser, ctx = make_browser(p, mobile=False)
                page = ctx.new_page()
                page.goto(
                    f"https://search.naver.com/search.naver"
                    f"?query={quote(merchant_name)}&where=blog",
                    wait_until="domcontentloaded", timeout=15000
                )
                page.wait_for_timeout(1000)

                anchors = page.locator(
                    "a[href*='blog.naver.com'], a[href*='post.naver.com']"
                ).all()
                for a in anchors[:target]:
                    try:
                        href = a.get_attribute("href") or ""
                        if href and href not in seen_urls:
                            seen_urls.add(href)
                            try:
                                title = a.inner_text().strip()[:120]
                            except Exception:
                                title = ""
                            links.append({"url": href, "title": title, "excerpt": ""})
                    except Exception:
                        continue

                browser.close()
        except Exception as e:
            print(f"[블로그 목록 Playwright 폴백 오류] {e}")

        if progress_cb:
            progress_cb(len(links), target)

    print(f"[블로그 목록] 수집된 링크: {len(links)}건")
    return links


def _collect_blog_links(page, links: list, seen_urls: set):
    """현재 페이지에서 블로그 링크 추출 (Playwright용 헬퍼)"""
    anchors = page.locator(
        "a[href*='blog.naver.com'], a[href*='post.naver.com'], "
        "a[href*='m.blog.naver.com']"
    ).all()
    for a in anchors:
        try:
            href = a.get_attribute("href") or ""
            if not href or href in seen_urls:
                continue
            if any(x in href for x in ["PostList", "photo", "media"]):
                continue
            seen_urls.add(href)
            try:
                title = a.inner_text().strip()[:120]
            except Exception:
                title = ""
            links.append({"url": href, "title": title, "excerpt": ""})
        except Exception:
            continue


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
        receipt = crawl_receipt_reviews(place_id, target=min(official_r or 100, 100))
        result["naver_receipt_reviews"] = receipt
        upd(40, f"영수증리뷰 {len(receipt)}건 수집 완료")

        # 2. 블로그 리뷰 목록 수집 (43~56% 구간)
        official_b = counts.get("blog_total", 0)
        blog_target = min(official_b or 30, 30)

        def blog_list_progress(current, total):
            pct = 43 + int((min(current, total) / max(total, 1)) * 13)
            upd(pct, f"블로그리뷰 목록 수집 중... ({current}건 / 목표 {total}건)")

        upd(43, f"블로그리뷰 목록 수집 중... (공식 {official_b}건, 최대 {blog_target}건)")
        blog_links = crawl_blog_links(
            place_id=place_id,
            merchant_name=name,        # ← 가맹점명 전달 (검색 쿼리용)
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

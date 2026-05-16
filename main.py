"""
SNS 분석 자동화 솔루션 - 백엔드 API v19
영수증리뷰 수집 방식 (영상+버튼 텍스트 확인):
  ① 화면 맨 아래까지 스크롤
  ② "펼쳐서 더보기" 버튼 클릭
  ③ 새 리뷰 로드 대기
  ④ 버튼이 없을 때까지 반복
"""

import json
import os
import re
import uuid
import threading
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
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

app = FastAPI(title="SNS 분석 솔루션 API", version="19.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 파일 기반 영속성 ──────────────────────────────────────────────
DATA_DIR = Path("/tmp/sns_analyzer_data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
MERCHANTS_FILE = DATA_DIR / "merchants.json"
JOBS_DIR       = DATA_DIR / "jobs"
REPORTS_DIR    = DATA_DIR / "reports"
JOBS_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[저장 오류] {path}: {e}")

MERCHANTS: List[Dict] = _load_json(MERCHANTS_FILE, [])
CRAWL_JOBS: Dict[str, Dict] = {}
REPORTS: Dict[str, Dict] = {}

def save_merchants(): _save_json(MERCHANTS_FILE, MERCHANTS)
def save_job(job): _save_json(JOBS_DIR / f"{job['id']}.json", job)
def load_job(job_id):
    if job_id in CRAWL_JOBS: return CRAWL_JOBS[job_id]
    data = _load_json(JOBS_DIR / f"{job_id}.json", None)
    if data: CRAWL_JOBS[job_id] = data
    return data
def save_report(mid, report): _save_json(REPORTS_DIR / f"{mid}.json", report)
def load_report(mid):
    if mid in REPORTS: return REPORTS[mid]
    data = _load_json(REPORTS_DIR / f"{mid}.json", None)
    if data: REPORTS[mid] = data
    return data

executor = ThreadPoolExecutor(max_workers=2)


# ── Pydantic ──────────────────────────────────────────────────────
class MerchantCreate(BaseModel):
    name: str; region: str; place_id: str; instagram_tag: Optional[str] = ""

class MerchantUpdate(BaseModel):
    name: Optional[str]=None; region: Optional[str]=None
    place_id: Optional[str]=None; instagram_tag: Optional[str]=None

class CrawlRequest(BaseModel):
    merchant_id: str


# ── 광고 판별 ──────────────────────────────────────────────────────
AD_KW = [
    "협찬","제공받","유료광고","스폰서","서포터즈","체험단",
    "무상제공","소정의 원고료","원고료를 받고","업체로부터",
    "브랜드로부터","광고임을","PPL","paid partnership",
    "sponsored","#광고","#협찬","#체험단","#서포터즈",
    "#소정의원고료","원고료","제품을 제공","무료로 받",
    "무료체험","지원받","지원을 받","제공해주","광고비",
]
AD_KW_WORD = ["광고"]
ORG_KW = [
    "내돈내산","내돈내먹","솔직후기","솔직리뷰","개인적인 의견",
    "자비로","직접 구매","내 돈 주고","내돈주고","순수 후기",
    "광고아님","비광고","광고 아님","돈 받지 않",
]

def classify_ad(text):
    t = text.lower()
    ad = sum(1 for kw in AD_KW if kw.lower() in t)
    ad += sum(1 for kw in AD_KW_WORD
              if re.search(r'(?<![가-힣a-z])' + re.escape(kw) + r'(?![가-힣a-z])', t))
    org = sum(1 for kw in ORG_KW if kw.lower() in t)
    if ad > 0 and ad >= org: return "광고"
    elif org > 0: return "내돈내산"
    return "판별불가"

def get_basis(text, ad_type):
    if ad_type == "광고":
        found = [kw for kw in AD_KW+AD_KW_WORD if kw.lower() in text.lower()]
        return f"광고 표시 발견: '{found[0]}'" if found else "광고 관련 표현 포함"
    if ad_type == "내돈내산":
        found = [kw for kw in ORG_KW if kw.lower() in text.lower()]
        return f"내돈내산 표시 발견: '{found[0]}'" if found else "내돈내산 표현 포함"
    return "광고/내돈내산 표시 없음"


# ── 공통 브라우저 팩토리 ─────────────────────────────────────────
def make_pc_browser(p):
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox","--disable-setuid-sandbox",
            "--disable-dev-shm-usage","--disable-gpu",
            "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled",
        ]
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="ko-KR",
    )
    return browser, ctx

def make_mobile_browser(p):
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox","--disable-setuid-sandbox",
            "--disable-dev-shm-usage","--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]
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
    return browser, ctx


# ════════════════════════════════════════════════════════════
# STEP 0: 공식 리뷰 수 파싱
# - 홈 페이지: 방문자 리뷰 239, 블로그 리뷰 77
# - 리뷰 탭 페이지: 키워드·별점 리뷰 14
# ════════════════════════════════════════════════════════════
def get_official_counts(place_id):
    counts = {
        "receipt_total": 0,
        "receipt_text_total": 0,
        "receipt_keyword": 0,
        "blog_total": 0,
    }
    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)

            # 1. 홈 페이지: 방문자 리뷰 / 블로그 리뷰 수
            page = ctx.new_page()
            page.goto(
                f"https://pcmap.place.naver.com/restaurant/{place_id}/home",
                wait_until="domcontentloaded", timeout=30000
            )
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
            page.close()

            m1 = re.search(r'방문자\s*리뷰\s*([\d,]+)', text)
            m2 = re.search(r'블로그\s*리뷰\s*([\d,]+)', text)
            if m1: counts["receipt_total"] = int(m1.group(1).replace(",",""))
            if m2: counts["blog_total"]    = int(m2.group(1).replace(",",""))

            # 2. 리뷰 탭 페이지: 키워드·별점 리뷰 수
            page2 = ctx.new_page()
            page2.goto(
                f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor",
                wait_until="domcontentloaded", timeout=30000
            )
            page2.wait_for_timeout(2000)
            text2 = page2.inner_text("body")
            page2.close()

            # "키워드·별점 리뷰 14" 또는 "키워드 별점 리뷰 14" 패턴
            m3 = re.search(r'키워드[·\s]*별점\s*리뷰\s*([\d,]+)', text2)
            if m3:
                counts["receipt_keyword"] = int(m3.group(1).replace(",",""))

            browser.close()

        # 텍스트 리뷰 수 계산
        if counts["receipt_total"] > 0:
            if counts["receipt_keyword"] > 0:
                counts["receipt_text_total"] = counts["receipt_total"] - counts["receipt_keyword"]
            else:
                counts["receipt_text_total"] = counts["receipt_total"]

        print(f"[공식 수] 방문자:{counts['receipt_total']} 키워드별점:{counts['receipt_keyword']} 텍스트:{counts['receipt_text_total']} 블로그:{counts['blog_total']}")

    except Exception as e:
        print(f"[공식 수 오류] {e}")
    return counts


# ════════════════════════════════════════════════════════════
# STEP 1: 영수증(방문자) 리뷰
# 동작: ① 맨 아래 스크롤 → ② "펼쳐서 더보기" 클릭 → 반복
# URL: 모바일 우선, 실패 시 PC 버전 시도
# ════════════════════════════════════════════════════════════
def crawl_receipt_reviews(place_id, target=500, progress_cb=None):
    reviews = []
    seen_texts = set()

    urls_to_try = [
        ("mobile", f"https://m.place.naver.com/restaurant/{place_id}/review/visitor?entry=ple&reviewSort=recent"),
        ("pc",     f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor?entry=ple&reviewSort=recent"),
    ]

    for url_type, attempt_url in urls_to_try:
        print(f"[영수증] 시도({url_type}): {attempt_url}")
        try:
            with sync_playwright() as p:
                if url_type == "mobile":
                    browser, ctx = make_mobile_browser(p)
                else:
                    browser, ctx = make_pc_browser(p)

                page = ctx.new_page()
                page.goto(attempt_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

                body_text = page.inner_text("body")
                print(f"[영수증] 텍스트 길이: {len(body_text)}자")
                print(f"[영수증] 샘플(100자): {body_text[:100]}")

                # 페이지 정상 로드 확인
                is_valid = len(body_text) > 300 and any(
                    kw in body_text for kw in ["리뷰","별점","방문","영수증","음식"]
                )
                if not is_valid:
                    print(f"[영수증] 페이지 유효하지 않음 → 다음 URL")
                    browser.close()
                    continue

                round_num = 0
                max_rounds = 80  # 최대 80라운드 (라운드당 ~10건 × 80 = 800건 커버)

                while round_num < max_rounds:
                    round_num += 1

                    # ① 맨 아래까지 스크롤
                    for _ in range(5):
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(350)

                    # ② JS로 직접 텍스트 추출 (page.content() + BeautifulSoup 대신 → 메모리 절약)
                    try:
                        texts = page.evaluate("""
                            () => {
                                const SKIP = new Set(['펼쳐서 더보기','더보기','반응 남기기','좋아요','신고','접기']);
                                const results = [];
                                // div.pui__vn15t2 직접 추출
                                document.querySelectorAll('div.pui__vn15t2').forEach(el => {
                                    let t = (el.innerText || '').trim();
                                    SKIP.forEach(s => { t = t.replace(s, '').trim(); });
                                    if (t.length >= 10) results.push(t);
                                });
                                // 폴백: li 전체 텍스트
                                if (results.length === 0) {
                                    document.querySelectorAll('li.pui__X35jYm, li[class*="pui__X35jYm"]').forEach(li => {
                                        let t = (li.innerText || '').trim();
                                        SKIP.forEach(s => { t = t.replace(s, '').trim(); });
                                        if (t.length >= 10) results.push(t);
                                    });
                                }
                                return results;
                            }
                        """)
                    except Exception as e:
                        print(f"[영수증] JS 추출 오류: {e}")
                        texts = []

                    if round_num == 1:
                        print(f"[영수증 진단] JS 추출: {len(texts or [])}건, 샘플: {(texts or ['없음'])[0][:60]}")

                    for text in (texts or []):
                        text = text.strip()
                        if len(text) >= 10 and text not in seen_texts:
                            seen_texts.add(text)
                            ad_type = classify_ad(text)
                            reviews.append({
                                "text": text[:500],
                                "ad_type": ad_type,
                                "ad_basis": get_basis(text, ad_type),
                                "source": "naver_receipt",
                            })

                    current = len(reviews)
                    if progress_cb:
                        progress_cb(current, target,
                            f"영수증리뷰 수집 중... ({current}건 / 목표 {target}건, {round_num}라운드)")
                    print(f"[영수증] 라운드 {round_num}: {current}건")

                    if current >= target:
                        print(f"[영수증] 목표 달성: {current}건")
                        break

                    # ③ "펼쳐서 더보기" 버튼 클릭
                    clicked = False
                    for sel in [
                        "a:has-text('펼쳐서 더보기')",
                        "button:has-text('펼쳐서 더보기')",
                        "span:has-text('펼쳐서 더보기')",
                        "a.fvwqf",
                        "a.place_bluelink",
                    ]:
                        try:
                            btn = page.locator(sel).last
                            if btn.is_visible(timeout=1500):
                                btn.scroll_into_view_if_needed()
                                page.wait_for_timeout(200)
                                btn.click()
                                page.wait_for_timeout(1200)
                                clicked = True
                                print(f"[영수증] 버튼 클릭: {sel}")
                                break
                        except Exception:
                            continue

                    if not clicked:
                        print(f"[영수증] 버튼 없음 → 종료 ({current}건)")
                        break

                browser.close()

                if len(reviews) > 0:
                    print(f"[영수증] {url_type} URL 성공")
                    break

        except Exception as e:
            print(f"[영수증 오류] {url_type}: {e}")
            import traceback; traceback.print_exc()

    print(f"[영수증] 최종: {len(reviews)}건")
    return reviews


def _collect_reviews_from_html(html, reviews, seen_texts, round_num=0):
    """HTML에서 리뷰 텍스트 수집"""
    from bs4 import BeautifulSoup
    bs = BeautifulSoup(html, "html.parser")
    SKIP = {"펼쳐서 더보기","더보기","반응 남기기","좋아요","신고","접기","펼치기"}

    # 1라운드에서 진단 로그
    if round_num == 1:
        all_li = bs.find_all("li")
        print(f"[영수증 진단] 전체 li: {len(all_li)}개")
        if all_li:
            classes = [str(li.get("class","")) for li in all_li[:8]]
            print(f"[영수증 진단] li 클래스 샘플: {classes}")
        all_div = bs.find_all("div", class_=re.compile("vn15t2|review|Review"))
        print(f"[영수증 진단] review 관련 div: {len(all_div)}개")

    for li_sel, content_sel in [
        ("li.pui__X35jYm.EjjAW", "div.pui__vn15t2"),
        ("li.pui__X35jYm",       "div.pui__vn15t2"),
        ("li[class*='pui__X35jYm']", "div[class*='pui__vn15t2']"),
        ("li.EjjAW",             "div.pui__vn15t2"),
        (None,                   "div.pui__vn15t2"),  # div 직접
    ]:
        if li_sel:
            items = bs.select(li_sel)
        else:
            items = bs.select("div.pui__vn15t2")

        if not items:
            continue

        for item in items:
            if content_sel and li_sel:
                el = item.select_one(content_sel) or item
            else:
                el = item
            text = el.get_text(separator=" ", strip=True)
            for s in SKIP:
                text = text.replace(s, "").strip()
            if len(text) >= 10 and text not in seen_texts:
                seen_texts.add(text)
                ad_type = classify_ad(text)
                reviews.append({
                    "text": text[:500],
                    "ad_type": ad_type,
                    "ad_basis": get_basis(text, ad_type),
                    "source": "naver_receipt",
                })
        if items:
            break


# ════════════════════════════════════════════════════════════
# STEP 2: 블로그 리뷰 목록 수집
# 동작: 동일하게 스크롤 → "펼쳐서 더보기" 반복
# ════════════════════════════════════════════════════════════
def crawl_blog_links(place_id, merchant_name="", target=100, progress_cb=None):
    links = []
    seen_urls = set()

    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)
            page = ctx.new_page()

            url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/ugc"
            print(f"[블로그] 접속: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)

            if progress_cb: progress_cb(0, target)

            round_num = 0
            max_rounds = 20

            while round_num < max_rounds:
                round_num += 1

                for _ in range(6):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(400)

                _collect_blog_links(page, links, seen_urls)
                current = len(links)

                if progress_cb: progress_cb(current, target)
                print(f"[블로그] 라운드 {round_num}: {current}건")

                if current >= target:
                    break

                clicked = False
                for sel in [
                    "a:has-text('펼쳐서 더보기')",
                    "button:has-text('펼쳐서 더보기')",
                    "a.fvwqf", "a.place_bluelink",
                ]:
                    try:
                        btn = page.locator(sel).last
                        if btn.is_visible(timeout=1500):
                            btn.scroll_into_view_if_needed()
                            btn.click()
                            page.wait_for_timeout(1500)
                            clicked = True
                            break
                    except Exception:
                        continue

                if not clicked:
                    print(f"[블로그] '펼쳐서 더보기' 없음 → 종료 ({current}건)")
                    break

            browser.close()

    except Exception as e:
        print(f"[블로그 목록 오류] {e}")

    # 폴백
    if len(links) < 5 and merchant_name:
        print("[블로그] 수집 부족 → 네이버 블로그 검색 폴백")
        extra = _crawl_blog_links_via_search(merchant_name, target-len(links), seen_urls)
        links.extend(extra)

    if progress_cb: progress_cb(len(links), target)
    print(f"[블로그] 최종: {len(links)}건")
    return links


def _collect_blog_links(page, links, seen_urls):
    from bs4 import BeautifulSoup
    bs = BeautifulSoup(page.content(), "html.parser")
    for a in bs.find_all("a", href=True):
        href = a["href"]
        if not ("blog.naver.com" in href or "post.naver.com" in href):
            continue
        if href in seen_urls: continue
        if any(x in href for x in ["PostList","?tab=","/profile","CategoryList"]): continue
        seen_urls.add(href)
        title = a.get_text(strip=True)[:120]
        excerpt = ""
        parent = a.find_parent("li") or a.find_parent("div")
        if parent: excerpt = parent.get_text(strip=True)[:300]
        links.append({"url": href, "title": title, "excerpt": excerpt})


def _crawl_blog_links_via_search(merchant_name, target, seen_urls):
    links = []
    try:
        import requests as req_lib
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        for start in range(1, min(target,80)+1, 10):
            if len(links) >= target: break
            try:
                r = req_lib.get(
                    f"https://search.naver.com/search.naver?query={quote(merchant_name)}&where=blog&start={start}",
                    headers=headers, timeout=8
                )
                html = r.text
                blog_urls = re.findall(r'href="(https?://(?:blog\.naver\.com|post\.naver\.com)[^"]+)"', html)
                titles_raw = re.findall(r'<a[^>]+class="[^"]*title[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)
                clean_titles = [re.sub(r'<[^>]+>','',t).strip() for t in titles_raw]
                for i, u in enumerate(blog_urls):
                    if u in seen_urls or len(links) >= target: break
                    if any(x in u for x in ["PostList","?tab=","/profile"]): continue
                    seen_urls.add(u)
                    links.append({"url":u,"title":(clean_titles[i] if i<len(clean_titles) else "")[:120],"excerpt":""})
            except Exception as e:
                print(f"[블로그 검색 폴백] {e}")
                break
    except Exception as e:
        print(f"[블로그 검색 폴백 오류] {e}")
    return links


# ════════════════════════════════════════════════════════════
# STEP 3: 블로그 원문 방문 → 광고 판별
# ════════════════════════════════════════════════════════════
def classify_blog_originals(blog_links, progress_cb=None):
    if not blog_links: return []
    results = []
    total = len(blog_links)

    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)

            for idx, item in enumerate(blog_links):
                page = ctx.new_page()
                try:
                    url = item["url"].replace("m.blog.naver.com","blog.naver.com")
                    page.goto(url, wait_until="domcontentloaded", timeout=8000)
                    page.wait_for_timeout(800)

                    full_text = ""
                    try:
                        frame = page.frame(name="mainFrame")
                        if frame:
                            frame.wait_for_load_state("domcontentloaded", timeout=3000)
                            for sel in [".se-main-container","#postViewArea",".post-view","body"]:
                                try:
                                    el = frame.locator(sel).first
                                    if el.is_visible(timeout=500):
                                        full_text = el.inner_text()
                                        break
                                except Exception: continue
                    except Exception: pass
                    if not full_text:
                        try: full_text = page.inner_text("body")
                        except Exception: full_text = ""

                    ad_type = classify_ad(full_text)
                    title = item.get("title","")
                    if not title:
                        try: title = page.title()[:120]
                        except Exception: pass

                    results.append({
                        "title": title or "제목 없음",
                        "text": full_text[:500],
                        "ad_type": ad_type,
                        "ad_basis": get_basis(full_text, ad_type),
                        "source": "naver_blog",
                        "url": item["url"],
                    })
                    print(f"[블로그 원문] {idx+1}/{total} {ad_type} - {(title or '')[:30]}")

                except Exception as e:
                    excerpt = item.get("excerpt","")
                    ad_type = classify_ad(excerpt)
                    results.append({
                        "title": item.get("title","제목 없음"),
                        "text": excerpt[:500],
                        "ad_type": ad_type,
                        "ad_basis": "원문 접근 실패, 미리보기로 판별",
                        "source": "naver_blog",
                        "url": item.get("url",""),
                    })
                finally:
                    try: page.close()
                    except Exception: pass

                if progress_cb:
                    progress_cb(idx+1, total)

            browser.close()

    except Exception as e:
        print(f"[블로그 원문 오류] {e}")
        for item in blog_links[len(results):]:
            text = item.get("excerpt","")
            ad_type = classify_ad(text)
            results.append({
                "title": item.get("title","제목 없음"),
                "text": text[:500],
                "ad_type": ad_type,
                "ad_basis": get_basis(text,ad_type)+" (미리보기 기반)",
                "source": "naver_blog",
                "url": item.get("url",""),
            })

    print(f"[블로그 원문] 판별 완료: {len(results)}건")
    return results


# ════════════════════════════════════════════════════════════
# STEP 4: 네이버 검색 콘텐츠 수
# ════════════════════════════════════════════════════════════
def crawl_naver_search_count(merchant_name, region):
    query = f"{region} {merchant_name}".strip()
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)
            page = ctx.new_page()
            page.goto(
                f"https://search.naver.com/search.naver?query={quote(query)}&where=blog",
                wait_until="domcontentloaded", timeout=20000
            )
            page.wait_for_timeout(1500)
            text = page.inner_text("body")
            m = re.search(r'약\s*([\d,]+)\s*개', text)
            if m: count = int(m.group(1).replace(",",""))
            if count == 0:
                items = page.locator("li.bx").all()
                count = len(items)
            browser.close()
    except Exception as e:
        print(f"[네이버 검색 오류] {e}")
    return count


# ════════════════════════════════════════════════════════════
# STEP 5: 인스타그램 콘텐츠 수
# ════════════════════════════════════════════════════════════
def crawl_instagram_count(tag):
    clean = tag.replace(" ","").replace("#","")
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_mobile_browser(p)
            page = ctx.new_page()
            page.goto(
                f"https://www.instagram.com/explore/tags/{clean}/",
                wait_until="domcontentloaded", timeout=25000
            )
            page.wait_for_timeout(3500)
            text = page.inner_text("body")
            for pat, unit in [
                (r'([\d.]+)만\s*(?:개\s*)?게시물',"만"),
                (r'게시물\s*([\d,]+)',""),
                (r'([\d,]+(?:\.\d+)?[KMk만천]?)\s*posts?',""),
            ]:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    ns = m.group(1).replace(",","")
                    if unit=="만" or "만" in ns:
                        count = int(float(ns.replace("만",""))*10000)
                    elif ns[-1:].upper()=="K":
                        count = int(float(ns[:-1])*1000)
                    elif ns[-1:].upper()=="M":
                        count = int(float(ns[:-1])*1000000)
                    else:
                        try: count = int(float(ns))
                        except: count = 0
                    break
            if count == 0:
                imgs = page.locator("img[alt]").all()
                count = max(0,(len(imgs)-3)*25)
            browser.close()
    except Exception as e:
        print(f"[인스타그램 오류] {e}")
    return count


# ════════════════════════════════════════════════════════════
# 메인 크롤링 오케스트레이터
# ════════════════════════════════════════════════════════════
def crawl_merchant(job_id, merchant):
    place_id = merchant["place_id"]
    name     = merchant["name"]
    region   = merchant.get("region","")
    ig_tag   = merchant.get("instagram_tag") or name

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
        job.update({"status":"running","progress":pct,"message":msg})
        CRAWL_JOBS[job_id] = job
        save_job(job)
        print(f"[{pct}%] {msg}")

    # 하트비트
    _stop_hb = threading.Event()
    def _heartbeat():
        while not _stop_hb.is_set():
            _stop_hb.wait(30)
            if not _stop_hb.is_set():
                job = CRAWL_JOBS.get(job_id,{})
                if job.get("status")=="running":
                    save_job(job)
                    print(f"[HB] {job_id} {job.get('progress')}%")
    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    try:
        # 0. 공식 수치
        upd(5, "플레이스 공식 리뷰 수 확인 중...")
        counts = get_official_counts(place_id)
        result["place_counts"] = counts

        # 1. 영수증 리뷰
        official_r    = counts.get("receipt_total", 0)      # 방문자 리뷰 전체
        official_text = counts.get("receipt_text_total", 0) # 사진·영상 리뷰
        official_kw   = counts.get("receipt_keyword", 0)    # 키워드·별점 리뷰
        # 수집 목표: 사진·영상 리뷰 수 기준, 상한 500건
        crawl_target = min((official_text or official_r or 250), 500)

        upd(10, f"영수증리뷰 수집 중... (사진·영상 {official_text or official_r}건 목표)")

        def receipt_progress(loaded, total, msg=None):
            pct = 10 + int((min(loaded, total) / max(total,1)) * 28)
            upd(min(pct, 38), msg or f"영수증리뷰 수집 중... ({loaded}건)")

        receipt = crawl_receipt_reviews(
            place_id,
            target=crawl_target,
            progress_cb=receipt_progress,
        )
        result["naver_receipt_reviews"] = receipt
        upd(40, f"영수증리뷰 {len(receipt)}건 수집 완료")

        # 2. 블로그 목록
        official_b = counts.get("blog_total", 0)
        blog_target = min(official_b or 50, 100)

        def blog_list_progress(current, total):
            pct = 43 + int((min(current,total)/max(total,1))*13)
            upd(pct, f"블로그리뷰 목록 수집 중... ({current}건 / 목표 {total}건)")

        upd(43, f"블로그리뷰 목록 수집 중... (공식 {official_b}건)")
        blog_links = crawl_blog_links(
            place_id=place_id, merchant_name=name,
            target=blog_target, progress_cb=blog_list_progress,
        )
        upd(57, f"블로그 링크 {len(blog_links)}건 확보, 원문 방문 시작...")

        # 3. 블로그 원문 판별
        def blog_orig_progress(done, total):
            pct = 57 + int((done/max(total,1))*18)
            upd(pct, f"블로그 원문 방문 중... ({done}/{total}건)")

        blog_reviews = classify_blog_originals(blog_links, progress_cb=blog_orig_progress)
        result["naver_blog_reviews"] = blog_reviews
        upd(75, f"블로그리뷰 {len(blog_reviews)}건 분석 완료")

        # 4. 네이버 검색
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
        job = CRAWL_JOBS.get(job_id,{})
        job.update({"status":"error","message":f"오류: {str(e)}"})
        CRAWL_JOBS[job_id] = job
        save_job(job)
        print(f"[ERROR] {e}")
        return

    _stop_hb.set()

    receipt_list = result["naver_receipt_reviews"]
    blog_list    = result["naver_blog_reviews"]

    result["summary"] = {
        # 방문자 리뷰 상세 구분
        "official_receipt_count":      result["place_counts"].get("receipt_total", 0),       # 전체 (239)
        "official_receipt_text_count": result["place_counts"].get("receipt_text_total", 0),  # 사진·영상 (225)
        "official_receipt_keyword":    result["place_counts"].get("receipt_keyword", 0),     # 키워드·별점 (14)
        "official_blog_count":         result["place_counts"].get("blog_total", 0),
        # 실제 수집 수
        "total_receipt_reviews":  len(receipt_list),
        "total_blog_reviews":     len(blog_list),
        "naver_search_count":     result["naver_search_count"],
        "instagram_count":        result["instagram_count"],
        # 광고 판별
        "blog_ad_count":      sum(1 for r in blog_list if r["ad_type"]=="광고"),
        "blog_organic_count": sum(1 for r in blog_list if r["ad_type"]=="내돈내산"),
        "blog_unknown_count": sum(1 for r in blog_list if r["ad_type"]=="판별불가"),
        "receipt_ad_count":      sum(1 for r in receipt_list if r["ad_type"]=="광고"),
        "receipt_organic_count": sum(1 for r in receipt_list if r["ad_type"]=="내돈내산"),
        "receipt_unknown_count": sum(1 for r in receipt_list if r["ad_type"]=="판별불가"),
    }

    REPORTS[merchant["id"]] = result
    save_report(merchant["id"], result)

    done_job = {**CRAWL_JOBS.get(job_id,{}),
                "status":"done","progress":100,
                "message":"분석 완료","report_id":merchant["id"]}
    CRAWL_JOBS[job_id] = done_job
    save_job(done_job)
    print(f"[DONE] {name} / 영수증:{len(receipt_list)} 블로그:{len(blog_list)}")


# ════════════════════════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════════════════════════
@app.get("/")
async def root(): return {"message":"SNS 분석 솔루션 API v19"}

@app.get("/api/merchants")
async def get_merchants(): return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(data: MerchantCreate):
    m = {"id":str(uuid.uuid4())[:8],"name":data.name,"region":data.region,
         "place_id":data.place_id,"instagram_tag":data.instagram_tag or data.name,
         "created_at":datetime.now().isoformat()}
    MERCHANTS.append(m); save_merchants(); return m

@app.put("/api/merchants/{mid}")
async def update_merchant(mid: str, data: MerchantUpdate):
    m = next((m for m in MERCHANTS if m["id"]==mid), None)
    if not m: raise HTTPException(404,"가맹점 없음")
    for f in ["name","region","place_id","instagram_tag"]:
        v = getattr(data,f)
        if v is not None: m[f] = v
    save_merchants(); return m

@app.delete("/api/merchants/{mid}")
async def delete_merchant(mid: str):
    global MERCHANTS
    MERCHANTS = [m for m in MERCHANTS if m["id"]!=mid]
    save_merchants(); return {"deleted":mid}

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    merchant = next((m for m in MERCHANTS if m["id"]==req.merchant_id), None)
    if not merchant: raise HTTPException(404,"가맹점 없음")
    job_id = str(uuid.uuid4())
    job = {"id":job_id,"merchant_id":req.merchant_id,"merchant_name":merchant["name"],
           "status":"pending","progress":0,"message":"분석 대기 중...",
           "started_at":datetime.now().isoformat()}
    CRAWL_JOBS[job_id] = job
    save_job(job)
    executor.submit(crawl_merchant, job_id, merchant)
    return {"job_id":job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job(job_id: str):
    j = load_job(job_id)
    if not j: raise HTTPException(404,"작업 없음")
    return j

@app.get("/api/reports/{mid}")
async def get_report(mid: str):
    r = load_report(mid)
    if not r: raise HTTPException(404,"리포트 없음. 분석을 먼저 실행하세요.")
    return r

@app.get("/api/health")
async def health():
    return {"status":"ok","playwright":PLAYWRIGHT_AVAILABLE,
            "merchants":len(MERCHANTS),"reports":len(REPORTS)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT",8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

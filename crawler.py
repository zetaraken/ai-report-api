"""
crawler.py - SNS 분석 독립 크롤러 v40
subprocess로 실행되어 greenlet 충돌을 원천 차단.
결과는 JSON 파일로 저장.

v40 변경사항:
  1. 클릭 간격 랜덤화: 1.2초 고정 → 2~5초 랜덤
  2. 스크롤 자연화: 한 번에 맨 아래가 아니라 조금씩 내리기
  3. 네이버 로그인 쿠키 주입: 환경변수 NAVER_COOKIES에서 읽어서 Playwright에 주입

실행: python crawler.py <place_id> <merchant_name> <region> <ig_tag> <crawl_target> <blog_target> <output_path>
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright


# ── 네이버 쿠키 로드 ──────────────────────────────────────────────
# Railway 환경변수에 개별 등록된 쿠키들을 읽어서 Playwright용 배열로 변환
# 등록된 변수: ASID, BUC, NAC, nid_inf, NID_JST 등
def load_naver_cookies():
    cookie_defs = [
        ("NID_AUT",   ".naver.com"),
        ("NID_SES",   ".naver.com"),
        ("NID_JST",   ".nid.naver.com"),
        ("BUC",       ".naver.com"),
        ("ASID",      ".naver.com"),
        ("NAC",       ".naver.com"),
        ("nid_inf",   ".naver.com"),
        ("nid_buk",   ".nid.naver.com"),
        ("NNB",       ".naver.com"),
    ]
    cookies = []
    for name, domain in cookie_defs:
        value = os.environ.get(name, "")
        if value:
            cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
            })
    if cookies:
        print(f"[쿠키] {len(cookies)}개 로드: {[c['name'] for c in cookies]}")
    else:
        print("[쿠키] 환경변수 없음 → 비로그인 모드")
    return cookies

NAVER_COOKIES = load_naver_cookies()


# ── 사람처럼 동작하는 헬퍼 함수 ──────────────────────────────────
def human_sleep(min_sec=2.0, max_sec=5.0):
    """랜덤 대기 — 봇 감지 회피"""
    t = random.uniform(min_sec, max_sec)
    time.sleep(t)
    return t

def human_scroll(page, steps=5):
    """조금씩 나눠서 스크롤 — 사람처럼"""
    try:
        total_height = page.evaluate("document.body.scrollHeight")
        current = page.evaluate("window.scrollY")
        step_size = max((total_height - current) // steps, 100)
        for i in range(steps):
            next_pos = current + step_size * (i + 1)
            page.evaluate(f"window.scrollTo(0, {next_pos})")
            time.sleep(random.uniform(0.15, 0.4))
        # 마지막엔 맨 아래
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(random.uniform(0.2, 0.5))
    except Exception:
        pass

def inject_cookies(ctx):
    """Playwright 컨텍스트에 네이버 쿠키 주입"""
    if not NAVER_COOKIES:
        return
    try:
        formatted = []
        for c in NAVER_COOKIES:
            cookie = {
                "name":   c.get("name", ""),
                "value":  c.get("value", ""),
                "domain": c.get("domain", ".naver.com"),
                "path":   c.get("path", "/"),
            }
            if c.get("secure"): cookie["secure"] = True
            if c.get("httpOnly"): cookie["httpOnly"] = True
            formatted.append(cookie)
        ctx.add_cookies(formatted)
        print(f"[쿠키] {len(formatted)}개 주입 완료")
    except Exception as e:
        print(f"[쿠키] 주입 오류: {e}")


# ── 광고 판별 ─────────────────────────────────────────────────────
AD_KW = [
    # 직접 협찬/광고 표시 텍스트
    "협찬","제공받","유료광고","스폰서","서포터즈","체험단",
    "무상제공","소정의 원고료","원고료를 받고","업체로부터",
    "브랜드로부터","광고임을","PPL","paid partnership",
    "sponsored","#광고","#협찬","#체험단","#서포터즈",
    "#소정의원고료","원고료","제품을 제공","무료로 받",
    "무료체험","지원받","지원을 받","제공해주","광고비",
    # 명확한 협찬 표시
    "무료로 제공받아","초대받아 방문",
]
AD_KW_WORD = ["광고"]
ORG_KW = [
    "내돈내산","내돈내먹","솔직후기","솔직리뷰","개인적인 의견",
    "자비로","직접 구매","내 돈 주고","내돈주고","순수 후기",
    "광고아님","비광고","광고 아님","돈 받지 않",
    "개인 방문","개인방문","자발적","자비",
]

def classify_ad(text):
    t = text.lower()
    ad = sum(1 for kw in AD_KW if kw.lower() in t)
    ad += sum(1 for kw in AD_KW_WORD
              if re.search(r'(?<![가-힣a-z])' + re.escape(kw) + r'(?![가-힣a-z])', t))
    org = sum(1 for kw in ORG_KW if kw.lower() in t)
    if ad > 0 and ad >= org: return "광고"
    if org > 0: return "내돈내산"
    return "내돈내산"  # 협찬 배지 없고 광고 키워드도 없으면 내돈내산

def get_basis(text, ad_type):
    if ad_type == "광고":
        found = [kw for kw in AD_KW + AD_KW_WORD if kw.lower() in text.lower()]
        return f"광고 표시 발견: '{found[0]}'" if found else "광고 관련 표현 포함"
    if ad_type == "내돈내산":
        found = [kw for kw in ORG_KW if kw.lower() in text.lower()]
        return f"내돈내산 표시 발견: '{found[0]}'" if found else "내돈내산 표현 포함"
    return "광고/내돈내산 표시 없음"


# ── 브라우저 팩토리 ───────────────────────────────────────────────
def make_pc_browser(p):
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox","--disable-setuid-sandbox",
              "--disable-dev-shm-usage","--disable-gpu",
              "--window-size=1920,1080",
              "--disable-blink-features=AutomationControlled"]
    )
    ctx = browser.new_context(
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"),
        viewport={"width": 1920, "height": 1080},
        locale="ko-KR",
    )
    inject_cookies(ctx)
    return browser, ctx

def make_mobile_browser(p):
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox","--disable-setuid-sandbox",
              "--disable-dev-shm-usage","--disable-gpu",
              "--disable-blink-features=AutomationControlled"]
    )
    ctx = browser.new_context(
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/16.6 Mobile/15E148 Safari/604.1"),
        viewport={"width": 390, "height": 844},
        locale="ko-KR",
    )
    inject_cookies(ctx)
    return browser, ctx


# ════════════════════════════════════════════════════════════
# STEP 0: 공식 리뷰 수
# ════════════════════════════════════════════════════════════
def get_official_counts(place_id):
    counts = {"receipt_total":0,"receipt_text_total":0,"receipt_keyword":0,"blog_total":0,"addr_keyword":""}
    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)

            page = ctx.new_page()
            page.goto(f"https://pcmap.place.naver.com/restaurant/{place_id}/home",
                      wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.0)
            text = page.inner_text("body")
            page.close()

            m1 = re.search(r'방문자\s*리뷰\s*([\d,]+)', text)
            m2 = re.search(r'블로그\s*리뷰\s*([\d,]+)', text)
            if m1: counts["receipt_total"] = int(m1.group(1).replace(",",""))
            if m2: counts["blog_total"]    = int(m2.group(1).replace(",",""))

            # 주소에서 검색 필터링용 동네명 추출
            addr_kw = ""
            html_home = page.content()

            # 네이버 플레이스 HTML 실제 구조:
            # <span class="TjXg1">지번</span>"경기 화성시 동탄구 방교동 771-4"
            dong_patterns = [
                # 실제 확인된 패턴: TjXg1 클래스 span 다음 지번주소 텍스트
                r'TjXg1[^>]*>지번</span>\s*"?경기[^"]*?([가-힣]{2,5}(?:동|읍|면|리))\s+\d',
                r'TjXg1[^>]*>지번</span>[^가-힣]{0,30}([가-힣]{2,5}(?:동|읍|면|리))\s+\d',
                # JSON 형태
                r'"jibunAddress"\s*:\s*"[^"]*?([가-힣]{2,5}(?:동|읍|면|리))\s+\d',
                # 지번 텍스트 기반
                r'지번[^가-힣]{0,20}([가-힣]{2,5}(?:동|읍|면|리))\s+\d',
                # 지번 형식 (동명 + 번지)
                r'([가-힣]{2,5}(?:동|읍|면|리))\s+\d{2,4}-\d{1,4}',
            ]
            for pat in dong_patterns:
                m = re.search(pat, html_home)
                if m:
                    addr_kw = m.group(1)
                    print(f"[주소 키워드] '{addr_kw}' 추출 (HTML 지번)")
                    break

            # 폴백: innerText에서 읍면동 추출
            if not addr_kw:
                dong_in_text = re.search(
                    r'(?:[가-힣]+시|[가-힣]+군)\s+'
                    r'(?:[가-힣]{2,5}(?:구|군)\s+)?'
                    r'([가-힣]{1,5}(?:동|읍|면|리))'
                    r'(?=\s*\d)',
                    text
                )
                if dong_in_text:
                    addr_kw = dong_in_text.group(1)
                    print(f"[주소 키워드] '{addr_kw}' 추출 (텍스트)")

            if not addr_kw:
                print(f"[주소 키워드] 추출 실패 — region 폴백 사용")

            if not addr_kw:
                print(f"[주소 키워드] 추출 실패 — region 폴백 사용")

            counts["addr_keyword"] = addr_kw

            page2 = ctx.new_page()
            page2.goto(f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor",
                       wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.0)
            text2 = page2.inner_text("body")
            page2.close()
            browser.close()

            m3 = re.search(r'키워드[·\s]*별점\s*리뷰\s*([\d,]+)', text2)
            if m3: counts["receipt_keyword"] = int(m3.group(1).replace(",",""))

        if counts["receipt_total"] > 0:
            counts["receipt_text_total"] = (
                counts["receipt_total"] - counts["receipt_keyword"]
                if counts["receipt_keyword"] > 0
                else counts["receipt_total"]
            )
        print(f"[공식 수] 방문자:{counts['receipt_total']} 키워드별점:{counts['receipt_keyword']} 텍스트:{counts['receipt_text_total']} 블로그:{counts['blog_total']}")
    except Exception as e:
        print(f"[공식 수 오류] {e}")
    return counts


# ════════════════════════════════════════════════════════════
# STEP 1: 영수증(방문자) 리뷰  v42
# Playwright → Selenium 전환 + BeautifulSoup 파싱
# Selenium은 Playwright와 봇 감지 패턴이 달라 차단 우회 가능성 높음
# ════════════════════════════════════════════════════════════
def crawl_receipt_reviews(place_id, target=500):
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from bs4 import BeautifulSoup

    reviews = []
    seen_texts = set()

    def make_driver():
        opts = Options()
        opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=390,844")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument(
            "user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/16.6 Mobile/15E148 Safari/604.1"
        )
        driver = webdriver.Chrome(options=opts)
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"}
        )
        driver.get("https://www.naver.com")
        for c in NAVER_COOKIES:
            try:
                driver.add_cookie({
                    "name": c["name"], "value": c["value"],
                    "domain": c.get("domain", ".naver.com"), "path": "/"
                })
            except Exception:
                pass
        if NAVER_COOKIES:
            print(f"[영수증] Selenium 쿠키 {len(NAVER_COOKIES)}개 주입")
        return driver

    def collect_from_html(html):
        soup = BeautifulSoup(html, "html.parser")
        texts = []
        SKIP = {"펼쳐서 더보기","더보기","반응 남기기","좋아요","신고","접기"}
        for el in soup.select("div.pui__vn15t2"):
            t = el.get_text(strip=True)
            for s in SKIP: t = t.replace(s, "").strip()
            if len(t) >= 10:
                texts.append(t)
        if not texts:
            for li in soup.select("li.pui__X35jYm"):
                t = li.get_text(strip=True)
                for s in SKIP: t = t.replace(s, "").strip()
                if len(t) >= 10:
                    texts.append(t)
        return texts

    def sel_scroll(driver, steps=5):
        try:
            total = driver.execute_script("return document.body.scrollHeight")
            current = driver.execute_script("return window.scrollY")
            step = max((total - current) // steps, 100)
            for i in range(steps):
                driver.execute_script(f"window.scrollTo(0, {current + step * (i+1)})")
                time.sleep(random.uniform(0.2, 0.5))
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(random.uniform(0.3, 0.6))
        except Exception:
            pass

    url = f"https://m.place.naver.com/restaurant/{place_id}/review/visitor?entry=ple&reviewSort=recent"
    print(f"[영수증] Selenium v42 시작: {url}")
    driver = None
    try:
        driver = make_driver()
        driver.get(url)
        time.sleep(4.0)

        body = driver.find_element(By.TAG_NAME, "body").text
        if len(body) < 300 or not any(kw in body for kw in ["리뷰","별점","방문","음식"]):
            print("[영수증] 페이지 유효하지 않음")
            raise Exception("invalid page")

        round_num = 0
        zero_streak = 0
        no_btn_streak = 0

        while round_num < 30:
            round_num += 1
            sel_scroll(driver, steps=random.randint(4, 7))

            raw = collect_from_html(driver.page_source)
            new_count = 0
            for t in raw:
                t = t.strip()
                if len(t) >= 10 and t not in seen_texts:
                    seen_texts.add(t)
                    reviews.append({
                        "text": t[:500],
                        "source": "naver_receipt",
                    })
                    new_count += 1
                elif len(t) >= 10:
                    seen_texts.add(t)

            current = len(reviews)
            print(f"[영수증] 라운드 {round_num}: +{new_count}건 → 누적 {current}건")
            _write_progress(
                f"영수증리뷰 수집 중... ({current}건 / 목표 {target}건, {round_num}라운드)",
                10 + int((min(current, target) / max(target, 1)) * 28)
            )

            if current >= target:
                print(f"[영수증] 목표 달성: {current}건")
                break

            texts_found = len([t for t in raw if len(t.strip()) >= 10])
            if texts_found == 0:
                zero_streak += 1
                if zero_streak >= 5:
                    print("[영수증] 텍스트 없음 5회 연속 → 종료")
                    break
            else:
                zero_streak = 0

            clicked = False
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//*[contains(text(),'펼쳐서 더보기')]"))
                )
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                wait_sec = random.uniform(1.2, 2.5)
                print(f"[영수증] 버튼 클릭 — {wait_sec:.1f}초 대기")
                time.sleep(wait_sec)
            except Exception:
                pass

            if not clicked:
                no_btn_streak += 1
                print(f"[영수증] 버튼 없음 (연속 {no_btn_streak}회)")
                if no_btn_streak >= 3:
                    print("[영수증] 버튼 3회 연속 미발견 → 종료")
                    break
                for _ in range(3):
                    sel_scroll(driver, steps=random.randint(3, 5))
                    time.sleep(random.uniform(0.4, 0.8))
            else:
                no_btn_streak = 0

    except Exception as e:
        print(f"[영수증] Selenium 오류: {e}")
    finally:
        if driver:
            try: driver.quit()
            except: pass

    print(f"[영수증] 최종: {len(reviews)}건")
    return reviews

# ════════════════════════════════════════════════════════════
# STEP 2: 블로그 링크 수집
# ════════════════════════════════════════════════════════════
def crawl_blog_links(place_id, merchant_name="", target=100):
    from bs4 import BeautifulSoup
    links = []
    seen_urls = set()

    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)
            page = ctx.new_page()
            url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/ugc"
            print(f"[블로그] 접속: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(3.0)

            for round_num in range(1, 21):
                for _ in range(6):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(0.4)

                bs = BeautifulSoup(page.content(), "html.parser")
                for a in bs.find_all("a", href=True):
                    href = a["href"]
                    if not ("blog.naver.com" in href or "post.naver.com" in href): continue
                    if href in seen_urls: continue
                    if any(x in href for x in ["PostList","?tab=","/profile","CategoryList"]): continue
                    seen_urls.add(href)
                    title = a.get_text(strip=True)[:120]
                    parent = a.find_parent("li") or a.find_parent("div")
                    excerpt = parent.get_text(strip=True)[:300] if parent else ""
                    links.append({"url": href, "title": title, "excerpt": excerpt})

                current = len(links)
                print(f"[블로그] 라운드 {round_num}: {current}건")
                _write_progress(f"블로그리뷰 목록 수집 중... ({current}건 / 목표 {target}건)", 43)

                if current >= target: break

                clicked = False
                for sel in ["a:has-text('펼쳐서 더보기')", "button:has-text('펼쳐서 더보기')", "a.fvwqf"]:
                    try:
                        btn = page.locator(sel).last
                        if btn.is_visible(timeout=1500):
                            btn.scroll_into_view_if_needed()
                            btn.click()
                            time.sleep(1.5)
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    print(f"[블로그] 버튼 없음 → 종료 ({current}건)")
                    break

            browser.close()
    except Exception as e:
        print(f"[블로그 목록 오류] {e}")

    # 폴백
    if len(links) < 5 and merchant_name:
        print("[블로그] 수집 부족 → 검색 폴백")
        import requests as req_lib
        headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36", "Accept-Language": "ko-KR"}
        for start in range(1, min(target, 80)+1, 10):
            if len(links) >= target: break
            try:
                r = req_lib.get(
                    f"https://search.naver.com/search.naver?query={quote(merchant_name)}&where=blog&start={start}",
                    headers=headers, timeout=8)
                blog_urls = re.findall(r'href="(https?://(?:blog\.naver\.com|post\.naver\.com)[^"]+)"', r.text)
                for u in blog_urls:
                    if u in seen_urls or len(links) >= target: break
                    if any(x in u for x in ["PostList","?tab=","/profile"]): continue
                    seen_urls.add(u)
                    links.append({"url": u, "title": "", "excerpt": ""})
            except Exception:
                break

    print(f"[블로그] 최종: {len(links)}건")
    return links


# ════════════════════════════════════════════════════════════
# STEP 3: 블로그 원문 광고 판별
# ════════════════════════════════════════════════════════════
def classify_blog_originals(blog_links):
    results = []
    total = len(blog_links)
    if not total: return results

    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)
            for idx, item in enumerate(blog_links):
                page = ctx.new_page()
                try:
                    url = item["url"].replace("m.blog.naver.com","blog.naver.com")
                    page.goto(url, wait_until="domcontentloaded", timeout=8000)
                    time.sleep(0.8)

                    # ── 협찬 배지 감지 ──────────────────────────────
                    # 네이버 #협찬 배지는 이미지로 렌더링되어 innerText로 추출 불가
                    # HTML 소스 및 JS 변수에서 협찬 정보 탐지
                    is_sponsored_badge = False
                    try:
                        html_source = page.content()
                        # 네이버 블로그 협찬 정보 패턴
                        sponsored_patterns = [
                            # ① 공정위 협찬 배지 이미지 서버 (실제 확인된 도메인)
                            "reviewnote.cloud",    # 리뷰노트 배지 서버1 (확인)
                            "reviewnote.co.kr",    # 리뷰노트 배지 서버2 (확인)
                            "reviewnote.webp",     # 리뷰노트 배지 이미지 파일명 (확인)
                            "imagepool.io",        # 협찬 배지 CDN 서버 (확인)
                            "gongjeong/v1/image",  # 공정위 배지 이미지 경로 공통
                            # ② 주요 체험단 플랫폼 도메인
                            "revu.net",            # 레뷰
                            "tagby.io",            # 태그바이
                            "cloudreview",         # 클라우드리뷰
                            "chehumdan.com",       # 체험단닷컴
                            "tenping.kr",          # 텐핑
                            "linkprice.com",       # 링크프라이스
                            "posting.monster",     # 포스팅몬스터
                            # ③ 공정위 관련 JS/HTML 변수
                            "ffdInfo",             # 협찬 정보 JS 변수
                            "ffdYn",               # 협찬 여부 플래그
                            '"ffd":true',          # JSON 협찬 플래그
                            "isSponsor",           # 스폰서 여부
                        ]
                        for pat in sponsored_patterns:
                            if pat in html_source:
                                is_sponsored_badge = True
                                print(f"[블로그 원문] 협찬 감지(HTML): '{pat}'")
                                break

                        # iframe 내부도 확인
                        if not is_sponsored_badge:
                            try:
                                frame = page.frame(name="mainFrame")
                                if frame:
                                    frame_html = frame.content()
                                    for pat in sponsored_patterns:
                                        if pat in frame_html:
                                            is_sponsored_badge = True
                                            print(f"[블로그 원문] 협찬 감지(iframe): '{pat}'")
                                            break
                            except Exception:
                                pass

                    except Exception as e:
                        print(f"[블로그 원문] 배지 감지 오류: {e}")

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

                    # 협찬 배지가 감지된 경우 텍스트에 협찬 키워드 주입
                    if is_sponsored_badge:
                        full_text = "협찬 " + full_text

                    ad_type = classify_ad(full_text)
                    title = item.get("title","")
                    if not title:
                        try: title = page.title()[:120]
                        except Exception: pass

                    results.append({
                        "title": title or "제목 없음",
                        "text": full_text[:500],
                        "ad_type": ad_type,
                        "ad_basis": "협찬 배지 감지 (이미지형 UI)" if is_sponsored_badge else get_basis(full_text, ad_type),
                        "source": "naver_blog",
                        "url": item["url"],
                    })
                    print(f"[블로그 원문] {idx+1}/{total} {ad_type} - {(title or '')[:30]}")

                except Exception:
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

                pct = 57 + int(((idx+1)/max(total,1))*18)
                _write_progress(f"블로그 원문 방문 중... ({idx+1}/{total}건)", pct)

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

    print(f"[블로그 원문] 완료: {len(results)}건")
    return results


# ════════════════════════════════════════════════════════════
# STEP 4: 네이버 검색 수
# ════════════════════════════════════════════════════════════
def crawl_naver_search_count(merchant_name, region, addr_keyword=""):
    """
    네이버 블로그 검색 총 건수 수집.
    - 쿼리: "가맹점명" "도로명" 형태로 주소 기반 AND 필터링
    - 도로명 없을 시: "가맹점명" "지역명" 폴백
    - 1차: 블로그 탭 HTML 원본에서 숫자 직접 추출
    - 2차: 통합검색 페이지 블로그 섹션 카운트
    - 3차: li.bx 노출 건수 폴백
    """
    # 주소 도로명이 있으면 가장 정확한 AND 조건으로 검색
    # 예: '"순자매감자탕" "동탄기흥로257번가길"'
    if addr_keyword:
        query = f'"{merchant_name}" "{addr_keyword}"'
    elif region:
        query = f'"{merchant_name}" "{region}"'
    else:
        query = f'"{merchant_name}"'
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)
            page = ctx.new_page()

            # 1차: 블로그 탭 — networkidle까지 대기 후 HTML 파싱
            page.goto(
                f"https://search.naver.com/search.naver?query={quote(query)}&where=blog",
                wait_until="networkidle", timeout=25000
            )
            time.sleep(2.5)

            # HTML 원본에서 숫자 패턴 탐색
            html = page.content()
            text = page.inner_text("body")

            # 네이버 블로그 탭 총 건수 패턴들
            # 주의: "totalCount" 같은 범용 JSON 키는 방문자리뷰 수 등과 혼동되므로 제외
            for pattern in [
                r'약\s*([\d,]+)\s*개',                          # "약 1,234개"
                r'<strong[^>]*>\s*([\d,]+)\s*</strong>\s*개',   # <strong>1,234</strong>개
                r'([\d,]+)\s*개의?\s*검색결과',                  # "1,234개의 검색결과"
                r'검색결과\s*([\d,]+)',                          # "검색결과 1,234"
                r'결과\s*([\d,]+)\s*개',                        # "결과 1,234개"
                r'"blogTotal"\s*:\s*(\d+)',                     # 블로그 전용 JSON 키
                r'"blog_total"\s*:\s*(\d+)',
            ]:
                # HTML에서 먼저 탐색
                m = re.search(pattern, html)
                if not m:
                    m = re.search(pattern, text)
                if m:
                    val = int(m.group(1).replace(",", ""))
                    if val > 5:  # 의미있는 숫자만
                        count = val
                        print(f"[네이버 검색] 패턴 '{pattern}' 매칭: {count}건")
                        break

            # 2차: CSS 셀렉터로 총 건수 요소 탐색
            if count == 0:
                for sel in [
                    ".title_num",          # 네이버 블로그탭 카운트
                    ".blog_count strong",
                    ".total_count",
                    "span.num_total",
                    ".result_num strong",
                    "em.num",
                ]:
                    try:
                        els = page.locator(sel).all()
                        for el in els:
                            t = el.inner_text().strip().replace(",", "").replace("약", "").strip()
                            if t.isdigit() and int(t) > 5:
                                count = int(t)
                                print(f"[네이버 검색] 셀렉터 '{sel}': {count}건")
                                break
                        if count > 0:
                            break
                    except Exception:
                        continue

            # 3차: 통합검색 페이지에서 블로그 섹션 건수 파싱
            if count == 0:
                try:
                    page.goto(
                        f"https://search.naver.com/search.naver?query={quote(query)}",
                        wait_until="networkidle", timeout=25000
                    )
                    time.sleep(2.0)
                    html2 = page.content()
                    # 블로그 섹션 총 건수
                    for pattern in [
                        r'"blog"[^}]*"totalCount"\s*:\s*(\d+)',
                        r'blog.*?총\s*([\d,]+)\s*건',
                        r'블로그.*?([\d,]+)\s*건',
                    ]:
                        m = re.search(pattern, html2, re.DOTALL)
                        if m:
                            val = int(m.group(1).replace(",", ""))
                            if val > 5:
                                count = val
                                print(f"[네이버 검색] 통합검색 패턴: {count}건")
                                break
                except Exception as e2:
                    print(f"[네이버 검색] 통합검색 오류: {e2}")

            # 4차: li.bx 노출 건수 (폴백)
            if count == 0:
                try:
                    items = page.locator("li.bx").all()
                    count = len(items)
                    print(f"[네이버 검색] 폴백(li.bx): {count}건")
                except Exception:
                    pass

            print(f"[네이버 검색] 최종: {count}건 (쿼리: '{query}')")
            browser.close()
    except Exception as e:
        print(f"[네이버 검색 오류] {e}")
    return count


# ════════════════════════════════════════════════════════════
# STEP 5: 인스타그램 수
# ════════════════════════════════════════════════════════════
def crawl_instagram_count(tag):
    clean = tag.replace(" ","").replace("#","")
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_mobile_browser(p)
            page = ctx.new_page()
            page.goto(f"https://www.instagram.com/explore/tags/{clean}/",
                      wait_until="domcontentloaded", timeout=25000)
            time.sleep(3.5)
            text = page.inner_text("body")
            for pat, unit in [
                (r'([\d.]+)만\s*(?:개\s*)?게시물',"만"),
                (r'게시물\s*([\d,]+)',""),
                (r'([\d,]+(?:\.\d+)?[KMk만천]?)\s*posts?',""),
            ]:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    ns = m.group(1).replace(",","")
                    try:
                        if unit=="만" or "만" in ns: count = int(float(ns.replace("만",""))*10000)
                        elif ns[-1:].upper()=="K": count = int(float(ns[:-1])*1000)
                        elif ns[-1:].upper()=="M": count = int(float(ns[:-1])*1000000)
                        else: count = int(float(ns))
                    except: count = 0
                    break
            if count == 0:
                count = max(0,(len(page.locator("img[alt]").all())-3)*25)
            browser.close()
    except Exception as e:
        print(f"[인스타그램 오류] {e}")
    return count


# ── 진행 상태 파일 기록 ──────────────────────────────────────────
_progress_path = None

def _write_progress(message, pct):
    if not _progress_path: return
    try:
        Path(_progress_path).write_text(
            json.dumps({"progress": pct, "message": message}, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 7:
        print("Usage: crawler.py <place_id> <merchant_name> <region> <ig_tag> <crawl_target> <blog_target> <output_path> [progress_path]")
        sys.exit(1)

    place_id      = args[0]
    merchant_name = args[1]
    region        = args[2]
    ig_tag        = args[3]
    crawl_target  = int(args[4])
    blog_target   = int(args[5])
    output_path   = args[6]
    _progress_path = args[7] if len(args) > 7 else None

    print(f"[크롤러 시작] place_id={place_id} target={crawl_target}")

    _write_progress("플레이스 공식 리뷰 수 확인 중...", 5)
    counts = get_official_counts(place_id)

    _write_progress(f"영수증리뷰 수집 중... (목표 {crawl_target}건)", 10)
    receipt = crawl_receipt_reviews(place_id, target=crawl_target)

    _write_progress(f"블로그리뷰 목록 수집 중... (목표 {blog_target}건)", 43)
    blog_links = crawl_blog_links(place_id, merchant_name=merchant_name, target=blog_target)

    _write_progress(f"블로그 원문 방문 중...", 57)
    blog_reviews = classify_blog_originals(blog_links)

    _write_progress("네이버 검색결과 집계 중...", 80)
    naver_cnt = crawl_naver_search_count(merchant_name, region, counts.get("addr_keyword", ""))

    _write_progress("인스타그램 집계 중...", 91)
    ig_cnt = crawl_instagram_count(ig_tag)

    receipt_list = receipt
    blog_list    = blog_reviews

    result = {
        "place_counts": counts,
        "naver_receipt_reviews": receipt_list,
        "naver_blog_reviews": blog_list,
        "naver_search_count": naver_cnt,
        "instagram_count": ig_cnt,
        "summary": {
            "official_receipt_count":      counts.get("receipt_total", 0),
            "official_receipt_text_count": counts.get("receipt_text_total", 0),
            "official_receipt_keyword":    counts.get("receipt_keyword", 0),
            "official_blog_count":         counts.get("blog_total", 0),
            "total_receipt_reviews":  len(receipt_list),
            "total_blog_reviews":     len(blog_list),
            "naver_search_count":     naver_cnt,
            "instagram_count":        ig_cnt,
            "blog_ad_count":      sum(1 for r in blog_list if r.get("ad_type")=="광고"),
            "blog_organic_count": sum(1 for r in blog_list if r.get("ad_type")=="내돈내산"),
            "blog_unknown_count": sum(1 for r in blog_list if r.get("ad_type")=="판별불가"),
        }
    }

    Path(output_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_progress("분석 완료", 100)
    print(f"[크롤러 완료] 영수증:{len(receipt_list)} 블로그:{len(blog_list)} → {output_path}")

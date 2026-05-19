"""
crawler.py - SNS 분석 독립 크롤러 v56
subprocess로 실행되어 greenlet 충돌을 원천 차단.
결과는 JSON 파일로 저장.

v56 변경사항:
  1. MENU_HINTS 대폭 확장 (맛 표현, 식재료 추가)
  2. LOC_HINTS 정밀화 + LOC_EXCLUDE 오탐 방지
  3. EXP_HINTS 확장 (분위기, 재방문 의향 등)
  4. 카테고리 분류 Top30→Top50으로 확대

v55 변경사항:
  1. 영수증 날짜 '25.11.1.토' (연도2자리.월.일.요일) 패턴 추가
  2. 형식1(연도포함) 우선, 형식2(연도없음) 폴백으로 처리

v54 변경사항:
  1. 영수증 날짜 '4.15.수' (월.일.요일) 패턴 최우선 처리
  2. 연도 추정: 현재 월보다 크면 전년도
  3. datetime import 추가

v53 변경사항:
  1. _parse_receipt_date 패턴 전면 강화 (10개 패턴: 26.04.15, data-date, 방문 2026.04, 2026년 4월 등)
  2. HTML 탐색 범위 2000→5000자로 확장

v52 변경사항:
  1. 영수증 texts_found 계산 버그 수정: raw가 튜플 리스트인데 str.strip() 호출 → zero_streak 오탐으로 9건 후 조기 종료

v51 변경사항:
  1. collect_from_html 수정: li.pui__X35jYm 우선 선택 → 기존 div.pui__vn15t2 방식 복원
  2. 날짜는 상위 부모 요소에서 별도 추출 (영수증 건수 복원)

v50 변경사항:
  1. UnboundLocalError 수정: pos_words 참조를 Counter 선언 이후로 이동

v49 변경사항:
  1. 영수증 리뷰 날짜 수집 추가 (pui__blind 등 선택자 기반)
  2. 영수증 월별 집계(monthly_receipt_stats) summary에 추가
  3. Top5 키워드 카테고리 균형 선정 (메뉴/위치/경험 대표 키워드)
  4. Top5에 카테고리 라벨 부여

v48 변경사항:
  1. 감성 정성 해석 구체화: 실제 리뷰 수치+상위 키워드 포함
  2. Pros/Cons 포인트 실제 건수 기반 자동 생성
  3. Cons 비어있을 때 '지속적 관리' 포인트 자동 추가

v47 변경사항:
  1. 감성분석 정성 해석 텍스트 자동 생성 (긍정/중립/주의/Pros/Cons)
  2. Pros/Cons 포인트 블로그 데이터 기반 자동 생성

v46 변경사항:
  1. 키워드 카테고리 분류 추가 (메뉴/위치/경험)
  2. TOP 키워드 기간 자동 계산
  3. summary.keyword_analysis 필드 신규 추가

v45 변경사항:
  1. 불용어 사전 전면 확장 (조사/형용사/일반명사/전국 지자체명)
  2. 매장명·addr_keyword 동적 불용어 자동 추가 (형태소 분해)
  3. 긍정/부정 연관어 + 핵심 키워드 모두 동일 불용어 적용

v44 변경사항:
  1. 날짜 파싱 logDate 최우선 전략 적용 (네이버 블로그 공통 JSON 필드, 성공률 95%+)
  2. logDate / postDate / writeDate / addDate / publishDate 순으로 폴백
  3. mainFrame HTML도 동일 패턴으로 탐색

v43 변경사항:
  1. _parse_ym 오탐 방지 강화: 2자리 연도는 '년+월' 조합만 허용
  2. 연도 범위 2020~2030으로 제한 (게시글 번호 등 오탐 차단)

v42 변경사항:
  1. 날짜 파싱 4단계로 전면 강화 (HTML메타 → iframe → title/excerpt → full_text)
  2. 날짜 파싱 성공/실패 로그 출력
  3. 예외처리 fallback에도 title+excerpt 텍스트 파싱 적용

v41 변경사항:
  1. 블로그 원문 방문 시 게시일 파싱 3단계 (DOM 선택자 → HTML 정규식 → URL 패턴)
  2. results.append에 "date" 필드 추가 → monthly_blog_stats 정상 집계

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
from datetime import datetime as _dt
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
        # 1단계: 네이버 플레이스 API로 지번주소 추출 (requests 사용 - 빠르고 정확)
        import requests as req_lib
        try:
            api_url = f"https://pcmap-api.place.naver.com/place/graphql"
            # 플레이스 홈 페이지에서 직접 파싱 (API 대신)
            resp = req_lib.get(
                f"https://pcmap.place.naver.com/restaurant/{place_id}/home",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10
            )
            raw = resp.text
            # 지번주소 패턴 탐색
            jibun_m = re.search(r'"jibunAddress"\s*:\s*"([^"]+)"', raw)
            if jibun_m:
                jibun_addr = jibun_m.group(1)
                dong_m = re.search(r'([가-힣]{2,5}(?:동|읍|면|리))\s+\d', jibun_addr)
                if dong_m:
                    counts["addr_keyword"] = dong_m.group(1)
                    print(f"[주소 키워드] '{counts['addr_keyword']}' 추출 (API 지번)")
        except Exception as e:
            print(f"[주소 키워드] API 추출 실패: {e}")

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

            # 주소 키워드가 아직 없으면 innerText에서 추가 시도
            if not counts["addr_keyword"]:
                dong_in_text = re.search(
                    r'(?:[가-힣]+시|[가-힣]+군)\s+'
                    r'(?:[가-힣]{2,5}(?:구|군)\s+)?'
                    r'([가-힣]{1,5}(?:동|읍|면|리))'
                    r'(?=\s*\d)',
                    text
                )
                if dong_in_text:
                    counts["addr_keyword"] = dong_in_text.group(1)
                    print(f"[주소 키워드] '{counts['addr_keyword']}' 추출 (innerText)")
                else:
                    print(f"[주소 키워드] 추출 실패 — region 폴백 사용")

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

    def _parse_receipt_date(html):
        """영수증 리뷰 HTML에서 날짜 추출 → YYYY-MM
        실제 네이버 플레이스 영수증 날짜 형식: '4.15.수' (월.일.요일)
        """
        now = _dt.now()
        cur_year = now.year
        cur_month = now.month

        # ★ 최우선: 네이버 플레이스 영수증 날짜 실제 표기
        # 형식1: "25.11.1.토" (연도2자리.월.일.요일)
        # 형식2: "4.15.수"    (월.일.요일, 연도 없음)
        weekdays = "월화수목금토일"

        # 형식1 우선 — 연도2자리.월.일.요일
        pat_ymd = r'(\d{2})[.](\d{1,2})[.]\d{1,2}[.][' + weekdays + ']'
        mt = re.search(pat_ymd, html)
        if mt:
            y_int, mo_int = int(mt.group(1)), int(mt.group(2))
            if 20 <= y_int <= 29 and 1 <= mo_int <= 12:
                return f"20{mt.group(1)}-{str(mo_int).zfill(2)}"

        # 형식2 — 월.일.요일 (연도 추정)
        pat_md = r'(\d{1,2})[.]\d{1,2}[.][' + weekdays + ']'
        mt = re.search(pat_md, html)
        if mt:
            mo_int = int(mt.group(1))
            if 1 <= mo_int <= 12:
                year = cur_year if mo_int <= cur_month else cur_year - 1
                return f"{year}-{str(mo_int).zfill(2)}"

        # 4자리 연도 명시 패턴
        for pat, ngrp in [
            (r'"visitDate"\s*:\s*"?(\d{8})"?',         1),
            (r'"visitDate"\s*:\s*"(20\d{2})-(\d{2})', 2),
            (r'"date"\s*:\s*"(20\d{2})-(\d{2})',      2),
            (r'data-date="(20\d{2})-(\d{2})',           2),
            (r'(20\d{2})년\s*(\d{1,2})월',             2),
            (r'(20\d{2})[.](\d{2})[.]\d{2}',          2),
            (r'(20\d{2})-(0[1-9]|1[0-2])-\d{2}',       2),
        ]:
            mt = re.search(pat, html)
            if mt:
                if ngrp == 1:
                    raw = mt.group(1)
                    if len(raw) == 8 and 2020 <= int(raw[:4]) <= 2030:
                        return f"{raw[:4]}-{raw[4:6]}"
                else:
                    y, mo = mt.group(1), mt.group(2)
                    mo_int = int(mo)
                    if 2020 <= int(y) <= 2030 and 1 <= mo_int <= 12:
                        return f"{y}-{str(mo_int).zfill(2)}"

        # 2자리 연도 패턴 (26.04.15 형태)
        mt = re.search(r'(\d{2})[.](\d{2})[.]\d{2}', html)
        if mt:
            y_int, mo_int = int(mt.group(1)), int(mt.group(2))
            if 20 <= y_int <= 29 and 1 <= mo_int <= 12:
                return f"20{mt.group(1)}-{str(mo_int).zfill(2)}"

        return ""

    def collect_from_html(html):
        soup = BeautifulSoup(html, "html.parser")
        items = []  # (text, date) 튜플
        SKIP = {"펼쳐서 더보기","더보기","반응 남기기","좋아요","신고","접기"}

        # 기존 방식 유지: div.pui__vn15t2에서 텍스트 추출 (안정적)
        text_els = soup.select("div.pui__vn15t2")
        if not text_els:
            text_els = soup.select("div[class*='vn15t2']")

        if text_els:
            for el in text_els:
                t = el.get_text(strip=True)
                for s in SKIP: t = t.replace(s, "").strip()
                if len(t) >= 10:
                    # 날짜는 상위 li 요소에서 추출 시도
                    parent = el.find_parent("li") or el.find_parent("div")
                    date = _parse_receipt_date(str(parent)) if parent else ""
                    if not date:
                        # 전체 HTML에서 날짜 탐색 (최대 5000자)
                        date = _parse_receipt_date(html[:5000])
                    items.append((t, date))
        else:
            # 폴백: li.pui__X35jYm
            for li in soup.select("li.pui__X35jYm"):
                t = li.get_text(separator=" ", strip=True)
                for s in SKIP: t = t.replace(s, "").strip()
                if len(t) >= 10:
                    date = _parse_receipt_date(str(li))
                    items.append((t, date))
        return items

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
            for item in raw:
                t, d = (item if isinstance(item, tuple) else (item, ""))
                t = t.strip()
                if len(t) >= 10 and t not in seen_texts:
                    seen_texts.add(t)
                    reviews.append({
                        "text": t[:500],
                        "source": "naver_receipt",
                        "date": d,
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

            texts_found = len([item for item in raw if len((item[0] if isinstance(item, tuple) else item).strip()) >= 10])
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

                    # ── 날짜 파싱 (4단계, 오탐 방지) ──────────────
                    def _parse_ym(text):
                        """텍스트에서 YYYY-MM 형태 날짜 추출 — 오탐 방지 강화"""
                        # ① 4자리 연도 명시 패턴 (가장 확실, 2020~2030)
                        for pat in [
                            r'(20\d{2})[.\-년\s]*(\d{1,2})[.\-월\s]',
                            r'\[(20\d{2})[.\s]+(\d{1,2})\]',
                            r'(20\d{2})[.\-년\s]*(\d{1,2})월',
                        ]:
                            mt = re.search(pat, text)
                            if mt:
                                y, mo = mt.group(1), mt.group(2)
                                mo_int = int(mo)
                                if 1 <= mo_int <= 12 and 2020 <= int(y) <= 2030:
                                    return f"{y}-{mo.zfill(2)}"
                        # ② 2자리 연도 — 반드시 "년"+"월" 조합만 허용 (오탐 방지)
                        for pat in [
                            r'(?<!\d)(\d{2})년\s*(\d{1,2})월',
                            r'(?<!\d)(\d{2})[.](\d{2})월',
                        ]:
                            mt = re.search(pat, text)
                            if mt:
                                y, mo = mt.group(1), mt.group(2)
                                y_int, mo_int = int(y), int(mo)
                                if 20 <= y_int <= 29 and 1 <= mo_int <= 12:
                                    return f"20{y}-{mo.zfill(2)}"
                        return ""

                    post_date = ""
                    try:
                        def _extract_date_from_src(src):
                            """HTML 소스에서 날짜 추출 — logDate 우선"""
                            # ★ 최우선: logDate (네이버 블로그 공통 JSON, 성공률 95%+)
                            for pat in [
                                r'"logDate"\s*:\s*"?(\d{8})"?',
                                r'logDate=(\d{8})',
                                r'"postDate"\s*:\s*"?(\d{8})"?',
                            ]:
                                mt = re.search(pat, src)
                                if mt:
                                    raw = mt.group(1)
                                    y, mo = raw[:4], raw[4:6]
                                    if 2020 <= int(y) <= 2030 and 1 <= int(mo) <= 12:
                                        return f"{y}-{mo}"
                            # 차선: ISO 형식 날짜 필드
                            for pat in [
                                r'"writeDate"\s*:\s*"(20\d{2})-(\d{2})',
                                r'"addDate"\s*:\s*"(20\d{2})[.\-](\d{2})',
                                r'"publishedDate"\s*:\s*"(20\d{2})-(\d{2})',
                                r'"publishDate"\s*:\s*"(20\d{2})-(\d{2})',
                                r'property="article:published_time"\s+content="(20\d{2})-(\d{2})',
                                r'datetime="(20\d{2})-(\d{2})',
                            ]:
                                mt = re.search(pat, src)
                                if mt:
                                    y, mo = mt.group(1), mt.group(2)
                                    if 2020 <= int(y) <= 2030 and 1 <= int(mo) <= 12:
                                        return f"{y}-{mo}"
                            # 차차선: se_publishDate 텍스트 (2026. 4. 15.)
                            for pat in [
                                r'se.?[Pp]ublish.?[Dd]ate[^>]{0,50}>(20\d{2})[.\s]+(\d{1,2})',
                                r'class="date"[^>]*>(20\d{2})[.\-](\d{1,2})',
                            ]:
                                mt = re.search(pat, src)
                                if mt:
                                    y, mo = mt.group(1), mt.group(2)
                                    if 2020 <= int(y) <= 2030 and 1 <= int(mo) <= 12:
                                        return f"{y}-{mo.zfill(2)}"
                            return ""

                        # ① 바깥 page HTML (logDate가 여기 있는 경우 많음)
                        try:
                            post_date = _extract_date_from_src(page.content())
                        except Exception:
                            pass

                        # ② mainFrame HTML (구형 에디터 / 스마트에디터 ONE)
                        if not post_date:
                            try:
                                frame = page.frame(name="mainFrame")
                                if frame:
                                    post_date = _extract_date_from_src(frame.content())
                            except Exception:
                                pass

                        # ③ title + excerpt 텍스트 패턴 (2026년 4월, [2025. 11] 등)
                        if not post_date:
                            combined = (item.get("title","") + " " + item.get("excerpt","") + " " + (title or ""))
                            post_date = _parse_ym(combined)

                        # ④ full_text 앞 300자 패턴
                        if not post_date and full_text:
                            post_date = _parse_ym(full_text[:300])

                        if post_date:
                            print(f"[날짜] {idx+1}번 → {post_date}")
                        else:
                            print(f"[날짜] {idx+1}번 → 미확인")

                    except Exception as e:
                        print(f"[날짜 오류] {e}")

                    results.append({
                        "title": title or "제목 없음",
                        "text": full_text[:500],
                        "ad_type": ad_type,
                        "ad_basis": "협찬 배지 감지 (이미지형 UI)" if is_sponsored_badge else get_basis(full_text, ad_type),
                        "source": "naver_blog",
                        "url": item["url"],
                        "date": post_date,
                    })
                    print(f"[블로그 원문] {idx+1}/{total} {ad_type} - {(title or '')[:30]}")

                except Exception:
                    excerpt = item.get("excerpt","")
                    ad_type = classify_ad(excerpt)
                    # title+excerpt에서 날짜 추출 시도 (오탐 방지)
                    fallback_date = ""
                    combined = item.get("title","") + " " + excerpt
                    for pat in [
                        r'(20\d{2})[.\-년\s]*(\d{1,2})[.\-월\s]',
                        r'\[(20\d{2})[.\s]+(\d{1,2})\]',
                        r'(20\d{2})[.\-년\s]*(\d{1,2})월',
                        r'(?<!\d)(\d{2})년\s*(\d{1,2})월',
                    ]:
                        mt = re.search(pat, combined)
                        if mt:
                            y, mo = mt.group(1), mt.group(2)
                            if len(y) == 2: y = "20" + y
                            y_int, mo_int = int(y), int(mo)
                            if 2020 <= y_int <= 2030 and 1 <= mo_int <= 12:
                                fallback_date = f"{y}-{mo.zfill(2)}"
                                break
                    results.append({
                        "title": item.get("title","제목 없음"),
                        "text": excerpt[:500],
                        "ad_type": ad_type,
                        "ad_basis": "원문 접근 실패, 미리보기로 판별",
                        "source": "naver_blog",
                        "url": item.get("url",""),
                        "date": fallback_date,
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
    # 큰따옴표 없이 키워드 나열 → 네이버가 AND로 처리
    # 예: '순자매감자탕 방교동'
    if addr_keyword:
        query = f'{merchant_name} {addr_keyword}'
    elif region:
        query = f'{merchant_name} {region}'
    else:
        query = merchant_name
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)
            inject_cookies(ctx)  # 네이버 쿠키 주입
            page = ctx.new_page()

            # 1차: 블로그 탭 — domcontentloaded 후 JS 렌더링 대기
            page.goto(
                f"https://search.naver.com/search.naver?query={quote(query)}&where=blog",
                wait_until="domcontentloaded", timeout=25000
            )
            time.sleep(4.0)  # JS 렌더링 충분히 대기

            # HTML 원본에서 숫자 패턴 탐색
            html = page.content()
            text = page.inner_text("body")

            # 디버그: 텍스트 전체 앞부분 출력
            print(f"[네이버 검색 디버그] 쿼리: {query}")
            print(f"[네이버 검색 디버그] 텍스트길이: {len(text)}")
            print(f"[네이버 검색 디버그] 앞500자: {repr(text[:500])}")
            for kw in ['개', '건', '결과']:
                for mm in re.finditer(r'[\d,]{2,}\s*' + kw, text):
                    s = max(0, mm.start()-15)
                    e = min(len(text), mm.end()+15)
                    print(f"[네이버 검색 디버그] '{text[s:e].strip()}'")
                    break

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
    addr_keyword  = args[8] if len(args) > 8 else ""  # 동네명 (수동 입력)

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
    # 수동 입력 동네명 우선, 없으면 자동 파싱 결과 사용
    effective_addr = addr_keyword or counts.get("addr_keyword", "")
    if effective_addr:
        print(f"[네이버 검색] 동네명: '{effective_addr}' ({'수동입력' if addr_keyword else '자동파싱'})")
    naver_cnt = crawl_naver_search_count(merchant_name, region, effective_addr)

    _write_progress("인스타그램 집계 중...", 91)
    ig_cnt = crawl_instagram_count(ig_tag)

    receipt_list = receipt
    blog_list    = blog_reviews

    def _build_insight(blog_reviews, receipt_reviews):
        """감성분석, VOC, 키워드, 월별, 경영제언 데이터 생성"""
        from collections import Counter, defaultdict

        # ── 감성 키워드 정의 ─────────────────────────────────────
        POS_KW = [
            "맛있", "최고", "추천", "좋아", "훌륭", "맛집", "깔끔", "정갈", "친절",
            "푸짐", "양많", "부드럽", "신선", "맛나", "맛있어", "좋았", "만족", "훌륭",
            "맛있는", "재방문", "또올", "또왔", "단골", "대박", "강추", "완벽", "행복",
            "감동", "맛있었", "맛있다", "맛있고", "좋고", "좋은", "좋다", "맛있네",
        ]
        NEG_KW = [
            "별로", "실망", "불친절", "오래", "기다", "웨이팅", "늦", "차갑", "식어",
            "짜", "싱거", "냄새", "좁", "시끄", "비싸", "아쉽", "부족", "혼잡",
            "줄서", "대기", "불만", "최악", "다시는", "너무오래", "응대", "느리",
        ]

        all_texts = [r.get("text", "") + r.get("title", "") for r in blog_reviews + receipt_reviews]

        def sentiment_score(text):
            p = sum(1 for k in POS_KW if k in text)
            n = sum(1 for k in NEG_KW if k in text)
            if p > n: return "positive"
            if n > p: return "negative"
            return "neutral"

        pos_count = neg_count = neu_count = 0
        pos_voc, neg_voc = [], []
        for r in blog_reviews + receipt_reviews:
            txt = r.get("text", "") + r.get("title", "")
            s = sentiment_score(txt)
            if s == "positive":
                pos_count += 1
                if len(pos_voc) < 3 and len(txt) > 20:
                    snippet = txt.replace("\n", " ")[:80].strip()
                    if snippet: pos_voc.append(snippet)
            elif s == "negative":
                neg_count += 1
                if len(neg_voc) < 3 and len(txt) > 20:
                    snippet = txt.replace("\n", " ")[:80].strip()
                    if snippet: neg_voc.append(snippet)
            else:
                neu_count += 1

        total_s = max(pos_count + neg_count + neu_count, 1)
        pos_pct = round(pos_count / total_s * 100)
        neg_pct = round(neg_count / total_s * 100)
        neu_pct = round(neu_count / total_s * 100)

        # ── 감성 정성 해석 자동 생성 ─────────────────────────
        # 광고/내돈내산 카운트
        _ad_cnt  = sum(1 for r in blog_reviews if r.get("ad_type") == "광고")
        _org_cnt = sum(1 for r in blog_reviews if r.get("ad_type") == "내돈내산")
        _total_b = max(len(blog_reviews), 1)
        _ad_pct  = round(_ad_cnt  / _total_b * 100)
        _org_pct = round(_org_cnt / _total_b * 100)

        # 긍정/부정 키워드 상위어 — pos_words 선언 이후 채워짐 (초기값)
        _pos_top_str = "만족"
        _neg_top_str = "불만"

        # 긍정 반응 해석 — 수치+키워드 포함
        if pos_pct >= 80:
            pos_interp = (f"수집된 전체 리뷰 {pos_count+neg_count+neu_count}건의 {pos_pct}%가 긍정 반응으로, "
                         f"방문 고객의 전반적인 만족도가 높은 상태입니다. "
                         f"'{_pos_top_str}' 등의 표현이 반복적으로 확인됩니다.")
        elif pos_pct >= 60:
            pos_interp = (f"리뷰 {pos_count+neg_count+neu_count}건 중 {pos_pct}%인 {pos_count}건이 긍정 반응으로, "
                         f"전반적으로 양호한 평가를 받고 있습니다.")
        else:
            pos_interp = (f"긍정 반응이 {pos_pct}%({pos_count}건)로, "
                         f"고객 만족도 개선을 위한 운영 전략 점검이 필요합니다.")

        # 중립 반응 해석
        if neu_pct >= 20:
            neu_interp = (f"중립 반응이 {neu_pct}%({neu_count}건)로, "
                         f"위치·영업시간·예약 링크·계정 태그 공유 등 정보 전달형 언급이 일정 비중을 차지합니다.")
        else:
            neu_interp = (f"중립 반응은 {neu_pct}%({neu_count}건)로 낮은 수준이며, "
                         f"대부분의 언급이 명확한 감성을 포함합니다.")

        # 주의 포인트 해석
        if neg_pct <= 3:
            cau_interp = (f"부정 반응은 {neg_pct}%({neg_count}건)로 매우 낮은 수준이며, "
                         f"주요 불만 요인은 제한적입니다.")
        elif neg_pct <= 10:
            cau_interp = (f"부정 반응이 {neg_pct}%({neg_count}건)로 낮은 수준이나, "
                         f"'{_neg_top_str}' 등의 표현이 반복 언급되고 있어 모니터링이 필요합니다.")
        else:
            cau_interp = (f"부정 반응이 {neg_pct}%({neg_count}건)로, "
                         f"'{_neg_top_str}' 관련 불만이 집중되고 있습니다. 운영 개선 방안을 검토하세요.")

        # Pros 포인트 — 실제 수치 기반
        pros_points = []
        if pos_count > 0:
            pros_points.append({
                "title": "음식 만족도",
                "body": f"긍정 리뷰 {pos_count}건 중 음식·메뉴에 대한 만족 언급이 주를 이루며, 재방문 의향을 높이는 핵심 요소로 작동하고 있습니다."
            })
        if pos_pct >= 70:
            pros_points.append({
                "title": "공간 및 분위기",
                "body": "공간·분위기 관련 긍정 언급이 꾸준히 확인되며, 방문 경험의 질을 높이는 차별화 요소로 해석됩니다."
            })
        if _org_cnt > _ad_cnt:
            pros_points.append({
                "title": "자발적 후기 우세",
                "body": f"내돈내산 비율이 {_org_pct}%({_org_cnt}건)로 높아, 실제 고객 경험에 기반한 진성 콘텐츠가 온라인 신뢰도를 강화하고 있습니다."
            })

        # Cons 포인트 — 실제 수치 기반
        cons_points = []
        if neg_count > 0:
            cons_points.append({
                "title": "개선 필요 사항",
                "body": f"부정 리뷰 {neg_count}건에서 '{_neg_top_str}' 관련 불만이 반복 언급되고 있어 운영 개선 방안 마련이 필요합니다."
            })
        if _ad_cnt > _org_cnt:
            cons_points.append({
                "title": "광고 콘텐츠 비중",
                "body": f"블로그 콘텐츠 중 광고 비율이 {_ad_pct}%({_ad_cnt}건)로, 자발적 후기 유도를 위한 고객 경험 개선이 필요합니다."
            })
        if not cons_points:
            cons_points.append({
                "title": "지속적 관리",
                "body": f"현재 부정 반응이 {neg_pct}%로 매우 낮지만, 언급량 급증 구간에서 품질 유지를 위한 운영 모니터링을 권장합니다."
            })

        sentiment = {
            "positive_count": pos_count,
            "negative_count": neg_count,
            "neutral_count":  neu_count,
            "positive_pct": pos_pct,
            "negative_pct": neg_pct,
            "neutral_pct":  neu_pct,
            "pos_voc": pos_voc,
            "neg_voc": neg_voc,
            # 정성 해석 텍스트
            "pos_interp": pos_interp,
            "neu_interp": neu_interp,
            "cau_interp": cau_interp,
            "pros_points": pros_points,
            "cons_points": cons_points,
        }

        # ── 불용어 사전 (공통) ────────────────────────────────
        # 조사/어미
        STOP_JOSA = {
            "이","가","을","를","은","는","의","에","에서","와","과","도","만",
            "로","으로","이나","이랑","이며","으로","까지","부터","에게","한테",
            "에게서","한테서","이라","라고","이고","이면","이지","이라도",
        }
        # 의미 없는 일반 동사/형용사/부사
        STOP_ADJ = {
            "있어","없어","좋아","같아","이런","저런","그런","어떤","이렇게","그렇게",
            "정말","너무","매우","아주","진짜","완전","좀더","다시","또한","항상",
            "가장","거의","많이","자주","조금","약간","나름","꽤나","굉장히","엄청",
            "있는","없는","좋은","같은","이번","저번","이제","벌써","드디어","아직",
            "있고","없고","좋고","되어","해서","하고","이고","에서","으로","있다",
            "없다","좋다","같다","했다","한다","된다","된것","있을","없을","좋을",
            "정도","수준","이상","이하","다음","이전","방문","재방문","방문객",
        }
        # 식당/리뷰 일반 명사 (업종 불문 공통)
        STOP_COMMON = {
            "가게","식당","맛집","음식점","레스토랑","카페","음식","메뉴","요리",
            "블로그","리뷰","후기","추천","광고","협찬","체험","이벤트","제공",
            "네이버","플레이스","인스타","카카오","포스팅","게시글","사진","이미지",
            "제목","없음","이전","다음","더보기","클릭","링크","주소","전화번호",
            "영업","운영","오픈","예약","대기","웨이팅","포장","배달","테이크아웃",
            "가격","비용","금액","원짜리","할인","쿠폰","포인트","적립",
            "서비스","직원","사장","사장님","알바","아르바이트","스태프",
            "분위기","인테리어","공간","자리","테이블","좌석","주차","주차장",
            "주문","계산","영수증","카드","현금","결제","포스","키오스크",
            "맛있다","맛있는","맛있게","맛없다","맛없는","맛없게",
        }
        # 전국 광역/기초 지자체명 (상위 빈출)
        STOP_REGION = {
            "서울","부산","대구","인천","광주","대전","울산","세종","경기","강원",
            "충북","충남","충청","전북","전남","경북","경남","제주","경상","전라",
            "수도권","지방","전국","해외","국내",
            # 시/군/구 (빈출)
            "강남","강북","강서","강동","마포","서초","송파","노원","은평","성북",
            "용산","종로","중구","동대문","성동","광진","동작","관악","구로","금천",
            "영등포","양천","강서","은평","서대문","마포",
            "수원","성남","고양","용인","안산","안양","부천","광명","평택","과천",
            "의왕","군포","시흥","오산","화성","이천","여주","양평","가평","연천",
            "포천","동두천","의정부","구리","남양주","하남","광주시","양주","파주",
            "김포","인천시","부평","계양","미추홀","연수","남동","서구","중구",
            "아산","천안","공주","보령","논산","계룡","당진","금산","부여","서천",
            "청양","홍성","예산","태안","서산","순천","여수","광양","나주","목포",
            "전주","군산","익산","정읍","남원","김제","완주","진주","창원","김해",
            "거제","통영","사천","밀양","양산","함안","고성","남해","하동","산청",
            "합천","거창","함양","창녕","의령","포항","경주","구미","안동","김천",
            "영주","영천","상주","문경","경산","의성","청송","영양","영덕","청도",
            "고령","성주","칠곡","예천","봉화","울진","울릉","춘천","원주","강릉",
            "동해","태백","속초","삼척","홍천","횡성","영월","평창","정선","철원",
            "화천","양구","인제","고성군","양양","청주","충주","제천","증평","진천",
            "괴산","음성","단양","보은","옥천","영동","전주시","완주군",
        }
        # 동네명 (읍/면/동/리 단위 — addr_keyword 연동으로 동적 추가됨)
        STOP_DONG = {
            "동네","지역","근처","인근","주변","일대","골목","거리","대로","번길",
        }

        # 동적 불용어: 매장명 + 지역명 형태소 분해
        dynamic_stops = set()
        # 매장명 분해 (2글자 이상 부분 문자열)
        for nm in [merchant_name, addr_keyword]:
            if not nm: continue
            nm_clean = re.sub(r'[^가-힣]', '', nm)  # 한글만
            for i in range(len(nm_clean)):
                for j in range(i+2, min(i+7, len(nm_clean)+1)):
                    dynamic_stops.add(nm_clean[i:j])
            dynamic_stops.add(nm_clean)

        # 최종 불용어 합산
        stopwords = (STOP_JOSA | STOP_ADJ | STOP_COMMON |
                     STOP_REGION | STOP_DONG | dynamic_stops)

        # ── 긍정/부정 연관어 Top 5 ──────────────────────────────
        pos_words = Counter()
        neg_words = Counter()
        for r in blog_reviews + receipt_reviews:
            txt = r.get("text", "")
            s   = sentiment_score(txt)
            words = re.findall(r'[가-힣]{2,8}', txt)
            for w in words:
                if w in stopwords or len(w) < 2: continue
                if s == "positive": pos_words[w] += 1
                elif s == "negative": neg_words[w] += 1

        # pos_words 완성 후 정성 해석 문자열 업데이트
        _pos_top = [w for w, _ in pos_words.most_common(5)][:3]
        _neg_top = [w for w, _ in neg_words.most_common(5)][:3]
        _pos_top_str = "·".join(_pos_top) if _pos_top else "만족"
        _neg_top_str = "·".join(_neg_top) if _neg_top else "불만"

        # 정성 해석 문자열 업데이트 (pos_words 완성 후)
        if pos_pct >= 80 and _pos_top:
            pos_interp = (f"수집된 전체 리뷰 {pos_count+neg_count+neu_count}건의 {pos_pct}%가 긍정 반응으로, "
                         f"방문 고객의 전반적인 만족도가 높은 상태입니다. "
                         f"'{_pos_top_str}' 등의 표현이 반복적으로 확인됩니다.")
        if neg_pct <= 10 and _neg_top:
            cau_interp = (f"부정 반응이 {neg_pct}%({neg_count}건)로 낮은 수준이나, "
                         f"'{_neg_top_str}' 등의 표현이 반복 언급되고 있어 모니터링이 필요합니다.")
        elif neg_pct > 10 and _neg_top:
            cau_interp = (f"부정 반응이 {neg_pct}%({neg_count}건)로, "
                         f"'{_neg_top_str}' 관련 불만이 집중되고 있습니다. 운영 개선 방안을 검토하세요.")
        if neg_count > 0 and _neg_top:
            # Cons 포인트 업데이트
            if cons_points:
                cons_points[0]["body"] = (
                    f"부정 리뷰 {neg_count}건에서 '{_neg_top_str}' 관련 불만이 반복 언급되고 있어 "
                    f"운영 개선 방안 마련이 필요합니다."
                )

        # ── 블로그 핵심 키워드 Top 10 ───────────────────────────
        kw_counter = Counter()
        for r in blog_reviews:
            words = re.findall(r'[가-힣]{2,8}', r.get("text","") + r.get("title",""))
            for w in words:
                if w not in stopwords and len(w) >= 2:
                    kw_counter[w] += 1
        top_keywords_blog = [{"word": w, "count": c} for w, c in kw_counter.most_common(10)]

        receipt_kw = Counter()
        for r in receipt_reviews:
            words = re.findall(r'[가-힣]{2,8}', r.get("text","") + r.get("title",""))
            for w in words:
                if w not in stopwords and len(w) >= 2:
                    receipt_kw[w] += 1
        top_keywords_receipt = [{"word": w, "count": c} for w, c in receipt_kw.most_common(10)]

        # ── 키워드 카테고리 분류 ─────────────────────────────────
        # 메뉴 관련 단어 패턴 (음식명, 재료, 조리법)
        # ── 키워드 카테고리 힌트 사전 (전면 강화) ──────────────
        # 메뉴: 음식명, 재료, 조리법 포함 단어
        MENU_HINTS = {
            # 조리법/형태
            "탕","찌개","국","전골","구이","볶음","튀김","무침","조림","비빔","냉면",
            "떡볶이","순대","만두","전","부침","김치","나물","된장","청국장",
            "파스타","스테이크","샐러드","버거","피자","리조또","그라탕",
            "초밥","라멘","우동","소바","덮밥","카레","돈까스",
            "삼겹","갈비","곱창","막창","오겹","항정살","차돌","꽃등심",
            "삼계탕","추어탕","설렁탕","곰탕","해장국","순대국",
            # 재료/식재료
            "사골","육수","국물","전복","새우","게살","랍스터","킹크랩","연어","참치",
            "한우","흑돼지","오리","닭","낙지","오징어","굴","홍합","바지락",
            "두부","버섯","가지","아보카도","명란","성게","성게알",
            # 음료/디저트
            "커피","라떼","에스프레소","아메리카노","케이크","마카롱","빙수","아이스크림",
            "디저트","와인","맥주","소주","막걸리","하이볼","칵테일","사케",
            # 식사 유형
            "코스","오마카세","런치","디너","브런치","뷔페","세트","정식","한상",
            # 맛 표현 (메뉴 관련)
            "담백","얼큰","깔끔","진한","부드러운","바삭","촉촉","고소","달콤","새콤",
        }

        # 위치: 실제 장소·접근성 관련 단어만 (새로·바로 등 제외)
        LOC_HINTS = {
            "역","출구","번출구","역앞","역근처","역에서",
            "골목","거리","대로","번가","번길","로에",
            "상권","지구","단지","타운","빌딩","타워","플라자","몰","센터",
            "주차장","주차","발렛","무료주차",
            "도보","분거리","걸어서","버스","지하철","교통",
        }
        # 위치 힌트 제외어 (오탐 방지 — 이 단어만 단독으로 있으면 위치 아님)
        LOC_EXCLUDE = {"새로","바로","으로","처음","다시","자주","매일","항상","드디어"}

        # 경험: 방문 경험, 서비스, 분위기, 이용 목적
        EXP_HINTS = {
            # 이용 목적
            "회식","단체","모임","소모임","돌잔치","생일파티","기념일","프로포즈","데이트","커플",
            "가족","친구","동창","혼밥","혼술","혼자","혼행",
            # 예약/대기
            "예약","웨이팅","대기","줄","번호표","노쇼","취소","캐치테이블","캐치",
            # 분위기/공간
            "분위기","인테리어","뷰","야경","루프탑","테라스","정원","마당","통창","채광",
            "아늑","조용한","넓은","쾌적","한옥","모던","빈티지","감성",
            # 서비스
            "친절","불친절","응대","직원","서비스","케어","빠른","느린",
            "포장","배달","테이크아웃","픽업",
            # 재방문 의향
            "재방문","단골","또올","또와","자주와","즐겨찾기",
        }

        def classify_keyword(word, count):
            """키워드를 메뉴/위치/경험으로 분류 (오탐 방지 포함)"""
            # 메뉴 우선
            for hint in MENU_HINTS:
                if hint in word:
                    return "menu"
            # 위치: 제외어 단독 포함 시 other 처리
            for exc in LOC_EXCLUDE:
                if word == exc or word.startswith(exc):
                    return "other"
            for hint in LOC_HINTS:
                if hint in word:
                    return "location"
            # 경험
            for hint in EXP_HINTS:
                if hint in word:
                    return "experience"
            return "other"

        # 전체 블로그+영수증 합산 Top 50으로 카테고리 분류 (범위 확대)
        all_kw = kw_counter + receipt_kw
        menu_kw, loc_kw, exp_kw = [], [], []
        top50 = all_kw.most_common(50)
        for w, c in top50:
            cat = classify_keyword(w, c)
            item = {"word": w, "count": c}
            if cat == "menu":       menu_kw.append(item)
            elif cat == "location": loc_kw.append(item)
            elif cat == "experience": exp_kw.append(item)
        top30 = top50[:30]  # 기존 호환

        # 기간 계산 (monthly 데이터 기반)
        all_months = sorted([r.get("date","")[:7] for r in blog_reviews if r.get("date","") and len(r.get("date","")) >= 7])
        kw_period = ""
        if all_months:
            def fmt_ym(ym):
                y, m = ym.split("-")
                return f"{y[2:]}.{m}"
            kw_period = f"{fmt_ym(all_months[0])}~{fmt_ym(all_months[-1])}" if len(all_months) > 1 else fmt_ym(all_months[0])

        # Top 5: 각 카테고리에서 최대 2개씩 선정 후 전체 빈도순 정렬
        def _top5_diverse(menu_kw, loc_kw, exp_kw, top30):
            """메뉴/위치/경험/기타 카테고리 균형 있게 Top5 선정"""
            selected = []
            used = set()
            # 각 카테고리에서 최고빈도 1개씩 먼저
            for lst in [menu_kw, exp_kw, loc_kw]:
                if lst and lst[0]["word"] not in used:
                    selected.append(lst[0])
                    used.add(lst[0]["word"])
            # 부족하면 각 카테고리 2순위
            for lst in [menu_kw, exp_kw, loc_kw]:
                if len(selected) >= 5: break
                if len(lst) > 1 and lst[1]["word"] not in used:
                    selected.append(lst[1])
                    used.add(lst[1]["word"])
            # 그래도 부족하면 top30에서 미선정 항목 추가
            for item in top30:
                if len(selected) >= 5: break
                if item["word"] not in used:
                    selected.append(item)
                    used.add(item["word"])
            return selected[:5]

        top5_diverse = _top5_diverse(menu_kw, loc_kw, exp_kw,
                                      [{"word":w,"count":c} for w,c in top30])

        # Top5 카테고리 라벨 부여
        def _label_kw(word):
            for hint in MENU_HINTS:
                if hint in word: return "menu"
            for hint in LOC_HINTS:
                if hint in word: return "location"
            for hint in EXP_HINTS:
                if hint in word: return "experience"
            return "other"

        top5_labeled = []
        cat_labels = {"menu":"메뉴 키워드","location":"위치 키워드",
                      "experience":"경험 키워드","other":"핵심 키워드"}
        for item in top5_diverse:
            cat = _label_kw(item["word"])
            top5_labeled.append({
                "word":  item["word"],
                "count": item["count"],
                "category": cat_labels[cat],
            })

        keyword_analysis = {
            "period": kw_period,
            "top_all": [{"word": w, "count": c} for w, c in top30[:15]],
            "menu":     menu_kw[:5],
            "location": loc_kw[:5],
            "experience": exp_kw[:5],
            "top5": top5_labeled,
        }

        # ── 월별 블로그 집계 ────────────────────────────────────
        monthly = defaultdict(lambda: {"total":0,"ad":0,"organic":0,"unknown":0})
        for r in blog_reviews:
            date = r.get("date","")
            if date and len(date) >= 7:
                ym = date[:7]
                monthly[ym]["total"] += 1
                at = r.get("ad_type","")
                if at == "광고": monthly[ym]["ad"] += 1
                elif at == "내돈내산": monthly[ym]["organic"] += 1
                else: monthly[ym]["unknown"] += 1
        monthly_blog_stats = [{"month": k, **v} for k, v in sorted(monthly.items())]

        # ── 월별 영수증 집계 ─────────────────────────────────
        monthly_receipt = defaultdict(int)
        for r in receipt_reviews:
            date = r.get("date","")
            if date and len(date) >= 7:
                monthly_receipt[date[:7]] += 1
        monthly_receipt_stats = [{"month": k, "count": v} for k, v in sorted(monthly_receipt.items())]

        # ── 경영 제언 자동 생성 ─────────────────────────────────
        insights = []
        total_blog = len(blog_reviews)
        if total_blog > 0:
            ad_pct = round(sum(1 for r in blog_reviews if r.get("ad_type")=="광고") / total_blog * 100)
            org_pct = round(sum(1 for r in blog_reviews if r.get("ad_type")=="내돈내산") / total_blog * 100)
            neg_pct_val = round(neg_count / total_s * 100)

            if org_pct >= 60:
                insights.append({
                    "type": "positive",
                    "title": "높은 내돈내산 비율",
                    "body": f"블로그 리뷰 중 내돈내산이 {org_pct}%로, 실제 고객의 자발적 방문 후기가 많습니다. 브랜드 신뢰도가 높은 상태입니다."
                })
            if ad_pct >= 40:
                insights.append({
                    "type": "warning",
                    "title": "광고 비율 점검 필요",
                    "body": f"블로그 리뷰 중 광고성 게시글이 {ad_pct}%입니다. 내돈내산 후기 유도 이벤트(예: 리뷰 작성 시 음료 서비스)를 검토해 보세요."
                })
            if neg_pct_val >= 15:
                # 부정 키워드 상위 추출
                top_neg = [w for w, _ in neg_words.most_common(3)]
                neg_str = ", ".join(top_neg) if top_neg else "대기·혼잡"
                insights.append({
                    "type": "warning",
                    "title": "부정 반응 모니터링 필요",
                    "body": f"전체 리뷰 중 부정 반응이 {neg_pct_val}%입니다. 주요 불만 키워드: {neg_str}. 피크 타임 운영 효율화를 검토하세요."
                })
            if neg_pct_val < 15 and pos_count / total_s >= 0.7:
                insights.append({
                    "type": "positive",
                    "title": "긍정 반응 우세",
                    "body": f"전체 리뷰의 {round(pos_count/total_s*100)}%가 긍정 반응입니다. 현재 서비스 품질을 유지하면서 재방문 고객 혜택을 강화하면 충성 고객 비율이 높아집니다."
                })
            if len(top_keywords_blog) > 0:
                top_word = top_keywords_blog[0]["word"]
                insights.append({
                    "type": "info",
                    "title": f"핵심 언급 키워드: '{top_word}'",
                    "body": f"블로그 리뷰에서 '{top_word}'가 가장 많이 언급됩니다. 이 키워드를 네이버 플레이스 소개글과 마케팅 문구에 적극 활용하세요."
                })

        return {
            "sentiment": sentiment,
            "pos_keywords": [{"word": w, "count": c} for w, c in pos_words.most_common(5)],
            "neg_keywords": [{"word": w, "count": c} for w, c in neg_words.most_common(5)],
            "top_keywords_blog":    top_keywords_blog,
            "keyword_analysis":     keyword_analysis,
            "top_keywords_receipt": top_keywords_receipt,
            "monthly_blog_stats":   monthly_blog_stats,
            "monthly_receipt_stats": monthly_receipt_stats,
            "insights":             insights,
        }

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
            **_build_insight(blog_list, receipt_list),
        }
    }

    Path(output_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_progress("분석 완료", 100)
    print(f"[크롤러 완료] 영수증:{len(receipt_list)} 블로그:{len(blog_list)} → {output_path}")

"""
crawler.py - SNS 분석 독립 크롤러 v34
subprocess로 실행되어 greenlet 충돌을 원천 차단.
결과는 JSON 파일로 저장.

실행: python crawler.py <place_id> <merchant_name> <region> <ig_tag> <crawl_target> <blog_target> <output_path>
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright


# ── 광고 판별 ─────────────────────────────────────────────────────
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
    return browser, ctx


# ════════════════════════════════════════════════════════════
# STEP 0: 공식 리뷰 수
# ════════════════════════════════════════════════════════════
def get_official_counts(place_id):
    counts = {"receipt_total":0,"receipt_text_total":0,"receipt_keyword":0,"blog_total":0}
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
# STEP 1: 영수증(방문자) 리뷰  v35
# 핵심 변경: 20라운드마다 브라우저 완전 재시작
#   - Playwright DOM 누적 hang 원천 차단
#   - seen_texts로 중복 리뷰 필터링 (재시작 후에도 이어서 수집)
#   - 재시작 시 스크롤을 빠르게 내려서 새 리뷰 위치로 이동
# ════════════════════════════════════════════════════════════
def crawl_receipt_reviews(place_id, target=500):
    reviews = []
    seen_texts = set()

    JS_COLLECT = """
        () => {
            const SKIP = new Set(['펼쳐서 더보기','더보기','반응 남기기','좋아요','신고','접기']);
            const r = [];
            document.querySelectorAll('div.pui__vn15t2').forEach(el => {
                if (el.dataset.collected === '1') return;
                let t = (el.innerText || '').trim();
                SKIP.forEach(s => { t = t.replace(s, '').trim(); });
                if (t.length >= 10) { r.push(t); el.dataset.collected = '1'; }
            });
            if (!r.length) {
                document.querySelectorAll('li.pui__X35jYm, li[class*="pui__X35jYm"]').forEach(li => {
                    if (li.dataset.collected === '1') return;
                    let t = (li.innerText || '').trim();
                    SKIP.forEach(s => { t = t.replace(s, '').trim(); });
                    if (t.length >= 10) { r.push(t); li.dataset.collected = '1'; }
                });
            }
            return r;
        }
    """
    JS_CLICK_BTN = """
        () => {
            const byText = [...document.querySelectorAll('a, button, span')].find(
                el => (el.innerText || '').trim().includes('펼쳐서 더보기')
            );
            if (byText) { byText.click(); return 'text'; }
            const byClass = document.querySelector('a.fvwqf, a.place_bluelink');
            if (byClass) { byClass.click(); return 'class'; }
            return null;
        }
    """
    JS_REMOVE = """
        () => {
            let cnt = 0;
            document.querySelectorAll(
                'li.pui__X35jYm[data-collected="1"], li[class*="pui__X35jYm"][data-collected="1"]'
            ).forEach(li => {
                try { li.parentNode && li.parentNode.removeChild(li); cnt++; } catch(e) {}
            });
            document.querySelectorAll('div.pui__vn15t2[data-collected="1"]').forEach(div => {
                try { div.parentNode && div.parentNode.removeChild(div); cnt++; } catch(e) {}
            });
            return cnt;
        }
    """

    mobile_url = f"https://m.place.naver.com/restaurant/{place_id}/review/visitor?entry=ple&reviewSort=recent"

    # ── 세션 단위 크롤링 함수 ─────────────────────────────────
    # 브라우저 1회 실행당 최대 ROUNDS_PER_SESSION 라운드 수행
    ROUNDS_PER_SESSION = 10  # 10라운드마다 재시작 (27라운드 hang 지점 원천 회피)

    def run_session(session_num):
        """브라우저 새로 시작 → 최대 ROUNDS_PER_SESSION 라운드 수집 → 종료"""
        nonlocal reviews, seen_texts
        collected_this_session = 0

        print(f"[영수증] ▶ 세션 {session_num} 시작 (현재 누적 {len(reviews)}건)")
        try:
            with sync_playwright() as p:
                browser, ctx = make_mobile_browser(p)
                page = ctx.new_page()
                page.set_default_timeout(15000)
                page.goto(mobile_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3.0)

                body_text = page.inner_text("body")
                if not (len(body_text) > 300 and any(kw in body_text for kw in ["리뷰","별점","방문","음식"])):
                    print(f"[영수증] 세션 {session_num}: 페이지 유효하지 않음")
                    browser.close()
                    return False  # 재시도 필요

                # 세션 번호에 따라 최대 라운드 동적 조정
                # 세션2 이후엔 중복 구간을 통과해야 하므로 더 많은 라운드 필요
                max_rounds_this_session = ROUNDS_PER_SESSION + (session_num - 1) * ROUNDS_PER_SESSION
                print(f"[영수증] 세션 {session_num} 최대 라운드: {max_rounds_this_session}")

                # 세션 시작 → 바로 수집 (스킵 없음, seen_texts가 중복 차단)
                # 실제 수집 라운드
                round_num = 0
                zero_streak = 0
                no_btn_streak = 0

                while round_num < max_rounds_this_session:
                    round_num += 1

                    for _ in range(4):
                        try:
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            time.sleep(0.25)
                        except Exception:
                            pass

                    try:
                        texts = page.evaluate(JS_COLLECT)
                    except Exception as e:
                        print(f"[영수증] 세션 {session_num} 수집 오류: {e}")
                        break

                    new_count = 0
                    for text in (texts or []):
                        t = text.strip()
                        if len(t) >= 10 and t not in seen_texts:
                            seen_texts.add(t)
                            ad_type = classify_ad(t)
                            reviews.append({
                                "text": t[:500],
                                "ad_type": ad_type,
                                "ad_basis": get_basis(t, ad_type),
                                "source": "naver_receipt",
                            })
                            new_count += 1
                            collected_this_session += 1
                        elif len(t) >= 10:
                            seen_texts.add(t)  # 중복이어도 seen_texts에 등록

                    current = len(reviews)
                    global_round = (session_num - 1) * ROUNDS_PER_SESSION + round_num
                    dup_count = len([t for t in (texts or []) if len(t.strip()) >= 10]) - new_count
                    print(f"[영수증] 세션{session_num} 라운드{round_num}(전체{global_round}): +{new_count}건(중복{dup_count}건) → 누적 {current}건")

                    _write_progress(
                        f"영수증리뷰 수집 중... ({current}건 / 목표 {target}건, 세션{session_num}-{round_num}라운드)",
                        10 + int((min(current, target) / max(target, 1)) * 28)
                    )

                    if current >= target:
                        print(f"[영수증] 목표 달성: {current}건")
                        browser.close()
                        return True  # 완료

                    # zero_streak: 텍스트 자체가 없을 때만 누적
                    # 중복으로 걸러진 경우(dup_count > 0)는 아직 읽을 리뷰가 있는 것
                    texts_found = len([t for t in (texts or []) if len(t.strip()) >= 10])
                    if texts_found == 0:
                        zero_streak += 1
                        if zero_streak >= 5:
                            print(f"[영수증] 텍스트 없음 5회 연속 → 세션 종료")
                            break
                        if zero_streak >= 3:
                            for _ in range(6):
                                try:
                                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                    time.sleep(0.4)
                                except Exception:
                                    pass
                    else:
                        zero_streak = 0  # 중복이든 신규든 텍스트가 있으면 리셋

                    try:
                        clicked = page.evaluate(JS_CLICK_BTN)
                    except Exception as e:
                        print(f"[영수증] 세션 {session_num} 클릭 오류: {e}")
                        break

                    if clicked:
                        no_btn_streak = 0
                        print(f"[영수증] JS 버튼 클릭 성공 ({clicked})")
                        time.sleep(1.2)
                    else:
                        no_btn_streak += 1
                        if no_btn_streak >= 3:
                            print("[영수증] 버튼 3회 연속 미발견 → 세션 종료")
                            break
                        for _ in range(3):
                            try:
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                time.sleep(0.4)
                            except Exception:
                                pass

                    if round_num % 5 == 0:
                        try:
                            removed = page.evaluate(JS_REMOVE)
                            print(f"[영수증] DOM 정리: {removed}개 제거")
                        except Exception:
                            pass

                browser.close()
                print(f"[영수증] 세션 {session_num} 완료: +{collected_this_session}건")
                return collected_this_session > 0  # 수집된 게 있으면 다음 세션 계속

        except Exception as e:
            print(f"[영수증] 세션 {session_num} 오류: {e}")
            return len(reviews) > 0

    # ── 세션 반복 실행 ────────────────────────────────────────
    MAX_SESSIONS = 10
    for session_num in range(1, MAX_SESSIONS + 1):
        done = run_session(session_num)
        if len(reviews) >= target:
            print(f"[영수증] 목표 달성으로 종료")
            break
        if not done:
            print(f"[영수증] 세션 {session_num} 수집 없음 → 종료")
            break

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
def crawl_naver_search_count(merchant_name, region):
    query = f"{region} {merchant_name}".strip()
    count = 0
    try:
        with sync_playwright() as p:
            browser, ctx = make_pc_browser(p)
            page = ctx.new_page()
            page.goto(f"https://search.naver.com/search.naver?query={quote(query)}&where=blog",
                      wait_until="domcontentloaded", timeout=20000)
            time.sleep(1.5)
            text = page.inner_text("body")
            m = re.search(r'약\s*([\d,]+)\s*개', text)
            if m: count = int(m.group(1).replace(",",""))
            if count == 0:
                count = len(page.locator("li.bx").all())
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
    naver_cnt = crawl_naver_search_count(merchant_name, region)

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
            "blog_ad_count":      sum(1 for r in blog_list if r["ad_type"]=="광고"),
            "blog_organic_count": sum(1 for r in blog_list if r["ad_type"]=="내돈내산"),
            "blog_unknown_count": sum(1 for r in blog_list if r["ad_type"]=="판별불가"),
            "receipt_ad_count":      sum(1 for r in receipt_list if r["ad_type"]=="광고"),
            "receipt_organic_count": sum(1 for r in receipt_list if r["ad_type"]=="내돈내산"),
            "receipt_unknown_count": sum(1 for r in receipt_list if r["ad_type"]=="판별불가"),
        }
    }

    Path(output_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_progress("분석 완료", 100)
    print(f"[크롤러 완료] 영수증:{len(receipt_list)} 블로그:{len(blog_list)} → {output_path}")

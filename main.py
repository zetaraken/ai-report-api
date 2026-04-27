# main.py (v2.0 - 네이버 플레이스 정밀 크롤링 버전)
import asyncio
from playwright.async_api import async_playwright

async def crawl_naver_place(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url)
        
        # 1. 플레이스 내부 iframe으로 진입
        iframe_element = await page.wait_for_selector("#entryIframe")
        frame = await iframe_element.content_frame()
        
        # 2. 리뷰 탭 클릭
        await frame.click("text=리뷰")
        await asyncio.sleep(2)
        
        # 3. '더보기' 버튼이 없을 때까지 무한 클릭
        while True:
            try:
                more_button = await frame.query_selector("a:has-text('더보기')")
                if more_button:
                    await more_button.click()
                    await asyncio.sleep(1.5) # 로딩 대기
                else:
                    break
            except:
                break

        # 4. 전체 리뷰 데이터 파싱
        reviews = await frame.query_selector_all(".z6_tU")
        results = []
        for review in reviews:
            content = await review.query_selector(".rvp67")
            date = await review.query_selector(".time")
            results.append({
                "content": await content.inner_text() if content else "",
                "date": await date.inner_text() if date else ""
            })
            
        await browser.close()
        return results

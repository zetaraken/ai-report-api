# ai-report-api / main.py 중 일부 수정

def run_crawl_task(job_id, merchant_id):
    try:
        # 가맹점 정보 조회
        merchant = next((m for m in MERCHANTS if m["id"] == merchant_id), None)
        merchant_name = merchant["name"] if merchant else "알 수 없는 매장"
        
        CRAWL_JOBS[job_id].update({"progress": 50, "message": "데이터 분석 중..."})
        time.sleep(3) 

        # [핵심] 이미지 20260428_175145_2.png의 표와 카드에 들어갈 데이터 구조입니다.
        report_data = {
            "merchant_name": merchant_name,
            "summary": {
                "total_mentions": 156,
                "receipt_reviews": 24,
                "place_blogs": 15,
                "naver_blogs": 82,
                "instagram": 45,
                "youtube_views": 5200
            },
            "monthly_data": [
                {"month": "2026-05", "naver": 82, "insta": 45, "receipt": 24, "place": 15, "youtube": 5, "total": 171},
                {"month": "2026-04", "naver": 74, "insta": 38, "receipt": 20, "place": 12, "youtube": 3, "total": 147}
            ]
        }

        CRAWL_JOBS[job_id].update({
            "status": "done",
            "message": "수집 완료",
            "progress": 100,
            "result": report_data,
            "finished_at": now_iso()
        })
    except Exception as e:
        CRAWL_JOBS[job_id].update({"status": "error", "message": str(e)})

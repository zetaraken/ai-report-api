"""
SNS 분석 자동화 솔루션 - 백엔드 API v34
크롤링을 subprocess(crawler.py)로 분리 실행 → greenlet 충돌 완전 차단
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="SNS 분석 솔루션 API", version="34.0.0")
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


# ════════════════════════════════════════════════════════════
# 크롤링 오케스트레이터 — subprocess로 crawler.py 실행
# ════════════════════════════════════════════════════════════
def crawl_merchant(job_id, merchant):
    place_id = merchant["place_id"]
    name     = merchant["name"]
    region   = merchant.get("region", "")
    ig_tag   = merchant.get("instagram_tag") or name

    def upd(pct, msg):
        job = CRAWL_JOBS.get(job_id, {})
        job.update({"status": "running", "progress": pct, "message": msg})
        CRAWL_JOBS[job_id] = job
        save_job(job)
        print(f"[{pct}%] {msg}")

    # 하트비트
    _stop_hb = threading.Event()
    def _heartbeat():
        while not _stop_hb.is_set():
            _stop_hb.wait(30)
            if not _stop_hb.is_set():
                job = CRAWL_JOBS.get(job_id, {})
                if job.get("status") == "running":
                    # 진행 파일에서 최신 상태 읽기
                    try:
                        prog = json.loads(progress_path.read_text(encoding="utf-8"))
                        job.update({"progress": prog["progress"], "message": prog["message"]})
                        CRAWL_JOBS[job_id] = job
                    except Exception:
                        pass
                    save_job(job)
                    print(f"[HB] {job_id} {job.get('progress')}%")

    # 임시 파일 경로
    output_path   = DATA_DIR / f"crawler_result_{job_id}.json"
    progress_path = DATA_DIR / f"crawler_progress_{job_id}.json"
    progress_path.write_text(json.dumps({"progress": 0, "message": "분석 시작..."}), encoding="utf-8")

    upd(5, "플레이스 공식 리뷰 수 확인 중...")

    # place_counts 먼저 파악 (target 계산용)
    # crawler.py 내부에서 처리하므로 여기서는 기본값 사용
    crawl_target = 500
    blog_target  = 100

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    try:
        # crawler.py 위치: main.py와 같은 디렉토리
        crawler_path = Path(__file__).parent / "crawler.py"

        cmd = [
            sys.executable, str(crawler_path),
            place_id, name, region, ig_tag,
            str(crawl_target), str(blog_target),
            str(output_path), str(progress_path),
        ]

        print(f"[v34] subprocess 시작: {' '.join(cmd)}")
        upd(5, "크롤러 프로세스 시작 중...")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

        # subprocess stdout 실시간 출력 + 진행 상태 폴링
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"[crawler] {line}")
            # 진행 파일 폴링
            try:
                prog = json.loads(progress_path.read_text(encoding="utf-8"))
                pct = prog.get("progress", 5)
                msg = prog.get("message", "")
                if pct > CRAWL_JOBS.get(job_id, {}).get("progress", 0):
                    upd(pct, msg)
            except Exception:
                pass

        proc.wait()
        _stop_hb.set()

        if proc.returncode != 0:
            raise RuntimeError(f"crawler.py 비정상 종료: returncode={proc.returncode}")

        if not output_path.exists():
            raise RuntimeError("크롤러 결과 파일 없음")

        # 결과 로드
        crawler_result = json.loads(output_path.read_text(encoding="utf-8"))

        receipt_list = crawler_result.get("naver_receipt_reviews", [])
        blog_list    = crawler_result.get("naver_blog_reviews", [])

        result = {
            "merchant_id":   merchant["id"],
            "merchant_name": name,
            "crawled_at":    datetime.now().isoformat(),
            "naver_receipt_reviews": receipt_list,
            "naver_blog_reviews":    blog_list,
            "naver_search_count":    crawler_result.get("naver_search_count", 0),
            "instagram_count":       crawler_result.get("instagram_count", 0),
            "place_counts":          crawler_result.get("place_counts", {}),
            "summary":               crawler_result.get("summary", {}),
        }

        REPORTS[merchant["id"]] = result
        save_report(merchant["id"], result)

        done_job = {**CRAWL_JOBS.get(job_id, {}),
                    "status": "done", "progress": 100,
                    "message": "분석 완료", "report_id": merchant["id"]}
        CRAWL_JOBS[job_id] = done_job
        save_job(done_job)
        print(f"[DONE] {name} / 영수증:{len(receipt_list)} 블로그:{len(blog_list)}")

    except Exception as e:
        _stop_hb.set()
        job = CRAWL_JOBS.get(job_id, {})
        job.update({"status": "error", "message": f"오류: {str(e)}"})
        CRAWL_JOBS[job_id] = job
        save_job(job)
        print(f"[ERROR] {e}")

    finally:
        # 임시 파일 정리
        for p in [output_path, progress_path]:
            try: p.unlink()
            except Exception: pass


# ════════════════════════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════════════════════════
@app.get("/")
async def root(): return {"message": "SNS 분석 솔루션 API v34"}

@app.get("/api/merchants")
async def get_merchants(): return MERCHANTS

@app.post("/api/merchants")
async def add_merchant(data: MerchantCreate):
    m = {"id": str(uuid.uuid4())[:8], "name": data.name, "region": data.region,
         "place_id": data.place_id, "instagram_tag": data.instagram_tag or data.name,
         "created_at": datetime.now().isoformat()}
    MERCHANTS.append(m); save_merchants(); return m

@app.put("/api/merchants/{mid}")
async def update_merchant(mid: str, data: MerchantUpdate):
    m = next((m for m in MERCHANTS if m["id"] == mid), None)
    if not m: raise HTTPException(404, "가맹점 없음")
    for f in ["name", "region", "place_id", "instagram_tag"]:
        v = getattr(data, f)
        if v is not None: m[f] = v
    save_merchants(); return m

@app.delete("/api/merchants/{mid}")
async def delete_merchant(mid: str):
    global MERCHANTS
    MERCHANTS = [m for m in MERCHANTS if m["id"] != mid]
    save_merchants(); return {"deleted": mid}

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    merchant = next((m for m in MERCHANTS if m["id"] == req.merchant_id), None)
    if not merchant: raise HTTPException(404, "가맹점 없음")
    job_id = str(uuid.uuid4())
    job = {"id": job_id, "merchant_id": req.merchant_id, "merchant_name": merchant["name"],
           "status": "pending", "progress": 0, "message": "분석 대기 중...",
           "started_at": datetime.now().isoformat()}
    CRAWL_JOBS[job_id] = job
    save_job(job)
    executor.submit(crawl_merchant, job_id, merchant)
    return {"job_id": job_id}

@app.get("/api/crawl-jobs/{job_id}")
async def get_job(job_id: str):
    j = load_job(job_id)
    if not j: raise HTTPException(404, "작업 없음")
    return j

@app.get("/api/reports/{mid}")
async def get_report(mid: str):
    r = load_report(mid)
    if not r: raise HTTPException(404, "리포트 없음. 분석을 먼저 실행하세요.")
    return r

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "v34",
            "merchants": len(MERCHANTS), "reports": len(REPORTS)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

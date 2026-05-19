"""
SNS 분석 자동화 솔루션 - 백엔드 API v36
크롤링을 subprocess(crawler.py)로 분리 실행 → greenlet 충돌 완전 차단

v36 변경사항:
  1. 데이터 저장소 JSON 파일 → 시놀로지 MariaDB로 전환
  2. DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD 환경변수로 연결
  3. 가맹점/리포트/작업 모두 DB 테이블로 영속 저장
  4. MariaDB 미설정 시 기존 JSON 파일 방식으로 자동 폴백
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

app = FastAPI(title="SNS 분석 솔루션 API", version="36.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DB 연결 설정 ──────────────────────────────────────────────────
DB_HOST     = os.environ.get("DB_HOST", "")
DB_PORT     = int(os.environ.get("DB_PORT", "3306"))
DB_NAME     = os.environ.get("DB_NAME", "sns_analyzer")
DB_USER     = os.environ.get("DB_USER", "snsuser")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
USE_DB      = bool(DB_HOST and DB_PASSWORD)

db_pool = None

def init_db():
    """MariaDB 초기화 — 테이블 생성"""
    global db_pool
    if not USE_DB:
        print("[DB] 환경변수 미설정 → JSON 파일 모드로 동작")
        return
    try:
        import pymysql
        from pymysql import pool as pymysql_pool
        db_pool = pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASSWORD, database=DB_NAME,
            charset="utf8mb4", autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        cur = db_pool.cursor()
        # 가맹점 테이블
        cur.execute("""
            CREATE TABLE IF NOT EXISTS merchants (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                place_id VARCHAR(100) NOT NULL,
                instagram_tag VARCHAR(255),
                addr_keyword VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # 리포트 테이블 (JSON blob)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                merchant_id VARCHAR(36) PRIMARY KEY,
                data LONGTEXT NOT NULL,
                crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # 작업 테이블
        cur.execute("""
            CREATE TABLE IF NOT EXISTS crawl_jobs (
                id VARCHAR(36) PRIMARY KEY,
                merchant_id VARCHAR(36),
                merchant_name VARCHAR(255),
                status VARCHAR(20) DEFAULT 'pending',
                progress INT DEFAULT 0,
                message TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                data LONGTEXT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        print(f"[DB] MariaDB 연결 성공: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    except Exception as e:
        print(f"[DB] 연결 실패 → JSON 파일 모드로 폴백: {e}")
        db_pool = None

def get_conn():
    """DB 커넥션 반환 (끊김 시 재연결)"""
    global db_pool
    if db_pool is None:
        return None
    try:
        db_pool.ping(reconnect=True)
        return db_pool
    except Exception as e:
        print(f"[DB] 재연결 시도: {e}")
        try:
            import pymysql
            db_pool = pymysql.connect(
                host=DB_HOST, port=DB_PORT, user=DB_USER,
                password=DB_PASSWORD, database=DB_NAME,
                charset="utf8mb4", autocommit=True,
                cursorclass=pymysql.cursors.DictCursor,
            )
            return db_pool
        except Exception as e2:
            print(f"[DB] 재연결 실패: {e2}")
            return None

# ── JSON 파일 폴백 설정 ───────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/sns_analyzer_data"))
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

# ── 가맹점 CRUD ──────────────────────────────────────────────────
def load_merchants():
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM merchants ORDER BY created_at")
                return list(cur.fetchall())
        except Exception as e:
            print(f"[DB] load_merchants 오류: {e}")
    return _load_json(MERCHANTS_FILE, [])

def save_merchant_db(m):
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO merchants (id, name, place_id, instagram_tag, addr_keyword, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        name=%s, place_id=%s, instagram_tag=%s, addr_keyword=%s
                """, (m["id"], m["name"], m["place_id"], m.get("instagram_tag",""), m.get("addr_keyword",""),
                      m.get("created_at", datetime.now().isoformat()),
                      m["name"], m["place_id"], m.get("instagram_tag",""), m.get("addr_keyword","")))
            return True
        except Exception as e:
            print(f"[DB] save_merchant 오류: {e}")
    # 폴백
    merchants = _load_json(MERCHANTS_FILE, [])
    merchants = [x for x in merchants if x["id"] != m["id"]] + [m]
    _save_json(MERCHANTS_FILE, merchants)
    return False

def delete_merchant_db(mid):
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM merchants WHERE id=%s", (mid,))
            return True
        except Exception as e:
            print(f"[DB] delete_merchant 오류: {e}")
    # 폴백
    merchants = _load_json(MERCHANTS_FILE, [])
    _save_json(MERCHANTS_FILE, [x for x in merchants if x["id"] != mid])
    return False

# ── 리포트 CRUD ──────────────────────────────────────────────────
def save_report(mid, report):
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO reports (merchant_id, data, crawled_at)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE data=%s, crawled_at=%s
                """, (mid, json.dumps(report, ensure_ascii=False),
                      report.get("crawled_at", datetime.now().isoformat()),
                      json.dumps(report, ensure_ascii=False),
                      report.get("crawled_at", datetime.now().isoformat())))
            return
        except Exception as e:
            print(f"[DB] save_report 오류: {e}")
    _save_json(REPORTS_DIR / f"{mid}.json", report)

def load_report(mid):
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM reports WHERE merchant_id=%s", (mid,))
                row = cur.fetchone()
                if row:
                    return json.loads(row["data"])
        except Exception as e:
            print(f"[DB] load_report 오류: {e}")
    return _load_json(REPORTS_DIR / f"{mid}.json", None)

# ── 작업 CRUD ────────────────────────────────────────────────────
CRAWL_JOBS: Dict[str, Dict] = {}

def save_job(job):
    CRAWL_JOBS[job["id"]] = job
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO crawl_jobs (id, merchant_id, merchant_name, status, progress, message, started_at, data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        status=%s, progress=%s, message=%s, data=%s
                """, (job["id"], job.get("merchant_id",""), job.get("merchant_name",""),
                      job.get("status","pending"), job.get("progress",0), job.get("message",""),
                      job.get("started_at", datetime.now().isoformat()),
                      json.dumps(job, ensure_ascii=False),
                      job.get("status","pending"), job.get("progress",0),
                      job.get("message",""), json.dumps(job, ensure_ascii=False)))
            return
        except Exception as e:
            print(f"[DB] save_job 오류: {e}")
    _save_json(JOBS_DIR / f"{job['id']}.json", job)

def load_job(job_id):
    if job_id in CRAWL_JOBS:
        return CRAWL_JOBS[job_id]
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM crawl_jobs WHERE id=%s", (job_id,))
                row = cur.fetchone()
                if row:
                    data = json.loads(row["data"])
                    CRAWL_JOBS[job_id] = data
                    return data
        except Exception as e:
            print(f"[DB] load_job 오류: {e}")
    data = _load_json(JOBS_DIR / f"{job_id}.json", None)
    if data:
        CRAWL_JOBS[job_id] = data
    return data

executor = ThreadPoolExecutor(max_workers=2)

# ── Pydantic ──────────────────────────────────────────────────────
class MerchantCreate(BaseModel):
    name: str; place_id: str; instagram_tag: Optional[str] = ""; addr_keyword: Optional[str] = ""

class MerchantUpdate(BaseModel):
    name: Optional[str]=None; place_id: Optional[str]=None
    instagram_tag: Optional[str]=None; addr_keyword: Optional[str]=None

class CrawlRequest(BaseModel):
    merchant_id: str


# ════════════════════════════════════════════════════════════
# 크롤링 오케스트레이터
# ════════════════════════════════════════════════════════════
def crawl_merchant(job_id, merchant):
    place_id     = merchant["place_id"]
    name         = merchant["name"]
    region       = merchant.get("addr_keyword", "")
    ig_tag       = merchant.get("instagram_tag") or name
    addr_keyword = merchant.get("addr_keyword", "")

    def upd(pct, msg):
        job = CRAWL_JOBS.get(job_id, {})
        job.update({"status": "running", "progress": pct, "message": msg})
        CRAWL_JOBS[job_id] = job
        save_job(job)
        print(f"[{pct}%] {msg}")

    output_path   = DATA_DIR / f"crawler_result_{job_id}.json"
    progress_path = DATA_DIR / f"crawler_progress_{job_id}.json"
    progress_path.write_text(json.dumps({"progress": 0, "message": "분석 시작..."}), encoding="utf-8")

    _stop_hb = threading.Event()
    def _heartbeat():
        while not _stop_hb.is_set():
            _stop_hb.wait(30)
            if not _stop_hb.is_set():
                job = CRAWL_JOBS.get(job_id, {})
                if job.get("status") == "running":
                    try:
                        prog = json.loads(progress_path.read_text(encoding="utf-8"))
                        job.update({"progress": prog["progress"], "message": prog["message"]})
                        CRAWL_JOBS[job_id] = job
                    except Exception:
                        pass
                    save_job(job)
                    print(f"[HB] {job_id} {job.get('progress')}%")

    upd(5, "크롤러 시작 중...")
    crawl_target = 220
    blog_target  = 100

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    try:
        crawler_path = Path(__file__).parent / "crawler.py"
        cmd = [
            sys.executable, str(crawler_path),
            place_id, name, region, ig_tag,
            str(crawl_target), str(blog_target),
            str(output_path), str(progress_path),
            addr_keyword,
        ]

        print(f"[v36] subprocess 시작")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8",
        )

        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"[crawler] {line}")
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
        for p in [output_path, progress_path]:
            try: p.unlink()
            except Exception: pass


# ════════════════════════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════════════════════════
@app.get("/")
async def root(): return {"message": "SNS 분석 솔루션 API v36", "db": "MariaDB" if get_conn() else "JSON파일"}

@app.get("/api/merchants")
async def get_merchants(): return load_merchants()

@app.post("/api/merchants")
async def add_merchant(data: MerchantCreate):
    m = {"id": str(uuid.uuid4())[:8], "name": data.name,
         "place_id": data.place_id, "instagram_tag": data.instagram_tag or data.name,
         "addr_keyword": data.addr_keyword or "",
         "created_at": datetime.now().isoformat()}
    save_merchant_db(m)
    return m

@app.put("/api/merchants/{mid}")
async def update_merchant(mid: str, data: MerchantUpdate):
    merchants = load_merchants()
    m = next((m for m in merchants if m["id"] == mid), None)
    if not m: raise HTTPException(404, "가맹점 없음")
    for f in ["name", "place_id", "instagram_tag", "addr_keyword"]:
        v = getattr(data, f)
        if v is not None: m[f] = v
    save_merchant_db(m)
    return m

@app.delete("/api/merchants/{mid}")
async def delete_merchant(mid: str):
    delete_merchant_db(mid)
    return {"deleted": mid}

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    merchants = load_merchants()
    merchant = next((m for m in merchants if m["id"] == req.merchant_id), None)
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
    conn = get_conn()
    return {
        "status": "ok", "version": "v36",
        "storage": "MariaDB" if conn else "JSON파일",
        "db_host": DB_HOST if conn else None,
    }

@app.on_event("startup")
async def startup():
    init_db()

if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

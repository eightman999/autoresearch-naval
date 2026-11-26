"""NSDB 網羅性監査の共通ライブラリ (読み取り専用 / 低頻度取得)。

- NSDB は mode=ro でのみ開く（書き込み禁止）。
- HTTP 取得は 1 req/s 以下、正直な User-Agent、HTML をディスクにキャッシュ（二重取得なし）。
- 連続エラー/Cloudflare・CAPTCHA 検知で例外を投げて停止する（回避しない）。
- 全リクエストを requests_log.jsonl に追記する。
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests

NSDB_PATH = "/Users/eightman/dev/data/nsdb/NSDB.db"
SCRATCH = Path("/private/tmp/claude-501/-Users-eightman-dev/0ec7c03e-08af-473b-871d-d48b22f6efb4/scratchpad/nsdb_audit")
CACHE_DIR = SCRATCH / "cache"
REQ_LOG = SCRATCH / "requests_log.jsonl"
MIRROR = "https://myownonpmirror.com"
ORIGIN = "https://www.navypedia.org"
USER_AGENT = "nsdb-completeness-audit/1.0 (personal research; read-only; <=1 req/s)"
MIN_INTERVAL = 1.05
MAX_CONSECUTIVE_ERRORS = 5
CACHE_BUDGET_BYTES = 500 * 1024 * 1024


class StopCrawl(RuntimeError):
    """Cloudflare/CAPTCHA/ログイン要求/連続エラーなど、回避せず停止すべき状態。"""


def open_ro(path: str = NSDB_PATH) -> sqlite3.Connection:
    """NSDB を読み取り専用で開く。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=1")
    return conn


def clean_text(text) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


def canon_url(url: str, base: str = MIRROR + "/") -> str:
    """URL を『ホスト非依存・.html 統一・小文字パス』のキー(path のみ)へ正規化する。

    navypedia.org 形式 (https://www.navypedia.org/ships/x/y.htm) とミラー形式
    (https://myownonpmirror.com/ships/x/y.html) を同一キー '/ships/x/y.html' にする。
    """
    if not url:
        return ""
    p = urlparse(urljoin(base, url))
    path = re.sub(r"\.htm$", ".html", p.path)
    # Sarawak など、サイト側の壊れた href: '.../file:///C:/seawar/ships/sarawak/x.htm' -> '/ships/sarawak/x.html'
    m = re.search(r"/seawar(/ships/.+)$", path)
    if m:
        path = m.group(1)
    return posixpath.normpath(path)  # '/a/../b' -> '/b'


class Crawler:
    def __init__(self, base: str = MIRROR, delay: float = MIN_INTERVAL):
        self.base = base
        self.delay = delay
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"})
        self.last = 0.0
        self.consec_err = 0
        self.n_net = 0
        self.n_cache = 0
        self.t0 = time.time()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def cache_path(self, url: str) -> Path:
        p = urlparse(url)
        key = hashlib.sha1(url.encode()).hexdigest()[:10]
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", p.path.strip("/"))[-120:]
        host = "m" if "myownonpmirror" in p.netloc else "o"
        return CACHE_DIR / f"{host}__{name}__{key}.html"

    def cache_size(self) -> int:
        return sum(f.stat().st_size for f in CACHE_DIR.glob("*.html"))

    def get(self, url: str) -> tuple[int, bytes]:
        """(status, 生bytes) を返す。キャッシュ済みならネットワークに出ない。
        文字コードは meta charset (windows-1251 等) 依存のため bytes のまま保持し BeautifulSoup に渡す。"""
        cp = self.cache_path(url)
        if cp.exists():
            self.n_cache += 1
            raw = cp.read_bytes()
            m = re.match(rb"<!--STATUS:(\d+)-->\n", raw)
            return int(m.group(1)), raw[m.end():]
        if self.cache_size() > CACHE_BUDGET_BYTES:
            raise StopCrawl("cache budget exceeded")
        wait = self.delay - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        t = time.time()
        status, text, err = 0, b"", ""
        try:
            r = self.sess.get(url, timeout=30)
            status, text = r.status_code, r.content
        except requests.RequestException as e:  # noqa: PERF203
            err = repr(e)[:200]
        self.last = time.time()
        self.n_net += 1
        with open(REQ_LOG, "a") as f:
            f.write(json.dumps({"t": round(t, 2), "url": url, "status": status, "bytes": len(text),
                                "sec": round(self.last - t, 2), "err": err}) + "\n")
        low = text[:4000].decode("latin-1").lower()
        if status in (403, 429, 503) or "cf-chl" in low or "just a moment" in low or "captcha" in low \
                or "attention required" in low:
            raise StopCrawl(f"blocked/challenge status={status} url={url}")
        if status == 0 or status >= 500:
            self.consec_err += 1
            if self.consec_err >= MAX_CONSECUTIVE_ERRORS:
                raise StopCrawl(f"{self.consec_err} consecutive errors, last={status} {err} url={url}")
            return status, b""
        self.consec_err = 0
        cp.write_bytes(f"<!--STATUS:{status}-->\n".encode() + text)
        return status, text

    def stats(self) -> dict:
        return {"network_requests": self.n_net, "cache_hits": self.n_cache,
                "elapsed_sec": round(time.time() - self.t0, 1), "cache_bytes": self.cache_size()}

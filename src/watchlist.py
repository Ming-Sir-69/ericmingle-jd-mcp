"""EricMingle local categorized shortlist; never a platform favorite or cart."""
import math
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time


class Watchlist:
    def __init__(self, platform, validate_url, data_dir, clock=time.time):
        self.platform, self.validate_url, self.clock = platform, validate_url, clock
        self.path = Path(data_dir) / "watchlist.sqlite3"

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path, timeout=5)
        os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        db.executescript("CREATE TABLE IF NOT EXISTS list(url TEXT PRIMARY KEY, category TEXT NOT NULL, target_price REAL); CREATE TABLE IF NOT EXISTS quotes(url TEXT PRIMARY KEY, title TEXT, price REAL, observed_at REAL);")
        try:
            with db:
                yield db
        finally:
            db.close()

    def result(self, data):
        return {"ok": True, "status": "ok", "platform": self.platform, "data": data, "fields": {}, "scope": "local_watchlist", "background_monitoring": False}

    def upsert(self, url, category="未分类", target_price=None):
        url = self.validate_url(url)
        if not isinstance(category, str) or not 1 <= len(category.strip()) <= 64:
            raise ValueError("category must be 1–64 characters")
        if target_price is not None and (isinstance(target_price, bool) or not isinstance(target_price, (int, float)) or not math.isfinite(target_price) or not 0 <= target_price <= 1e9):
            raise ValueError("target_price must be a finite nonnegative amount")
        with self.connect() as db:
            db.execute("INSERT INTO list VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET category=excluded.category,target_price=excluded.target_price", (url, category.strip(), target_price))
        return self.result({"url": url, "category": category.strip(), "target_price": target_price})

    def observe(self, result):
        """Internal only: feed results from this service's real search/detail reads."""
        try:
            self._observe(result)
        except (OSError, sqlite3.Error):
            pass  # Optional local caching must never hide a successful platform read.

    def _observe(self, result):
        if not isinstance(result, dict) or result.get("ok") is False or result.get("success") is False or result.get("state") in {"error", "risk_control"}:
            return
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        items = data.get("items", [data])
        if not isinstance(items, list):
            return
        rows = []
        for item in items[:50]:
            if not isinstance(item, dict):
                continue
            amount = item.get("price")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0 or item.get("price_is_from") or "起" in str(item.get("price_text", "")):
                continue
            try:
                url = self.validate_url(item.get("product_url") or item.get("url") or "")
            except (ValueError, TypeError):
                continue
            rows.append((url, str(item.get("title") or item.get("name") or "")[:300], amount, self.clock()))
        if rows:
            with self.connect() as db:
                db.executemany("INSERT INTO quotes VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,price=excluded.price,observed_at=excluded.observed_at", rows)

    def list(self, category=""):
        if not isinstance(category, str) or len(category) > 64:
            raise ValueError("invalid category")
        with self.connect() as db:
            rows = db.execute("SELECT l.*,q.title,q.price,q.observed_at FROM list l LEFT JOIN quotes q ON q.url=l.url WHERE (?='' OR category=?) ORDER BY category,l.url LIMIT 500", (category, category)).fetchall()
        return self.result({"items": [dict(row) for row in rows]})

    def check(self):
        now = self.clock()
        rows = self.list()["data"]["items"]
        matches = [dict(row, age_seconds=round(now-row["observed_at"]), review_required=True) for row in rows if row["target_price"] is not None and row["price"] is not None and row["observed_at"] is not None and 0 <= now-row["observed_at"] <= 3600 and row["price"] <= row["target_price"]]
        return self.result({"matches": matches, "max_quote_age_seconds": 3600, "message": "仅检查本服务最近真实读取的展示价格；无联网刷新或后台通知。命中后请本人核对规格、运费和优惠，再自行下单支付。"})


def register_watchlist(mcp, store):
    from mcp.types import ToolAnnotations

    def call(method, *args):
        try:
            return method(*args)
        except ValueError as exc:
            return {"ok": False, "status": "error", "error_code": "invalid_input", "fields": {}, "message": str(exc)}
        except (OSError, sqlite3.Error):
            return {"ok": False, "status": "error", "error_code": "local_store_unavailable", "fields": {}}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def watchlist_upsert(url: str, category: str = "未分类", target_price: float | None = None) -> dict:
        """保存到本地分类购物清单；不是平台原生收藏或购物车。target_price 为自己的目标价。"""
        return call(store.upsert, url, category, target_price)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def watchlist_list(category: str = "") -> dict:
        """按分类列出本地购物清单及本服务最近观测的价格，不请求平台。"""
        return call(store.list, category)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def watchlist_check() -> dict:
        """返回一小时内真实展示价达到目标的条目；仅调用时检查，无后台监控或主动通知。"""
        return call(store.check)

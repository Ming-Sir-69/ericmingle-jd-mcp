"""Local, JD-only read adapter for the fixed MIT-licensed JD-Taobao-MCP source.

Uses only the pinned upstream MIT pure text/URL/price helper module.
The existing daily Brave is reached only through its task-scoped WebBridge.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import os
import sqlite3
from pathlib import Path
import re
import sys
from typing import Any, Awaitable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, quote_plus, unquote

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "references" / "JD-Taobao-MCP"
sys.path.insert(0, str(UPSTREAM))

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
import httpx

sys.path.insert(0, str(ROOT / "src"))
from commerce_state import result, RiskStore
from shopping_dom import favorite_script
from jd_commerce import JDCommerce, WriteJournal
from http_security import protect_http
from watchlist import Watchlist, register_watchlist
from notifications import Notifications, register_notifications
from jd_dom import SEARCH_SCRIPT, DETAIL_SCRIPT, DETAIL_SELECTORS, READINESS_SCRIPT
from jd_lists_dom import favorite_list_script, conversation_list_script
from jd_search_dom import search_page_script
import importlib.util
_helper_spec = importlib.util.spec_from_file_location("jd_pure_helpers", UPSTREAM / "jd_taobao_mcp" / "extractors" / "helpers.py")
_helpers = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helpers)
compact_text, normalize_url, parse_price = _helpers.compact_text, _helpers.normalize_url, _helpers.parse_price

JD_LOGIN_URL = "https://passport.jd.com/new/login.aspx"
BRIDGE_ENDPOINT = os.environ.get("WEBBRIDGE_URL", os.environ.get("JD_WEBBRIDGE_URL", "http://127.0.0.1:10086/command"))
BRIDGE_SESSION = os.environ.get("WEBBRIDGE_SESSION", os.environ.get("JD_WEBBRIDGE_SESSION", "commerce-mcp-jd"))
GROUP_TITLE = os.environ.get("WEBBRIDGE_GROUP", os.environ.get("JD_WEBBRIDGE_GROUP", "京东MCP"))
HTTP_HOST = os.environ.get("MCP_HOST", os.environ.get("JD_MCP_HOST", "127.0.0.1"))
HTTP_PORT = int(os.environ.get("MCP_PORT", os.environ.get("JD_MCP_PORT", "8842")))
META_CODE = r"""(() => { /* JD_META: read-only status, no form values or credentials */
const t=(document.body?.innerText || '').slice(0,12000);
const u=new URL(location.href); u.hash=''; u.username=''; u.password='';
if (/^(search\.jd\.com|search\.360buy\.com)$/.test(u.hostname) && u.pathname.toLowerCase()==='/search') {
  const q=new URLSearchParams(); for(const [k,v] of u.searchParams) if(k==='keyword'||k==='q') q.append(k,v); u.search=q.toString();
} else u.search='';
const authControls=[...document.querySelectorAll('input[type="password"],input[autocomplete="one-time-code"],[role="dialog"][class*="login" i],[class*="login-dialog" i],[class*="login-modal" i],#loginDialog,#J_login')].some(el=>{
 const s=getComputedStyle(el); return s.display!=='none' && s.visibility!=='hidden' && s.visibility!=='collapse' && s.opacity!=='0'
 && [...el.getClientRects()].some(r=>r.width>0&&r.height>0);
});
const riskUrl=u.hostname==='cfe.m.jd.com' && /^\/privatedomain\/risk_handler(?:\/|$)/.test(u.pathname);
if(riskUrl)u.pathname='/privatedomain/risk_handler/';
const loginRequired=authControls||u.hostname==='passport.jd.com'||u.hostname.startsWith('plogin.')||u.pathname.toLowerCase().includes('/login');
const riskControl=riskUrl || /访问频繁|访问过于频繁|频繁.{0,12}(访问|搜索|操作)|系统繁忙|操作频繁|请求频繁|安全验证|滑块|人机验证|captcha|异常访问|无法搜索|验证失败/i.test(t);
const modernLoggedIn=[...document.querySelectorAll('div.dt.cw-icon > a.nickname[href*="home.jd.com"]')].some(el=>{
 const s=getComputedStyle(el);if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||s.opacity==='0'||![...el.getClientRects()].some(r=>r.width>0&&r.height>0)||!String(el.textContent||'').trim())return false;
 try{return new URL(el.getAttribute('href'),location.href).hostname==='home.jd.com';}catch{return false;}
});
return JSON.stringify({url:u.href,title:document.title.slice(0,300),
page_visible:document.visibilityState==='visible',
risk_control:riskControl,
requires_user_login:loginRequired,
likely_logged_in:!riskControl && !loginRequired && !t.includes('请登录') && (modernLoggedIn || /退出(?:登录)?/.test(t) || [...document.querySelectorAll('#ttbar-login .nickname,a[href*="logout"]')].some(el=>el.getClientRects().length))}); })()"""
RISK_RE = re.compile(r"访问频繁|访问过于频繁|频繁.{0,12}(?:访问|搜索|操作)|系统繁忙|操作频繁|请求频繁|安全验证|滑块|人机验证|captcha|异常访问|无法搜索|验证失败", re.I)



def ensure_jd_url(url: str) -> str:
    """Reject other platforms and ambiguous authorities before any navigation."""
    try:
        if not isinstance(url, str) or len(url) > 2048 or "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise ValueError
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host.isascii():
            raise ValueError
        if parsed.username is not None or parsed.password is not None or "%" in parsed.netloc:
            raise ValueError
        if parsed.port not in {None, 443 if parsed.scheme == "https" else 80}:
            raise ValueError
        if not any(host == root or host.endswith("." + root) for root in ("jd.com", "360buy.com")):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("仅允许真实 jd.com 或 360buy.com 的 HTTP(S) URL；拒绝其他平台及伪装域名。") from None
    return url


def ensure_product_url(url: str) -> str:
    """Expose only numeric product pages, not account/cart/transaction endpoints."""
    ensure_jd_url(url)
    parsed = urlparse(url)
    if parsed.hostname not in {"item.jd.com", "item.360buy.com", "item.m.jd.com"}:
        raise ValueError("请提供京东商品链接，例如 https://item.jd.com/123.html。")
    pattern = r"/(?:product/)?[0-9]+\.html" if parsed.hostname == "item.m.jd.com" else r"/[0-9]+\.html"
    if not re.fullmatch(pattern, parsed.path):
        raise ValueError("仅支持京东商品编号对应的 .html 详情页。")
    # Tracking queries are unnecessary for a read and can contain unrelated actions.
    return urlunparse(("https", parsed.hostname, parsed.path, "", "", ""))


def public_url(url: str) -> str:
    """Publish page identity, never redirect/session query strings or fragments."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} and not url.startswith("//"):
            return url
        query = ""
        if risk_url(url):
            return urlunparse((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], "/privatedomain/risk_handler/", "", "", ""))
        if parsed.hostname in {"search.jd.com", "search.360buy.com"} and parsed.path.lower() == "/search":
            query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                               if key in {"keyword", "q"}])
        # Remove userinfo too, even though navigation rejects it. Product IDs
        # remain in the path; login, verification and asset URLs lose all query.
        return urlunparse((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", query, ""))
    except ValueError:
        return ""


def public_result(value: Any) -> Any:
    """One recursive boundary covers normal, nested and blocked tool results."""
    if isinstance(value, dict):
        return {key: public_result(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_result(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("http://", "https://", "//")):
        return public_url(value)
    return value


def risk_url(url):
    parsed=urlparse(url)
    return parsed.hostname=='cfe.m.jd.com' and bool(re.match(r'/privatedomain/risk_handler(?:/|$)',parsed.path))


class BridgeError(RuntimeError):
    """Intentionally exclude upstream message/URL/token from exception text."""


class SessionBoundaryError(BridgeError):
    """A task page cannot be selected with verified identity."""


class WebBridgeClient:
    def __init__(self, *, transport=None):
        self.http = httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False, transport=transport)

    async def command(self, action: str, args: dict[str, Any] | None = None) -> Any:
        if action == 'cdp':
            if not isinstance(args, dict) or set(args) != {'method','params'} or args['method'] != 'Page.bringToFront' or args['params'] != {}:
                raise BridgeError('Only fixed owned-page fronting is allowed')
        elif action not in {"list_tabs", "find_tab", "navigate", "snapshot", "evaluate", "close_session"}:
            raise BridgeError("Unsupported bridge action")
        response = await self.http.post(BRIDGE_ENDPOINT, json={"action": action, "args": args or {}, "session": BRIDGE_SESSION})
        response.raise_for_status()
        envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get("ok") is not True or "data" not in envelope:
            raise BridgeError("WebBridge command failed")
        data = envelope["data"]
        if isinstance(data, dict) and data.get("success") is False:
            raise BridgeError("WebBridge action failed")
        return data

    async def aclose(self):
        await self.http.aclose()


def decoded_data(value: Any) -> Any:
    if isinstance(value, dict) and "result" in value:
        value = value["result"]
    elif isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise BridgeError("Invalid bridge data") from None
    return value


def requires_login(url: str, text: str = "") -> bool:
    parsed = urlparse(url)
    return (parsed.hostname == "passport.jd.com" or (parsed.hostname or "").startswith("plogin.")
            or "/login" in parsed.path.lower()
            or ("请登录" in text and not any(marker in text for marker in ("商品详情", "搜索结果"))))


def decoded_text(value, limit=None):
    text = str(value or "")
    for _ in range(2):
        if not re.search(r"%[0-9a-f]{2}", text, re.I):
            break
        try:
            following = unquote(text, errors="strict")
        except UnicodeDecodeError:
            break
        if following == text:
            break
        text = following
    return compact_text(text, limit)


def display_price_text(value):
    return re.sub(r"\s+", "", decoded_text(value))


def actual_image_url(value):
    image = decoded_text(value)
    if image.startswith("//"):
        image = "https:" + image
    parsed = urlparse(image)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not (parsed.hostname == "360buyimg.com" or parsed.hostname.endswith(".360buyimg.com")) or re.search(r"\.(?:html?|php|aspx?)(?:$|/)", parsed.path, re.I):
        return ""
    return public_url(image)


def detail_fields(reason: str, url: str) -> dict[str, Any]:
    result = {"product_url": url, "product_parameters": [], "good_reviews": [], "bad_reviews": []}
    for field, target in (("product_parameters", 1), ("good_reviews", 5), ("bad_reviews", 2)):
        result[field + "_status"] = {"field": field, "required": True, "count": 0, "target_count": target,
                                    "complete": False, "reason": reason}
    return result


class JDBridgeAdapter(JDCommerce):
    """Use only session-created tabs in the user's already-running Brave."""
    def __init__(self, bridge: WebBridgeClient, *, data_dir=None):
        self.bridge = bridge
        self._lock = asyncio.Lock()
        self.risk_store = RiskStore(data_dir or os.environ.get("MCP_DATA_DIR", os.environ.get("JD_MCP_DATA_DIR", str(ROOT / "runtime"))))
        self.__risk = {"risk_control": True} if self.risk_store.load() else None
        self.watchlist = Watchlist("jd", ensure_product_url, self.risk_store.path.parent)
        self.write_journal = WriteJournal(self.risk_store.path.parent)
        self._decode = decoded_data
        self._product_url = ensure_product_url
        self.notifications = Notifications(self.watchlist, self.product)
        self._clock = time.monotonic
        self._last_business_at: float | None = None

    @property
    def _risk(self):
        return self.__risk

    @_risk.setter
    def _risk(self, signals):
        self.risk_store.save(bool(signals))
        self.__risk = signals

    def identity(self):
        return {"platform": "jd", "network": "existing-browser", "backend": "kimi-webbridge",
                "session": BRIDGE_SESSION, "profile_path": None, "browser_profile": "existing-daily-Brave",
                "login_verified": False, "read_only": False, "capabilities": {"favorite_item": "implemented_unverified_live", "unfavorite_item": "implemented_unverified_live", "add_to_cart": "implemented_unverified_live", "remove_from_cart": "implemented_unverified_live", "cart_list":"implemented_unverified_live", "merchant_messages":"implemented_unverified_live", "contact_merchant":"implemented_unverified_live"}}

    async def owned_tabs(self):
        data = decoded_data(await self.bridge.command("list_tabs"))
        tabs = data.get("tabs", []) if isinstance(data, dict) else data
        if not isinstance(tabs, list):
            raise BridgeError("Invalid session tab list")
        return [tab for tab in tabs if isinstance(tab, dict) and not tab.get("borrowed", False)
                and tab.get("session", BRIDGE_SESSION) == BRIDGE_SESSION and isinstance(tab.get("url"), str)]

    async def select_owned(self, tabs):
        if len(tabs) != 1:
            raise SessionBoundaryError("Session page count is ambiguous")
        tab = tabs[0]
        if not isinstance(tab.get("tabId"), int) or isinstance(tab["tabId"], bool):
            raise SessionBoundaryError("Session page identity is missing")
        ensure_jd_url(tab["url"])
        try:
            selected = decoded_data(await self.bridge.command("find_tab", {"url": tab["url"]}))
        except Exception:
            raise SessionBoundaryError("Session page selection failed") from None
        if (not isinstance(selected, dict) or selected.get("success") is not True or selected.get("borrowed")
                or selected.get("session", BRIDGE_SESSION) != BRIDGE_SESSION
                or selected.get("tabId") != tab["tabId"] or isinstance(selected.get("tabId"), bool)):
            raise SessionBoundaryError("Session page selection was not verified")

    async def inspect_page(self, *, business=False):
        meta = decoded_data(await self.bridge.command("evaluate", {"code": META_CODE}))
        if not isinstance(meta, dict) or not isinstance(meta.get("url"), str):
            raise BridgeError("Invalid page metadata")
        ensure_jd_url(meta["url"])
        risk = bool(meta.get("risk_control")) or risk_url(meta["url"])
        login = bool(meta.get("requires_user_login"))
        # Never snapshot a login/verification page while the user may be typing
        # OTP/password. Semantic snapshots are only for ordinary business pages.
        if business and not risk and not login:
            snapshot = await self.bridge.command("snapshot")
            snapshot_text = snapshot if isinstance(snapshot, str) else json.dumps(snapshot, ensure_ascii=False)
            risk = bool(RISK_RE.search(snapshot_text))
        result = {"current_url": meta["url"], "title": compact_text(meta.get("title"), 300),
                  "requires_user_verification": risk, "risk_control": risk,
                  "requires_user_login": login, "likely_logged_in": bool(meta.get("likely_logged_in")) and not login,
                  "browser_running": True, "page_visible": bool(meta.get("page_visible"))}
        if risk:
            self._risk = result
        return result

    def blocked(self, signals, *, product_url=None):
        risk = bool(signals.get("risk_control"))
        result = {**signals, **self.identity(), "success": False,
                  "status": "risk_control" if risk else "login_required",
                  "message": "页面提示访问频繁、系统繁忙或安全验证；已停止查询，不自动重试。" if risk else "请在日常Brave京东任务页由本人完成登录。"}
        if product_url is None:
            result.update(items=[], count=0)
        else:
            result.update(detail_fields("risk_control" if risk else "login_required", product_url))
        return public_result(result)

    async def navigate(self, url, *, business=False, reuse_product_page=False):
        ensure_jd_url(url)
        tabs = await self.owned_tabs()
        if tabs:
            await self.select_owned(tabs)
            if business:
                before = await self.inspect_page()
                if before["risk_control"] or before["requires_user_login"]:
                    return before  # Preserve a page where the user is still authenticating.
                if reuse_product_page:
                    try:
                        same_product = ensure_product_url(before["current_url"]) == ensure_product_url(url)
                    except ValueError:
                        same_product = False
                    if same_product:
                        return {**before, "reused_current_page": True}
        nav = decoded_data(await self.bridge.command("navigate", {"url": url, "newTab": not bool(tabs), "group_title": GROUP_TITLE}))
        if isinstance(nav, dict) and isinstance(nav.get("url"), str):
            ensure_jd_url(nav["url"])
        signals = await self.inspect_page(business=business and not reuse_product_page)
        return {**signals, "reused_current_page": False} if reuse_product_page else signals

    async def login(self):
        async with self._lock:
            tabs = await self.owned_tabs()
            if tabs:
                await self.select_owned(tabs)
                signals = await self.inspect_page()
            else:
                signals = await self.navigate(JD_LOGIN_URL)
            if signals["risk_control"]:
                return self.blocked(signals)
            return public_result({**signals, **self.identity(), "success": True,
                                  "message": "已保留日常Brave的京东任务页；若仍要求认证，请本人完成后手动调用status_jd。"})

    async def status(self):
        async with self._lock:
            tabs = await self.owned_tabs()
            if not tabs:
                return {**self.identity(), "success": True, "browser_running": False, "likely_logged_in": False,
                        "requires_user_login": True, "requires_user_verification": bool(self._risk), "risk_control": bool(self._risk), "risk_lock_active": bool(self._risk)}
            await self.select_owned(tabs)
            signals = await self.inspect_page()
            if not signals["risk_control"] and not signals["requires_user_login"] and signals["likely_logged_in"]:
                self._risk = None  # A manual status check after user recovery releases the stop.
            return public_result({**signals, **self.identity(), "risk_control": bool(self._risk), "risk_lock_active": bool(self._risk), "success": True,
                                  "status": "risk_control" if self._risk else "page_signals",
                                  "message": "仅页面登录迹象，未读取Cookie；真实读取能力仍需对应查询验收。"})

    def reserve_business(self):
        now = self._clock()
        if self._last_business_at is not None:
            retry = math.ceil(30 - (now - self._last_business_at))
            if retry > 0:
                return {**self.identity(), "success": False, "status": "cooldown", "retry_after_s": retry,
                        "message": "请等待冷却时间后再手动查询；本服务不会自动等待、刷新或重试。"}
        self._last_business_at = now
        return None

    async def search(self, keyword: str, max_results: int = 5, min_price=None, max_price=None, sort="default",page=1):
        if not isinstance(keyword, str):
            raise ValueError("关键词必须是文本")
        keyword = keyword.strip()
        if not keyword or len(keyword) > 200:
            raise ValueError("关键词须为1到200字")
        if sort not in {"default", "price_asc", "price_desc"}:
            raise ValueError("无效排序")
        if any(price is not None and (not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price < 0) for price in (min_price, max_price)):
            raise ValueError("无效价格")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise ValueError("价格区间无效")
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise ValueError("max_results必须是整数")
        if not isinstance(page,int) or isinstance(page,bool) or page<1:
            raise ValueError('page须为大于等于1的整数')
        limit = max(1, min(20, max_results))
        async with self._lock:
            if self._risk:
                return self.blocked(self._risk)
            cooldown = self.reserve_business()
            if cooldown:
                return cooldown
            signals = await self.navigate("https://search.jd.com/Search?keyword=" + quote_plus(keyword), business=True)
            if signals["risk_control"] or signals["requires_user_login"]:
                return self.blocked(signals)
            if page!=1:
                pagination=await self._search_page(page)
                if not pagination.get('success'):return public_result({**self.identity(),**pagination,'page':page})
            code = "(() => JSON.stringify((" + SEARCH_SCRIPT + ")(" + str(40) + ")))()"
            raw = decoded_data(await self.bridge.command("evaluate", {"code": code}))
            if not isinstance(raw, list):
                raise BridgeError("Invalid search extraction")
            after = await self.inspect_page(business=True)
            if after["risk_control"] or after["requires_user_login"]:
                return self.blocked(after)
            items, seen = [], set()
            for row in raw:
                if not isinstance(row, dict):
                    continue
                try:
                    url = ensure_product_url(normalize_url(signals["current_url"], row.get("url")))
                except ValueError:
                    continue
                title = decoded_text(row.get("title"), 300)
                price = parse_price(display_price_text(row.get("price_text")))
                if not title or url in seen or ((min_price is not None or max_price is not None) and price is None):
                    continue
                if price is not None and ((min_price is not None and price < min_price) or (max_price is not None and price > max_price)):
                    continue
                seen.add(url)
                items.append({"platform": "jd", "title": title, "url": url, "product_url": url, "price": price,
                              "price_text": display_price_text(row.get("price_text"))[:80], "shop": decoded_text(row.get("shop"), 120),
                              "image": actual_image_url(row.get("image")),
                              "image_status": {"complete": bool(actual_image_url(row.get("image"))),
                                               "reason": "ok" if actual_image_url(row.get("image")) else "no_absolute_image_source"}})
            if sort != "default":
                items.sort(key=lambda item: (item["price"] is None, -(item["price"] or 0) if sort == "price_desc" else (item["price"] or 0)))
            items = items[:limit]
            out = public_result({**after, **self.identity(), "success": bool(items), "status": "ok" if items else "no_products_extracted",
                                  "keyword": keyword, "search_url": after["current_url"], "count": len(items), "items": items,
                                  "page":page,"page_scope":"platform_ui",
                                  "sort_scope": "page_local" if sort != "default" else "platform_default",
                                  "filters": {"min_price": min_price, "max_price": max_price, "sort": sort, "include_details": False},
                                  "message": "已读取少量商品。" if items else "未提取到可核对商品，可能无结果或页面结构变化；不会自动重试。"})
            try:
                self.watchlist.observe(out)
            except (OSError, sqlite3.Error):
                out["local_quote_saved"] = False
            return out

    async def _search_page(self,page):
        deadline=time.monotonic()+10
        while True:
            try:state=await self._within(self._commerce_eval(search_page_script(page)),deadline)
            except TimeoutError:return self._fail('page_not_ready')
            if not state.get('success'):return state
            if state.get('active_page') and state.get('ids'):break
            if time.monotonic()>=deadline:return self._fail('page_not_ready')
            await asyncio.sleep(min(0.25,deadline-time.monotonic()))
        if state.get('active_page')==page:return state
        before=set(state['ids'])
        clicked=await self._commerce_eval(search_page_script(page,click=True))
        if not clicked.get('success'):return clicked
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:state=await self._within(self._commerce_eval(search_page_script(page)),deadline)
            except TimeoutError:break
            if not state.get('success'):return state
            if state.get('active_page')==page and state.get('ids') and set(state['ids'])!=before:return state
            await asyncio.sleep(min(0.25,max(0,deadline-time.monotonic())))
        return self._fail('page_unverified',message='已单次点页码，页码与商品结果集尚未同时确认变化；没有再次点击。')

    async def product(self, url: str):
        url = ensure_product_url(url)
        async with self._lock:
            if self._risk:
                return self.blocked(self._risk, product_url=url)
            cooldown = self.reserve_business()
            if cooldown:
                return cooldown
            signals = await self.navigate(url, business=True, reuse_product_page=True)
            if signals["risk_control"] or signals["requires_user_login"]:
                return self.blocked(signals, product_url=url)
            ready_code = "(async () => await (" + READINESS_SCRIPT + ")(() => JSON.parse(" + META_CODE + ")))()"
            readiness = decoded_data(await self.bridge.command("evaluate", {"code": ready_code}))
            if not isinstance(readiness, dict):
                raise BridgeError("Invalid product readiness state")
            if readiness.get("risk_control") or readiness.get("requires_user_login"):
                signals.update(risk_control=bool(readiness.get("risk_control")),
                               requires_user_verification=bool(readiness.get("risk_control")),
                               requires_user_login=bool(readiness.get("requires_user_login")))
                if signals["risk_control"]:
                    self._risk = signals
                return self.blocked(signals, product_url=url)
            if not readiness.get("ready"):
                return public_result({**signals, **self.identity(), **detail_fields("page_not_ready", url),
                                      "success": False, "status": "page_not_ready",
                                      "message": "当前商品DOM在10秒内未就绪；未刷新、未重试官网请求。"})
            code = "(() => JSON.stringify((" + DETAIL_SCRIPT + ")(" + json.dumps(DETAIL_SELECTORS) + ")))()"
            raw = decoded_data(await self.bridge.command("evaluate", {"code": code}))
            if not isinstance(raw, dict):
                raise BridgeError("Invalid product extraction")
            after = await self.inspect_page(business=True)
            if after["risk_control"] or after["requires_user_login"] or RISK_RE.search(str(raw.get("body_text", ""))):
                if RISK_RE.search(str(raw.get("body_text", ""))):
                    after.update(risk_control=True, requires_user_verification=True)
                    self._risk = after
                return self.blocked(after, product_url=url)
            if ensure_product_url(after["current_url"]) != url:
                raise BridgeError("Product page changed")
            title = decoded_text(raw.get("title") or raw.get("meta", {}).get("og_title"), 500)
            result = {**after, **self.identity(), **detail_fields("not_visible_or_not_extracted", url),
                      "success": bool(title), "status": "ok" if title else "no_product_content", "title": title,
                      "reused_current_page": bool(signals.get("reused_current_page")),
                      "url": after["current_url"], "price": parse_price(display_price_text(raw.get("price_text"))),
                      "price_text": display_price_text(raw.get("price_text"))[:120], "shop": decoded_text(raw.get("shop"), 200)}
            images = list(dict.fromkeys(actual_image_url(image) for image in raw.get("images", []) if actual_image_url(image)))[:15]
            result.update(images=images, images_status={"complete": bool(images),
                                                       "reason": "ok" if images else "no_absolute_image_source"})
            for field, source, limit in (("product_parameters", "detail_product_parameters", 80), ("good_reviews", "high_praise_reviews", 5), ("bad_reviews", "high_dissatisfied_reviews", 2)):
                values = raw.get(source) or (raw.get("product_parameters") if field == "product_parameters" else []) or []
                allowed = ("name", "value", "group") if field == "product_parameters" else ("content", "time", "variant", "helpful_count", "sentiment")
                values = [{key:row[key] for key in allowed if key in row} for row in values if isinstance(row, dict)][:limit]
                result[field] = values
                status = result[field + "_status"]
                status.update(count=len(values), complete=bool(values) if field == "product_parameters" else len(values) >= limit,
                              reason="ok" if len(values) >= (1 if field == "product_parameters" else limit) else "not_visible_or_insufficient")
            try:
                self.watchlist.observe(result)
            except (OSError, sqlite3.Error):
                result["local_quote_saved"] = False
            return public_result(result)

    async def favorite_list(self,page=1):
        if not isinstance(page,int) or isinstance(page,bool) or page<1:
            raise ValueError('page须为大于等于1的整数')
        return await self._native_list('favorites',page)

    async def conversation_list(self):
        return await self._native_list('conversations')

    async def _native_list(self,kind,page=1):
        async with self._lock:
            blocked=self._commerce_guard()
            if blocked:return blocked
            url='https://t.jd.com/home/follow' if kind=='favorites' else 'https://jdcs.jd.com/index.action'
            signals=await self.navigate(url,business=True)
            if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
            script=favorite_list_script(page) if kind=='favorites' else conversation_list_script()
            raw=await self._ready_list(script)
            if not raw.get('success'):return public_result({**self.identity(),**raw})
            if kind=='favorites' and page!=1:
                candidates={p['url'] for p in raw.get('pages',[]) if p.get('page')==page and isinstance(p.get('url'),str)}
                if len(candidates)!=1:return public_result(self._fail('unsupported',page=page,message='当前关注页未提供可核对的该页链接；不猜翻页URL。'))
                signals=await self.navigate(candidates.pop(),business=True)
                if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
                raw=await self._ready_list(script)
            raw.pop('pages',None)
            return public_result({**self.identity(),**raw})

    async def _ready_list(self,script):
        deadline=time.monotonic()+10
        raw={'success':False,'error':'page_not_ready','items':[],'count':0}
        while True:
            try:raw=await self._within(self._commerce_eval(script),deadline)
            except TimeoutError:return raw
            if raw.get('items') or not raw.get('success') or time.monotonic()>=deadline:return raw
            await asyncio.sleep(min(0.25,deadline-time.monotonic()))

    async def write(self, action: str, url: str, sku: str | None = None, qty: int = 1):
        url = ensure_product_url(url)
        if action not in {"favorite", "unfavorite", "add_to_cart", "remove_from_cart"}:
            raise ValueError("不支持该操作")
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= 3:
            raise ValueError("qty须为1至3的整数")
        if sku is not None and (not isinstance(sku, str) or len(sku) > 100 or not sku.isascii() or not sku.isdigit()):
            raise ValueError("sku须为至多100位数字")
        async with self._lock:
            if self._risk:
                return self.blocked(self._risk, product_url=url)
            if action in {"add_to_cart", "remove_from_cart"}:
                return {**self.identity(), "success": False, "status": "unsupported", "fields": {"action":"unsupported"}, "product_url": url, "message": "尚无法可靠核实购物车中同商品、规格与数量的最终状态；本工具未执行任何点击。"}
            cooldown = self.reserve_business()
            if cooldown:
                return cooldown
            signals = await self.navigate(url, business=True, reuse_product_page=True)
            if signals["risk_control"] or signals["requires_user_login"]:
                return self.blocked(signals, product_url=url)
            product_id = re.search(r"([0-9]+)\.html$", url)[1]
            raw = decoded_data(await self.bridge.command("evaluate", {"code":favorite_script("jd", product_id, action == "favorite")}))
            if not isinstance(raw, dict):
                raise BridgeError("Invalid favorite response")
            if raw.get("risk_control"):
                self._risk = {"risk_control":True}
            safe = {key:raw[key] for key in ("success","error","risk_control","requires_user_login","already_in_target_state","audit","fields","message") if key in raw}
            return public_result({**self.identity(), **safe, "product_url":url, "action":action})

    async def close(self):
        async with self._lock:
            await self.bridge.command("close_session")
            return {**self.identity(), "success": True, "browser_running": False,
                    "message": "仅关闭京东MCP本session任务页，日常Brave继续运行。"}


async def public_tool_call(operation: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Stop exception text before the SDK can render/log a private URL."""
    try:
        return result(public_result(await operation))
    except Exception as exc:
        return {"success": False, "platform": "jd", "network": "existing-browser", "login_verified": False,
                "ok": False, "status": "error", "error_code": "invalid_input" if isinstance(exc, ValueError) else ("session_boundary" if isinstance(exc, SessionBoundaryError) else "tool_error"), "fields": {}, "data": {},
                "message": "无法确认唯一京东任务页，已停止操作并保留现有页面。" if isinstance(exc, SessionBoundaryError)
                           else "工具调用未完成。请检查输入，或在可见浏览器中本人完成登录/验证。",
                "exception_type": type(exc).__name__}


def build_mcp(adapter: JDBridgeAdapter) -> FastMCP:
    server = FastMCP(
        "JD-Local-Commerce",
        instructions="京东少量查询、可验证的收藏/购物车及明确对象的商家交流。登录/验证码由本人在可见窗口完成。禁止购买、下单、支付或改账户。小量查询，遇风控停止。status_jd 的登录迹象不代表验证成功。",
        host=HTTP_HOST, port=HTTP_PORT, streamable_http_path="/mcp", stateless_http=True,
        json_response=True, log_level="WARNING",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=[f"{HTTP_HOST}:{HTTP_PORT}", f"localhost:{HTTP_PORT}"],
            allowed_origins=[f"http://{HTTP_HOST}:{HTTP_PORT}", f"http://localhost:{HTTP_PORT}"],
        ),
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def login_jd() -> dict[str, Any]:
        """在日常Brave的京东MCP标签组打开京东登录页；本人扫码或输入认证信息。"""
        return await public_tool_call(adapter.login())

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def status_jd() -> dict[str, Any]:
        """被动报告京东浏览器和登录迹象；不启动浏览器、不返回 Cookie 值、login_verified 恒为 false。"""
        return await public_tool_call(adapter.status())

    @server.tool(annotations=read)
    async def search_jd(keyword: str, max_results: int = 5, min_price: float | None = None,
                        max_price: float | None = None, sort: str = "default",page: int = 1) -> dict[str, Any]:
        """搜索京东商品（每次最多返回20件）；page只能点当前实际可见的页码并核结果变化。本页本地筛价/排序；sort: default/price_asc/price_desc。"""
        return await public_tool_call(adapter.search(keyword, max_results, min_price, max_price, sort,page))

    @server.tool(annotations=read)
    async def get_jd_product(url: str) -> dict[str, Any]:
        """读取京东数字编号 .html 商品页，返回参数/最多5条好评及2条差评；缺失字段附原因。拒绝其他平台与账户/交易页面。"""
        return await public_tool_call(adapter.product(url))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def close_jd_browser() -> dict[str, Any]:
        """仅显式关闭本session创建的任务页，日常Brave与登录环境继续保留。"""
        return await public_tool_call(adapter.close())

    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @server.tool(annotations=write)
    async def favorite_jd_item(url: str) -> dict[str, Any]:
        """单次关注指定商品；只有唯一控件及明确状态可核实时点击。"""
        return await public_tool_call(adapter.write("favorite", url))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
    async def unfavorite_jd_item(url: str) -> dict[str, Any]:
        """对称取消指定商品关注；已取消返回成功，不重复点击。"""
        return await public_tool_call(adapter.write("unfavorite", url))

    @server.tool(annotations=read)
    async def favorite_list(page: int = 1) -> dict[str, Any]:
        """读取京东官方已关注商品页的当前渲染行；页码仅沿实际可见分页链接，缺字段标missing，不宣称全量。"""
        return await public_tool_call(adapter.favorite_list(page))

    @server.tool(annotations=read)
    async def conversation_list() -> dict[str, Any]:
        """读取官方客服会话概览（对象、时间、短摘要、可取得的商品定位）；不发消息、不导出全文。"""
        return await public_tool_call(adapter.conversation_list())

    @server.tool(annotations=read)
    async def cart_list() -> dict[str, Any]:
        """读取京东官方购物车的商品编号、规格、数量及单价；不结算。"""
        return await public_tool_call(adapter.cart_list())

    @server.tool(annotations=write)
    async def add_to_cart(url: str, sku_text: str | None = None, qty: int = 1, operation_id: str | None = None) -> dict[str, Any]:
        """精确商品与已选规格新增1至3件；operation_id持久去重，未知时同ID仅只读核对，不再次点击；省略时同参数沿用默认ID。"""
        return await public_tool_call(adapter.add_to_cart(url, sku_text, qty, operation_id))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
    async def remove_from_cart(item_id: str, sku_text: str | None = None) -> dict[str, Any]:
        """按数字商品编号及精确规格删除唯一购物车行；多行或对象不明时拒绝。"""
        return await public_tool_call(adapter.remove_from_cart(item_id, sku_text))

    @server.tool(annotations=read)
    async def merchant_messages(url: str) -> dict[str, Any]:
        """读取指定SKU的官方客服会话最近消息；官方PID/商品卡/唯一活动对象不一致时拒绝。"""
        return await public_tool_call(adapter.merchant_messages(url))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
    async def contact_merchant(url: str, text: str) -> dict[str, Any]:
        """明确商品URL及1至500字正文；确认官方PID/商品卡/唯一活动对象后单次发送，读回才成功；结果未知不重试。"""
        return await public_tool_call(adapter.contact_merchant(url, text))

    register_watchlist(server, adapter.watchlist)
    if hasattr(adapter, "notifications"):
        register_notifications(server, adapter.notifications)
    return server


async def run_server(server: FastMCP, adapter: JDBridgeAdapter, *, stdio: bool) -> None:
    try:
        adapter.notifications.start()
        if stdio:
            await server.run_stdio_async()
        else:
            import uvicorn
            await uvicorn.Server(uvicorn.Config(protect_http(server.streamable_http_app(), host=HTTP_HOST, port=HTTP_PORT), host=HTTP_HOST, port=HTTP_PORT,
                                               log_level="warning")).serve()
    finally:
        # Release only loopback HTTP resources; never auto-close daily browser tabs.
        await adapter.notifications.close()
        await adapter.bridge.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent local JD commerce MCP (HTTP 127.0.0.1:8842/mcp)")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--stdio", action="store_true", help="stdio protocol, for local clients or offline checks")
    transport.add_argument("--http", action="store_true", help="Streamable HTTP (default)")
    args = parser.parse_args()
    os.umask(0o077)
    adapter = JDBridgeAdapter(WebBridgeClient())
    asyncio.run(run_server(build_mcp(adapter), adapter, stdio=args.stdio))


if __name__ == "__main__":
    main()

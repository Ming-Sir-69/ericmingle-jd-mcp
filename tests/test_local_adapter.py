"""Local-only contract checks: no credentials or live shopping access required."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "src" / "server.py"


def load_adapter(test: unittest.TestCase):
    test.assertTrue(SERVER.is_file(), "missing JD-only local adapter entrypoint")
    spec = importlib.util.spec_from_file_location("jd_local_adapter", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class URLBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_adapter(self)

    def test_rejects_cross_platform_and_disguised_domains(self):
        for url in (
            "https://item.taobao.com/item.htm?id=1", "https://detail.tmall.com/item.htm?id=1",
            "https://item.jd.com.evil.example/1.html", "https://eviljd.com/1.html",
            "https://jd.com@evil.example/", "https://evil.example@item.jd.com/1.html",
            "https://item.jd.com\\@evil.example/1.html", "https://item.jd.com:7897/1.html",
            "file:///tmp/jd.com", "javascript:alert('jd.com')", "https://127.0.0.1/?jd.com",
            "https://jd.com/%0a\n", "https://ｊｄ.com/", "https://jd.com%2eevil.example/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.module.ensure_jd_url(url)

    def test_allows_real_jd_and_360buy_hosts(self):
        for url in ("https://item.jd.com/123.html", "https://passport.jd.com/new/login.aspx",
                    "https://jd.com/", "https://item.360buy.com/123.html", "https://360buy.com/"):
            self.assertEqual(self.module.ensure_jd_url(url), url)

    def test_product_tool_rejects_account_and_transaction_pages(self):
        for url in ("https://cart.jd.com/", "https://trade.jd.com/shopping/order/getOrderInfo.action",
                    "https://passport.jd.com/new/login.aspx", "https://www.jd.com/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.module.ensure_product_url(url)

    def test_public_urls_remove_private_parameters_and_keep_public_search_keyword(self):
        examples = {
            "https://passport.jd.com/new/login.aspx?token=PRIVATE_LOGIN_TOKEN&q=PRIVATE_LOGIN_TOKEN#PRIVATE_LOGIN_TOKEN":
                "https://passport.jd.com/new/login.aspx",
            "https://risk.jd.com/security/verify?token=PRIVATE_LOGIN_TOKEN#PRIVATE_LOGIN_TOKEN":
                "https://risk.jd.com/security/verify",
            "https://item.jd.com/123.html?token=PRIVATE_LOGIN_TOKEN#PRIVATE_LOGIN_TOKEN":
                "https://item.jd.com/123.html",
            "https://search.jd.com/Search?keyword=headphones&q=public&token=PRIVATE_LOGIN_TOKEN#PRIVATE_LOGIN_TOKEN":
                "https://search.jd.com/Search?keyword=headphones&q=public",
        }
        for original, expected in examples.items():
            with self.subTest(original=original):
                self.assertTrue(hasattr(self.module, "public_url"), "missing public URL boundary")
                self.assertEqual(self.module.public_url(original), expected)

class FakeBridge:
    def __init__(self):
        self.calls = []
        self.tabs = []
        self.url = "https://www.jd.com/"
        self.text = "京东首页 你好 我的京东"
        self.result = []
        self.login_overlay = False
        self.visible = True
        self.find_response = None

    async def command(self, action, args=None):
        self.calls.append((action, args or {}))
        if action == "list_tabs":
            return {"tabs": self.tabs}
        if action == "navigate":
            self.url = args["url"]
            self.tabs = [{"tabId": 123, "url": self.url, "borrowed": False}]
            return {"tabId": 123, "url": self.url}
        if action == "find_tab":
            if isinstance(self.find_response, Exception):
                raise self.find_response
            if self.find_response is not None:
                return self.find_response
            return {"success": True, "tabId": 123, "url": args["url"], "borrowed": False}
        if action == "cdp":
            self.visible = True
            return {"success": True}
        if action == "snapshot":
            return {"url": self.url, "title": "京东", "text": self.text}
        if action == "evaluate":
            if "JD_READY" in args["code"]:
                return {"type": "string", "value": json.dumps({"ready": True})}
            if "JD_META" in args["code"]:
                risk = any(marker in self.text for marker in ("访问频繁", "系统繁忙", "安全验证"))
                return {"type": "string", "value": json.dumps({"url": self.url, "title": "京东", "risk_control": risk, "page_visible": self.visible,
                    "requires_user_login": "passport.jd.com" in self.url or self.login_overlay, "likely_logged_in": "我的京东" in self.text})}
            return {"type": "string", "value": json.dumps(self.result)}
        if action == "close_session":
            self.tabs = []
            return {"closed": 1}
        raise AssertionError("unexpected bridge action")

    async def aclose(self):
        self.transport_closed = True


class BridgeBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = load_adapter(self)
        self.assertTrue(hasattr(self.module, "JDBridgeAdapter"), "missing existing-browser backend")
        self.bridge = FakeBridge()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.adapter = self.module.JDBridgeAdapter(self.bridge, data_dir=directory.name)

    async def test_login_uses_fixed_group_and_owned_session_page(self):
        result = await self.adapter.login()
        self.assertEqual(result["network"], "existing-browser")
        self.assertFalse(result["login_verified"])
        nav = [args for action, args in self.bridge.calls if action == "navigate"]
        self.assertEqual(nav, [{"url": "https://passport.jd.com/new/login.aspx", "newTab": True, "group_title": "京东MCP"}])
        await self.adapter.login()
        self.assertEqual([args["newTab"] for action, args in self.bridge.calls if action == "navigate"], [True])
        self.assertTrue(all(action not in {"cdp", "network", "click", "fill"} for action, _ in self.bridge.calls))

    async def test_login_preserves_any_existing_page_and_ongoing_login(self):
        for overlay in (False, True):
            self.bridge.calls.clear()
            self.bridge.url = "https://item.jd.com/123.html"
            self.bridge.tabs = [{"tabId": 123, "url": self.bridge.url}]
            self.bridge.login_overlay = overlay
            result = await self.adapter.login()
            self.assertEqual(result["current_url"], "https://item.jd.com/123.html")
            self.assertFalse(any(action in {"navigate", "snapshot"} for action, _ in self.bridge.calls))

    async def test_ambiguous_or_mismatched_tab_selection_is_session_boundary(self):
        self.bridge.tabs = [{"tabId": 123, "url": "https://www.jd.com/"}]
        for response in ([{"success": True, "tabId": 123}], {"tabId": 123},
                         {"success": True, "tabId": 999}, {"success": True, "tabId": 123, "borrowed": True},
                         {"success": False, "tabId": 123}, self.module.BridgeError("PRIVATE_TOKEN")):
            self.bridge.find_response = response
            self.bridge.calls.clear()
            result = await self.module.public_tool_call(self.adapter.status())
            self.assertEqual(result.get("error_code"), "session_boundary")
            self.assertFalse(any(action in {"navigate", "snapshot", "evaluate"} for action, _ in self.bridge.calls))
        self.bridge.find_response = None
        self.bridge.tabs.append({"tabId": 999, "url": "https://www.jd.com/"})
        self.bridge.calls.clear()
        result = await self.module.public_tool_call(self.adapter.login())
        self.assertEqual(result.get("error_code"), "session_boundary")
        self.assertEqual(self.bridge.calls, [("list_tabs", {})])

    async def test_same_url_login_overlay_stops_snapshot_and_product_extraction(self):
        self.bridge.url = "https://item.jd.com/123.html"
        self.bridge.tabs = [{"tabId": 123, "url": self.bridge.url}]
        self.bridge.login_overlay = True
        result = await self.adapter.product(self.bridge.url)
        self.assertEqual(result.get("status"), "login_required")
        self.assertFalse(any(action in {"navigate", "snapshot"} for action, _ in self.bridge.calls))
        self.assertFalse(any(action == "evaluate" and "JD_META" not in args["code"] for action, args in self.bridge.calls))

    async def test_search_detail_share_30_second_cooldown_without_automatic_commands(self):
        clock = [100.0]
        self.adapter._clock = lambda: clock[0]
        self.bridge.result = [{"title": "商品123", "url": "https://item.jd.com/123.html", "price_text": "￥12.50"}]
        first = await self.adapter.search("耳机", max_results=1)
        self.assertTrue(first["success"])
        clock[0] = 119.5
        self.bridge.result = {"title": "商品123", "body_text": "商品详情"}
        count = len(self.bridge.calls)
        blocked = await self.adapter.product("https://item.jd.com/123.html")
        self.assertEqual(blocked.get("status"), "cooldown")
        self.assertEqual(blocked.get("retry_after_s"), 11)
        self.assertEqual(len(self.bridge.calls), count)
        await self.adapter.status()
        await self.adapter.login()
        self.assertFalse(any(action == "navigate" for action, _ in self.bridge.calls[count:]))
        clock[0] = 130.0
        detail = await self.adapter.product("https://item.jd.com/123.html")
        self.assertTrue(detail["success"])
        count = len(self.bridge.calls)
        repeated = await self.adapter.search("耳机", max_results=1)
        self.assertEqual(repeated.get("status"), "cooldown")
        self.assertEqual(repeated.get("retry_after_s"), 30)
        self.assertEqual(len(self.bridge.calls), count)

    async def test_status_is_passive_and_does_not_create_tab(self):
        result = await self.adapter.status()
        self.assertFalse(result["browser_running"])
        self.assertEqual(self.bridge.calls, [("list_tabs", {})])

    async def test_status_only_selects_owned_jd_tab_and_redacts_token(self):
        self.bridge.url = "https://passport.jd.com/new/login.aspx?token=PRIVATE_LOGIN_TOKEN#PRIVATE_LOGIN_TOKEN"
        self.bridge.tabs = [{"tabId": 123, "url": self.bridge.url, "borrowed": False}, {"url": "https://private.example/", "borrowed": True}]
        result = await self.adapter.status()
        self.assertNotIn("PRIVATE_LOGIN_TOKEN", json.dumps(result))
        self.assertEqual(result["current_url"], "https://passport.jd.com/new/login.aspx")
        selects = [args for action, args in self.bridge.calls if action == "find_tab"]
        self.assertEqual(selects, [{"url": self.bridge.url}])
        self.assertFalse(any(action in {"navigate", "snapshot"} for action, _ in self.bridge.calls))

    async def test_risk_control_stops_extraction_and_further_business_requests(self):
        for text in ("由于访问频繁导致无法搜索", "系统繁忙，请稍后再试", "安全验证 请拖动滑块"):
            self.bridge.calls.clear()
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            self.adapter = self.module.JDBridgeAdapter(self.bridge, data_dir=directory.name)
            self.bridge.text = text
            first = await self.adapter.search("耳机", max_results=2)
            count = len(self.bridge.calls)
            second = await self.adapter.product("https://item.jd.com/123.html")
            self.assertEqual(first["status"], "risk_control")
            self.assertEqual(second["status"], "risk_control")
            self.assertFalse(first["success"])
            self.assertEqual(len(self.bridge.calls), count)
            self.assertFalse(any(action == "evaluate" and "JD_META" not in args["code"] for action, args in self.bridge.calls))

    async def test_cross_platform_product_is_rejected_without_bridge_call(self):
        with self.assertRaises(ValueError):
            await self.adapter.product("https://item.taobao.com/item.htm?id=1")
        self.assertEqual(self.bridge.calls, [])

    async def test_borrowed_tabs_are_never_selected(self):
        self.bridge.tabs = [{"url": "https://www.jd.com/", "borrowed": True},
                            {"url": "https://www.jd.com/", "session": "another-task"}]
        result = await self.adapter.status()
        self.assertFalse(result["browser_running"])
        self.assertFalse(any(action == "find_tab" for action, _ in self.bridge.calls))

    async def test_encoded_card_fields_and_relative_image_are_handled_honestly(self):
        self.bridge.result = [{"title": "%E6%A1%8C%E9%9D%A2%E6%94%AF%E6%9E%B6", "shop": "%E4%BA%AC%E4%B8%9C%E5%BA%97",
                              "url": "https://item.jd.com/100066041106.html", "price_text": "¥\n22\n.\n4", "image": "jfs%2Ftest.jpg"}]
        result = await self.adapter.search("支架", max_results=1)
        item = result["items"][0]
        self.assertEqual(item["title"], "桌面支架")
        self.assertEqual(item["shop"], "京东店")
        self.assertEqual(item["price"], 22.4)
        self.assertEqual(item["image"], "")
        self.assertFalse(item["image_status"]["complete"])

    async def test_detail_decodes_names_and_split_price_without_inventing_image_origin(self):
        self.bridge.result = {"title": "%E6%A1%8C%E9%9D%A2%E6%94%AF%E6%9E%B6", "shop": "%E4%BA%AC%E4%B8%9C%E5%BA%97",
                              "price_text": "¥\n22\n.\n4", "body_text": "商品详情", "images": ["jfs%2Ftest.jpg"]}
        result = await self.adapter.product("https://item.jd.com/100066041106.html")
        self.assertEqual(result["title"], "桌面支架")
        self.assertEqual(result["shop"], "京东店")
        self.assertEqual(result["price"], 22.4)
        self.assertEqual(result["images"], [])
        self.assertFalse(result["images_status"]["complete"])

    async def test_search_is_small_and_returns_sanitized_jd_items(self):
        self.bridge.result = [{"title": "耳机", "url": "https://item.jd.com/123.html?token=PRIVATE_LOGIN_TOKEN",
                              "price_text": "￥12.50", "image": "https://img10.360buyimg.com/a.jpg?token=PRIVATE_LOGIN_TOKEN"},
                              {"title": "另一个商品", "url": "https://item.jd.com/124.html", "price_text": "￥13.50"}]
        result = await self.adapter.search("耳机", max_results=50)
        self.assertEqual(result["items"][0]["price"], 12.50)
        self.assertNotIn("PRIVATE_LOGIN_TOKEN", json.dumps(result))
        self.assertEqual(result["count"], 2)
        self.assertFalse(result["filters"]["include_details"])

    async def test_service_exit_only_releases_http_and_keeps_session_tabs(self):
        class EndedStdio:
            async def run_stdio_async(self):
                pass
        await self.module.run_server(EndedStdio(), self.adapter, stdio=True)
        self.assertEqual(self.bridge.calls, [])
        self.assertTrue(self.bridge.transport_closed)

    async def test_same_product_url_reuses_current_dom_without_any_navigation(self):
        self.bridge.url = "https://item.jd.com/100066041106.html?tracking=ignored"
        self.bridge.tabs = [{"tabId": 123, "url": self.bridge.url}]
        self.bridge.result = {"title": "手机支架", "price_text": "¥38", "body_text": "商品详情"}
        result = await self.adapter.product("https://item.jd.com/100066041106.html#fragment")
        self.assertTrue(result["success"])
        self.assertTrue(result.get("reused_current_page"))
        self.assertFalse(any(action == "navigate" for action, _ in self.bridge.calls))
        self.assertTrue(any(action == "evaluate" and "JD_READY" in args["code"] for action, args in self.bridge.calls))

    async def test_product_returns_limited_structured_fields_and_no_page_text(self):
        self.bridge.result = {"title": "商品123", "price_text": "￥12.50", "body_text": "商品详情",
                              "product_parameters": [{"name": "品牌", "value": "测试"}],
                              "high_praise_reviews": [{"content": "好用"}] * 9,
                              "high_dissatisfied_reviews": [{"content": "不足"}] * 5}
        result = await self.adapter.product("https://item.jd.com/123.html")
        self.assertTrue(result["success"])
        self.assertEqual(len(result["good_reviews"]), 5)
        self.assertEqual(len(result["bad_reviews"]), 2)
        self.assertEqual(result["product_parameters"][0]["value"], "测试")
        self.assertNotIn("body_text", result)
        self.assertNotIn("cookie_names", result)

    async def test_close_is_only_explicit_session_close(self):
        await self.adapter.close()
        self.assertEqual(self.bridge.calls, [("close_session", {})])


class JDCardScriptTests(unittest.TestCase):
    def run_script(self, script, mode, visible=True):
        import shutil
        import subprocess
        module = load_adapter(self)
        harness = r"""
const fs=require('fs'); const input=JSON.parse(fs.readFileSync(0,'utf8'));
const node=(text,attrs={})=>({innerText:text,textContent:text,getAttribute:k=>attrs[k]||'',querySelector:()=>null,querySelectorAll:()=>[]});
const image={currentSrc:'https://img12.360buyimg.com/n7/jfs/test.jpg',src:'https://search.jd.com/jfs%2Fwrong.jpg',
 naturalWidth:300,naturalHeight:300,getAttribute:k=>k==='data-lazy-img'?'jfs%2Fwrong.jpg':''};
const link=node(input.visible?'可见桌面支架':'',{});
link.href='https://chat.jd.com/?pid=100066041106&wname=%25E6%25A1%258C%25E9%259D%25A2%25E6%2594%25AF%25E6%259E%25B6&seller=%25E4%25BA%25AC%25E4%25B8%259C%25E5%25BA%2597&imgUrl=jfs%252Fwrong.jpg';
const prices=['¥\n38','¥\n22\n.\n4','¥\n17\n.\n8','¥\n36\n.\n1'];
const cards=prices.map((price,index)=>({className:'_wrapper_1y1ag_3 plugin_goodsCardWrapper',
 getAttribute:k=>k==='data-sku'?String(100066041106+index):'',
 querySelector(selector){
  if(selector.includes('price')) return node(price);
  if(selector==='img'||selector.includes('img[')) return image;
  if(selector.includes('shop')) return node('%E4%BA%AC%E4%B8%9C%E5%BA%97');
  if(selector.includes('title')||selector.includes('name')) return input.visible?node('可见桌面支架'):null;
  if(selector.includes('a[')||selector.includes('.p-name a')) return link;
  return null;
 }}));
global.location={href:'https://item.jd.com/100066041106.html'};
global.document={body:{innerText:'商品详情'},querySelector(selector){
 if(input.mode==='detail') {
  if(selector.includes('price')) return node(prices[1]);
  if(selector.includes('shop')||selector==='.name a') return node('%E4%BA%AC%E4%B8%9C%E5%BA%97');
  if(selector==='.sku-name'||selector==='h1') return node('%E6%A1%8C%E9%9D%A2%E6%94%AF%E6%9E%B6');
 }
 return null;
},querySelectorAll(selector){
 if(input.mode==='detail') return selector.includes('#spec-img')?[image]:[];
 if(selector==='[data-sku]'||selector.includes('goodsCardWrapper')) return cards;
 if(selector.startsWith('a[href*="chat.jd.com"]')) return [link];
 return [];
}};
process.stdout.write(JSON.stringify(eval('('+input.script+')')(input.mode==='detail'?input.selectors:4)));
"""
        result = subprocess.run([shutil.which("node"), "-e", harness],
            input=json.dumps({"script": script, "mode": mode, "visible": visible, "selectors": module.DETAIL_SELECTORS}),
            text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_current_div_sku_cards_read_visible_split_prices_and_real_image(self):
        module = load_adapter(self)
        rows = self.run_script(module.SEARCH_SCRIPT, "search")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["title"], "可见桌面支架")
        self.assertEqual(rows[0]["shop"], "京东店")
        self.assertEqual([rows[i]["price_text"] for i in range(4)], ["¥38", "¥22.4", "¥17.8", "¥36.1"])
        self.assertEqual(rows[0]["image"], "https://img12.360buyimg.com/n7/jfs/test.jpg")

    def test_encoded_fallback_title_and_detail_fields_decode_before_output(self):
        module = load_adapter(self)
        rows = self.run_script(module.SEARCH_SCRIPT, "search", visible=False)
        self.assertEqual(rows[0]["title"], "桌面支架")
        detail = self.run_script(module.DETAIL_SCRIPT, "detail")
        self.assertEqual(detail["title"], "桌面支架")
        self.assertEqual(detail["shop"], "京东店")
        self.assertEqual(detail["price_text"], "¥22.4")
        self.assertEqual(detail["images"], ["https://img12.360buyimg.com/n7/jfs/test.jpg"])


class JDDetailReadinessTests(unittest.TestCase):
    def run_detail(self, mode, appears_after=0, never=False):
        import shutil
        import subprocess
        module = load_adapter(self)
        if mode == "ready":
            self.assertTrue(hasattr(module, "READINESS_SCRIPT"), "missing bounded DOM readiness script")
        harness = r"""
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
let now=0,waits=0; Date.now=()=>now; global.setTimeout=(callback,delay)=>{now+=delay;waits++;callback();};
const title='飞利浦2026新款手机支架 DLK2311B/93【行情 报价 价格 评测】-京东';
const node=(text,className='')=>({innerText:text,textContent:text,className,getAttribute:()=>'',querySelector:()=>null,querySelectorAll:()=>[]});
const skeletons=Array.from({length:5},()=>node('','continuous-skeleton-item--price'));
const real=node('¥38\n补贴价\n累计评价 2万+\n降价通知'.replaceAll('\n',String.fromCharCode(10)),'page-right-price');
const present=()=>!input.never && now>=input.appears_after;
global.document={title,body:{innerText:'商品详情'},querySelector(selector){
 if(selector==='h1') return node('最小单价计算器');
 if(selector.includes('price')) return skeletons[0];
 return null;
},querySelectorAll(selector){
 if(selector.includes('page-right-price')) return present()?[real]:[];
 if(selector.toLowerCase().includes('price')) return present()?[...skeletons,real]:skeletons;
 return [];
}};
(async()=>{
 const fn=eval('('+input.script+')');
 const result=input.mode==='ready'?JSON.parse(await fn(()=>({risk_control:false,requires_user_login:false}))):fn(input.selectors);
 process.stdout.write(JSON.stringify({result,now,waits}));
})().catch(e=>{process.stderr.write(e.message);process.exitCode=1;});
"""
        script = module.READINESS_SCRIPT if mode == "ready" else module.DETAIL_SCRIPT
        result = subprocess.run([shutil.which("node"), "-e", harness],
            input=json.dumps({"script": script, "mode": mode, "appears_after": appears_after, "never": never,
                              "selectors": module.DETAIL_SELECTORS}), text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_real_detail_title_and_price_skip_plugin_h1_and_empty_skeletons(self):
        result = self.run_detail("detail")["result"]
        self.assertEqual(result["title"], "飞利浦2026新款手机支架 DLK2311B/93")
        self.assertEqual(result["price_text"], "¥38")

    def test_readiness_waits_only_for_current_dom_and_stops_by_ten_seconds(self):
        ready = self.run_detail("ready", appears_after=600)
        self.assertTrue(ready["result"]["ready"])
        self.assertEqual(ready["now"], 600)
        timed_out = self.run_detail("ready", never=True)
        self.assertFalse(timed_out["result"]["ready"])
        self.assertTrue(timed_out["result"]["timed_out"])
        self.assertEqual(timed_out["now"], 10000)


class FixedStatusScriptTests(unittest.TestCase):
    def test_same_url_authentication_controls_are_detected_without_reading_values(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        self.assertIsNotNone(node, "local JS runtime is required for this script contract check")
        module = load_adapter(self)
        harness = r"""
const fs=require('fs'); const input=JSON.parse(fs.readFileSync(0,'utf8'));
global.location={href:'https://item.jd.com/123.html?token=PRIVATE_TOKEN#PRIVATE_TOKEN'};
const secret={getClientRects:()=>input.hidden?[]:[{width:20,height:20}]};
Object.defineProperty(secret,'value',{get(){throw Error('credential was read')}});
global.getComputedStyle=()=>({display:'block',visibility:input.hidden?'hidden':'visible',opacity:'1'});
global.document={title:'商品123', body:{innerText:'商品详情 我的京东 退出登录'},
querySelector(selector){
  if(input.kind==='password' && selector.includes('input[type="password"]')) return secret;
  if(input.kind==='otp' && selector.includes('one-time-code')) return secret;
  if(input.kind==='overlay' && selector.includes('login-dialog')) return secret;
  return null;
}, querySelectorAll(selector){ const el=this.querySelector(selector); return el?[el]:[]; }};
process.stdout.write(eval(input.code));
"""
        for kind, hidden in ((kind, hidden) for kind in ("password", "otp", "overlay") for hidden in (False, True)):
            result = subprocess.run([node, "-e", harness], input=json.dumps({"kind": kind, "hidden": hidden, "code": module.META_CODE}),
                                    text=True, capture_output=True, check=True)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["requires_user_login"], not hidden, (kind, hidden))
            self.assertEqual(payload["likely_logged_in"], hidden, (kind, hidden))
            self.assertNotIn("PRIVATE_TOKEN", result.stdout)
            self.assertNotIn("text", payload)


class BridgeEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_commands_use_fixed_session_and_real_envelope(self):
        module = load_adapter(self)
        self.assertTrue(hasattr(module, "WebBridgeClient"), "missing bridge transport")
        import httpx
        calls = []
        async def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "data": {"tabs": []}})
        client = module.WebBridgeClient(transport=httpx.MockTransport(handler))
        try:
            self.assertEqual(await client.command("list_tabs"), {"tabs": []})
            self.assertEqual(calls, [{"action": "list_tabs", "args": {}, "session": "commerce-mcp-jd"}])
            self.assertFalse(client.http._trust_env)
        finally:
            await client.aclose()

    async def test_inner_failed_action_is_not_treated_as_selected_tab(self):
        module = load_adapter(self)
        import httpx
        client = module.WebBridgeClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={"ok": True, "data": {"success": False, "message": "PRIVATE_TOKEN"}})))
        try:
            result = await module.public_tool_call(client.command("find_tab", {"url": "https://www.jd.com/"}))
            self.assertFalse(result["success"])
            self.assertEqual(result.get("exception_type"), "BridgeError")
            self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))
        finally:
            await client.aclose()

    async def test_failed_envelope_does_not_expose_private_bridge_message(self):
        module = load_adapter(self)
        self.assertTrue(hasattr(module, "WebBridgeClient"), "missing bridge transport")
        import httpx
        client = module.WebBridgeClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={"ok": False, "error": {"code": "ERROR", "message": "PRIVATE_TOKEN"}})))
        try:
            result = await module.public_tool_call(client.command("snapshot"))
            self.assertFalse(result["success"])
            self.assertEqual(result.get("exception_type"), "BridgeError")
            self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))
        finally:
            await client.aclose()


class ToolErrorPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_tool_errors_never_publish_private_exception_text(self):
        module = load_adapter(self)

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)

        class FailingAdapter:
            watchlist = module.Watchlist("jd", module.ensure_product_url, directory.name)
            async def fail(self, *args, **kwargs):
                raise RuntimeError("https://passport.jd.com/new/login.aspx?token=PRIVATE_TOKEN")

            login = status = search = product = close = fail

        server = module.build_mcp(FailingAdapter())
        cases = {
            "login_jd": {}, "status_jd": {}, "search_jd": {"keyword": "耳机"},
            "get_jd_product": {"url": "https://item.jd.com/123.html"}, "close_jd_browser": {},
        }
        for name, arguments in cases.items():
            with self.subTest(tool=name):
                try:
                    result = await server.call_tool(name, arguments)
                except Exception:
                    self.fail("private exception reached SDK ToolError instead of the public return boundary")
                serialized = json.dumps(result, default=lambda block: block.model_dump())
                self.assertNotIn("PRIVATE_TOKEN", serialized)
                self.assertIn("RuntimeError", serialized)
                self.assertIn('"success": false', serialized.replace('\\"', '"'))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_exposes_platform_tools_without_platform_switch_or_launch(self):
        load_adapter(self)
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        command = ROOT / "bin/jd-mcp"
        self.assertTrue(command.is_file(), "missing local executable launcher")
        async with stdio_client(StdioServerParameters(command=str(command), args=["--stdio"])) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                self.assertEqual({tool.name for tool in result.tools}, {
                    "login_jd", "status_jd", "search_jd", "get_jd_product", "close_jd_browser", "favorite_jd_item", "unfavorite_jd_item", "watchlist_upsert", "watchlist_list", "watchlist_check", "watchlist_remove", "cart_list", "add_to_cart", "remove_from_cart", "merchant_messages", "contact_merchant", "notification_configure", "notification_status", "notify_owner", "favorite_list", "conversation_list"})
                for tool in result.tools:
                    self.assertNotIn("platform", tool.inputSchema.get("properties", {}))

                blocked = await session.call_tool("get_jd_product", {"url": "https://item.taobao.com/item.htm?id=1"})
                self.assertFalse(blocked.isError)  # Handled rejection, no private SDK ToolError text.
                self.assertFalse(blocked.structuredContent["success"])
                self.assertEqual(blocked.structuredContent["exception_type"], "ValueError")


if __name__ == "__main__":
    unittest.main()

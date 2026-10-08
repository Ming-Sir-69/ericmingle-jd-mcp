import tempfile
import unittest
from test_local_adapter import load_adapter, FakeBridge

class ShoppingTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_limit_accepts_twenty_and_reports_page_local_sort(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge()
            bridge.result=[{'title':f'商品{i}','url':f'https://item.jd.com/{i}.html','price_text':str(i)} for i in range(1,26)]
            adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            result=await adapter.search('手机',20,sort='price_desc')
            self.assertEqual(result['count'],20)
            self.assertEqual(result['sort_scope'],'page_local')
            self.assertEqual(result['items'][0]['price'],25)
    async def test_risk_persists_close_and_requires_login(self):
        module = load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge = FakeBridge()
            adapter = module.JDBridgeAdapter(bridge, data_dir=folder)
            adapter._risk = {'risk_control':True}
            await adapter.close()
            adapter = module.JDBridgeAdapter(bridge, data_dir=folder)
            self.assertTrue(adapter._risk)
            bridge.tabs = [{'tabId':123,'url':'https://www.jd.com/'}]
            bridge.text = '京东首页'
            await adapter.status()
            self.assertTrue(adapter._risk)
            bridge.text = '退出 我的京东'
            await adapter.status()
            self.assertFalse(adapter._risk)

    async def test_price_filter_backfills_and_caps_five(self):
        module = load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge = FakeBridge()
            bridge.result = [{'title':'商品'+str(i),'url':f'https://item.jd.com/{i}.html','price_text':str(i)} for i in range(1,10)]
            adapter = module.JDBridgeAdapter(bridge, data_dir=folder)
            result = await module.public_tool_call(adapter.search('手机', 5, min_price=3))
            self.assertEqual(result['count'], 5)
            self.assertEqual([x['price'] for x in result['items']], [3,4,5,6,7])
            self.assertTrue(result['ok'])
            self.assertIn('data', result)

    async def test_write_rejects_checkout_without_browser(self):
        module = load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge = FakeBridge()
            adapter = module.JDBridgeAdapter(bridge, data_dir=folder)
            result = await module.public_tool_call(adapter.write('favorite','https://trade.jd.com/shopping/order/getOrderInfo.action'))
            self.assertEqual(result['error_code'],'invalid_input')
            self.assertEqual(bridge.calls,[])

    async def test_write_shares_cooldown_and_risk_without_bridge_calls(self):
        module = load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge = FakeBridge()
            adapter = module.JDBridgeAdapter(bridge,data_dir=folder)
            adapter._clock = lambda:1005
            adapter._last_business_at = 1000
            result=await module.public_tool_call(adapter.write('favorite','https://item.jd.com/123.html'))
            self.assertEqual(result['status'],'cooldown')
            self.assertEqual(bridge.calls,[])
            adapter._risk = {'risk_control':True}
            result=await module.public_tool_call(adapter.write('unfavorite','https://item.jd.com/123.html'))
            self.assertEqual(result['status'],'risk_control')
            self.assertEqual(bridge.calls,[])

    async def test_service_detail_feeds_local_observation_without_network(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge()
            bridge.result={'title':'文具','price_text':'¥9.9'}
            adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            url='https://item.jd.com/123.html'
            adapter.watchlist.upsert(url,'办公',10)
            await adapter.product(url)
            calls=len(bridge.calls)
            self.assertEqual(len(adapter.watchlist.check()['data']['matches']),1)
            self.assertEqual(len(bridge.calls),calls)

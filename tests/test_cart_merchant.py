import json
import tempfile
import unittest
from test_local_adapter import load_adapter, FakeBridge

class CommerceBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.rows=[]
        self.mutation_calls=0
        self.mutation_result={'success':True,'audit':{'changed':True,'verified':True}}
        self.apply=True
        self.options=['黑色']
    async def command(self, action, args=None):
        code=(args or {}).get('code','')
        if action=='evaluate' and 'JD_CART_LIST' in code:
            self.calls.append((action,args))
            return {'success':True,'items':list(self.rows),'complete':True}
        if action=='evaluate' and 'JD_CART_DETAIL' in code:
            self.calls.append((action,args))
            return {'success':True,'product_id':'123','sku_options':self.options,'selected_sku':'黑色','quantity':1}
        if action=='evaluate' and 'JD_CART_MUTATE' in code:
            self.calls.append((action,args)); self.mutation_calls+=1
            if self.apply:
                if '"action": "add"' in code:
                    self.rows=[{'product_id':'123','sku_text':'黑色','quantity':(self.rows[0]['quantity'] if self.rows else 0)+1,'item_id':'123'}]
                else:self.rows=[]
            return self.mutation_result
        return await super().command(action,args)

class CartMerchantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module=load_adapter(self)
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.bridge=CommerceBridge()
        self.bridge.tabs=[{'tabId':123,'url':self.bridge.url}]
        self.adapter=self.module.JDBridgeAdapter(self.bridge,data_dir=self.folder.name)
        self.adapter._clock=lambda:1000
        self.adapter._commerce_timeout=0.01
    async def test_cart_input_boundaries_zero_browser_calls(self):
        for qty in [0,4,True,1.2]:
            r=await self.module.public_tool_call(self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',qty))
            self.assertEqual(r['error_code'],'invalid_input')
        for item in ['123junk','https://trade.jd.com/checkout','']:
            r=await self.module.public_tool_call(self.adapter.remove_from_cart(item))
            self.assertEqual(r['error_code'],'invalid_input')
        self.assertEqual(self.bridge.calls,[])
    async def test_cart_existing_quantity_adds_increment_and_operation_deduplicates(self):
        self.bridge.rows=[{'product_id':'123','item_id':'123','sku_text':'黑色','quantity':2}]
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'test-op')
        self.assertTrue(r['success']);self.assertEqual(r['item']['quantity'],3)
        self.assertEqual(self.bridge.mutation_calls,1)
        self.adapter._last_business_at=None
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'test-op')
        self.assertTrue(r['already_in_target_state']);self.assertEqual(self.bridge.mutation_calls,1)
        r=await self.adapter.add_to_cart('https://item.jd.com/124.html','黑色',1,'test-op')
        self.assertEqual(r['error'],'operation_id_conflict')
    async def test_missing_sku_and_multiple_matches_never_mutate(self):
        self.bridge.options=['黑色','白色']
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html')
        self.assertEqual(r['error'],'needs_sku');self.assertEqual(self.bridge.mutation_calls,0)
        self.adapter._last_business_at=None
        self.bridge.rows=[{'product_id':'123','item_id':'123','sku_text':'黑色','quantity':1}]*2
        r=await self.adapter.remove_from_cart('123','黑色')
        self.assertEqual(r['error'],'ambiguous_item');self.assertEqual(self.bridge.mutation_calls,0)
    async def test_cart_add_remove_readback(self):
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色')
        self.assertTrue(r['success']);self.assertEqual(self.bridge.mutation_calls,1)
        self.adapter._last_business_at=None
        r=await self.adapter.remove_from_cart('123','黑色')
        self.assertTrue(r['success']);self.assertEqual(self.bridge.rows,[])
    async def test_unknown_add_persists_stop_across_restart(self):
        self.bridge.apply=False;self.bridge.mutation_result={'success':False,'error':'unverified','audit':{'changed':True}}
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色')
        self.assertEqual(r['error'],'unknown');self.assertEqual(self.bridge.mutation_calls,1)
        self.adapter=self.module.JDBridgeAdapter(self.bridge,data_dir=self.folder.name)
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色')
        self.assertEqual(r['error'],'unknown');self.assertEqual(self.bridge.mutation_calls,1)
    async def test_cart_risk_and_cooldown_share_existing_boundary(self):
        self.adapter._last_business_at=999
        r=await self.adapter.cart_list();self.assertEqual(r['status'],'cooldown');self.assertEqual(self.bridge.calls,[])
        self.adapter._risk={'risk_control':True}
        r=await self.adapter.remove_from_cart('123');self.assertTrue(r['risk_control']);self.assertEqual(self.bridge.calls,[])
    async def test_merchant_requires_explicit_object_and_body(self):
        for url,text in [('https://trade.jd.com/checkout','问库存'),('https://item.jd.com/123.html',''),('https://item.jd.com/123.html','x'*501)]:
            r=await self.module.public_tool_call(self.adapter.contact_merchant(url,text))
            self.assertEqual(r['error_code'],'invalid_input')
        self.assertEqual(self.bridge.calls,[])
    async def test_tools_expose_separate_cart_and_merchant_contracts(self):
        tools={t.name:t for t in await self.module.build_mcp(self.adapter).list_tools()}
        for name in ['cart_list','add_to_cart','remove_from_cart','merchant_messages','contact_merchant']:
            self.assertIn(name,tools)
        self.assertTrue(tools['contact_merchant'].annotations.destructiveHint)
        self.assertTrue(tools['contact_merchant'].annotations.openWorldHint)
    async def test_jd_numeric_sku_identity_matches_row_without_display_label(self):
        self.bridge.rows=[{'product_id':'123','item_id':'123','sku_id':'123','sku_text':None,'quantity':1}]
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色')
        self.assertTrue(r['success']);self.assertEqual(r['item']['quantity'],2);self.assertEqual(self.bridge.mutation_calls,1)
        self.adapter._last_business_at=None
        r=await self.adapter.remove_from_cart('123','白色')
        self.assertEqual(r['error'],'needs_sku');self.assertEqual(self.bridge.mutation_calls,1)
    async def test_real_empty_partial_cart_does_not_block_add_and_count_confirms(self):
        async def partial_state():
            return {'success':False,'error':'cart_incomplete','complete':False,'items':[],'total_count':66}
        self.adapter._cart_state=partial_state
        self.bridge.mutation_result={'success':True,'audit':{'changed':True,'verified':True,'cart_count_before':66,'cart_count_after':67}}
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html',None,1,'r2-jd-test-1')
        self.assertTrue(r['success']);self.assertEqual(self.bridge.mutation_calls,1)
    async def test_remove_missing_quantity_never_clicks(self):
        self.bridge.rows=[{'product_id':'123','sku_id':'123','sku_text':None,'quantity':None}]
        r=await self.adapter.remove_from_cart('123')
        self.assertEqual(r['error'],'quantity_unverified');self.assertEqual(self.bridge.mutation_calls,0)
    async def test_add_write_timeout_reads_back_once_and_never_reclicks(self):
        original=self.bridge.command
        async def timed_out(action,args=None):
            result=await original(action,args)
            if action=='evaluate' and 'JD_CART_MUTATE' in (args or {}).get('code',''):
                raise TimeoutError('write response lost')
            return result
        self.bridge.command=timed_out
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'timeout-test')
        self.assertTrue(r['success']);self.assertEqual(self.bridge.mutation_calls,1)
    async def test_unknown_cart_write_reports_operation_id_after_bounded_readback(self):
        self.bridge.apply=False
        self.bridge.mutation_result={'success':True,'audit':{'changed':True,'verified':False}}
        self.adapter._commerce_timeout=0.01
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'unknown-test')
        self.assertEqual(r['error'],'unknown');self.assertEqual(r['operation_id'],'unknown-test');self.assertEqual(self.bridge.mutation_calls,1)
    async def test_second_detail_navigation_waits_for_controls_without_click_retry(self):
        original=self.bridge.command;reads=0
        async def loading(action,args=None):
            nonlocal reads
            if action=='evaluate' and 'JD_CART_DETAIL' in (args or {}).get('code',''):
                reads+=1
                if reads==2:return {'success':False,'error':'page_not_ready'}
            return await original(action,args)
        self.bridge.command=loading
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'ready-test')
        self.assertTrue(r['success']);self.assertGreaterEqual(reads,3);self.assertEqual(self.bridge.mutation_calls,1)
    async def test_merchant_send_response_loss_recovers_from_new_message(self):
        messages=[];sends=0
        async def command(action,args=None):
            nonlocal sends
            code=(args or {}).get('code','')
            if action=='evaluate' and 'JD_MERCHANT_CONVERSATION' in code:
                import re
                cfg=json.loads(re.search(r'const cfg=(.*?);',code)[1])
                if cfg['body'] is not None:
                    sends+=1;messages.append({'text':cfg['body']});raise TimeoutError('response lost')
                return {'success':True,'object':{'product_id':'123','recipient':'商品店'},'send_available':True,'draft_present':False,'messages':list(messages)}
            return await CommerceBridge.command(self.bridge,action,args)
        self.bridge.command=command
        r=await self.adapter.contact_merchant('https://item.jd.com/123.html','问库存')
        self.assertTrue(r['success']);self.assertEqual(sends,1)
        self.adapter._last_business_at=None
        r=await self.adapter.contact_merchant('https://item.jd.com/123.html','问库存')
        self.assertTrue(r['already_in_target_state']);self.assertEqual(sends,1)
    async def test_lost_write_response_does_not_accept_an_old_success_toast(self):
        original=self.bridge.command
        async def command(action,args=None):
            code=(args or {}).get('code','')
            if action=='evaluate' and 'JD_CART_DETAIL' in code:
                r=await original(action,args);return {**r,'cart_count':66,'had_success_toast':True}
            if action=='evaluate' and 'JD_CART_LIST' in code:return {'success':True,'complete':False,'items':[]}
            if action=='evaluate' and 'JD_CART_MUTATE' in code:raise TimeoutError('not known whether sent')
            if action=='evaluate' and 'JD_CART_PROBE' in code:
                import re
                cfg=json.loads(re.search(r'const cfg=(.*?);',code)[1]);return {'success':True,'verified':not cfg['had_toast']}
            return await original(action,args)
        self.bridge.command=command
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'old-toast-test')
        self.assertEqual(r['error'],'unknown')
    async def test_cart_navigation_waits_for_initial_render_without_renavigation(self):
        original=self.bridge.command;reads=0
        async def command(action,args=None):
            nonlocal reads
            if action=='evaluate' and 'JD_CART_LIST' in (args or {}).get('code',''):
                reads+=1
                if reads==1:return {'success':False,'error':'cart_incomplete','items':[],'complete':False,'total_count':66}
            return await original(action,args)
        self.bridge.rows=[{'product_id':'123','sku_id':'123','quantity':1}]
        self.bridge.command=command
        r=await self.adapter.cart_list()
        self.assertTrue(r['success']);self.assertEqual(reads,2)
        self.assertEqual(len([c for c in self.bridge.calls if c[0]=='navigate']),1)
    async def test_unrendered_cart_times_out_as_partial_not_bridge_error(self):
        self.adapter._commerce_ready_timeout=0.01
        original=self.bridge.command
        async def command(action,args=None):
            if action=='evaluate' and 'JD_CART_LIST' in (args or {}).get('code',''):
                return {'success':False,'error':'cart_incomplete','items':[],'complete':False,'total_count':66}
            return await original(action,args)
        self.bridge.command=command
        r=await self.module.public_tool_call(self.adapter.cart_list())
        self.assertEqual(r['error_code'],'cart_incomplete')
    async def test_pending_remove_resumes_confirmation_without_initial_delete(self):
        self.bridge.rows=[{'product_id':'123','sku_id':'123','sku_text':None,'quantity':1}]
        self.adapter.write_journal.set('remove:123',True)
        self.bridge.url='https://cart.jd.com/cart_index';self.bridge.tabs=[{'tabId':123,'url':self.bridge.url}]
        original=self.bridge.command;confirms=0
        async def command(action,args=None):
            nonlocal confirms
            if action=='evaluate' and 'JD_CART_CONFIRM' in (args or {}).get('code',''):
                import re
                cfg=json.loads(re.search(r'const cfg=(.*?);',(args or {})['code'])[1])
                if not cfg['click']:return {'success':True,'confirmation_needed':True}
                confirms+=1;self.bridge.rows=[];return {'success':True,'confirmed':True}
            return await original(action,args)
        self.bridge.command=command
        r=await self.adapter.remove_from_cart('123')
        self.assertTrue(r['success']);self.assertEqual(confirms,1);self.assertEqual(self.bridge.mutation_calls,0)
        self.assertFalse(any(c[0]=='navigate' for c in self.bridge.calls))
    async def test_official_cart_redirect_confirms_exact_target_with_truthful_proof(self):
        original=self.bridge.command
        async def command(action,args=None):
            if action=='evaluate' and 'JD_CART_LIST' in (args or {}).get('code',''):
                return {'success':True,'items':list(self.bridge.rows),'complete':False}
            return await original(action,args)
        self.bridge.command=command
        self.bridge.mutation_result={'success':True,'redirected_to_cart':True,'audit':{'changed':True,'verified':False,'cart_count_before':66,'cart_count_after':66}}
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'redirect-test')
        self.assertTrue(r['success']);self.assertEqual(r['audit']['proof'],'cart_target_observed');self.assertEqual(self.bridge.mutation_calls,1)
    async def test_same_pending_operation_reconciles_counter_and_exact_target_without_click(self):
        import hashlib
        fingerprint=hashlib.sha256(json.dumps(['123','黑色',1],ensure_ascii=False).encode()).hexdigest()
        self.adapter.write_journal.record('add:reconcile-test',{'status':'pending','fingerprint':fingerprint,'before_quantity':None,'before_total_count':66,'requested_quantity':1})
        self.bridge.rows=[{'product_id':'123','sku_id':'123','sku_text':None,'quantity':1}]
        async def readback():return {'success':True,'complete':False,'items':list(self.bridge.rows),'total_count':67}
        self.adapter._cart_state=readback
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'reconcile-test')
        self.assertTrue(r['success']);self.assertEqual(r['audit']['proof'],'cart_total_and_target');self.assertTrue(r['audit']['reconciled'])
        self.assertEqual(self.bridge.mutation_calls,0)
    async def test_reconcile_target_alone_or_wrong_quantity_cannot_claim_success(self):
        import hashlib
        fingerprint=hashlib.sha256(json.dumps(['123','黑色',1],ensure_ascii=False).encode()).hexdigest()
        self.adapter.write_journal.record('add:reconcile-test',{'status':'pending','fingerprint':fingerprint,'before_quantity':None,'before_total_count':66,'requested_quantity':1})
        self.bridge.rows=[{'product_id':'123','sku_id':'123','sku_text':None,'quantity':1}]
        async def readback():return {'success':True,'complete':False,'items':list(self.bridge.rows),'total_count':66}
        self.adapter._cart_state=readback
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'reconcile-test')
        self.assertEqual(r['error'],'unknown');self.assertEqual(self.bridge.mutation_calls,0)
    async def test_reconcile_count_represents_rows_not_requested_unit_count(self):
        import hashlib
        fingerprint=hashlib.sha256(json.dumps(['123','黑色',2],ensure_ascii=False).encode()).hexdigest()
        self.adapter.write_journal.record('add:reconcile-two',{'status':'pending','fingerprint':fingerprint,'before_quantity':None,'before_total_count':66,'requested_quantity':2})
        async def readback():return {'success':True,'complete':False,'items':[{'product_id':'123','sku_id':'123','sku_text':None,'quantity':2}],'total_count':67}
        self.adapter._cart_state=readback
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',2,'reconcile-two')
        self.assertTrue(r['success']);self.assertEqual(r['audit']['proof'],'cart_total_and_target');self.assertEqual(self.bridge.mutation_calls,0)
    async def test_cart_unavailable_before_write_refuses_without_click(self):
        async def unavailable():return {'success':False,'error':'cart_unavailable','items':[],'complete':False}
        self.adapter._cart_state=unavailable
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'unavailable-before')
        self.assertEqual(r['error'],'cart_unavailable');self.assertEqual(self.bridge.mutation_calls,0)
    async def test_cart_unavailable_after_write_stops_readback_and_keeps_pending(self):
        reads=0
        async def states():
            nonlocal reads
            reads+=1
            if reads==1:return {'success':True,'complete':False,'items':[],'total_count':66}
            return {'success':False,'error':'cart_unavailable','items':[],'complete':False}
        self.adapter._cart_state=states
        self.bridge.mutation_result={'success':True,'audit':{'changed':True,'verified':False}}
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色',1,'unavailable-after')
        self.assertEqual(r['error'],'unknown');self.assertEqual(r['reason'],'cart_unavailable');self.assertEqual(reads,2)
        self.assertEqual(self.adapter.write_journal.get('add:unavailable-after')['status'],'pending')
        self.assertEqual(self.bridge.mutation_calls,1)
    async def test_cart_fronts_only_the_verified_owned_page_before_cart_navigation(self):
        self.bridge.url='https://www.jd.com/';self.bridge.tabs=[{'tabId':123,'url':self.bridge.url}]
        original=self.bridge.command
        async def command(action,args=None):
            if action=='cdp':self.bridge.calls.append((action,args));return {'success':True}
            return await original(action,args)
        self.bridge.command=command
        await self.adapter.cart_list()
        actions=[x[0] for x in self.bridge.calls]
        self.assertIn('cdp',actions);self.assertLess(actions.index('find_tab'),actions.index('cdp'));self.assertLess(actions.index('cdp'),actions.index('navigate'))
        self.assertEqual([x[1] for x in self.bridge.calls if x[0]=='cdp'],[{'method':'Page.bringToFront','params':{}}])
    async def test_evidenced_risk_route_persists_lock_and_prevents_front_or_navigation(self):
        self.bridge.url='https://cfe.m.jd.com/privatedomain/risk_handler/index.html?token=PRIVATE';self.bridge.tabs=[{'tabId':123,'url':self.bridge.url}]
        r=await self.adapter.status()
        self.assertTrue(r['risk_control']);self.assertTrue(self.adapter._risk)
        self.bridge.calls.clear()
        r=await self.adapter.cart_list();self.assertTrue(r['risk_control']);self.assertEqual(self.bridge.calls,[])
        fresh=self.module.JDBridgeAdapter(self.bridge,data_dir=self.folder.name);self.assertTrue(fresh._risk)
    async def test_front_refuses_ambiguous_owned_pages_and_product_never_fronts(self):
        self.bridge.tabs=[{'tabId':123,'url':'https://www.jd.com/'},{'tabId':124,'url':'https://www.jd.com/'}]
        r=await self.module.public_tool_call(self.adapter.cart_list());self.assertEqual(r['error_code'],'session_boundary')
        self.assertFalse(any(x[0]=='cdp' for x in self.bridge.calls))
        self.bridge.tabs=[];self.bridge.calls.clear();self.adapter._last_business_at=None
        self.bridge.result={'title':'商品','price_text':'10'}
        await self.adapter.product('https://item.jd.com/123.html')
        self.assertFalse(any(x[0]=='cdp' for x in self.bridge.calls))

class FixedCDPClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_allows_only_front_empty_params_without_target_override(self):
        module=load_adapter(self)
        import httpx
        requests=[]
        def respond(req):requests.append(json.loads(req.content));return httpx.Response(200,json={'ok':True,'data':{}})
        client=module.WebBridgeClient(transport=httpx.MockTransport(respond));self.addAsyncCleanup(client.aclose)
        await client.command('cdp',{'method':'Page.bringToFront','params':{}})
        for args in [{'method':'Runtime.evaluate','params':{}},{'method':'Page.bringToFront','params':{'targetId':'other'}},{'method':'Page.bringToFront','params':{},'targetId':'other'}]:
            with self.assertRaises(module.BridgeError):await client.command('cdp',args)
        self.assertEqual(len(requests),1);self.assertEqual(requests[0]['args'],{'method':'Page.bringToFront','params':{}})

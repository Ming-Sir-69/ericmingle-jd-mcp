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
        self.adapter=self.module.JDBridgeAdapter(self.bridge,data_dir=self.folder.name)
        self.adapter._clock=lambda:1000
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
        self.assertEqual(r['error'],'unverified');self.assertEqual(self.bridge.mutation_calls,1)
        self.adapter=self.module.JDBridgeAdapter(self.bridge,data_dir=self.folder.name)
        r=await self.adapter.add_to_cart('https://item.jd.com/123.html','黑色')
        self.assertEqual(r['error'],'previous_action_unverified');self.assertEqual(self.bridge.mutation_calls,1)
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

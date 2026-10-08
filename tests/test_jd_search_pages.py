import unittest
import tempfile
import json
import shutil
import subprocess
from test_local_adapter import load_adapter, FakeBridge


class JDSearchPageTests(unittest.IsolatedAsyncioTestCase):
    def test_observed_pager_dom_clicks_only_unique_exact_visible_page(self):
        load_adapter(self)
        from jd_search_dom import search_page_script
        runner=r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));let clicks=0;
const node=(value,cls)=>({innerText:value,textContent:value,className:cls,getClientRects:()=>[{}],getAttribute:k=>k==='data-sku'?'123':null,click:()=>{clicks++;}});
const first=node('1','_pagination_item_1gv36_29 _active_1gv36_'),second=node('2','_pagination_item_1gv36_29');
const document={body:{innerText:''},querySelectorAll:s=>s==='[data-sku]'?[node('','')]:s.includes('_pagination_item_')?(p.duplicate?[first,second,second]:[first,second]):[]};
(async()=>process.stdout.write(JSON.stringify({data:JSON.parse(await vm.runInNewContext(p.script,{document,location:{href:'https://search.jd.com/Search?keyword=支架'},URL,Set,getComputedStyle:()=>({display:'block',visibility:'visible'})})),clicks})))();
'''
        for page,duplicate,expected in ((2,False,1),(2,True,0),(99,False,0)):
            script=search_page_script(page,True)
            self.assertNotIn('setTimeout',script)
            process=subprocess.run([shutil.which('node'),'-e',runner],input=json.dumps({'script':script,'duplicate':duplicate}),text=True,capture_output=True,timeout=5)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(json.loads(process.stdout)['clicks'],expected)

    async def test_page_two_clicks_once_and_waits_for_changed_items(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge();original=bridge.command;clicks=0;reads=0
            async def command(action,args=None):
                nonlocal clicks,reads
                code=(args or {}).get('code','')
                if action=='evaluate' and 'JD_SEARCH_PAGE' in code:
                    if '"click": true' in code:
                        clicks+=1;return {'success':True,'changed':True,'active_page':1,'ids':['111']}
                    reads+=1
                    return {'success':True,'active_page':1 if clicks==0 else 2,'ids':['111'] if reads<3 else ['222']}
                if action=='evaluate' and 'goodsCardWrapper' in code:
                    return [{'title':'第二页商品','url':'https://item.jd.com/222.html','price_text':'¥20'}]
                return await original(action,args)
            bridge.command=command;adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            result=await adapter.search('支架',1,page=2)
            self.assertTrue(result['success']);self.assertEqual(result['page'],2)
            self.assertEqual(result['items'][0]['url'],'https://item.jd.com/222.html')
            self.assertEqual(clicks,1);self.assertGreaterEqual(reads,3)
            self.assertEqual(len([1 for action,_ in bridge.calls if action=='navigate']),1)

    async def test_nonvisible_page_is_unsupported_and_no_click_retry(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge();original=bridge.command
            async def command(action,args=None):
                code=(args or {}).get('code','')
                if action=='evaluate' and 'JD_SEARCH_PAGE' in code:
                    if '"click": true' in code:return {'success':False,'error':'unsupported','changed':False}
                    return {'success':True,'active_page':1,'ids':['111']}
                return await original(action,args)
            bridge.command=command;adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            result=await adapter.search('支架',1,page=99)
            self.assertEqual(result['error'],'unsupported')

    async def test_invalid_page_rejected_before_browser(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge();adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            for page in (0,True,1.5):
                result=await module.public_tool_call(adapter.search('支架',page=page))
                self.assertEqual(result['error_code'],'invalid_input')
            self.assertEqual(bridge.calls,[])

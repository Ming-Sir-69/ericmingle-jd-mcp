import json
import shutil
import subprocess
import tempfile
import unittest
from test_local_adapter import load_adapter, FakeBridge

LIST_RUNNER=r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));
const n=(text='',extra={})=>({innerText:text,textContent:text,getClientRects:()=>[{}],getAttribute:()=>null,querySelector:()=>null,...extra});
const link=n('桌面支架',{getAttribute:k=>k==='href'?'https://item.jd.com/123.html':null});
const bad=n('错误对象',{getAttribute:k=>k==='href'?'https://evil.example/999.html':null});
const row=n('桌面支架 ¥12.50',{querySelectorAll:s=>s==='a[href]'?[link,bad]:s==='.dialog-detail-header-name'?[n('商品店')]:s==='.dialog-detail-header-right-time'?[n('09:00')]:s==='.dialog-detail-content-msg'?[n('您好')]:[]});
const document={body:{innerText:''},querySelectorAll:s=>s==='div.item-inner'||s==='div.dialog'?[row]:[]};
(async()=>process.stdout.write(await vm.runInNewContext(p.script,{document,location:{href:p.url},URL,Set,getComputedStyle:()=>({display:'block',visibility:'visible'})})))();
'''


class JDListTests(unittest.IsolatedAsyncioTestCase):
    def test_observed_list_dom_reads_exact_id_price_and_message_locator(self):
        module=load_adapter(self)
        from jd_lists_dom import favorite_list_script, conversation_list_script
        for script,url in [(favorite_list_script(),'https://t.jd.com/home/follow'),(conversation_list_script(),'https://jdcs.jd.com/index.action')]:
            process=subprocess.run([shutil.which('node'),'-e',LIST_RUNNER],input=json.dumps({'script':script,'url':url}),text=True,capture_output=True,timeout=5)
            self.assertEqual(process.returncode,0,process.stderr)
            result=json.loads(process.stdout)
            self.assertEqual(result['count'],1)
            self.assertEqual(result['items'][0]['product_url'],'https://item.jd.com/123.html')
            if 'FAVORITE' in script:self.assertEqual(result['items'][0]['price'],12.5)
            else:self.assertEqual(result['items'][0]['recipient'],'商品店')
    async def test_favorite_list_reads_native_page_and_exposes_tool(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge()
            bridge.result={'success':True,'items':[{'item_id':'123','url':'https://item.jd.com/123.html','title':'已关注商品','price':12.5}], 'count':1}
            adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            result=await adapter.favorite_list()
            self.assertTrue(result['success']);self.assertEqual(result['items'][0]['item_id'],'123')
            self.assertEqual([a['url'] for op,a in bridge.calls if op=='navigate'],['https://t.jd.com/home/follow'])
            tools={t.name:t for t in await module.build_mcp(adapter).list_tools()}
            self.assertIn('favorite_list',tools);self.assertTrue(tools['favorite_list'].annotations.readOnlyHint)

    async def test_conversation_list_reads_official_page_and_preserves_locator(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge()
            bridge.result={'success':True,'items':[{'recipient':'商品店','product_url':'https://item.jd.com/123.html','last_message_time':'09:00'}], 'count':1}
            adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            result=await adapter.conversation_list()
            self.assertEqual(result['items'][0]['product_url'],'https://item.jd.com/123.html')
            self.assertEqual([a['url'] for op,a in bridge.calls if op=='navigate'],['https://jdcs.jd.com/index.action'])
            tools={t.name:t for t in await module.build_mcp(adapter).list_tools()}
            self.assertIn('conversation_list',tools)

    async def test_list_boundaries_stop_risk_and_invalid_page_without_navigation(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge();adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            for page in (0,True,1.5):
                result=await module.public_tool_call(adapter.favorite_list(page))
                self.assertEqual(result['error_code'],'invalid_input')
            adapter._risk={'risk_control':True}
            self.assertTrue((await adapter.conversation_list())['risk_control'])
            self.assertEqual(bridge.calls,[])
    async def test_initial_list_hydration_reads_same_page_without_renavigation(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge();original=bridge.command;reads=0
            async def command(action,args=None):
                nonlocal reads
                if action=='evaluate' and 'JD_FAVORITE_LIST' in (args or {}).get('code',''):
                    reads+=1
                    return {'success':True,'items':[],'count':0} if reads==1 else {'success':True,'items':[{'item_id':'123'}],'count':1}
                return await original(action,args)
            bridge.command=command;adapter=module.JDBridgeAdapter(bridge,data_dir=folder)
            result=await adapter.favorite_list()
            self.assertEqual(result['count'],1);self.assertEqual(reads,2)
            self.assertEqual(len([1 for action,_ in bridge.calls if action=='navigate']),1)

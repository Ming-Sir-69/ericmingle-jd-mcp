import json,shutil,subprocess,tempfile,unittest
from test_local_adapter import load_adapter,FakeBridge

RUNNER=r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));
const nick={tagName:'A',innerText:'PRIVATE_NICKNAME',textContent:'PRIVATE_NICKNAME',getClientRects:()=>[{width:10,height:10}],getAttribute:k=>k==='href'?'https://home.jd.com/':null};
const auth={getClientRects:()=>[{width:10,height:10}]};
const document={body:{innerText:p.login?'请登录':p.risk?'安全验证':'京东商品'},title:'京东商品',visibilityState:'visible',querySelectorAll(s){if(s.includes('input[type="password"]'))return p.auth?[auth]:[];if(s==='div.dt.cw-icon > a.nickname[href*="home.jd.com"]')return p.comment_only?[]:[nick];return[];}};
const output=JSON.parse(vm.runInNewContext(p.script,{document,location:{href:p.risk_url?'https://cfe.m.jd.com/privatedomain/risk_handler/?secret=PRIVATE':'https://item.jd.com/123.html'},URL,URLSearchParams,getComputedStyle:()=>({display:p.hidden?'none':'block',visibility:'visible',opacity:'1'})}));
process.stdout.write(JSON.stringify(output));
'''

class ModernLoginTests(unittest.IsolatedAsyncioTestCase):
    def run_case(self,**case):
        module=load_adapter(self)
        r=subprocess.run([shutil.which('node'),'-e',RUNNER],input=json.dumps({'script':module.META_CODE,**case}),capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn('PRIVATE_NICKNAME',r.stdout)
        return json.loads(r.stdout)
    def test_observed_modern_header_is_boolean_login_indicator(self):
        self.assertTrue(self.run_case()['likely_logged_in'])
        for case in [{'login':True},{'auth':True},{'risk':True},{'risk_url':True},{'hidden':True},{'comment_only':True}]:
            self.assertFalse(self.run_case(**case)['likely_logged_in'],case)
    async def test_manual_status_clears_persisted_lock_only_after_normal_logged_in_page(self):
        module=load_adapter(self)
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeBridge();bridge.url='https://item.jd.com/123.html';bridge.tabs=[{'tabId':123,'url':bridge.url}]
            adapter=module.JDBridgeAdapter(bridge,data_dir=folder);adapter._risk={'risk_control':True}
            original=bridge.command
            meta=self.run_case()
            async def command(action,args=None):
                if action=='evaluate' and 'JD_META' in (args or {}).get('code',''):return meta
                return await original(action,args)
            bridge.command=command
            r=await adapter.status();self.assertTrue(r['likely_logged_in']);self.assertFalse(adapter._risk)
            fresh=module.JDBridgeAdapter(bridge,data_dir=folder);self.assertFalse(fresh._risk)

import json
import shutil
import subprocess
import unittest
from pathlib import Path
import importlib.util

spec=importlib.util.spec_from_file_location('shopping_dom', Path(__file__).resolve().parents[1]/'src/shopping_dom.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
RUNNER=r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));
let clicks=0;
const location={href:p.url || (p.platform==='taobao'?'https://item.taobao.com/item.htm?id=123':'https://item.jd.com/123.html')};
const parent={className:p.parent_class || '',innerText:p.parent_text || '',getAttribute:k=>k==='href'?p.parent_href || '':null,parentElement:null,childNodes:[]};
const button={tagName:p.tag || 'A',className:p.control_class || '',innerText:p.active?(p.platform==='taobao'?'已收藏':'已关注'):(p.platform==='taobao'?'收藏商品':'关注'),id:p.control_id || 'safe-favorite',getAttribute:k=>k==='href'?p.button_href || null:k==='class'?p.control_class || null:null,getClientRects:()=>[{}],parentElement:parent,disabled:false,childNodes:[],
 click(){clicks++;if(p.checkout)location.href=p.platform==='taobao'?'https://buy.taobao.com/auction/order/confirm_order.htm':'https://trade.jd.com/shopping/order/getOrderInfo.action';else if(!p.unverified) this.innerText=p.desired?(p.platform==='taobao'?'已收藏':'已关注'):(p.platform==='taobao'?'收藏商品':'关注');}};
if(p.control_text) button.innerText=p.control_text;
if(p.unsafe) button.innerText='立即购买';
const document={body:{innerText:p.risk?'安全验证':''},querySelectorAll(selector){return selector.includes('input') || (p.control_selector && selector!==p.control_selector)?[]:(p.count===0?[]:p.count===2?[button,{...button}]:[button]);}};
const context={document,location,URL,Set,getComputedStyle:()=>({display:'block',visibility:'visible'}),setTimeout:fn=>{fn();}};
(async()=>{const data=JSON.parse(await vm.runInNewContext(p.script,context));process.stdout.write(JSON.stringify({data,clicks}));})();
'''

class FavoriteDOMTests(unittest.TestCase):
    def run_case(self, **case):
        platform='taobao' if '淘宝' in str(Path(__file__)) else 'jd'
        case.setdefault('desired',True)
        case.update(platform=platform,script=module.favorite_script(platform,'123',case['desired']))
        process=subprocess.run([shutil.which('node'),'-e',RUNNER],input=json.dumps(case),text=True,capture_output=True,timeout=5)
        self.assertEqual(process.returncode,0,process.stderr)
        return json.loads(process.stdout)

    def test_unambiguous_exact_product_favorite_and_already_state_no_repeat(self):
        self.assertEqual(self.run_case()['clicks'],1)
        result=self.run_case(active=True)
        self.assertEqual(result['clicks'],0)
        self.assertTrue(result['data']['already_in_target_state'])

    def test_wrong_domain_zero_multiple_controls_never_click(self):
        for case in [{'url':'https://evil.example/123.html'}, {'count':0},{'count':2},{'unsafe':True},{'button_href':'https://trade.jd.com/checkout'}]:
            self.assertEqual(self.run_case(**case)['clicks'],0)

    def test_ancestor_transaction_target_never_clicked(self):
        for case in [{'parent_href':'https://trade.jd.com/checkout/order.action'}, {'parent_text':'立即购买'}]:
            self.assertEqual(self.run_case(**case)['clicks'],0)

    def test_no_confirmation_has_single_click_and_unverified(self):
        result=self.run_case(unverified=True)
        self.assertEqual(result['clicks'],1)
        self.assertEqual(result['data']['error'],'unverified')

    def test_checkout_redirect_stops_with_explicit_code(self):
        result=self.run_case(checkout=True)
        self.assertEqual(result['clicks'],1)
        self.assertEqual(result['data']['error'],'checkout_page_reached')

    def test_unfavorite_exact_state_and_already_removed_are_idempotent(self):
        result=self.run_case(active=True,desired=False)
        self.assertEqual(result['clicks'],1)
        self.assertFalse(result['data']['audit']['after'])
        result=self.run_case(active=False,desired=False)
        self.assertEqual(result['clicks'],0)
        self.assertTrue(result['data']['already_in_target_state'])
        self.assertEqual(self.run_case(active=True,desired=False,count=2)['clicks'],0)

    def test_observed_current_ui_exact_control_and_parent_are_supported(self):
        platform='taobao' if '淘宝' in str(Path(__file__)) else 'jd'
        case={'tag':'DIV','control_id':'collectBtn','control_class':'RightButton--bW2fuIlt','parent_class':'RightButtonList--UDLr9knE','control_selector':'#collectBtn','control_text':'收藏'} if platform=='taobao' else {'tag':'SPAN','control_class':'follow-btn','parent_class':'sku-title','control_selector':'.sku-title > span.follow-btn','control_text':'收藏'}
        result=self.run_case(**case)
        self.assertEqual(result['clicks'],1)
        self.assertTrue(result['data']['success'])
        self.assertEqual(self.run_case(**dict(case,parent_class='site-nav-menu'))['clicks'],0)
        self.assertEqual(self.run_case(**dict(case,control_text='收藏夹'))['clicks'],0)

    def test_risk_never_clicked(self):
        self.assertEqual(self.run_case(risk=True)['clicks'],0)

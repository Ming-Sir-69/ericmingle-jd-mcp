import importlib.util,json,shutil,subprocess,unittest
from pathlib import Path

class CartDOMTests(unittest.TestCase):
    def run_case(self,**case):
        spec=importlib.util.spec_from_file_location('jd_cart_dom',Path(__file__).resolve().parents[1]/'src/jd_cart_dom.py')
        self.assertIsNotNone(spec)
        self.assertTrue(Path(spec.origin).exists(),'cart DOM guard is missing')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        p={'script':m.mutation_script('add','123','黑色',1),**case}
        runner=r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));let clicks=0;
const location={href:p.url||'https://item.jd.com/123.html'};
const parent={childNodes:p.unsafe?[{nodeType:3,textContent:'结算'}]:[],parentElement:null,getAttribute:()=>''};
const node=(text)=>({tagName:'DIV',innerText:text,textContent:text,getClientRects:()=>[{}],getAttribute:()=>'',parentElement:parent,childNodes:[],disabled:false});
const button=node('加入购物车');button.id='add-to-cart';button.click=()=>{clicks++;if(p.checkout)location.href='https://trade.jd.com/checkout';else if(p.redirect)location.href=p.redirect;};
const sku=node(p.bad_sku?'白色':'黑色');sku.className='specification-item-sku specification-item-sku--selected';
const qty={...node(''),tagName:'INPUT',id:'buy-num',value:p.bad_qty?'2':'1',dispatchEvent:()=>true};
const document={body:{innerText:p.risk?'安全验证':p.body||''},querySelectorAll(s){if(s==='#add-to-cart')return p.ambiguous?[button,button]:[button];if(s==='.specification-item-sku')return [sku];if(s==='.specification-item-sku--selected')return [sku];if(s==='#buy-num')return [qty];return [];}};
const context={document,location,URL,Set,Event:class {},getComputedStyle:()=>({display:'block',visibility:'visible'}),setTimeout:f=>f()};
(async()=>{let data=JSON.parse(await vm.runInNewContext(p.script,context));process.stdout.write(JSON.stringify({data,clicks}));})();
'''
        r=subprocess.run([shutil.which('node'),'-e',runner],input=json.dumps(p),capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)
    def test_exact_product_and_sku_single_click(self):
        r=self.run_case();self.assertEqual(r['clicks'],1);self.assertTrue(r['data']['audit']['changed'])
    def test_identity_sku_quantity_ambiguity_and_risk_zero_click(self):
        for case in [{'url':'https://evil.example/123.html'},{'url':'https://item.jd.com/124.html'},{'bad_sku':True},{'ambiguous':True},{'risk':True},{'unsafe':True}]:
            self.assertEqual(self.run_case(**case)['clicks'],0,case)
    def test_checkout_after_click_hard_stops(self):
        r=self.run_case(checkout=True);self.assertEqual(r['clicks'],1);self.assertEqual(r['data']['error'],'checkout_page_reached')
    def test_observed_cart_row_parses_sku_id_quantity_and_unique_delete(self):
        spec=importlib.util.spec_from_file_location('jd_cart_dom',Path(__file__).resolve().parents[1]/'src/jd_cart_dom.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        runner=r'''const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));
const node=(text,attrs={})=>({tagName:'DIV',innerText:text,textContent:text,className:'',getClientRects:()=>[{}],getAttribute:k=>attrs[k]||null,childNodes:[],parentElement:null});
let clicks=0,removed=false;const del={...node('删除'),tagName:'A',className:'_txt-btn_1jawj_14',click(){clicks++;removed=true;}};
const link={...node('支架',{'href':'https://item.jd.com/123.html'}),tagName:'A',className:'_goodTitle_1trrg_1'};
const qty={...node(''),tagName:'INPUT',type:'text',value:'1'};
const parent={...node(''),className:'_product-item_88ueb_1',getAttribute:k=>k==='data-skuuuid'?'abc':null};
const row={...node(''),className:'_row_88ueb_40 _good_88ueb_6',parentElement:parent,getAttribute:k=>k==='data-rowgid'?'123':null,
querySelectorAll(s){if(s.includes('_goodTitle_'))return[link];if(s==='input[type="text"]')return[];if(s==='input')return[qty];if(s.includes('_txt-btn_'))return[del];return[];}};
const document={body:{innerText:'购物车1'},querySelectorAll(s){if(s.includes('data-rowgid'))return removed?[]:[row];return[];}};
const context={document,location:{href:'https://cart.jd.com/cart_index'},URL,Set,getComputedStyle:()=>({display:'block',visibility:'visible'}),setTimeout:f=>f()};
(async()=>process.stdout.write(JSON.stringify({data:JSON.parse(await vm.runInNewContext(p.script,context)),clicks})))();'''
        r=subprocess.run([shutil.which('node'),'-e',runner],input=json.dumps({'script':m.cart_script()}),capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        out=json.loads(r.stdout)['data'];self.assertTrue(out['success']);self.assertEqual(out['items'][0]['sku_id'],'123');self.assertEqual(out['items'][0]['quantity'],1)
        r=subprocess.run([shutil.which('node'),'-e',runner],input=json.dumps({'script':m.mutation_script('remove','123',None,1)}),capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['clicks'],1)
    def test_requested_quantity_updates_unique_input_before_add(self):
        # A real input edit is reversible; only the subsequent guarded add click mutates cart.
        r=self.run_case(bad_qty=True)
        self.assertEqual(r['clicks'],1)
    def test_mutation_has_no_browser_sleep_poll(self):
        spec=importlib.util.spec_from_file_location('jd_cart_dom',Path(__file__).resolve().parents[1]/'src/jd_cart_dom.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        self.assertNotIn('setTimeout',m.mutation_script('add','123','黑色',1))
        self.assertNotIn('setTimeout',m.mutation_script('remove','123',None,1))
    def test_evidenced_jd_dialog_uses_delete_goods_not_move_to_favorites(self):
        spec=importlib.util.spec_from_file_location('jd_cart_dom',Path(__file__).resolve().parents[1]/'src/jd_cart_dom.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        runner=r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));let clicks=0;
const n=(text,cls='')=>({tagName:'DIV',innerText:text,textContent:text,className:cls,getClientRects:()=>[{}],getAttribute:()=>null,childNodes:[],parentElement:null});
const footer=n('','_dialogFooter_1c2j6_76');
const button={...n(p.move?'移入收藏':'删除商品','_dialogButton_1c2j6_83 _dialogNo_1c2j6_104'),tagName:'A',parentElement:footer,click(){clicks++;}};
const dialog={...n('','_dialog_1c2j6_1'),querySelectorAll:s=>p.multiple?[button,button]:[button]};
const link={...n('支架'),getAttribute:k=>k==='href'?'https://item.jd.com/123.html':null},qty={...n(''),type:'text',value:'1'};
const parent={...n('','_product-item_88ueb_1'),getAttribute:k=>k==='data-skuuuid'?'present':null};
const row={...n('','_row_88ueb_40 _good_88ueb_6'),parentElement:parent,querySelectorAll:s=>s.includes('_goodTitle_')?[link]:s==='input'?[qty]:[]};
const document={body:{innerText:'购物车1'},querySelectorAll(s){if(s==='div[data-rowgid]')return[row];if(s.includes('_dialog_'))return[dialog];return[];}};
(async()=>process.stdout.write(JSON.stringify({data:JSON.parse(await vm.runInNewContext(p.script,{document,location:{href:'https://cart.jd.com/cart_index'},URL,Set,getComputedStyle:()=>({display:'block',visibility:'visible'})})),clicks})))();
'''
        for case,expected in [({},1),({'move':True},0),({'multiple':True},0)]:
            r=subprocess.run([shutil.which('node'),'-e',runner],input=json.dumps({'script':m.confirm_delete_script('123',1),**case}),capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['clicks'],expected)
        r=subprocess.run([shutil.which('node'),'-e',runner],input=json.dumps({'script':m.confirm_delete_script('123',1,False)}),capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['clicks'],0);self.assertTrue(json.loads(r.stdout)['data']['confirmation_needed'])
    def test_official_cart_redirect_is_readback_pending_and_never_reclicks(self):
        r=self.run_case(redirect='https://cart.jd.com/cart_index')
        self.assertEqual(r['clicks'],1);self.assertTrue(r['data']['redirected_to_cart']);self.assertFalse(r['data']['audit']['verified'])
        r=self.run_case(redirect='https://passport.jd.com/new/login.aspx')
        self.assertEqual(r['clicks'],1);self.assertEqual(r['data']['error'],'page_changed')
    def test_observed_unavailable_cart_is_not_empty_or_incomplete(self):
        spec=importlib.util.spec_from_file_location('jd_cart_dom',Path(__file__).resolve().parents[1]/'src/jd_cart_dom.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        r=self.run_case(script=m.cart_script(),url='https://cart.jd.com/cart_index',body='购物车跑丢了 去购物刷新看看 购物车66 购物车(0)')
        self.assertEqual(r['data']['error'],'cart_unavailable');self.assertIsNone(r['data']['total_count']);self.assertEqual(r['clicks'],0)
    def test_evidenced_risk_url_stops_without_relying_on_challenge_text(self):
        r=self.run_case(url='https://cfe.m.jd.com/privatedomain/risk_handler/index.html?secret=PRIVATE')
        self.assertEqual(r['clicks'],0);self.assertEqual(r['data']['error'],'risk_control')
    def test_changed_page_diagnostic_has_host_path_without_query(self):
        r=self.run_case(url='https://item.jd.com/124.html?secret=PRIVATE')
        self.assertEqual(r['data']['current_page'],{'host':'item.jd.com','path':'/124.html'})
        self.assertNotIn('PRIVATE',json.dumps(r['data']))

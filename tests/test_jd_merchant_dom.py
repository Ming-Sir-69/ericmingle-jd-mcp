import importlib.util,json,shutil,subprocess,unittest,sys
from pathlib import Path
class MerchantDOMTests(unittest.TestCase):
 def run_case(self,**case):
  path=Path(__file__).resolve().parents[1]/'src/jd_merchant_dom.py'
  self.assertTrue(path.exists(),'merchant guarded sender missing')
  sys.path.insert(0,str(path.parent))
  spec=importlib.util.spec_from_file_location('jd_merchant_dom',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  runner=r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));let clicks=0,msgs=[];
const node=t=>({tagName:'DIV',innerText:t,textContent:t,getClientRects:()=>[{}],getAttribute:()=>null,childNodes:[],parentElement:null,disabled:false});
const name=node(p.wrong_shop?'其他店':'商品店'),dialog={...node(''),querySelectorAll:s=>s==='.dialog-detail-header-name'?[name]:[]};
const card={...node(''),tagName:'A',getAttribute:k=>k==='href'?'https://item.jd.com/'+(p.wrong_card?'124':'123')+'.html':null};
const editor={...node(''),tagName:'PRE',textContent:p.draft?'用户草稿':'',isContentEditable:true,dispatchEvent:()=>true};
const send={...node('发送'),click(){clicks++;if(!p.unverified)msgs.push(node(editor.textContent));}};
const document={body:{innerText:p.risk?'安全验证':''},querySelectorAll(s){if(s==='.dialog.active')return p.multiple?[dialog,dialog]:[dialog];if(s==='a[href]')return[card];if(s==='.message--content')return msgs;if(s==='pre.send-textarea[contenteditable]')return[editor];if(s==='.input-field--send-btn')return[send];return[];}};
const context={document,location:{href:p.url||'https://jdcs.jd.com/index.action?pid=123'},URL,Set,Event:class{},getComputedStyle:()=>({display:'block',visibility:'visible'}),setTimeout:f=>f()};
(async()=>process.stdout.write(JSON.stringify({data:JSON.parse(await vm.runInNewContext(p.script,context)),clicks})))();
'''
  r=subprocess.run([shutil.which('node'),'-e',runner],input=json.dumps({'script':m.merchant_script('123','问一下库存','商品店'),**case}),text=True,capture_output=True,timeout=5)
  self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)
 def test_verified_object_sends_once_and_reads_back(self):
  r=self.run_case();self.assertEqual(r['clicks'],1);self.assertTrue(r['data']['success']);self.assertTrue(r['data']['audit']['verified'])
 def test_wrong_product_recipient_ambiguous_session_and_draft_zero_send(self):
  for case in [{'wrong_card':True},{'wrong_shop':True},{'multiple':True},{'draft':True},{'url':'https://jdcs.jd.com/index.action?pid=124'},{'risk':True}]:
   self.assertEqual(self.run_case(**case)['clicks'],0,case)
 def test_unknown_receipt_single_send_and_no_retry(self):
  r=self.run_case(unverified=True);self.assertEqual(r['clicks'],1);self.assertEqual(r['data']['error'],'unverified')

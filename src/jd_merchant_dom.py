"""Guarded JD merchant conversation: official PID, active recipient, product card."""
from jd_cart_dom import wrap

def merchant_script(product_id,body=None,recipient=None):
 return wrap('JD_MERCHANT_CONVERSATION',{'id':product_id,'body':body,'recipient':recipient},SCRIPT)

SCRIPT=r'''
const normalize=s=>String(s||'').replace(/\s+/g,' ').trim();
const merchantPage=()=>{
 const u=new URL(location.href);
 return u.protocol==='https:'&&u.hostname==='jdcs.jd.com'&&u.pathname==='/index.action'&&u.searchParams.getAll('pid').length===1&&u.searchParams.get('pid')===cfg.id;
};
const object=()=>{
 if(!merchantPage())return null;
 const active=all('.dialog.active');if(active.length!==1)return null;
 const names=[...active[0].querySelectorAll('.dialog-detail-header-name')].filter(visible);
 if(names.length!==1||!text(names[0]))return null;
 const cards=all('a[href]').filter(n=>{try{const c=new URL(n.getAttribute('href'),location.href);return c.hostname==='item.jd.com'&&c.pathname==='/'+cfg.id+'.html';}catch{return false;}});
 if(cards.length!==1)return null;
 return {product_id:cfg.id,recipient:text(names[0]).slice(0,200)};
};
const messages=()=>all('.message--content').map(n=>({text:text(n).slice(0,1000)})).filter(x=>x.text).slice(-20);
let stop=guard();if(stop)return stop;
if(!merchantPage())return fail('page_changed');
const target=object();if(!target||(cfg.recipient!==null&&cfg.recipient!==target.recipient))return fail('merchant_identity_unverified');
const editors=all('pre.send-textarea[contenteditable]'),send=all('.input-field--send-btn').filter(n=>n.tagName==='DIV'&&text(n)==='发送');
if(cfg.body===null)return JSON.stringify({success:true,object:target,messages:messages(),send_available:editors.length===1&&send.length===1,draft_present:editors.length===1&&!!String(editors[0].textContent||'').trim()});
if(editors.length!==1||send.length!==1||!editors[0].isContentEditable||send[0].disabled||send[0].getAttribute('aria-disabled')==='true'||unsafe(editors[0])||unsafe(send[0]))return fail('unsupported');
if(String(editors[0].textContent||'').trim())return fail('draft_present');
const before=messages().filter(x=>x.text===normalize(cfg.body)).length;
editors[0].textContent=cfg.body;editors[0].dispatchEvent(new Event('input',{bubbles:true}));
stop=guard();if(stop)return stop;
const current=object();if(!current||current.recipient!==target.recipient||normalize(editors[0].textContent)!==normalize(cfg.body))return fail('merchant_identity_unverified');
const audit={changed:false,verified:false,product_id:cfg.id,recipient:target.recipient};
send[0].click();audit.changed=true;
stop=guard();if(stop)return JSON.stringify({...JSON.parse(stop),audit});
const now=object();if(!now||now.recipient!==target.recipient)return JSON.stringify({success:false,error:'merchant_identity_unverified',audit});
if(messages().filter(x=>x.text===normalize(cfg.body)).length>before)return JSON.stringify({success:true,object:target,audit:{...audit,verified:true},receipt:'message_visible_in_conversation'});
return JSON.stringify({success:false,error:'unverified',audit,message:'已单次发送，未取得会话读回；禁止自动重试。'});
'''

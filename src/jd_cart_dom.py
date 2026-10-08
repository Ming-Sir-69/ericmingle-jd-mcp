"""JD DOM selectors grounded in the 2026-10-08 live evidence."""
import json

def wrap(marker,cfg,body):
    return '(async()=>{/* '+marker+' */const cfg='+json.dumps(cfg,ensure_ascii=False)+';'+COMMON+body+'})()'

COMMON=r'''
const text=el=>String(el?.innerText||el?.textContent||'').replace(/\s+/g,' ').trim();
const visible=el=>el && el.getClientRects().length && getComputedStyle(el).display!=='none' && getComputedStyle(el).visibility!=='hidden';
const all=s=>[...document.querySelectorAll(s)].filter(visible);
const forbidden=/立即购买|购买|结算|提交订单|确认订单|支付|checkout|submit.?order|payment|buy|order\./i;
const unsafe=el=>{
 for(let n=el;n;n=n.parentElement){
  const direct=[...(n.childNodes||[])].filter(c=>c.nodeType===3).map(c=>c.textContent).join('');
  if(forbidden.test(direct+' '+String(n.getAttribute?.('href')||'')+' '+String(n.getAttribute?.('action')||'')+' '+String(n.getAttribute?.('onclick')||'')))return true;
 }return false;
};
const checkout=()=>{const u=new URL(location.href);return /(?:^|\.)(?:trade|buy|pay|cashier)\./i.test(u.hostname)||/checkout|confirm_order|submit.?order|payment|\/order\//i.test(u.pathname);};
const signals=()=>{const t=document.body?.innerText||'';return {risk_control:/访问频繁|访问过于频繁|系统繁忙|安全验证|滑块|人机验证|验证码|captcha|异常访问|验证失败/i.test(t),requires_user_login:all('input[type="password"],input[autocomplete="one-time-code"],[role="dialog"][class*="login" i]').length>0};};
const fail=error=>JSON.stringify({success:false,error});
const guard=()=>{const s=signals();if(s.risk_control||s.requires_user_login)return JSON.stringify({...s,success:false,error:s.risk_control?'risk_control':'login_required'});if(checkout())return fail('checkout_page_reached');return null;};
const detailIdentity=()=>{const u=new URL(location.href);return ['item.jd.com','item.360buy.com','item.m.jd.com'].includes(u.hostname)&&(u.pathname.match(/\/(?:product\/)?([0-9]+)\.html$/)||[])[1]===cfg.id;};
const cartIdentity=()=>{const u=new URL(location.href);return u.hostname==='cart.jd.com'&&['/cart.action','/cart_index'].includes(u.pathname);};
const skuState=()=>{const options=all('.specification-item-sku').map(text);const selected=all('.specification-item-sku--selected').map(text);return {sku_options:[...new Set(options)],selected_sku:selected.join(' / ')};};
'''

def detail_script(product_id):
    return wrap('JD_CART_DETAIL',{'id':product_id},r'''
const stopped=guard();if(stopped)return stopped;if(!detailIdentity())return fail('page_changed');
const sku=skuState(),qty=all('#buy-num');
return JSON.stringify({success:true,product_id:cfg.id,...sku,quantity:qty.length===1?Number(qty[0].value):null});
''')

# Cart selectors are filled only from observed cart row evidence. No legacy guesses.
CART_COMMON=r'''
const rows=()=>all('div[data-rowgid]').filter(n=>String(n.className).split(/\s+/).some(c=>c.startsWith('_row_'))&&String(n.className).split(/\s+/).some(c=>c.startsWith('_good_'))&&n.parentElement?.getAttribute?.('data-skuuuid')&&String(n.parentElement.className).split(/\s+/).some(c=>c.startsWith('_product-item_')));
const rowData=row=>{
 const links=[...row.querySelectorAll('a[class*="_goodTitle_"]')].filter(visible);
 if(links.length!==1)return null;
 let u;try{u=new URL(links[0].getAttribute('href'),location.href);}catch{return null;}
 const id=(u.pathname.match(/^\/([0-9]+)\.html$/)||[])[1];
 if(!['item.jd.com','item.360buy.com'].includes(u.hostname)||!id)return null;
 const qty=[...row.querySelectorAll('input[type="text"]')].filter(visible);
 const quantity=qty.length===1&&/^[0-9]+$/.test(qty[0].value)?Number(qty[0].value):null;
 return {item_id:id,product_id:id,sku_id:id,sku_text:null,title:text(links[0]).slice(0,300),quantity,unit_price:null,
 fields:{sku_text:'missing',unit_price:'missing',quantity:Number.isInteger(quantity)&&quantity>0?'ok':'missing'}};
};
const readRows=()=>{
 const found=rows(),items=found.slice(0,200).map(rowData).filter(Boolean);
 const advertised=(document.body?.innerText||'').match(/购物车\s*([0-9]+)/);
 const complete=!!advertised&&Number(advertised[1])===items.length&&found.length===items.length&&items.every(x=>Number.isInteger(x.quantity)&&x.quantity>0);
 return {success:items.length>0||complete,items,complete,count:items.length,total_count:advertised?Number(advertised[1]):null,
 fields:{items:items.length?'ok':'missing',cart_complete:complete?'ok':'missing'},...(complete?{}:{error:'cart_incomplete',message:'只读取当前渲染行；未能与购物车总数核对，禁止据此判断目标不存在。'})};
};
'''

def cart_script():
    return wrap('JD_CART_LIST',{},CART_COMMON+r'''
const stopped=guard();if(stopped)return stopped;if(!cartIdentity())return fail('page_changed');return JSON.stringify(readRows());
''')

def mutation_script(action,product_id,sku,qty):
    return wrap('JD_CART_MUTATE',{'action':action,'id':product_id,'sku':sku,'qty':qty},CART_COMMON+r'''
const stopped=guard();if(stopped)return stopped;
let el;
if(cfg.action==='add'){
 if(!detailIdentity())return fail('page_changed');
 const current=skuState();if(current.selected_sku!==cfg.sku)return fail('needs_sku');
 const quantity=all('#buy-num');
 if(quantity.length!==1||quantity[0].tagName!=='INPUT'||quantity[0].disabled||unsafe(quantity[0]))return fail('needs_quantity');
 if(Number(quantity[0].value)!==cfg.qty){
  const q=quantity[0],setter=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(q),'value')?.set;
  if(setter)setter.call(q,String(cfg.qty));else q.value=String(cfg.qty);
  q.dispatchEvent(new Event('input',{bubbles:true}));q.dispatchEvent(new Event('change',{bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,150));
  const now=guard();if(now)return now;
  if(!detailIdentity()||skuState().selected_sku!==cfg.sku||all('#buy-num').length!==1||Number(all('#buy-num')[0].value)!==cfg.qty)return fail('needs_quantity');
 }
 const controls=all('#add-to-cart').filter(n=>n.tagName==='DIV'&&text(n)==='加入购物车');
 if(controls.length!==1)return fail('ambiguous_control');el=controls[0];
}else{
 if(!cartIdentity())return fail('page_changed');
 const matching=rows().filter(row=>rowData(row)?.product_id===cfg.id);
 if(matching.length!==1)return fail('ambiguous_item');
 const data=rowData(matching[0]);
 if(data.quantity!==cfg.qty)return fail('quantity_changed');
 const controls=[...matching[0].querySelectorAll('a[class*="_txt-btn_"]')].filter(visible).filter(n=>text(n)==='删除');
 if(controls.length!==1)return fail('ambiguous_control');el=controls[0];
}
if(el.disabled||el.getAttribute?.('aria-disabled')==='true'||unsafe(el)||forbidden.test(text(el)))return fail('unsafe_control');
if((cfg.action==='add'&&!detailIdentity())||(cfg.action==='remove'&&!cartIdentity()))return fail('page_changed');
const badge=()=>{const values=all('#my-cart a').map(text).map(t=>(t.match(/^购物车\s*([0-9]+)$/)||[])[1]).filter(v=>v!==undefined);return values.length===1?Number(values[0]):null;};
const beforeCount=cfg.action==='add'?badge():null;
const hadToast=/成功加入购物车|已成功加入购物车|加入购物车成功/.test(document.body?.innerText||'');
const audit={action:cfg.action,product_id:cfg.id,sku_text:cfg.sku,quantity:cfg.qty,changed:false,verified:false,cart_count_before:beforeCount};
el.click();audit.changed=true;let confirmed=false;
for(let i=0;i<5;i++){
 const now=guard();if(now)return JSON.stringify({...JSON.parse(now),audit});
 if((cfg.action==='add'&&!detailIdentity())||(cfg.action==='remove'&&!cartIdentity()))return JSON.stringify({success:false,error:'page_changed',audit});
 if(cfg.action==='add'){
  const count=badge();audit.cart_count_after=count;
  if((beforeCount!==null&&count!==null&&count>beforeCount)||(!hadToast&&/成功加入购物车|已成功加入购物车|加入购物车成功/.test(document.body?.innerText||'')))audit.verified=true;
 }
 if(cfg.action==='remove'&&!confirmed){
  const dialogs=all('[role="dialog"]');
  if(dialogs.length){
   if(dialogs.length!==1)return JSON.stringify({success:false,error:'unverified',audit});
   const buttons=[...dialogs[0].querySelectorAll('button,a')].filter(visible).filter(n=>['删除','确定'].includes(text(n)));
   if(buttons.length!==1||unsafe(buttons[0])||buttons[0].disabled)return JSON.stringify({success:false,error:'unverified',audit});
   if(!cartIdentity())return JSON.stringify({success:false,error:'page_changed',audit});buttons[0].click();confirmed=true;
  }
 }
 if(cfg.action==='remove'&&!rows().some(row=>rowData(row)?.product_id===cfg.id))return JSON.stringify({success:true,audit:{...audit,verified:true}});
 await new Promise(resolve=>setTimeout(resolve,150));
}
return JSON.stringify({success:true,audit,confirmation:'cart_readback_required'});
''')

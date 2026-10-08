"""Auditable favorite-only mutations. Cart is unsupported until exact state is provable."""
import json


def favorite_script(platform, product_id, desired):
    config = {
        'platform': platform, 'id': product_id, 'desired': bool(desired),
        'hosts': ['item.taobao.com','detail.tmall.com'] if platform == 'taobao' else ['item.jd.com','item.360buy.com','item.m.jd.com'],
        'selectors': ['#collectBtn','.tb-social-fav','.tb-btn-fav','[class*="Favorite"] button','[class*="favorite"] button'] if platform == 'taobao' else ['.sku-title > span.follow-btn','#choose-btn-follow','#follow','#btn-follow','.follow'],
        'inactive': ['收藏商品','收藏宝贝','收藏'] if platform == 'taobao' else ['关注商品','关注','收藏'],
        'active': ['已收藏','取消收藏'] if platform == 'taobao' else ['已关注','取消关注','已收藏','取消收藏'],
    }
    return '(async () => { /* COMMERCE_FAVORITE: exactly one guarded DOM click */ const cfg=' + json.dumps(config,ensure_ascii=False) + ';' + SCRIPT + '})()'


SCRIPT = r'''
const clean = el => String(el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '').replace(/\s+/g,'').trim();
const visible = el => el && el.getClientRects().length && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
const forbidden = /立即购买|购买|结算|提交订单|确认订单|支付|buy|checkout|submit.?order|confirm_order|order\.|payment/i;
const unsafeAncestors = el => {
 for(let node=el; node; node=node.parentElement) {
  const href=String(node.getAttribute?.('href') || '')+' '+String(node.getAttribute?.('action') || '');
  const ownText=[...(node.childNodes || [])].filter(child=>child.nodeType===3).map(child=>child.textContent).join('');
  // Direct ancestor wording + href/action excludes unrelated sibling buy buttons.
  if(forbidden.test(href+' '+ownText) || (!node.childNodes?.length && forbidden.test(clean(node)))) return true;
 }
 return false;
};
const checkoutPage = () => { const u=new URL(location.href); return /(?:^|\.)(?:buy|trade|cashier|pay)\./i.test(u.hostname) || /checkout|confirm_order|submit.?order|payment|\/order\//i.test(u.pathname); };
const signals = () => {
 const t=document.body?.innerText || '';
 const risk=/访问频繁|访问过于频繁|系统繁忙|安全验证|滑块|人机验证|验证码|captcha|异常访问|验证失败/i.test(t);
 const auth=[...document.querySelectorAll('input[type="password"],input[autocomplete="one-time-code"],[role="dialog"][class*="login" i]')].some(visible);
 return {risk_control:risk,requires_user_login:auth || /亲，请登录|请登录/.test(t)};
};
const identity = () => {
 const u=new URL(location.href);
 const id=cfg.platform==='taobao'?u.searchParams.get('id'):(u.pathname.match(/\/(?:product\/)?([0-9]+)\.html$/)||[])[1];
 return cfg.hosts.includes(u.hostname) && id===cfg.id;
};
let initial=signals();
if(initial.risk_control || initial.requires_user_login) return JSON.stringify({...initial,success:false,error:initial.risk_control?'risk_control':'login_required'});
if(checkoutPage()) return JSON.stringify({success:false,error:'checkout_page_reached'});
if(!identity()) return JSON.stringify({success:false,error:'page_changed'});
const controls=()=>[...new Set(cfg.selectors.flatMap(selector=>[...document.querySelectorAll(selector)]))].filter(visible).filter(el=>{
 const text=clean(el), hint=String(el.getAttribute?.('href') || '') + ' ' + String(el.getAttribute?.('onclick') || '') + ' ' + String(el.id || '');
 const classes=String(el.className || '').split(/\s+/);
 const parentClasses=String(el.parentElement?.className || '').split(/\s+/);
 // Current UI evidence captured 2026-10-08: exact product control + parent structure.
 const modernCandidate=el.id==='collectBtn' || classes.includes('follow-btn');
 const modernControl=cfg.platform==='taobao'
  ? el.tagName==='DIV' && el.id==='collectBtn' && classes.some(c=>c.startsWith('RightButton--')) && parentClasses.some(c=>c.startsWith('RightButtonList--'))
  : el.tagName==='SPAN' && classes.includes('follow-btn') && parentClasses.includes('sku-title');
 return (modernCandidate?modernControl:/^(?:A|BUTTON)$/.test(el.tagName || '')) && [...cfg.active,...cfg.inactive].includes(text);
});
const candidates=controls();
if(candidates.length!==1) return JSON.stringify({success:false,error:'unsupported',fields:{action:'unsupported'},message:'无法唯一识别当前商品收藏控件，未点击。'});
const el=candidates[0];
const state=node=>cfg.active.includes(clean(node))?true:cfg.inactive.includes(clean(node))?false:null;
const before=state(el);
const audit={action:cfg.desired?'favorite':'unfavorite',product_id:cfg.id,control_text:clean(el),before,changed:false,verified:false};
if(before===cfg.desired) return JSON.stringify({success:true,already_in_target_state:true,audit:{...audit,verified:true,after:before}});
if(!identity() || el.disabled || forbidden.test(clean(el)+' '+String(el.getAttribute?.('href') || '')+' '+String(el.getAttribute?.('onclick') || '')+' '+String(el.id || '')) || unsafeAncestors(el)) return JSON.stringify({success:false,error:'unsafe_control',audit});
el.click(); audit.changed=true;
for(let i=0;i<10;i++) {
 const current=signals();
 if(current.risk_control || current.requires_user_login) return JSON.stringify({...current,success:false,error:current.risk_control?'risk_control':'login_required',audit});
 if(checkoutPage()) return JSON.stringify({success:false,error:'checkout_page_reached',audit});
 if(!identity()) return JSON.stringify({success:false,error:'page_changed',audit});
 const fresh=controls();
 if(fresh.length===1 && state(fresh[0])===cfg.desired) return JSON.stringify({success:true,audit:{...audit,verified:true,after:state(fresh[0])}});
 await new Promise(resolve=>setTimeout(resolve,150));
}
return JSON.stringify({success:false,error:'unverified',audit,message:'已单次点击，但页面未确认目标状态；禁止自动重试，请本人核对。'});
'''

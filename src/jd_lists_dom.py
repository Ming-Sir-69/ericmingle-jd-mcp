"""Read-only JD lists from the 2026-10-08 official page evidence."""
from jd_cart_dom import wrap


def favorite_list_script(page=1):
    return wrap('JD_FAVORITE_LIST',{'page':page},r'''
const stopped=guard();if(stopped)return stopped;
const current=new URL(location.href);
if(current.hostname!=='t.jd.com'||current.pathname!=='/home/follow')return fail('page_changed');
const items=[],seen=new Set();
for(const row of all('div.item-inner')){
 const links=[...row.querySelectorAll('a[href]')].filter(visible).map(a=>{
  try{const u=new URL(a.getAttribute('href'),location.href);const id=(u.pathname.match(/^\/([0-9]+)\.html$/)||[])[1];
   return ['item.jd.com','item.360buy.com'].includes(u.hostname)&&id?{a,id,url:'https://item.jd.com/'+id+'.html'}:null;
  }catch{return null;}
 }).filter(Boolean);
 const ids=[...new Set(links.map(x=>x.id))];if(ids.length!==1||seen.has(ids[0]))continue;
 const link=links.find(x=>text(x.a))||links[0];seen.add(link.id);
 const title=(text(link.a)||link.a.getAttribute('title')||link.a.querySelector('img')?.getAttribute('alt')||'').slice(0,300);
 const raw=text(row),money=raw.match(/[¥￥]\s*([0-9]+(?:\.[0-9]{1,2})?)/);
 const state=/已下架|失效|已失效/.test(raw)?'unavailable':/无货|缺货/.test(raw)?'out_of_stock':'unknown';
 items.push({item_id:link.id,product_id:link.id,url:link.url,product_url:link.url,title,price:money?Number(money[1]):null,
  availability:state,fields:{title:title?'ok':'missing',price:money?'ok':'missing',availability:state==='unknown'?'missing':'ok'}});
 if(items.length>=100)break;
}
const pages=all('a[href]').filter(a=>/^[0-9]+$/.test(text(a))).map(a=>{
 try{const u=new URL(a.getAttribute('href'),location.href);return u.protocol==='https:'&&u.hostname==='t.jd.com'&&u.pathname==='/home/follow'?{page:Number(text(a)),url:u.href}:null;}catch{return null;}
}).filter(Boolean);
return JSON.stringify({success:true,items,count:items.length,page:cfg.page,source:'platform_dom',platform_full_list:false,page_scope:'rendered',fields:{items:items.length?'ok':'missing'},pages});
''')


def conversation_list_script():
    return wrap('JD_CONVERSATION_LIST',{},r'''
const stopped=guard();if(stopped)return stopped;
const current=new URL(location.href);
if(current.hostname!=='jdcs.jd.com'||current.pathname!=='/index.action')return fail('page_changed');
const items=[];
for(const row of all('div.dialog').slice(0,100)){
 const names=[...row.querySelectorAll('.dialog-detail-header-name')].filter(visible);if(names.length!==1||!text(names[0]))continue;
 const when=[...row.querySelectorAll('.dialog-detail-header-right-time')].filter(visible);
 const summary=[...row.querySelectorAll('.dialog-detail-content-msg')].filter(visible);
 const products=[...row.querySelectorAll('a[href]')].map(a=>{
  try{const u=new URL(a.getAttribute('href'),location.href);const id=(u.pathname.match(/^\/([0-9]+)\.html$/)||[])[1];
   if(u.hostname==='item.jd.com'&&id)return id;
   if(u.hostname==='jdcs.jd.com'&&u.pathname==='/index.action'&&/^[0-9]+$/.test(u.searchParams.get('pid')||''))return u.searchParams.get('pid');
  }catch{}return null;
 }).filter(Boolean);
 const ids=[...new Set(products)],id=ids.length===1?ids[0]:null;
 items.push({recipient:text(names[0]).slice(0,200),last_message_time:when.length===1?text(when[0]).slice(0,80):null,
  last_message_summary:summary.length===1?text(summary[0]).slice(0,120):null,unread_count:null,
  product_url:id?'https://item.jd.com/'+id+'.html':null,fields:{unread_count:'missing',locator:id?'ok':'missing'}});
}
return JSON.stringify({success:true,items,count:items.length,source:'platform_dom',platform_full_list:false,fields:{items:items.length?'ok':'missing'}});
''')

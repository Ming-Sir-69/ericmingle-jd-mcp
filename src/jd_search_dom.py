"""JD search pagination observed in live/d-jd-search-page-evidence.json."""
from jd_cart_dom import wrap


def search_page_script(page,click=False):
    return wrap('JD_SEARCH_PAGE',{'page':page,'click':click},r'''
const stopped=guard();if(stopped)return stopped;
const u=new URL(location.href);
if(u.hostname!=='search.jd.com'||u.pathname!=='/Search')return fail('page_changed');
const pager=()=>all('div[class*="_pagination_item_"]').filter(n=>String(n.className).split(/\s+/).some(c=>c.startsWith('_pagination_item_'))&&/^[0-9]+$/.test(text(n)));
const state=()=>{
 const buttons=pager(),active=buttons.filter(n=>String(n.className).split(/\s+/).some(c=>c.startsWith('_active_')));
 const ids=[...new Set(all('[data-sku]').map(n=>n.getAttribute('data-sku')).filter(id=>/^[0-9]+$/.test(id)))].slice(0,200);
 return {active_page:active.length===1?Number(text(active[0])):null,ids,available_pages:buttons.map(n=>Number(text(n)))};
};
if(!cfg.click)return JSON.stringify({success:true,...state()});
const before=state();if(before.active_page===cfg.page)return JSON.stringify({success:true,changed:false,...before});
const target=pager().filter(n=>Number(text(n))===cfg.page);
if(target.length!==1||target[0].getAttribute('aria-disabled')==='true'||String(target[0].className).split(/\s+/).some(c=>c.startsWith('_disabled_')))return JSON.stringify({success:false,error:'unsupported',changed:false,...before,message:'请求页码当前不可见或控件不唯一；未点击，不猜URL。'});
target[0].click();return JSON.stringify({success:true,changed:true,...before});
''')

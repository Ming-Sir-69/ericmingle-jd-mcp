"""JD cart operations with observed DOM and persistent uncertain-write stops."""
import json
import hashlib
import os
from pathlib import Path
import re
from jd_cart_dom import cart_script, detail_script, mutation_script
from jd_merchant_dom import merchant_script

CART_URL = 'https://cart.jd.com/cart.action'

class WriteJournal:
    def __init__(self, directory):
        self.path=Path(directory)/'commerce-write-journal.json'
    def read(self):
        try:
            data=json.loads(self.path.read_text())
            return data if isinstance(data,dict) else {'*':True}
        except FileNotFoundError:return {}
        except (ValueError,OSError):return {'*':True}
    def get(self,key):
        return self.read().get(key)
    def record(self,key,value):
        self._save({**self.read(),key:value})
    def blocked(self,key):
        data=self.read();return bool(data.get('*') or data.get(key))
    def set(self,key,pending):
        data=self.read()
        if pending:data[key]=True
        else:data.pop(key,None)
        self._save(data)
    def _save(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        tmp=self.path.with_suffix('.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:json.dump(data,f)
        tmp.replace(self.path)

class JDCommerce:
    def _sku(self, text):
        if text is not None and (not isinstance(text,str) or not text.strip() or len(text)>200 or any(ord(c)<32 for c in text)):
            raise ValueError('sku_text须为1至200字规格文本')
        return text.strip() if text is not None else None
    def _fail(self, code, **values):
        return {**self.identity(),'success':False,'error':code,**values}
    async def _commerce_eval(self, code):
        # The adapter supplies its existing trusted decoding and bridge boundary.
        raw=self._decode(await self.bridge.command('evaluate',{'code':code}))
        if not isinstance(raw,dict):raise ValueError('无效页面返回')
        if raw.get('risk_control'):self._risk={'risk_control':True}
        return raw
    def _commerce_guard(self):
        if self._risk:return self.blocked(self._risk)
        return self.reserve_business()
    async def _cart_state(self):
        signals=await self.navigate(CART_URL,business=True)
        if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
        return await self._commerce_eval(cart_script())
    async def cart_list(self):
        async with self._lock:
            blocked=self._commerce_guard()
            if blocked:return blocked
            raw=await self._cart_state()
            return {**self.identity(),**raw}
    def _matching(self, state, item_id, sku):
        return [row for row in state.get('items',[]) if isinstance(row,dict) and row.get('product_id')==item_id and (sku is None or row.get('sku_text')==sku or (row.get('sku_text') is None and row.get('sku_id')==item_id))]
    async def add_to_cart(self,url,sku_text=None,qty=1,operation_id=None):
        url=self._product_url(url);sku=self._sku(sku_text)
        if not isinstance(qty,int) or isinstance(qty,bool) or not 1<=qty<=3:raise ValueError('qty须为1至3整数')
        if operation_id is not None and (not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',operation_id)):raise ValueError('operation_id须为1至100位英文数字或_.-')
        item_id=re.search(r'([0-9]+)\.html$',url)[1]
        fingerprint=hashlib.sha256(json.dumps([item_id,sku,qty],ensure_ascii=False).encode()).hexdigest()
        key='add:'+ (operation_id or fingerprint)
        async with self._lock:
            if self._risk:return self.blocked(self._risk)
            previous=self.write_journal.get(key)
            if previous:
                if not isinstance(previous,dict) or previous.get('fingerprint')!=fingerprint:return self._fail('operation_id_conflict')
                if previous.get('status')=='done':return {**self.identity(),'success':True,'already_in_target_state':True,'operation_id':operation_id or fingerprint,'item':previous.get('item')}
                return self._fail('previous_action_unverified')
            if self.write_journal.blocked('*'):return self._fail('previous_action_unverified')
            blocked=self.reserve_business()
            if blocked:return blocked
            signals=await self.navigate(url,business=True,reuse_product_page=True)
            if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
            detail=await self._commerce_eval(detail_script(item_id))
            if not detail.get('success'):return {**self.identity(),**detail}
            options=detail.get('sku_options',[])
            if len(options)>1 and sku is None:return self._fail('needs_sku',sku_options=options)
            if sku is None:sku=detail.get('selected_sku') or ''
            if sku and (sku not in options or detail.get('selected_sku')!=sku):return self._fail('needs_sku',sku_options=options)
            before=await self._cart_state()
            if not before.get('success') and before.get('error')!='cart_incomplete':return {**self.identity(),**before}
            matches=self._matching(before,item_id,sku)
            if len(matches)>1:return self._fail('ambiguous_item')
            old_qty=matches[0].get('quantity') if matches else (0 if before.get('complete') else None)
            if matches and (not isinstance(old_qty,int) or old_qty<1):return self._fail('quantity_unverified')
            signals=await self.navigate(url,business=True)
            if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
            self.write_journal.record(key,{'status':'pending','fingerprint':fingerprint})
            raw=await self._commerce_eval(mutation_script('add',item_id,sku,qty))
            if not raw.get('audit',{}).get('changed'):
                self.write_journal.set(key,False)
                return {**self.identity(),**raw}
            if raw.get('error') in {'risk_control','login_required','checkout_page_reached','page_changed'}:return {**self.identity(),**raw}
            after=await self._cart_state();matches=self._matching(after,item_id,sku)
            if not after.get('success') and after.get('error')!='cart_incomplete':return {**self.identity(),**after}
            quantity_verified=len(matches)==1 and old_qty is not None and matches[0].get('quantity')==old_qty+qty
            action_verified=bool(raw.get('audit',{}).get('verified'))
            if quantity_verified or action_verified:
                item=matches[0] if len(matches)==1 else {'product_id':item_id,'sku_id':item_id,'sku_text':sku,'quantity':None,'fields':{'quantity':'missing'}}
                self.write_journal.record(key,{'status':'done','fingerprint':fingerprint,'item':item})
                return {**self.identity(),'success':True,'item':item,'operation_id':operation_id or fingerprint,'audit':{**raw.get('audit',{}),'verified':True,'confirmation':'target_quantity_changed' if quantity_verified else 'success_toast_or_cart_count_changed','before_quantity':old_qty,'after_quantity':item.get('quantity')}}
            return self._fail('unverified',audit=raw.get('audit',{}),message='已单次加购，增量未确认；operation_id禁止重试。')
    async def remove_from_cart(self,item_id,sku_text=None):
        if not isinstance(item_id,str) or not re.fullmatch(r'[0-9]{1,30}',item_id):raise ValueError('item_id须为数字商品编号')
        sku=self._sku(sku_text)
        async with self._lock:
            blocked=self._commerce_guard()
            if blocked:return blocked
            if sku is not None:
                signals=await self.navigate('https://item.jd.com/'+item_id+'.html',business=True,reuse_product_page=True)
                if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
                detail=await self._commerce_eval(detail_script(item_id))
                if not detail.get('success'):return {**self.identity(),**detail}
                if detail.get('selected_sku')!=sku:return self._fail('needs_sku',sku_options=detail.get('sku_options',[]))
            before=await self._cart_state()
            if not before.get('success'):return {**self.identity(),**before}
            matches=self._matching(before,item_id,sku)
            if len(matches)>1:return self._fail('ambiguous_item')
            key='remove:'+item_id
            if not matches:
                if not before.get('complete'):return self._fail('cart_incomplete')
                self.write_journal.set(key,False)
                return {**self.identity(),'success':True,'already_in_target_state':True}
            if self.write_journal.blocked(key):return self._fail('previous_action_unverified')
            row_sku=matches[0].get('sku_text')
            if not isinstance(row_sku,str) and matches[0].get('sku_id')!=item_id:return self._fail('needs_sku')
            sku=row_sku
            self.write_journal.set(key,True)
            raw=await self._commerce_eval(mutation_script('remove',item_id,sku,matches[0].get('quantity')))
            if not raw.get('audit',{}).get('changed'):
                self.write_journal.set(key,False)
                return {**self.identity(),**raw}
            if not raw.get('success'):return {**self.identity(),**raw}
            after=await self._commerce_eval(cart_script())
            if not self._matching(after,item_id,sku) and raw.get('audit',{}).get('verified'):
                self.write_journal.set(key,False)
                return {**self.identity(),'success':True,'audit':raw.get('audit',{})}
            return self._fail('unverified',audit=raw.get('audit',{}))
    async def merchant_messages(self,url):
        return await self._merchant(url)
    async def contact_merchant(self,url,text):
        self._product_url(url)
        if not isinstance(text,str) or not text.strip() or not 1<=len(text)<=500 or '\x00' in text:raise ValueError('text须为1至500字正文')
        return await self._merchant(url,text)
    async def _merchant(self,url,text=None):
        url=self._product_url(url);item_id=re.search(r'([0-9]+)\.html$',url)[1]
        fingerprint=hashlib.sha256((item_id+'\n'+(text or '')).encode()).hexdigest()
        key='merchant:'+item_id+':'+fingerprint
        async with self._lock:
            blocked=self._commerce_guard()
            if blocked:return blocked
            if text is not None and self.write_journal.get(key):
                if self.write_journal.get(key).get('status')=='done':return {**self.identity(),'success':True,'already_in_target_state':True,'send_attempted':False}
                return self._fail('previous_action_unverified',send_attempted=False)
            signals=await self.navigate('https://jdcs.jd.com/index.action?pid='+item_id,business=True)
            if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
            probe=await self._commerce_eval(merchant_script(item_id))
            if not probe.get('success') or text is None:return {**self.identity(),**probe,'product_url':url,'send_attempted':False}
            target=probe.get('object') or {}
            if target.get('product_id')!=item_id or not target.get('recipient') or not probe.get('send_available') or probe.get('draft_present'):
                return self._fail('merchant_identity_unverified',send_attempted=False)
            self.write_journal.record(key,{'status':'pending'})
            raw=await self._commerce_eval(merchant_script(item_id,text,target['recipient']))
            if not raw.get('audit',{}).get('changed'):self.write_journal.set(key,False)
            elif raw.get('success') and raw.get('audit',{}).get('verified'):self.write_journal.record(key,{'status':'done'})
            return {**self.identity(),**raw,'product_url':url,'send_attempted':bool(raw.get('audit',{}).get('changed'))}

"""JD cart operations with observed DOM and persistent uncertain-write stops."""
import json
import asyncio
import time
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse
import re
from jd_cart_dom import cart_script, detail_script, mutation_script, add_probe_script, confirm_delete_script
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
    async def _front_cart_page(self):
        tabs=await self.owned_tabs()
        if not tabs:
            signals=await self.navigate('https://www.jd.com/',business=True)
            if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
            tabs=await self.owned_tabs()
        await self.select_owned(tabs)
        signals=await self.inspect_page()
        if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
        response=self._decode(await self.bridge.command('cdp',{'method':'Page.bringToFront','params':{}}))
        if response is False or isinstance(response,dict) and (response.get('success') is False or response.get('ok') is False):return self._fail('front_failed')
        signals=await self.inspect_page()
        if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
        if not signals.get('page_visible'):return self._fail('front_unverified')
        return None
    async def _cart_state(self):
        stopped=await self._front_cart_page()
        if stopped:return stopped
        signals=await self.navigate(CART_URL,business=True)
        if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
        deadline=time.monotonic()+getattr(self,'_commerce_ready_timeout',10)
        raw={'success':False,'error':'cart_incomplete','items':[],'complete':False}
        while True:
            try:raw=await self._within(self._commerce_eval(cart_script()),deadline)
            except TimeoutError:return raw
            loading=raw.get('error')=='cart_incomplete' and not raw.get('items') and (raw.get('total_count') or 0)>0
            if not loading or time.monotonic()>=deadline:return raw
            await asyncio.sleep(min(0.25,deadline-time.monotonic()))
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
                blocked=self.reserve_business()
                if blocked:return blocked
                return await self._reconcile_add(key,previous,item_id,sku,qty,operation_id or fingerprint)
            if self.write_journal.blocked('*'):return self._fail('previous_action_unverified')
            blocked=self.reserve_business()
            if blocked:return blocked
            front=await self._front_cart_page()
            if front:return front
            signals=await self.navigate(url,business=True,reuse_product_page=True)
            if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
            detail=await self._ready_detail(item_id)
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
            prepared=await self._ready_detail(item_id)
            if not prepared.get('success'):return {**self.identity(),**prepared}
            if prepared.get('selected_sku')!=sku:return self._fail('needs_sku',sku_options=prepared.get('sku_options',[]))
            self.write_journal.record(key,{'status':'pending','fingerprint':fingerprint,'product_id':item_id,'sku_text':sku,'requested_quantity':qty,'before_quantity':old_qty,'before_total_count':before.get('total_count')})
            deadline=time.monotonic()+getattr(self,'_commerce_timeout',20)
            try:
                raw=await self._within(self._commerce_eval(mutation_script('add',item_id,sku,qty)),min(deadline,time.monotonic()+5))
            except Exception:
                raw={'success':False,'error':'unknown','audit':{'changed':True,'verified':False,'cart_count_before':prepared.get('cart_count'),'had_success_toast':prepared.get('had_success_toast',True)}}

            if not raw.get('audit',{}).get('changed'):
                self.write_journal.set(key,False)
                return {**self.identity(),**raw}
            if raw.get('error') in {'risk_control','login_required','checkout_page_reached','page_changed'}:return {**self.identity(),**raw}
            action_verified=bool(raw.get('audit',{}).get('verified'))
            action_proof=raw.get('audit',{}).get('proof')
            redirected=bool(raw.get('redirected_to_cart'))
            if old_qty is None and not action_verified and not redirected:
                probe_deadline=min(deadline,time.monotonic()+5)
                while time.monotonic()<probe_deadline:
                    try:
                        probe=await self._within(self._commerce_eval(add_probe_script(item_id,raw.get('audit',{}).get('cart_count_before'),raw.get('audit',{}).get('had_success_toast',False))),probe_deadline)
                        if probe.get('error') in {'risk_control','login_required','checkout_page_reached','page_changed'}:return {**self.identity(),**probe}
                        action_verified=bool(probe.get('verified'))
                        action_proof=probe.get('proof')
                        redirected=bool(probe.get('redirected_to_cart'))
                        if action_verified or redirected:break
                    except Exception:break
                    await asyncio.sleep(min(0.25,max(0,probe_deadline-time.monotonic())))
            readback_error=None
            try:after=await self._within(self._cart_state(),deadline)
            except Exception as exc:after={};readback_error=type(exc).__name__
            while True:
                if after.get('error')=='cart_unavailable':
                    diagnostic={'before_total_count':before.get('total_count'),'after_total_count':None,'target_count':None,'cart_error':'cart_unavailable'}
                    self.write_journal.record(key,{**self.write_journal.get(key),'last_readback':diagnostic,'audit':raw.get('audit',{})})
                    return self._fail('unknown',reason='cart_unavailable',operation_id=operation_id or fingerprint,audit={**raw.get('audit',{}),'readback':diagnostic},message='京东购物车加载失败；已停止读回，不重试写入，结果仍未知。')
                if after.get('error') in {'risk_control','login_required','checkout_page_reached','page_changed'}:return {**self.identity(),**after}
                matches=self._matching(after,item_id,sku)
                quantity_verified=len(matches)==1 and old_qty is not None and matches[0].get('quantity')==old_qty+qty
                redirected_verified=redirected and len(matches)==1 and matches[0].get('quantity')==(old_qty+qty if old_qty is not None else qty)
                if quantity_verified or action_verified or redirected_verified:
                    item=matches[0] if len(matches)==1 else {'product_id':item_id,'sku_id':item_id,'sku_text':sku,'quantity':None,'fields':{'quantity':'missing'}}
                    self.write_journal.record(key,{'status':'done','fingerprint':fingerprint,'item':item})
                    return {**self.identity(),'success':True,'item':item,'operation_id':operation_id or fingerprint,'audit':{**raw.get('audit',{}),'verified':True,'proof':'cart_target_observed' if redirected_verified else 'cart_target_quantity_changed' if quantity_verified else action_proof or 'guarded_dom_receipt','confirmation':'cart_target_observed' if redirected_verified else 'target_quantity_changed' if quantity_verified else action_proof or 'guarded_dom_receipt','before_quantity':old_qty,'after_quantity':item.get('quantity')}}
                if time.monotonic()>=deadline:break
                await asyncio.sleep(min(0.25,deadline-time.monotonic()))
                try:after=await self._within(self._commerce_eval(cart_script()),deadline)
                except Exception as exc:readback_error=type(exc).__name__;break
            diagnostic={'before_total_count':before.get('total_count'),'after_total_count':after.get('total_count'),'target_count':len(matches),'target_quantities':[x.get('quantity') for x in matches],'cart_error':after.get('error'),'readback_error_type':readback_error}
            self.write_journal.record(key,{**self.write_journal.get(key),'last_readback':diagnostic,'audit':raw.get('audit',{})})
            return self._fail('unknown',operation_id=operation_id or fingerprint,audit={**raw.get('audit',{}),'readback':diagnostic},message='已单次尝试，20秒内增量未确认；同operation_id仅只读核对，禁止再次点击。')
    async def _reconcile_add(self,key,previous,item_id,sku,qty,operation_id):
        # Pending IDs never re-enter the detail page or the click script.
        try:after=await self._cart_state()
        except Exception as exc:return self._fail('unknown',operation_id=operation_id,reconciled=True,readback_error_type=type(exc).__name__)
        if after.get('error') in {'risk_control','login_required','checkout_page_reached','page_changed'}:return {**self.identity(),**after}
        matches=self._matching(after,item_id,sku)
        before_qty=previous.get('before_quantity')
        before_total=previous.get('before_total_count')
        after_total=after.get('total_count')
        known_before=isinstance(before_qty,int) and not isinstance(before_qty,bool) and before_qty>=0
        exact=len(matches)==1 and matches[0].get('quantity')==(before_qty+qty if known_before else qty)
        count_changed=isinstance(before_total,int) and not isinstance(before_total,bool) and isinstance(after_total,int) and not isinstance(after_total,bool) and after_total>before_total
        if exact and (known_before or count_changed):
            proof='cart_target_quantity_changed' if known_before else 'cart_total_and_target'
            self.write_journal.record(key,{**previous,'status':'done','item':matches[0]})
            return {**self.identity(),'success':True,'operation_id':operation_id,'item':matches[0],'audit':{'changed':False,'verified':True,'reconciled':True,'proof':proof,'before_quantity':before_qty,'after_quantity':matches[0].get('quantity'),'before_total_count':before_total,'after_total_count':after_total}}
        diagnostic={'before_total_count':before_total,'after_total_count':after_total,'target_count':len(matches),'target_quantities':[x.get('quantity') for x in matches],'cart_error':after.get('error')}
        self.write_journal.record(key,{**previous,'last_readback':diagnostic})
        return self._fail('unknown',reason=after.get('error') or 'insufficient_evidence',operation_id=operation_id,audit={'changed':False,'verified':False,'reconciled':True,'readback':diagnostic},message='只读后态仍缺少精确数量增量或真实总数变化证据；未再次加购。')
    async def _ready_detail(self,item_id):
        deadline=time.monotonic()+10
        while True:
            try:raw=await self._within(self._commerce_eval(detail_script(item_id)),deadline)
            except Exception:return self._fail('page_not_ready')
            if raw.get('error')!='page_not_ready':return raw
            if time.monotonic()>=deadline:return raw
            await asyncio.sleep(min(0.25,deadline-time.monotonic()))
    async def _within(self,operation,deadline):
        remaining=deadline-time.monotonic()
        if remaining<=0:
            operation.close()
            raise TimeoutError
        return await asyncio.wait_for(operation,timeout=remaining)
    async def remove_from_cart(self,item_id,sku_text=None):
        if not isinstance(item_id,str) or not re.fullmatch(r'[0-9]{1,30}',item_id):raise ValueError('item_id须为数字商品编号')
        sku=self._sku(sku_text)
        key='remove:'+item_id
        async with self._lock:
            blocked=self._commerce_guard()
            if blocked:return blocked
            front=await self._front_cart_page()
            if front:return front
            pending=self.write_journal.get(key)
            if pending:
                tabs=await self.owned_tabs();await self.select_owned(tabs)
                signals=await self.inspect_page(business=True)
                if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
                page=urlparse(signals['current_url'])
                if page.hostname!='cart.jd.com' or page.path not in {'/cart.action','/cart_index'}:return self._fail('previous_action_unverified')
                state=await self._commerce_eval(cart_script())
                if state.get('error')=='cart_unavailable':return self._fail('unknown',reason='cart_unavailable')
                matched=self._matching(state,item_id,sku)
                if len(matched)!=1:return self._fail('previous_action_unverified')
                quantity=matched[0].get('quantity')
                if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity<1:return self._fail('quantity_unverified')
                if isinstance(pending,dict) and pending.get('quantity')!=quantity:return self._fail('quantity_changed')
                return await self._finish_remove(item_id,sku,quantity,key,{'audit':{'changed':True,'verified':False,'resumed_confirmation_only':True}},pending)
            if sku is not None:
                signals=await self.navigate('https://item.jd.com/'+item_id+'.html',business=True,reuse_product_page=True)
                if signals['risk_control'] or signals['requires_user_login']:return self.blocked(signals)
                detail=await self._ready_detail(item_id)
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
            quantity=matches[0].get('quantity')
            if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity<1:return self._fail('quantity_unverified')
            row_sku=matches[0].get('sku_text')
            if not isinstance(row_sku,str) and matches[0].get('sku_id')!=item_id:return self._fail('needs_sku')
            sku=row_sku
            self.write_journal.record(key,{'status':'pending','quantity':quantity,'confirmation_attempted':False})
            deadline=time.monotonic()+getattr(self,'_commerce_timeout',20)
            try:raw=await self._within(self._commerce_eval(mutation_script('remove',item_id,sku,quantity)),min(deadline,time.monotonic()+5))
            except Exception:raw={'success':False,'error':'unknown','audit':{'changed':True,'verified':False}}
            if not raw.get('audit',{}).get('changed'):
                self.write_journal.set(key,False)
                return {**self.identity(),**raw}
            if raw.get('error') in {'risk_control','login_required','checkout_page_reached','page_changed'}:return {**self.identity(),**raw}
            return await self._finish_remove(item_id,sku,quantity,key,raw,self.write_journal.get(key),deadline)
    async def _finish_remove(self,item_id,sku,quantity,key,raw,pending,deadline=None):
        deadline=deadline or time.monotonic()+getattr(self,'_commerce_timeout',20)
        confirmed=isinstance(pending,dict) and bool(pending.get('confirmation_attempted'))
        while time.monotonic()<deadline:
            try:after=await self._within(self._commerce_eval(cart_script()),deadline)
            except Exception:break
            if after.get('error')=='cart_unavailable':return self._fail('unknown',reason='cart_unavailable',audit=raw.get('audit',{}))
            if after.get('error') in {'risk_control','login_required','checkout_page_reached','page_changed'}:return {**self.identity(),**after}
            if after.get('success') and not self._matching(after,item_id,sku):
                self.write_journal.set(key,False)
                return {**self.identity(),'success':True,'audit':{**raw.get('audit',{}),'verified':True}}
            if not confirmed:
                try:confirmation=await self._within(self._commerce_eval(confirm_delete_script(item_id,quantity,False)),deadline)
                except Exception:break
                if confirmation.get('confirmation_needed'):
                    confirmed=True
                    self.write_journal.record(key,{'status':'pending','quantity':quantity,'confirmation_attempted':True})
                    try:clicked=await self._within(self._commerce_eval(confirm_delete_script(item_id,quantity,True)),deadline)
                    except Exception:break
                    if not clicked.get('confirmed'):
                        self.write_journal.record(key,{'status':'pending','quantity':quantity,'confirmation_attempted':False})
                        if clicked.get('error')=='cart_unavailable':return self._fail('unknown',reason='cart_unavailable',audit=raw.get('audit',{}))
                        return {**self.identity(),**clicked,'audit':raw.get('audit',{})}
                    continue
                elif confirmation.get('error')=='cart_unavailable':return self._fail('unknown',reason='cart_unavailable',audit=raw.get('audit',{}))
                elif not confirmation.get('success'):return {**self.identity(),**confirmation,'audit':raw.get('audit',{})}
            await asyncio.sleep(min(0.25,max(0,deadline-time.monotonic())))
        return self._fail('unknown',audit=raw.get('audit',{}),message='已单次尝试删除，20秒内未取得目标行消失读回；禁止自动重试。')
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
            deadline=time.monotonic()+getattr(self,'_commerce_timeout',20)
            normalized=' '.join(text.split())
            before=sum(row.get('text')==normalized for row in probe.get('messages',[]) if isinstance(row,dict))
            try:raw=await self._within(self._commerce_eval(merchant_script(item_id,text,target['recipient'])),min(deadline,time.monotonic()+5))
            except Exception:raw={'success':False,'error':'unknown','audit':{'changed':True,'verified':False}}
            if not raw.get('audit',{}).get('changed'):
                self.write_journal.set(key,False)
                return {**self.identity(),**raw,'product_url':url,'send_attempted':False}
            if raw.get('error') in {'risk_control','login_required','checkout_page_reached','merchant_identity_unverified'}:return {**self.identity(),**raw,'product_url':url,'send_attempted':True}
            if raw.get('audit',{}).get('verified'):
                self.write_journal.record(key,{'status':'done'})
                return {**self.identity(),**raw,'product_url':url,'send_attempted':True}
            while time.monotonic()<deadline:
                try:after=await self._within(self._commerce_eval(merchant_script(item_id)),deadline)
                except Exception:break
                if after.get('error') in {'risk_control','login_required','checkout_page_reached','merchant_identity_unverified'}:return {**self.identity(),**after,'send_attempted':True}
                if after.get('object')!=target:return self._fail('merchant_identity_unverified',send_attempted=True)
                matched=sum(row.get('text')==normalized for row in after.get('messages',[]) if isinstance(row,dict))
                if matched>before:
                    self.write_journal.record(key,{'status':'done'})
                    return {**self.identity(),'success':True,'object':target,'send_attempted':True,'audit':{**raw.get('audit',{}),'verified':True},'receipt':'message_visible_in_conversation'}
                await asyncio.sleep(min(0.25,max(0,deadline-time.monotonic())))
            return self._fail('unknown',send_attempted=True,operation_id=fingerprint,audit=raw.get('audit',{}),message='已单次尝试发送，20秒内未取得消息读回；禁止自动重试。')

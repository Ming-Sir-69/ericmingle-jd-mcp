"""Personal notification outbox and opt-in low-frequency watchlist refresh.

Uses the existing macOS notification channel, or an owner-configured command.
Command acceptance is distinct from proof the owner saw a notification.
"""
import asyncio
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import time
from contextlib import contextmanager


async def send_native(title, body):
    def send():
        configured = os.environ.get('MCP_NOTIFY_COMMAND_JSON', '')
        if configured:
            command = json.loads(configured)
            if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
                raise ValueError('invalid owner notification command configuration')
            # Only trusted process configuration defines the executable; MCP input never does.
            completed = subprocess.run(command, input=json.dumps({'title': title, 'body': body}), text=True, capture_output=True, timeout=15)
        elif platform.system() == 'Darwin':
            script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
            completed = subprocess.run(['/usr/bin/osascript', '-e', script, title, body], capture_output=True, text=True, timeout=15)
        else:
            return {'status': 'failed', 'receipt': 'owner_notification_channel_not_configured'}
        return {'status': 'accepted' if completed.returncode == 0 else 'failed', 'receipt': 'receiver_accepted_display_unverified' if completed.returncode == 0 else 'receiver_rejected'}
    return await asyncio.to_thread(send)


class Notifications:
    def __init__(self, watchlist, refresh, *, sender=send_native, clock=time.time):
        self.store, self.refresh, self.sender, self.clock = watchlist, refresh, sender, clock
        self.task = None
        self.wake = asyncio.Event()

    @contextmanager
    def db(self):
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS notify_settings(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER, minutes INTEGER, paused TEXT);
                INSERT OR IGNORE INTO notify_settings VALUES(1,0,30,'');
                CREATE TABLE IF NOT EXISTS notify_refresh(url TEXT PRIMARY KEY, next_at REAL);
                CREATE TABLE IF NOT EXISTS notify_events(id TEXT PRIMARY KEY, url TEXT, title TEXT, body TEXT, delivery_status TEXT, receipt TEXT, created_at REAL, owner_pid INTEGER);
            ''')
            yield db

    def result(self, data):
        return {'ok': True, 'status': 'ok', 'platform': self.store.platform, 'fields': {}, 'data': data}

    def configure(self, enabled, interval_minutes=30):
        if type(enabled) is not bool or type(interval_minutes) is not int or not 30 <= interval_minutes <= 1440:
            raise ValueError('enabled must be boolean; interval_minutes must be 30–1440')
        with self.db() as db:
            db.execute('UPDATE notify_settings SET enabled=?,minutes=?,paused=? WHERE id=1', (int(enabled), interval_minutes, ''))
            db.execute('UPDATE notify_refresh SET next_at=?', (self.clock()+interval_minutes*60,))
        self.wake.set()
        return self.status()

    def status(self):
        with self.db() as db:
            settings = db.execute('SELECT * FROM notify_settings').fetchone()
            events = [dict(r) for r in db.execute('SELECT id,url,delivery_status,receipt,created_at FROM notify_events ORDER BY created_at DESC LIMIT 30')]
        return self.result({'enabled': bool(settings['enabled']), 'interval_minutes': settings['minutes'], 'paused_reason': settings['paused'], 'events': events, 'worker_running': bool(self.task and not self.task.done()), 'display_verified': False})

    def enqueue(self, key, url, title, body):
        event_id = hashlib.sha256((self.store.platform+'|'+key).encode()).hexdigest()
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO notify_events VALUES(?,?,?,?,?,?,?,?)', (event_id, url, title, body, 'pending', '', self.clock(), 0))
            state = db.execute('SELECT delivery_status FROM notify_events WHERE id=?', (event_id,)).fetchone()[0]
        self.wake.set()
        return self.result({'event_id': event_id, 'delivery_status': state, 'display_verified': False})

    def notify_owner(self, url, summary, evidence):
        url = self.store.validate_url(url)
        if any(not isinstance(s, str) or not 1 <= len(s.strip()) <= 1000 or '\x00' in s for s in (summary, evidence)):
            raise ValueError('summary and evidence must each contain 1–1000 characters')
        summary, evidence = summary.strip(), evidence.strip()
        return self.enqueue('deal|'+url+'|'+summary+'|'+evidence, url, f'EricMingle {self.store.platform} 购物提醒', summary+'\n依据（调用智能体判断）：'+evidence+'\n请本人核对条件后下单支付。\n'+url)

    async def run_once(self):
        now = self.clock()
        with self.db() as db:
            settings = dict(db.execute('SELECT * FROM notify_settings').fetchone())
        if settings['enabled'] and not settings['paused']:
            items = self.store.list()['data']['items']
            watched = [r for r in items if r['target_price'] is not None]
            with self.db() as db:
                for row in watched:
                    db.execute('INSERT OR IGNORE INTO notify_refresh VALUES(?,?)', (row['url'], now+settings['minutes']*60))
                due = db.execute('SELECT url FROM notify_refresh WHERE next_at<=? ORDER BY next_at LIMIT 1', (now,)).fetchone()
                if due:
                    db.execute('UPDATE notify_refresh SET next_at=? WHERE url=?', (now+settings['minutes']*60, due['url']))
            if due and any(r['url'] == due['url'] for r in watched):
                observed = await self.refresh(due['url'])
                codes = {observed.get(k) for k in ('status', 'error_code', 'error')}
                code = next((v for v in ('risk_control', 'auth_required', 'login_required') if v in codes), '')
                if code in {'risk_control', 'auth_required', 'login_required'}:
                    with self.db() as db:
                        db.execute('UPDATE notify_settings SET paused=? WHERE id=1', (code,))
                else:
                    self.store.observe(observed)
            for row in self.store.check()['data']['matches']:
                self.enqueue('price|'+row['url']+'|'+str(row['target_price']), row['url'], f'EricMingle {self.store.platform} 目标价命中', f"展示价 {row['price']} ≤ 目标 {row['target_price']}。请本人核对规格、运费、优惠后下单支付。\n{row['url']}")
        # Claim before delivery. An interrupted send is never blindly repeated.
        with self.db() as db:
            for event in db.execute("SELECT id,owner_pid FROM notify_events WHERE delivery_status='sending'").fetchall():
                try:
                    os.kill(event['owner_pid'], 0)
                except ProcessLookupError:
                    db.execute("UPDATE notify_events SET delivery_status='unknown',receipt='previous_sender_interrupted' WHERE id=?", (event['id'],))
            event = db.execute("SELECT * FROM notify_events WHERE delivery_status='pending' ORDER BY created_at LIMIT 1").fetchone()
            if not event:
                return self.status()
            claimed = db.execute("UPDATE notify_events SET delivery_status='sending',owner_pid=? WHERE id=? AND delivery_status='pending'", (os.getpid(), event['id'])).rowcount
        if not claimed:
            return self.status()
        try:
            receipt = await self.sender(event['title'], event['body'])
            state = receipt.get('status')
            if state not in {'accepted', 'failed', 'unknown'}:
                state = 'unknown'
            detail = receipt.get('receipt', '')[:200]
        except asyncio.CancelledError:
            with self.db() as db:
                db.execute("UPDATE notify_events SET delivery_status='unknown',receipt='worker_cancelled_no_automatic_resend' WHERE id=?", (event['id'],))
            raise
        except (TimeoutError, subprocess.TimeoutExpired):
            state, detail = 'unknown', 'delivery_interrupted_or_timed_out_no_automatic_resend'
        except Exception:
            state, detail = 'failed', 'notification_channel_failed'
        with self.db() as db:
            db.execute('UPDATE notify_events SET delivery_status=?,receipt=? WHERE id=?', (state, detail, event['id']))
        return self.status()

    def start(self):
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.loop())

    async def loop(self):
        while True:
            try:
                await self.run_once()
            except (OSError, sqlite3.Error, ValueError):
                pass
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
            except TimeoutError:
                pass

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


def register_notifications(mcp, notifications):
    from mcp.types import ToolAnnotations

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def notification_configure(enabled: bool, interval_minutes: int = 30) -> dict:
        """开启/关闭本平台目标价主动通知；每件商品至少30分钟刷新，遇认证/风控暂停，无自动交易。"""
        try:
            return notifications.configure(enabled, interval_minutes)
        except ValueError as exc:
            return {'ok': False, 'status': 'error', 'error_code': 'invalid_input', 'message': str(exc)}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def notification_status() -> dict:
        """读取本平台通知开关、暂停原因和投递回执；accepted仅表示通道接受，不证明本人已看见。"""
        return notifications.status()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def notify_owner(url: str, summary: str, evidence: str) -> dict:
        """智能体判断交易条件达成后通知本人；须明确本平台商品URL、条件摘要和依据；本人下单支付。同内容去重。"""
        try:
            result = notifications.notify_owner(url, summary, evidence)
            notifications.start()
            return result
        except ValueError as exc:
            return {'ok': False, 'status': 'error', 'error_code': 'invalid_input', 'message': str(exc)}

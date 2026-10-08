"""Personal notification outbox and opt-in low-frequency watchlist refresh.

Uses only the owner-configured mail MCP; no desktop notification fallback.
Command acceptance is distinct from proof the owner saw a notification.
"""
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from contextlib import contextmanager


async def send_mail(title, body, *, config_path=None, configuration=None, transport=None):
    """Submit exactly once through an owner-configured mail MCP; never fall back to UI alerts."""
    try:
        if configuration is None:
            configured = os.environ.get('MCP_MAIL_CONFIG')
            path = Path(configured) if configured else Path(config_path) if config_path else None
            configuration = json.loads(path.read_text()) if path and path.exists() else {}
        cfg = configuration
        if not isinstance(cfg, dict) or not all(isinstance(cfg.get(k), str) and cfg[k] for k in ('url', 'sender', 'recipient')):
            return {'status': 'failed', 'receipt': 'mail_channel_not_configured'}
        parsed = urlsplit(cfg['url'])
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
            return {'status': 'failed', 'receipt': 'invalid_owner_mail_configuration'}
        if any(c in cfg[k] for k in ('sender','recipient') for c in ('\r','\n',',')):
            return {'status': 'failed', 'receipt': 'invalid_owner_mail_configuration'}
        reference = 'commerce-' + hashlib.sha256((title+'|'+body).encode()).hexdigest()[:12]
        arguments = {'from':cfg['sender'], 'to':cfg['recipient'], 'subject':title+' ['+reference+']', 'body':body, 'isHtml':False, 'skipReview':True}
        async with httpx.AsyncClient(timeout=45, trust_env=False, follow_redirects=False, transport=transport) as client:
            response = await client.post(cfg['url'], headers={'Accept':'application/json, text/event-stream', **cfg.get('headers', {})}, json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'sendMail','arguments':arguments}})
        if response.status_code in (400,401,403,404):
            return {'status':'failed','receipt':'mail_endpoint_rejected'}
        if response.status_code != 200 or len(response.content) > 262144:
            return {'status':'unknown','receipt':'mail_submission_outcome_unknown_no_retry'}
        raw = response.text
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ')]
        envelope = next((event for event in reversed(events) if event.get('id') == 1), None) if events else json.loads(raw)
        if not isinstance(envelope, dict) or envelope.get('error'):
            return {'status':'failed','receipt':'mail_rpc_rejected'}
        result = envelope.get('result', {})
        if result.get('isError'):
            return {'status':'failed','receipt':'mail_tool_rejected'}
        message = json.loads(next(block['text'] for block in result.get('content', []) if block.get('type') == 'text'))
        if message.get('success') is True and message.get('message') == 'Message sent':
            return {'status':'accepted','receipt':'mail_reported_sent_delivery_unverified:'+reference}
        if message.get('message') == 'Compose window opened' or 'skipReview' in str(message.get('error','')):
            return {'status':'failed','receipt':'mail_review_required_not_sent'}
        return {'status':'unknown','receipt':'mail_submission_outcome_unknown_no_retry'}
    except httpx.ConnectError:
        return {'status':'failed','receipt':'mail_endpoint_unreachable'}
    except (httpx.TimeoutException, httpx.HTTPError):
        return {'status':'unknown','receipt':'mail_submission_outcome_unknown_no_retry'}
    except (OSError, ValueError, TypeError, KeyError, StopIteration):
        return {'status':'failed','receipt':'mail_configuration_or_response_invalid'}


class Notifications:
    def __init__(self, watchlist, refresh, *, sender=None, clock=time.time):
        self.store, self.refresh, self.clock = watchlist, refresh, clock
        self.sender = sender or (lambda title, body: send_mail(title, body, config_path=self.store.path.parent / "notification-mail.json"))
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
        return self.result({'enabled': bool(settings['enabled']), 'interval_minutes': settings['minutes'], 'paused_reason': settings['paused'], 'events': events, 'worker_running': bool(self.task and not self.task.done()), 'display_verified': False, 'channel': 'email'})

    def enqueue(self, key, url, title, body):
        event_id = hashlib.sha256((self.store.platform+'|'+key).encode()).hexdigest()
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO notify_events VALUES(?,?,?,?,?,?,?,?)', (event_id, url, title, body, 'pending', '', self.clock(), 0))
            state = db.execute('SELECT delivery_status FROM notify_events WHERE id=?', (event_id,)).fetchone()[0]
        self.wake.set()
        return self.result({'event_id': event_id, 'delivery_status': state, 'display_verified': False, 'channel': 'email'})

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
        except TimeoutError:
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
        """智能体判断交易条件达成后邮件通知本人；须明确本平台商品URL、条件摘要和依据；本人下单支付。同内容去重。"""
        try:
            result = notifications.notify_owner(url, summary, evidence)
            notifications.start()
            return result
        except ValueError as exc:
            return {'ok': False, 'status': 'error', 'error_code': 'invalid_input', 'message': str(exc)}

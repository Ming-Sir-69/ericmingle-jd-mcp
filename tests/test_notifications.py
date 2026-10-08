import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest


def module(name):
    path = Path(__file__).parents[1] / 'src' / (name + '.py')
    assert path.exists(), 'notification service is not implemented'
    spec = importlib.util.spec_from_file_location('notification_test_' + name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = module('notifications')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1000.0]
        self.url = 'https://item.taobao.com/item.htm?id=123'
        def validate(url):
            if url != self.url: raise ValueError('invalid product URL')
            return url
        self.store = module('watchlist').Watchlist('taobao', validate, Path(self.temp.name), clock=lambda: self.now[0])
        self.sent, self.reads = [], []
        async def refresh(url):
            self.reads.append(url)
            return {'ok': True, 'url': url, 'price': 9}
        async def sender(title, body):
            self.sent.append((title, body))
            return {'status': 'accepted', 'receipt': 'test receiver accepted'}
        self.refresh, self.sender = refresh, sender
        self.make = lambda: self.mod.Notifications(self.store, refresh, sender=sender, clock=lambda: self.now[0])
        self.service = self.make()

    async def test_real_cached_condition_notifies_once_across_restart(self):
        self.store.upsert(self.url, 'fixture', 10)
        self.store.observe({'url': self.url, 'price': 9})
        self.service.configure(True, 30)
        await self.service.run_once()
        await self.make().run_once()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.service.status()['data']['events'][0]['delivery_status'], 'accepted')

    async def test_scheduled_refresh_reuses_adapter_and_risk_pauses(self):
        self.store.upsert(self.url, 'fixture', 10)
        self.service.configure(True, 30)
        await self.service.run_once()
        self.now[0] += 1801
        await self.service.run_once()
        self.assertEqual(self.reads, [self.url])
        self.assertEqual(len(self.sent), 1)
        async def risk(url): return {'ok': False, 'status': 'risk_control'}
        self.service.refresh = risk
        self.now[0] += 1801
        await self.service.run_once()
        self.assertEqual(self.service.status()['data']['paused_reason'], 'risk_control')

    async def test_unknown_delivery_is_never_blindly_resent(self):
        async def unknown(title, body):
            self.sent.append((title, body))
            raise TimeoutError()
        self.service.sender = unknown
        self.service.notify_owner(self.url, '已确认条件', 'fixture owner assessment')
        await self.service.run_once()
        await self.make().run_once()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.service.status()['data']['events'][0]['delivery_status'], 'unknown')

    async def test_disabled_monitor_never_reads_site_but_owner_notice_delivers(self):
        self.store.upsert(self.url, 'fixture', 10)
        self.now[0] += 9999
        self.service.notify_owner(self.url, '请本人核对下单', 'agent assessment')
        await self.service.run_once()
        self.assertEqual(self.reads, [])
        self.assertEqual(len(self.sent), 1)

    async def test_cross_platform_url_and_fast_polling_rejected(self):
        with self.assertRaises(ValueError): self.service.configure(True, 1)
        with self.assertRaises(ValueError): self.service.notify_owner('https://attacker.example', 'x', 'y')

    async def test_shutdown_during_delivery_persists_unknown_and_stops(self):
        entered = asyncio.Event()
        async def slow(title, body):
            entered.set()
            await asyncio.sleep(60)
        self.service.sender = slow
        self.service.notify_owner(self.url, 'fixture cancellation', 'fixture')
        self.service.start()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.service.close(), 1)
        self.assertEqual(self.service.status()['data']['events'][0]['delivery_status'], 'unknown')

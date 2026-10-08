import asyncio
import importlib.util
import json
from pathlib import Path
import unittest
import httpx


class MailDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def load(self):
        path = Path(__file__).parents[1] / 'src/notifications.py'
        spec = importlib.util.spec_from_file_location('mail_delivery_test', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(hasattr(mod, 'send_mail'), 'mail-only delivery not implemented')
        return mod

    def config(self):
        address = 'owner' + chr(64) + 'example.invalid'
        return {'url': 'http://127.0.0.1:9876/mcp', 'sender': address, 'recipient': address}

    async def test_sends_only_to_configured_owner_without_desktop_fallback(self):
        mod = self.load()
        calls = []
        def handler(request):
            payload = json.loads(request.content);calls.append(payload)
            return httpx.Response(200, json={'jsonrpc':'2.0','id':1,'result':{'content':[{'type':'text','text':json.dumps({'success':True,'message':'Message sent'})}]}})
        out = await mod.send_mail('购物提醒', '明确条件及商品', configuration=self.config(), transport=httpx.MockTransport(handler))
        self.assertEqual(out['status'], 'accepted')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['params']['name'], 'sendMail')
        self.assertEqual(calls[0]['params']['arguments']['to'], self.config()['recipient'])
        self.assertNotIn('osascript', Path(mod.__file__).read_text())

    async def test_compose_review_is_not_reported_sent(self):
        mod = self.load()
        def handler(request):return httpx.Response(200,json={'id':1,'result':{'content':[{'type':'text','text':json.dumps({'success':True,'message':'Compose window opened'})}]}})
        out = await mod.send_mail('x','y',configuration=self.config(),transport=httpx.MockTransport(handler))
        self.assertEqual(out['status'], 'failed')
        self.assertEqual(out['receipt'], 'mail_review_required_not_sent')

    async def test_send_timeout_is_unknown_and_single_attempt(self):
        mod=self.load();calls=[]
        def handler(request):calls.append(1);raise httpx.ReadTimeout('synthetic')
        out=await mod.send_mail('x','y',configuration=self.config(),transport=httpx.MockTransport(handler))
        self.assertEqual(out['status'],'unknown');self.assertEqual(len(calls),1)

    async def test_no_config_has_no_transport_fallback(self):
        out=await self.load().send_mail('x','y',configuration={})
        self.assertEqual(out['status'],'failed')
        self.assertEqual(out['receipt'],'mail_channel_not_configured')

    async def test_server_sent_sse_receipt_parsed(self):
        mod=self.load()
        event={'id':1,'result':{'content':[{'type':'text','text':json.dumps({'success':True,'message':'Message sent'})}]}}
        def handler(request):return httpx.Response(200,text='event: message\ndata: '+json.dumps(event)+'\n\n')
        out=await mod.send_mail('x','y',configuration=self.config(),transport=httpx.MockTransport(handler))
        self.assertEqual(out['status'],'accepted')

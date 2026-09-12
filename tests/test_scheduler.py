import json
import pathlib
import sys
import threading
import time
import unittest
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import tkinter as tk

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
import request_engine as engine
import course_app


class Handler(BaseHTTPRequestHandler):
    records = []
    def log_message(self, *args):
        pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        Handler.records.append((self.path, dict(self.headers), body, time.time()))
        code = 403 if self.path.endswith('/403') else 302 if self.path.endswith('/redirect') else 200
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Location', '/unexpected')
        self.send_header('Set-Cookie', 'response_secret=DO_NOT_PRINT')
        self.end_headers()
        payload = {'status': False, 'msg': '本地模拟失败'} if self.headers.get('Cookie') == 'session=fail' else {'status': True, 'msg': ''}
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
    def setUp(self):
        Handler.records.clear()
    def plan(self, rounds=1, interval=0, target=None):
        return engine.Plan('收藏', '3389', '265899429', '阳光心理社', (
            engine.Account('a', '账号A', 'session=ok'), engine.Account('b', '账号B', 'session=fail')
        ), rounds, interval, target)
    def sender(self, tid, cid, cookie, url=None):
        return engine.send_once(tid, cid, cookie, self.url + '/collect')
    def test_real_http_body_agent_cookie_and_status(self):
        good = engine.send_once('3389', '265899429', r'Cookie: test\_token=LOCAL_ONLY', self.url + '/collect')
        self.assertIs(good.success, True)
        path, headers, body, _ = Handler.records[0]
        self.assertEqual(headers['Cookie'], 'test_token=LOCAL_ONLY')
        self.assertEqual(headers['User-Agent'], engine.USER_AGENT)
        self.assertEqual(body, b'tid=3389&courseid=265899429')
        self.assertNotIn('DO_NOT_PRINT', good.body)
        bad = engine.send_once('3389', '1', 'session=fail', self.url + '/collect')
        self.assertIs(bad.success, False)
    def test_403_and_redirect_never_success(self):
        for endpoint in ['/403', '/redirect']:
            result = engine.send_once('3389', '1', 'session=ok', self.url + endpoint)
            self.assertIs(result.success, False)
        self.assertEqual(len(Handler.records), 2)
    def test_status_is_strict_boolean(self):
        for body in ['<html>login</html>', '{}', '{"status":"true"}', '{"status":"false"}', '{"status":1}']:
            self.assertIsNone(engine.interpret(200, body, 'now', 1).success)
        self.assertIs(engine.interpret(200, '{"status":true}', 'now', 1).success, True)
    def test_multi_account_three_rounds_with_intervals(self):
        events = []
        engine.run_plan(self.plan(3, 45), threading.Event(), lambda k,v: events.append((k,v,time.perf_counter())), sender=self.sender)
        results = [v for k,v,_ in events if k == 'result']
        self.assertEqual(len(results), 6)
        self.assertEqual(Counter((r[0], r[1]) for r in results), Counter((r,k) for r in [1,2,3] for k in ['a','b']))
        self.assertEqual(Counter(r[3].success for r in results), {True:3, False:3})
        self.assertEqual(Counter(row[1]['Cookie'] for row in Handler.records), {'session=ok':3,'session=fail':3})
        for n in [1,2]:
            finish = max(t for k,v,t in events if k == 'result' and v[0] == n)
            next_start = next(t for k,v,t in events if k == 'round' and v == n+1)
            self.assertGreaterEqual(next_start - finish, 0.040)
    def test_future_start_not_early(self):
        target = time.time() + 0.18
        engine.run_plan(self.plan(target=target), threading.Event(), lambda *_: None, sender=self.sender)
        lateness = min(r[3] for r in Handler.records) - target
        self.assertGreaterEqual(lateness, 0)
        self.assertLess(lateness, 2)
        print(f'Local scheduled server arrival offset: {lateness * 1000:.1f} ms')
    def test_cancel_before_target_sends_nothing(self):
        stop = threading.Event()
        timer = threading.Timer(0.03, stop.set)
        timer.start()
        engine.run_plan(self.plan(target=time.time()+2), stop, lambda *_:None, sender=self.sender)
        timer.join()
        self.assertEqual(Handler.records, [])
    def test_more_than_eight_accounts_all_participate(self):
        accounts = tuple(engine.Account(str(i),str(i),f'session={i}') for i in range(12))
        plan = engine.Plan('收藏','3389','265899429','test',accounts,2,1)
        called, lock = [], threading.Lock()
        def sender(tid,cid,cookie,url=None):
            with lock: called.append(cookie)
            return engine.Result(200,True,'ok','{}','now',0)
        engine.run_plan(plan,threading.Event(),lambda *_:None,sender=sender)
        self.assertEqual(Counter(called),{f'session={i}':2 for i in range(12)})
    def test_timeout_is_unknown(self):
        with patch.object(engine,'post_form',side_effect=TimeoutError()):
            result=engine.send_once('3389','1','session=test')
        self.assertIsNone(result.success)
    def test_cancel_after_round_prevents_future_rounds(self):
        stop, results = threading.Event(), []
        def emit(kind, value):
            if kind == 'result':
                results.append(value)
                if len(results) == 2:
                    stop.set()
        engine.run_plan(self.plan(5,20), stop, emit, sender=self.sender)
        self.assertEqual(len(Handler.records), 2)
    def test_time_format_and_cookie_validation(self):
        stamp = engine.parse_start('2026-09-13 10:00:00.123')
        d = datetime.fromtimestamp(stamp, engine.TZ)
        self.assertEqual((d.hour, d.microsecond), (10,123000))
        for text in ['2026-09-13 10:00:00', '2026-99-13 10:00:00.123']:
            with self.assertRaises(ValueError): engine.parse_start(text)
        for raw in ['', 'bad', 'x=1\r\ny=2']:
            with self.assertRaises(ValueError): engine.normalize_cookie(raw)
    def test_catalog_pagination_and_categories(self):
        def reply(url, values, cookie):
            category, page = values['classifyid'], values['curPage']
            rows = [{'courseid':'1','coursename':'甲'}, {'courseid':'2','coursename':'乙'}]
            if category == '0':
                data = {'count':2,'crList':[rows[page-1]]}
            else:
                data = {'count':1,'crList':[rows[0 if category == '3976' else 1]]}
            return 200, json.dumps(data)
        with patch.object(engine, 'post_form', side_effect=reply):
            rows = engine.fetch_catalog('3389', 'session=test')
        self.assertEqual({r['id']:r['category'] for r in rows}, {'1':'选修课','2':'社团课'})
    def test_gui_filters_accounts_preview_results_and_cancel(self):
        root = tk.Tk()
        root.withdraw()
        try:
            gui = course_app.App(root)
            root.update_idletasks()
            self.assertEqual(len(gui.available_courses()),56)
            self.assertEqual(gui.cid.get(),'265899429')
            gui.category.set('选修课')
            self.assertEqual(len(gui.course_options),35)
            gui.category.set('社团课')
            self.assertEqual(len(gui.course_options),21)
            gui.search.set('阳光')
            self.assertEqual(len(gui.course_options),1)
            self.assertEqual(gui.cid.get(),'265899429')
            self.assertTrue(gui.insert_account('session=ok','A'))
            self.assertFalse(gui.insert_account('session=ok','duplicate'))
            gui.insert_account('session=fail','B')
            gui.rounds.set('3')
            gui.preview()
            self.assertIn('共 6 次请求',gui.output.get('1.0','end'))
            self.assertNotIn('session=ok',gui.output.get('1.0','end'))
            gui.active_plan = gui.make_plan()
            a = next(iter(gui.accounts.values()))
            gui.on_result((1,a.key,a.name,engine.interpret(200,'{"status":true}','now',5)))
            self.assertIn('成功',gui.status.get())
            gui.start_text.set(datetime.fromtimestamp(time.time()+2,engine.TZ).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])
            gui.send(True)
            self.assertTrue(gui.busy)
            gui.stop()
            deadline = time.monotonic()+2
            while gui.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            self.assertFalse(gui.busy)
            self.assertEqual(len(Handler.records),0)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)

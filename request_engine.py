"""HTTP、课程目录与可取消的多账号调度；不持久化 Cookie。"""
import concurrent.futures
from contextlib import contextmanager
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from urllib import request, parse, error

BASE = 'https://eryaxxk.chaoxing.com'
ACTION_URLS = {'报名': BASE + '/front/elective/saveJoinLog', '收藏': BASE + '/front/elective/saveCollectCourses'}
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
TZ = timezone(timedelta(hours=8))


def now_text():
    return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def parse_start(text):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}', text):
        raise ValueError('时间格式：YYYY-MM-DD HH:MM:SS.fff，例如 2026-09-13 10:00:00.000。')
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=TZ).timestamp()
    except ValueError:
        raise ValueError('日期或时间无效。') from None


def normalize_cookie(cookie):
    cookie = cookie.strip()
    if cookie.lower().startswith('cookie:'):
        cookie = cookie.partition(':')[2].strip()
    cookie = cookie.replace('\\_', '_')
    if '\r' in cookie or '\n' in cookie:
        raise ValueError('单个 Cookie 必须为一行；多个账号请使用批量添加。')
    if not cookie or '=' not in cookie:
        raise ValueError('请填写完整的 Cookie 值。')
    try:
        cookie.encode('latin-1')
    except UnicodeEncodeError:
        raise ValueError('Cookie 含无效字符，请重新复制请求标头中的值。') from None
    return cookie


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_form(url, values, cookie):
    cookie = normalize_cookie(cookie)
    tid = str(values.get('tid', values.get('id', '3389')))
    headers = {
        'User-Agent': USER_AGENT,
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest', 'Origin': BASE,
        'Referer': BASE + '/front/elective/taskDetail?' + parse.urlencode({'id': tid}),
        'Cookie': cookie,
    }
    req = request.Request(url, data=parse.urlencode(values).encode('ascii'), headers=headers, method='POST')
    try:
        response = request.build_opener(NoRedirect()).open(req, timeout=20)
    except error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read(1024 * 1024 + 1)
        text = raw[:1024 * 1024].decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
        # 仅记录正文与必要状态，永不记录请求 Cookie、Set-Cookie。
        for part in cookie.split(';'):
            value = part.strip().partition('=')[2]
            if len(value) >= 8:
                text = text.replace(value, '[凭据已隐藏]')
        if len(raw) > 1024 * 1024:
            text += '\n[响应已截断]'
        return response.code, text


@dataclass(frozen=True)
class Result:
    http: int | None
    success: bool | None
    message: str
    body: str
    started: str
    elapsed_ms: float

    @property
    def label(self):
        return '成功' if self.success is True else '失败' if self.success is False else '待核对'


def interpret(http, body, started, elapsed_ms):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = None
    if not 200 <= http < 300:
        message = f'HTTP {http}，服务器未返回成功响应'
        if isinstance(data, dict):
            message = str(data.get('msg') or data.get('message') or message)
        return Result(http, False, message, body, started, elapsed_ms)
    if isinstance(data, dict):
        status = data.get('status')
        if status is True:
            return Result(http, True, str(data.get('msg') or '服务器返回 status=true'), json.dumps(data, ensure_ascii=False, indent=2), started, elapsed_ms)
        if status is False:
            return Result(http, False, str(data.get('msg') or '服务器返回 status=false'), json.dumps(data, ensure_ascii=False, indent=2), started, elapsed_ms)
    return Result(http, None, '响应中没有布尔值 status，请在网页核对', body, started, elapsed_ms)


def send_once(tid, courseid, cookie, url=None):
    started, tick = now_text(), time.perf_counter()
    try:
        if not re.fullmatch(r'[0-9]+', tid) or not re.fullmatch(r'[0-9]+', courseid):
            raise ValueError('活动 ID 和课程 ID 必须为数字。')
        http, body = post_form(url or ACTION_URLS['报名'], {'tid': tid, 'courseid': courseid}, cookie)
        return interpret(http, body, started, (time.perf_counter() - tick) * 1000)
    except ValueError as exc:
        return Result(None, False, str(exc), '', started, (time.perf_counter() - tick) * 1000)
    except Exception:
        return Result(None, None, '网络异常或超时；请求可能已到达服务器，请在网页核对', '', started, (time.perf_counter() - tick) * 1000)


def fetch_catalog(tid, cookie):
    """通过当前账号正常可见的目录合并分类，不改变账号可见范围。"""
    courses = {}
    for classifyid, category in [('0', '未分类'), ('3976', '选修课'), ('3975', '社团课')]:
        page, received = 1, set()
        while True:
            http, text = post_form(BASE + '/front/elective/getRelatedCourseList', {
                'id': tid, 'curPage': page, 'pageSize': 200, 'classifyid': classifyid, 'keyword': ''
            }, cookie)
            try:
                data = json.loads(text)
            except ValueError:
                raise ValueError(f'目录请求返回 HTTP {http}，请检查登录状态。') from None
            if http != 200 or not isinstance(data, dict) or not isinstance(data.get('crList'), list):
                raise ValueError(f'目录请求失败（HTTP {http}），请检查登录状态。')
            before = len(received)
            for row in data['crList']:
                cid = str(row['courseid'])
                received.add(cid)
                courses[cid] = {'id': cid, 'name': row['coursename'], 'category': category,
                                'teacher': row.get('showteachers', ''), 'place': row.get('place', ''),
                                'limit': str(row.get('coursemaxpeople', ''))}
            if len(received) >= int(data.get('count', len(received))):
                break
            if len(received) == before:
                raise ValueError('目录分页不完整，保留原有课程目录。')
            page += 1
    return list(courses.values())


@dataclass(frozen=True)
class Account:
    key: str
    name: str
    cookie: str = field(repr=False)


@dataclass(frozen=True)
class Plan:
    action: str
    tid: str
    cid: str
    course_name: str
    accounts: tuple
    rounds: int
    interval_ms: int
    start_at: float | None = None


def validate_plan(plan):
    if plan.action not in ACTION_URLS:
        raise ValueError('请选择报名或收藏。')
    if not re.fullmatch(r'[0-9]+', plan.tid) or not re.fullmatch(r'[0-9]+', plan.cid):
        raise ValueError('活动 ID 和课程 ID 必须为数字。')
    if not plan.accounts:
        raise ValueError('请先添加至少一个账号 Cookie。')
    if not 1 <= plan.rounds <= 10000:
        raise ValueError('发送轮数须为 1–10000。')
    if not 0 <= plan.interval_ms <= 86400000:
        raise ValueError('轮间等待须为 0–86400000 毫秒。')
    for account in plan.accounts:
        normalize_cookie(account.cookie)


def wait_target(target, cancel):
    while not cancel.is_set():
        remaining = target - time.time()
        if remaining <= 0:
            return True
        cancel.wait(min(remaining, 0.25 if remaining > 1 else 0.02))
    return False


def wait_interval(milliseconds, cancel):
    deadline = time.perf_counter() + milliseconds / 1000
    while not cancel.is_set():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return False
        cancel.wait(remaining)
    return True


@contextmanager
def precise_timer():
    """Windows 任务期间请求 1ms 定时器分辨率，退出时配对恢复。"""
    timer = None
    try:
        import ctypes
        if hasattr(ctypes, 'windll') and ctypes.windll.winmm.timeBeginPeriod(1) == 0:
            timer = ctypes.windll.winmm
    except (AttributeError, OSError):
        pass
    try:
        yield
    finally:
        if timer is not None:
            timer.timeEndPeriod(1)


def run_plan(plan, cancel, emit, sender=send_once):
    """每轮各账号一次；上轮全部完成后等待指定毫秒，再开始下轮。"""
    try:
        validate_plan(plan)
        with precise_timer(), concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(plan.accounts))) as pool:
            # 在目标时间之前创建工作线程，降低启动时的额外延迟。
            gate = threading.Event()
            ready = [pool.submit(gate.wait) for _ in range(min(8, len(plan.accounts)))]
            gate.set()
            for task in ready:
                task.result()
            if plan.start_at is not None:
                emit('waiting', plan.start_at)
                if not wait_target(plan.start_at, cancel):
                    return
            for round_no in range(1, plan.rounds + 1):
                if cancel.is_set():
                    break
                emit('round', round_no)
                def attempt(account):
                    if cancel.is_set():
                        return None
                    return sender(plan.tid, plan.cid, account.cookie, url=ACTION_URLS[plan.action])
                pending = {pool.submit(attempt, account): account for account in plan.accounts}
                for future in concurrent.futures.as_completed(pending):
                    account = pending[future]
                    try:
                        result = future.result()
                    except Exception:
                        result = Result(None, None, '请求发生异常，请在网页核对', '', now_text(), 0)
                    if result is not None:
                        emit('result', (round_no, account.key, account.name, result))
                if round_no < plan.rounds and wait_interval(plan.interval_ms, cancel):
                    break
    except Exception as exc:
        emit('error', str(exc) if isinstance(exc, ValueError) else '任务异常结束，请检查设置。')
    finally:
        emit('done', cancel.is_set())

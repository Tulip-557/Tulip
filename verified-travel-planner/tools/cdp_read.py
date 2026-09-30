#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cdp_read.py —— CDP 只读读取器：让无头浏览器「滚起来」，取出渲染后的正文。

为什么需要它
------------
`--dump-dom` 只取**首屏静态 DOM**，两个后果：
  ① 懒加载内容（B站评论区就是典型）永远不出现；
  ② Shadow DOM 里的内容普通选择器穿不进去。
实测：同一视频，dump-dom 拿到评论 0 条，CDP 驱动滚动后拿到评论区
（用户名 / 时间 / 操作按钮）——**不是风控，是没触发加载**。

零第三方依赖
------------
内置一个最小 WebSocket 客户端（RFC 6455，客户端掩码 + 分片重组 + ping/pong）。
本机 Python 没有 websocket / websockets / requests，所以不引入它们。

只读边界（铁律 8）
------------------
本工具**只做**：打开公开页 → 滚动 → 读渲染结果。它**不做**，也不接受这些用法：
  · 下载音视频 / 导出 Cookie 或 Token / 代替用户登录
  · 绕过验证码、登录墙、风控
  · 猜接口签名（WBI 等）——那是 `social-sources.md` 列明的越线行为
要读登录后才可见的页面，走 `social_login.py` 的独立 profile，**风险落在用户账号上**。
"""

import argparse
import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

EDGE_CANDS = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    '/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium',
]


# ============================================================ 最小 WebSocket
class WSError(RuntimeError):
    pass


class MiniWS:
    """RFC 6455 客户端，够连 CDP 就行。只支持本地回环（无 TLS）。"""

    def __init__(self, host, port, path, timeout=40):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
               % (path, host, port, key))
        self.sock.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError('握手失败：连接被关闭')
            buf += chunk
        head, _, rest = buf.partition(b'\r\n\r\n')
        status = head.split(b'\r\n', 1)[0].decode('utf-8', 'replace')
        if '101' not in status:
            raise WSError('握手失败：' + status)
        self._buf = rest
        self._closed = False

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise WSError('连接中断')
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text):
        payload = text.encode('utf-8')
        frame = bytearray([0x81])                      # FIN + text
        n = len(payload)
        if n < 126:
            frame.append(0x80 | n)                     # 客户端必须掩码
        elif n < 65536:
            frame.append(0x80 | 126)
            frame += struct.pack('>H', n)
        else:
            frame.append(0x80 | 127)
            frame += struct.pack('>Q', n)
        mask = os.urandom(4)
        frame += mask
        frame += bytes(c ^ mask[i & 3] for i, c in enumerate(payload))
        self.sock.sendall(bytes(frame))

    def recv(self, timeout=None):
        """返回一条完整消息（自动重组分片、自动回 pong）。"""
        if timeout is not None:
            self.sock.settimeout(timeout)
        data = bytearray()
        while True:
            h = self._read(2)
            fin, opcode = h[0] & 0x80, h[0] & 0x0F
            n = h[1] & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            mk = self._read(4) if (h[1] & 0x80) else None
            payload = self._read(n) if n else b''
            if mk:
                payload = bytes(c ^ mk[i & 3] for i, c in enumerate(payload))
            if opcode == 0x8:                          # close
                raise WSError('被服务端关闭')
            if opcode == 0x9:                          # ping → pong
                self._send_pong(payload)
                continue
            if opcode == 0xA:                          # pong
                continue
            if opcode in (0x1, 0x2, 0x0):
                data += payload
            if fin and opcode != 0x0:
                return data.decode('utf-8', 'replace')
            if fin:
                return data.decode('utf-8', 'replace')

    def _send_pong(self, payload):
        frame = bytearray([0x8A, 0x80 | len(payload)])
        mask = os.urandom(4)
        frame += mask + bytes(c ^ mask[i & 3] for i, c in enumerate(payload))
        try:
            self.sock.sendall(bytes(frame))
        except OSError:
            pass

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self.sock.close()
            except OSError:
                pass


# ============================================================ CDP 封装
class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._pending = {}

    def call(self, method, params=None, sid=None, timeout=45):
        self._id += 1
        mid = self._id
        msg = {'id': mid, 'method': method, 'params': params or {}}
        if sid:
            msg['sessionId'] = sid
        self.ws.send(json.dumps(msg))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(1, deadline - time.time()))
            m = json.loads(raw)
            if m.get('id') == mid:
                if 'error' in m:
                    raise RuntimeError('%s → %s' % (method, m['error'].get('message')))
                return m.get('result', {})
            # 其余事件忽略（本工具不依赖事件流）
        raise TimeoutError(method + ' 超时')

    def ev(self, expr, sid, await_promise=False):
        r = self.call('Runtime.evaluate', {
            'expression': expr,
            'returnByValue': True,
            'awaitPromise': await_promise,
        }, sid)
        if r.get('exceptionDetails'):
            return {'__error__': r['exceptionDetails'].get('text', 'JS 异常')}
        res = r.get('result', {})
        return res.get('value')


def find_browser(explicit=None):
    for c in ([explicit] if explicit else []) + EDGE_CANDS:
        if c and Path(c).exists():
            return c
    return None


# 递归穿透 Shadow DOM 收集文本。
#
# ⚠️ 两个实测踩到的坑，别再犯：
# ① 不能拿 shadow root 的 `textContent` 当文本——它**包含 <style> 里的 CSS**，
#    开头就是 `:host{...}`。第一版按 `startsWith(':host')` 过滤，
#    结果把**含样式的那一层整块丢掉**（评论正文正躺在里面）。改为**按元素取叶子文本**。
# ② shadow root 是**多层嵌套**的（B站评论区实测 45 层），
#    必须边遍历边递归，不能只在外层数一遍。
DEEP_TEXT_JS = r"""
(() => {
  const out = [], seen = new Set();
  const push = (t) => {
    t = (t || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 1500 && !seen.has(t)) { seen.add(t); out.push(t); }
  };
  let roots = 0;
  const BLOCK = /^(P|LI|TD|TH|H[1-6]|DT|DD|PRE|BLOCKQUOTE|ARTICLE|SECTION)$/;
  const SKIP = { STYLE: 1, SCRIPT: 1, LINK: 1, TEMPLATE: 1, META: 1, TITLE: 1 };
  const walk = (root, depth) => {
    if (!root || depth > 20) return;
    roots++;
    let nodes = [];
    try { nodes = [...root.querySelectorAll('*')]; } catch (e) { return; }
    for (const el of nodes) {
      if (el.shadowRoot) { walk(el.shadowRoot, depth + 1); continue; }
      if (SKIP[el.tagName]) continue;
      if (el.children.length === 0) push(el.textContent);
      else if (BLOCK.test(el.tagName)) push(el.textContent);
    }
  };
  for (const el of document.querySelectorAll('*')) {
    if (el.shadowRoot) walk(el.shadowRoot, 0);
  }
  return JSON.stringify({
    lightDomText: (document.body.innerText || '').replace(/\s+/g, ' ').trim(),
    shadowRoots: roots,
    shadowTexts: out,
    title: document.title,
    url: location.href
  });
})()
"""


def read_page(browser, url, scroll=0, wait_ms=6000, scroll_step=600, scroll_wait=250,
              viewport=(1280, 900), quiet=False):
    """打开 url，按需滚动，返回渲染后的文本与结构信息。"""
    import random
    port = random.randint(9400, 9999)
    profile = tempfile.mkdtemp(prefix='cdpread_')
    env = dict(os.environ)
    env.pop('ELECTRON_RUN_AS_NODE', None)      # 否则 Electron 系二进制会跑成 node

    args = [browser, '--headless=new', '--disable-gpu', '--no-first-run',
            '--hide-scrollbars', '--mute-audio',
            '--remote-debugging-port=%d' % port,
            '--user-data-dir=' + profile,
            '--window-size=%d,%d' % viewport,
            'about:blank']
    proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    ws = None
    try:
        # 等 CDP 端口
        ver = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(
                        'http://127.0.0.1:%d/json/version' % port, timeout=1) as r:
                    ver = json.loads(r.read().decode('utf-8'))
                    break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError('浏览器启动即退出（exit %s）' % proc.returncode)
                time.sleep(0.25)
        if not ver:
            raise RuntimeError('CDP 端口未就绪（%d）' % port)

        ws_path = ver['webSocketDebuggerUrl'].split('127.0.0.1:%d' % port, 1)[1]
        ws = MiniWS('127.0.0.1', port, ws_path)
        cdp = CDP(ws)

        t = cdp.call('Target.createTarget', {'url': 'about:blank'})
        sid = cdp.call('Target.attachToTarget',
                       {'targetId': t['targetId'], 'flatten': True})['sessionId']
        cdp.call('Page.enable', {}, sid)
        cdp.call('Runtime.enable', {}, sid)

        if not quiet:
            print('浏览器 : %s' % ver.get('Browser'))
            print('协议   : %s' % ver.get('Protocol-Version'))
            print('导航   : %s' % url)
        cdp.call('Page.navigate', {'url': url}, sid)
        time.sleep(wait_ms / 1000.0)

        if scroll > 0:
            cdp.ev("""(async () => {
                for (let i = 0; i < %d; i++) {
                    window.scrollBy(0, %d);
                    await new Promise(r => setTimeout(r, %d));
                }
                return 1;
            })()""" % (scroll, scroll_step, scroll_wait), sid, await_promise=True)
            time.sleep(2.5)

        raw = cdp.ev(DEEP_TEXT_JS, sid)
        if isinstance(raw, dict) and '__error__' in raw:
            raise RuntimeError('取页面数据失败：' + raw['__error__'])
        return json.loads(raw)
    finally:
        if ws:
            ws.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            # 这里吞掉是故意的：收尾路径。terminate 失败只说明进程已经没了或还在
            # 关，退一步用 kill；kill 再失败也没有别的动作可做，而且此时抛异常会
            # 盖掉真正的错误（读取结果已经在手上了）。最外层还有
            # shutil.rmtree(ignore_errors=True) 兜住临时 profile。
            try:
                proc.kill()
            except Exception:
                pass
        import shutil
        shutil.rmtree(profile, ignore_errors=True)


# ---------------------------------------------------------------- 风控判据
# 判风控**看返回体特征**，不是 grep 关键词。
# 实测踩过两次：B站页头「登录」按钮文案被当成登录墙；
# 详情页 CSS 里的 `background-position:-3520px` 被当成风控码 `-352`。
RISK_MARKS = {
    '小红书 IP 风险': 'IP存在风险',
    '风控错误码 300012': '300012',
    '验证码中间页': '验证码中间页',
    '知乎接口风控': '暂时限制本次访问',
    'WAF 拦截页': 'submitWafFeedback',
    '账号异常': '账号异常',
}


def detect_risk(text):
    return [k for k, v in RISK_MARKS.items() if v in text]


def main():
    ap = argparse.ArgumentParser(
        description='CDP 只读读取器：驱动无头浏览器滚动取渲染后正文（含 Shadow DOM 穿透）。'
                    '只读公开页；不下载音视频、不绕风控、不猜接口签名。')
    ap.add_argument('url', help='要读的公开页面 URL')
    ap.add_argument('--scroll', type=int, default=0, help='滚动次数（默认 0，不滚动）')
    ap.add_argument('--scroll-step', type=int, default=600, help='每次滚动像素（默认 600）')
    ap.add_argument('--wait', type=int, default=6000, help='导航后等待毫秒（默认 6000）')
    ap.add_argument('--grep', help='只看含该关键词的文本块')
    ap.add_argument('--shadow-only', action='store_true', help='只输出 Shadow DOM 里的文本')
    ap.add_argument('--max', type=int, default=40, help='最多输出多少条文本块（默认 40）')
    ap.add_argument('--out', help='把完整结果写成 JSON')
    ap.add_argument('--browser', help='浏览器可执行文件路径')
    ap.add_argument('--json', action='store_true', help='输出原始 JSON')
    a = ap.parse_args()

    browser = find_browser(a.browser)
    if not browser:
        print('❌ 找不到浏览器（试过 Edge / Chrome / Chromium）。', file=sys.stderr)
        print('   用 --browser 指定可执行文件路径。', file=sys.stderr)
        return 2

    try:
        data = read_page(browser, a.url, scroll=a.scroll,
                         wait_ms=a.wait, scroll_step=a.scroll_step)
    except Exception as e:
        print('❌ 读取失败：%s' % e, file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    all_text = (data.get('lightDomText', '') + ' ' +
                ' '.join(data.get('shadowTexts', [])))
    risks = detect_risk(all_text)

    print('')
    print('标题   : %s' % (data.get('title') or '(无)')[:80])
    print('Shadow : %d 个 root ／ %d 条文本'
          % (data.get('shadowRoots', 0), len(data.get('shadowTexts', []))))
    if risks:
        print('风控   : ⚠ %s —— 按铁律 8 一次即停手，不重试、不绕' % '、'.join(risks))
    else:
        print('风控   : 未见拦截特征')

    blocks = data.get('shadowTexts', []) if a.shadow_only else (
        [data.get('lightDomText', '')] + data.get('shadowTexts', []))
    if a.grep:
        blocks = [b for b in blocks if a.grep in b]
    blocks = [b for b in blocks if b and b.strip()]

    print('')
    print('---- 正文（%d 块%s）----' % (min(len(blocks), a.max),
                                       '，已按关键词过滤' if a.grep else ''))
    for i, b in enumerate(blocks[:a.max], 1):
        print('%2d. %s' % (i, b[:300]))

    if a.out:
        Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                               encoding='utf-8')
        print('\n完整结果已写出：%s' % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
social_login.py — 用户授权登录态采集探针（阶段 3.5 · AUTH 档的可执行部分）

它做什么
  把「用你自己的登录态读社媒内容」拆成三步，每步都**不碰你的主浏览器**：
    ① --check   环境自检：有没有浏览器、CDP 通路通不通、profile 是否被占用
    ② --launch  用**独立 profile** 打开一个有头浏览器，你手动扫码登录（只此一次）
    ③ --grab    复用同一个 profile 起 headless，读**一页**的渲染后 DOM

为什么要有独立 profile
  2026-09-27 本机实测：Edge 对 Cookies 持**独占锁**（共享读报 err=32），
  且同 `--user-data-dir` 起第二个实例会直接失败——所以「复用你正在用的浏览器的登录态」
  技术上做不到。可行做法只有：开一个独立 profile 登录一次，之后一直复用**它**。

边界（与项目铁律一致，别绕）
  · **只读**：只 dump 页面 DOM，不点赞、不评论、不关注、不发帖、不改任何状态。
  · **不导出凭证**：本脚本从不读取、不解密、不写出任何 Cookie / Token / 密码。
  · **不批量**：一次 `--grab` 只读一页。批量翻页请走别处，且风险自担。
  · **不绕风控**：命中风控页立刻报错退出（码 2），不换 UA、不重试、不打验证码接口。

一句话结论（写在最前面，免得读漏）
  登录只提升**覆盖度**，不提升**证据等级**——抓到的笔记仍是 UGC，天花板 `[C·单源]`。
  而账号风险由**你**承担。值不值，你拍板。

用法
----
    python social_login.py --check                       # 环境自检
    python social_login.py --status                      # 登录态 profile 状态
    python social_login.py --launch                      # 开浏览器去扫码（小红书 / 抖音）
    python social_login.py --launch --url <登录页>        # 指定要先打开的页面
    python social_login.py --grab <笔记/搜索页 url>        # 读一页（复用登录态）
    python social_login.py --grab <url> --out page.html  # 顺带把原始 DOM 落盘

退出码：`0` 成功 ｜ `1` 执行出错 ｜ `2` 命中风控 / 环境不可用（不重试）
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ============================ 常量 ============================

PROFILE_DIR = Path.home() / '.verified-travel-planner' / 'social-profile'

# 命中的都是「风控拦截」的返回体特征——**看返回体，不看关键词**：
# 上轮就踩过 B站页面里的「登录」按钮文案被误判成登录墙。
RISK_MARKS = (
    ('安全限制', '小红书风控页（安全限制）'),
    ('IP存在风险', '小红书风控页（IP存在风险）'),
    ('300012', '小红书风控码 300012'),
    ('验证码中间页', '抖音验证码中间页'),
    ('请完成安全验证', '通用安全验证页'),
    ('环境异常', '平台环境异常页'),
    ('访问受限', '访问受限页'),
    ('当前请求存在异常', '接口级风控（知乎同款返回体）'),
    ('WAF', 'WAF 拦截页'),
)

# AUTH 档常见站点的登录入口（`--launch` 不带 --url 时按站点选）
LOGIN_URLS = {
    'xiaohongshu': 'https://www.xiaohongshu.com/explore',
    'douyin': 'https://www.douyin.com/',
    'weibo': 'https://m.weibo.cn/',
    'bilibili': 'https://www.bilibili.com/',
}

# 只读 host_key 用来判断「访问过哪些站点」的域名表（**不读 value、不解密**）
SITE_MARKS = {
    'xiaohongshu.com': '小红书',
    'douyin.com': '抖音',
    'weibo.cn': '微博',
    'weibo.com': '微博',
    'bilibili.com': 'B站',
    'zhihu.com': '知乎',
}

# 「登录态」标志 cookie：各平台**登录后才下发**，按 cookie 名判定。
# ⚠️ 别用「有没有 cookie」判登录——匿名访问同样会下发设备/风控 cookie
#    （2026-09-27 实测踩到：匿名访问过小红书/抖音/B站后，profile 里就有它们的域，
#     但一个登录态 cookie 都没有）。看 name，不看有没有。
LOGIN_COOKIE_NAMES = {
    'web_session': '小红书',
    'sessionid': '抖音',
    'sessionid_ss': '抖音',
    'SESSDATA': 'B站',
    'SUB': '微博',
    'z_c0': '知乎',
}

EDGE_CANDS = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    os.path.join(os.environ.get('LOCALAPPDATA', ''), r'Google\Chrome\Application\chrome.exe'),
]

W = 78


def find_browser():
    for p in EDGE_CANDS:
        if p and os.path.isfile(p):
            return p
    for name in ('msedge', 'chrome', 'chromium'):
        p = shutil.which(name)
        if p:
            return p
    return None


def browser_version(exe):
    """取版本字符串：优先从文件属性拿 ProductVersion（不起进程）。"""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              '(Get-Item "%s").VersionInfo.ProductVersion' % exe],
                             capture_output=True, timeout=20)
        v = (out.stdout or b'').decode('utf-8', 'replace').strip()
        if re.match(r'^\d+(\.\d+)+', v):
            return v
    except Exception:
        # 这里吞掉是故意的：版本号只是**展示信息**，不参与任何判定。
        # powershell 不可用 / 权限不足 / 超时都不该让整个探针失败，
        # 所以失败就退成「未知」——调用方按字符串展示即可。
        pass
    return '未知'


def port_busy(port=9222):
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(('127.0.0.1', port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def profile_locked():
    """独立 profile 是否正被有头浏览器占用（占用时不能再起 headless）。"""
    lock = PROFILE_DIR / 'SingletonLock'
    if lock.exists():
        return True
    # Windows 上没有 SingletonLock 文件，改用进程命令行探测
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" "
             "| Where-Object { $_.CommandLine -like '*social-profile*' }).Count"],
            capture_output=True, timeout=25)
        return (out.stdout or b'').decode('utf-8', 'replace').strip().isdigit() \
            and int((out.stdout or b'').decode().strip()) > 0
    except Exception:
        return False


def html_to_text(htm):
    h = re.sub(r'(?is)<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>', ' ', htm)
    h = re.sub(r'(?is)<!--.*?-->', ' ', h)
    h = re.sub(r'(?is)<br\s*/?>|</(p|div|li|tr|h[1-6]|section|article)>', '\n', h)
    h = re.sub(r'(?s)<[^>]+>', ' ', h)
    h = html_lib.unescape(h)
    h = re.sub(r'[ \t\u00a0]+', ' ', h)
    h = re.sub(r'\n\s*\n+', '\n', h)
    return h.strip()


def page_title(htm):
    m = re.search(r'(?is)<title[^>]*>(.*?)</title>', htm)
    return re.sub(r'\s+', ' ', m.group(1)).strip()[:90] if m else '?'


def detect_risk(htm):
    for mark, label in RISK_MARKS:
        if mark in htm:
            return label
    return None


def logged_sites():
    """判断独立 profile 里访问过哪些站点、其中哪些**真的登录了**。

    **只读 cookies 表的 host_key 与 name，从不读取 value、不解密、不导出。**
    返回 (访问过的站点, 已登录站点, 说明)；profile 被占用时无法探测。

    判登录看**标志性 cookie 名**（web_session / SESSDATA / sessionid…），
    不看「有没有 cookie」——匿名访问也会下发设备 cookie。
    """
    ck = PROFILE_DIR / 'Default' / 'Network' / 'Cookies'
    if not ck.is_file():
        return [], [], '无 Cookies 文件'
    tmp = ck.parent / '_probe_ro.sqlite'
    try:
        shutil.copy(ck, tmp)
    except Exception:
        return None, None, 'Cookies 被独占锁住（有头窗口还开着）——关掉窗口再探测'
    try:
        import sqlite3
        con = sqlite3.connect(str(tmp))
        pairs = con.execute('select host_key, name from cookies').fetchall()
        con.close()
    except Exception as exc:                                   # noqa: BLE001
        return None, None, '读取失败：%s' % type(exc).__name__
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    domains = {h for h, _ in pairs}
    names = {n for _, n in pairs}
    visited = sorted({label for dom, label in SITE_MARKS.items()
                      if any(dom in h for h in domains)})
    authed = sorted({label for cn, label in LOGIN_COOKIE_NAMES.items() if cn in names})
    return visited, authed, '共 %d 个域 / %d 条 cookie' % (len(domains), len(pairs))


# ============================ 三个子命令 ============================

def cmd_check():
    print('=' * W)
    print('登录态采集探针　环境自检')
    print('=' * W)
    exe = find_browser()
    ok = True
    if exe:
        print('  浏览器      ✅ %s' % exe)
        print('      版本     %s' % browser_version(exe))
    else:
        print('  浏览器      ❌ 未找到 Edge / Chrome 可执行文件')
        ok = False

    print('  CDP 端口     %s' % ('占用中（9222）' if port_busy(9222) else '空闲'))
    print('  独立 profile %s' % (PROFILE_DIR if PROFILE_DIR.is_dir() else '未建（先跑 --launch）'))
    ck = PROFILE_DIR / 'Default' / 'Network' / 'Cookies'
    if ck.is_file():
        print('      登录态   Cookies 文件 %d 字节（**不解密、不导出**）' % ck.stat().st_size)
        visited, authed, note = logged_sites()
        if visited is None:
            print('      登录态   %s' % note)
        else:
            print('      访问过   %s ｜ %s'
                  % ('、'.join(visited) if visited else '无', note))
            print('      已登录   %s'
                  % ('、'.join(authed) if authed
                     else '无——还没扫码，跑 --launch（匿名访问不算登录）'))
    print()
    print('  边界：只读 DOM ｜ 不导出凭证 ｜ 一次一页 ｜ 命中风控即停手')
    print('  ⚠️ 登录只提升覆盖度，**不提升证据等级**——UGC 天花板照旧 [C·单源]。')
    return 0 if ok else 2


def cmd_status():
    print('独立 profile：%s' % PROFILE_DIR)
    if not PROFILE_DIR.is_dir():
        print('  状态：未建 —— 登录态采集尚未起步，先跑 `--launch`')
        return 0
    total = sum(f.stat().st_size for f in PROFILE_DIR.rglob('*') if f.is_file())
    print('  状态：已建 ｜ 体积 %.1f MB ｜ 被占用：%s'
          % (total / 1048576, '是（有头实例在跑）' if profile_locked() else '否'))
    for name in ('Default', 'Profile 1'):
        ck = PROFILE_DIR / name / 'Network' / 'Cookies'
        if ck.is_file():
            print('  %-10s Cookies %d 字节 ｜ 最后写入 %s'
                  % (name, ck.stat().st_size,
                     time.strftime('%Y-%m-%d %H:%M', time.localtime(ck.stat().st_mtime))))
    visited, authed, note = logged_sites()
    if visited is None:
        print('  登录态探测：%s' % note)
    else:
        print('  访问过：%s（%s）' % ('、'.join(visited) if visited else '无', note))
        print('  已登录：%s' % ('、'.join(authed) if authed
                              else '无——匿名访问不算登录'))
        if not authed:
            print('  → 跑 `--launch` 扫码登录；登录前读小红书/抖音必被风控拦。')
    print()
    print('  提醒：登录态会过期。读不到内容时先跑 `--launch` 重新扫码，别急着重试。')
    return 0


def cmd_launch(url, site, wait):
    exe = find_browser()
    if not exe:
        print('❌ 未找到浏览器可执行文件')
        return 2
    if profile_locked():
        print('❌ 独立 profile 已被占用——先关掉那个窗口再来。')
        return 2
    target = url or LOGIN_URLS.get(site or 'xiaohongshu', LOGIN_URLS['xiaohongshu'])
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print('=' * W)
    print('打开独立浏览器窗口，请手动扫码登录')
    print('=' * W)
    print('  profile : %s' % PROFILE_DIR)
    print('  起始页  : %s' % target)
    print()
    print('  请在这个窗口里完成登录（扫码 / 密码都行，脚本不接触你的账号信息）。')
    print('  登录完成后**关掉这个窗口**，然后跑 --grab 读页面。')
    print('  ⚠️ 只读浏览，不要在这个窗口里做发布/互动，也不要开自动化批量。')
    print()
    proc = subprocess.Popen([exe, '--user-data-dir=' + str(PROFILE_DIR),
                             '--no-first-run', '--no-default-browser-check',
                             target])
    print('  已启动（PID %d）。' % proc.pid)
    print('  这个窗口是**独立**的，不影响你日常用的浏览器。')
    return 0


def cmd_grab(url, out, budget, wait):
    exe = find_browser()
    if not exe:
        print('❌ 未找到浏览器可执行文件')
        return 2
    if not PROFILE_DIR.is_dir():
        print('❌ 独立 profile 未建——先跑 `--launch` 扫码登录一次。')
        return 2
    if profile_locked():
        print('❌ 独立 profile 正被有头窗口占用（headless 无法复用）。'
              '先关掉那个窗口，或改用 --launch 直接人工浏览。')
        return 2

    print('读取 %s' % url)
    cmd = [exe, '--headless=new', '--disable-gpu', '--no-first-run',
           '--user-data-dir=' + str(PROFILE_DIR),
           '--virtual-time-budget=%d' % budget, '--dump-dom', url]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=wait)
    except subprocess.TimeoutExpired:
        print('❌ 读取超时（%ds）——页面可能过重或网络慢，可加大 --wait。' % wait)
        return 1
    dom = (r.stdout or b'').decode('utf-8', 'replace')
    print('  完成：%s ｜ DOM %d 字节 ｜ 耗时 %.1fs'
          % (page_title(dom), len(dom), time.time() - t0))

    risk = detect_risk(dom)
    if risk:
        print()
        print('⛔ 命中风控：%s' % risk)
        print('   按铁律 8——一次即停手，不换 UA、不重试、不打验证码接口。')
        print('   可能原因：登录态已过期（跑 --launch 重新扫码）或该账号/出口被限。')
        return 2

    text = html_to_text(dom)
    print('  正文 %d 字符' % len(text))
    if len(text) < 200:
        print('  ⚠️ 正文过短——可能是登录墙或 JS 未渲染完，加大 --virtual-time-budget 再看。')

    if out:
        Path(out).write_text(dom, encoding='utf-8')
        print('  原始 DOM 已写入 %s' % out)

    print()
    print('--- 正文预览（前 1200 字符）---')
    print(text[:1200])
    print('--- 预览结束 ---')
    print()
    print('⚠️ 这是 UGC，证据等级封顶 `[C·单源]`；')
    print('   票价 / 营业时间 / 车次余票 / 距离车程**一律不得**从这里取。')
    print('   写进线索卡时须记 url + 查看时间（connector_type: browser_logged_in）。')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='用户授权登录态采集探针（只读、一次一页、不导出凭证）。')
    ap.add_argument('--check', action='store_true', help='环境自检')
    ap.add_argument('--status', action='store_true', help='登录态 profile 状态')
    ap.add_argument('--launch', action='store_true', help='开独立浏览器窗口供你扫码登录')
    ap.add_argument('--grab', metavar='URL', help='复用登录态读一页（headless dump-dom）')
    ap.add_argument('--url', help='--launch 时先打开的页面')
    ap.add_argument('--site', choices=sorted(LOGIN_URLS), help='--launch 的登录站点预设')
    ap.add_argument('--out', help='--grab 时把原始 DOM 落盘到该路径')
    ap.add_argument('--virtual-time-budget', type=int, default=9000,
                    help='--grab 的渲染等待毫秒数（默认 9000）')
    ap.add_argument('--wait', type=int, default=90, help='--grab 的进程超时秒数（默认 90）')
    args = ap.parse_args()

    if args.check:
        return cmd_check()
    if args.status:
        return cmd_status()
    if args.launch:
        return cmd_launch(args.url, args.site, args.wait)
    if args.grab:
        return cmd_grab(args.grab, args.out, args.virtual_time_budget, args.wait)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
shoot.py — 用本机浏览器给路书截图 + 测响应式横向溢出（C 组第 18 / 20 项）

为什么需要它
------------
第 18 项要求「四档视口（320/390/768/1280）无横向溢出」，第 20 项要求
「页面渲染截图亲验」。原方案依赖 `agent-browser`，而它在本机没装、
也没有 Chromium 缓存 —— 于是这两项长期空着。

但本机有 **Microsoft Edge**（Chromium 内核，自带无头模式）。不用装任何东西。

⚠️ 踩过的坑（最重要的一节）
---------------------------
Edge 无头模式有 **约 496px 的最小视口**：`--window-size=320,900` 拿到的
`innerWidth` 是 **496**，而截图被**裁剪**到 320 —— 看起来就像「页面在
320px 下横向溢出了」，**实际是工具在裁图，不是页面的毛病**。

实测（--dump-dom 读 innerWidth）：

    指定 320  -> innerWidth=496      <- 被抬到最小值
    指定 390  -> innerWidth=496
    指定 496  -> innerWidth=496
    指定 768  -> innerWidth=742      <- 另有 26px 窗口装饰开销
    指定 1280 -> innerWidth=1254

**别把工具的裁剪当成页面的 bug。** 这一条如果不先验，报出去的结论就是错的。

所以本脚本分两条路：

* **溢出检测** —— 把路书塞进指定宽度的 `<iframe>` 里。media query 按 iframe
  尺寸生效，绕开最小视口限制；再用 `scrollWidth > clientWidth` 精确判定。
  需要 `--virtual-time-budget`，否则 dump 会在异步测量前就发生。
* **截图** —— 只用 ≥ 最小视口的宽度（默认 768 / 1280）。

用法
----
    python tools/shoot.py 路书.html                    # 溢出探针（四档）
    python tools/shoot.py 路书.html --shot             # 额外截图到 ./shots/
    python tools/shoot.py 路书.html --widths 320,414   # 自定义档位
    python tools/shoot.py 路书.html --json             # 结构化输出
    python tools/shoot.py 路书.html --browser <exe>    # 指定浏览器

退出码：0 = 四档均无溢出 ｜ 1 = 有溢出 ｜ 2 = 环境不可用（找不到浏览器）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 无头浏览器的最小可用视口（低于它的宽度会被抬高，见文件头说明）
MIN_VIEWPORT = 496
# 窗口装饰开销：指定宽 - 实际 innerWidth
CHROME_DECORATION = 26

PROBE_TMPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<div id="out">pending</div>
<iframe id="f" width="320" height="1200" style="border:0;display:block"></iframe>
<script>
var WIDTHS = %(widths)s;
var results = [], f = document.getElementById('f'), i = 0;
function next() {
  if (i >= WIDTHS.length) {
    document.getElementById('out').textContent = 'RESULT|' + results.join('|');
    return;
  }
  var w = WIDTHS[i++];
  f.width = w;
  f.src = %(uri)s;
  setTimeout(measure, 1500);
}
function measure() {
  try {
    var d = f.contentDocument;
    if (!d) { results.push(f.width + 'px: ERR 读不到 iframe 文档'); }
    else {
      var sw = d.documentElement.scrollWidth;
      var cw = d.documentElement.clientWidth;
      results.push(f.width + 'px: scrollW=' + sw + ' clientW=' + cw +
                   ' ' + (sw > cw ? 'OVERFLOW' : 'ok'));
    }
  } catch (e) { results.push(f.width + 'px: ERR ' + e.message); }
  setTimeout(next, 200);
}
next();
</script></body></html>
"""

RESULT_RE = re.compile(r'(\d+)px: scrollW=(\d+) clientW=(\d+) (ok|OVERFLOW)')

BROWSER_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
]


def find_browser(explicit=None):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    env = os.environ.get('VP_BROWSER', '').strip()
    if env and os.path.exists(env):
        return env
    for p in BROWSER_CANDIDATES:
        if os.path.exists(p):
            return p
    for name in ('msedge', 'chrome', 'chromium', 'chromium-browser', 'google-chrome'):
        found = shutil.which(name)
        if found:
            return found
    return None


def _base_flags():
    return ['--headless=new', '--disable-gpu', '--no-first-run',
            '--no-default-browser-check', '--disable-extensions',
            '--hide-scrollbars']


def probe_overflow(browser, html_path, widths, timeout=None):
    """把路书装进 iframe，逐档测 scrollWidth 是否超过 clientWidth。"""
    # as_uri() 会把中文路径百分号编码 —— file:// 下中文不编码会加载失败
    uri = json.dumps(Path(html_path).resolve().as_uri())
    probe = PROBE_TMPL % {'widths': json.dumps(widths), 'uri': uri}
    fd, tmp = tempfile.mkstemp(suffix='.html', prefix='vp_probe_')
    try:
        with io.open(fd, 'w', encoding='utf-8') as fh:
            fh.write(probe)
        cmd = [browser] + _base_flags() + [
            '--allow-file-access-from-files',
            '--window-size=1600,1200',
            '--virtual-time-budget=%d' % (1500 * len(widths) + 6000),
            '--dump-dom', 'file:///' + tmp.replace('\\', '/'),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout or (30 + 4 * len(widths)))
        out = proc.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return [], 'timeout'
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    rows = []
    for m in RESULT_RE.finditer(out):
        w, sw, cw, verdict = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        rows.append({'width': w, 'scroll_width': sw, 'client_width': cw,
                     'overflow': verdict == 'OVERFLOW', 'delta': sw - cw})
    # 同一档可能因重复渲染出现多次，取每条宽度最后一次
    dedup = {}
    for r in rows:
        dedup[r['width']] = r
    return [dedup[w] for w in widths if w in dedup], None


def shoot(browser, html_path, width, height, out_path):
    """截一张图。宽度需 >= MIN_VIEWPORT，否则会得到被裁剪的假象。"""
    if width < MIN_VIEWPORT:
        return None, ('宽度 %d < 无头浏览器最小视口 %d，截图会被裁剪而不是缩放 —— '
                      '要测窄屏请用溢出探针，不要看截图' % (width, MIN_VIEWPORT))
    cmd = [browser] + _base_flags() + [
        '--screenshot=' + os.path.abspath(out_path).replace('\\', '/'),
        '--window-size=%d,%d' % (width, height),
        Path(html_path).resolve().as_uri(),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path, None
    return None, '未生成截图'


def main():
    ap = argparse.ArgumentParser(description='路书截图 + 响应式横向溢出检测（无头浏览器）')
    ap.add_argument('html')
    ap.add_argument('--widths', default='320,390,768,1280',
                    help='溢出探针的视口档位，逗号分隔（默认 320,390,768,1280）')
    ap.add_argument('--shot', action='store_true', help='额外截图')
    ap.add_argument('--shot-widths', default='768,1280',
                    help='截图档位，须 >= %d（默认 768,1280）' % MIN_VIEWPORT)
    ap.add_argument('--height', type=int, default=1400, help='截图高度（默认 1400）')
    ap.add_argument('--outdir', default='shots', help='截图输出目录（默认 shots/）')
    ap.add_argument('--browser', default=None, help='浏览器可执行文件路径')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    if not os.path.isfile(a.html):
        print('文件不存在：%s' % a.html)
        return 2
    browser = find_browser(a.browser)
    if not browser:
        print('[!] 找不到 Edge / Chrome —— 本脚本依赖 Chromium 系浏览器的无头模式。')
        print('    可用 --browser <路径> 指定，或设 VP_BROWSER 环境变量。')
        return 2

    widths = [int(x) for x in a.widths.split(',') if x.strip()]
    rows, err = probe_overflow(browser, a.html, widths)

    shots = {}
    if a.shot:
        os.makedirs(a.outdir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(a.html))[0]
        for w in [int(x) for x in a.shot_widths.split(',') if x.strip()]:
            out = os.path.join(a.outdir, '%s_%d.png' % (stem, w))
            p, e = shoot(browser, a.html, w, a.height, out)
            shots[w] = {'path': p, 'error': e}

    bad = [r for r in rows if r['overflow']]

    if a.json:
        print(json.dumps({'browser': browser, 'html': a.html,
                          'overflow': rows, 'shots': shots,
                          'probe_error': err, 'ok': not bad},
                         ensure_ascii=False, indent=2))
        return 1 if bad else 0

    print('=' * 74)
    print('路书视口探针 —— %s' % os.path.basename(a.html))
    print('  浏览器 %s' % browser)
    print('=' * 74)
    if err:
        print('  [!] 探针失败：%s' % err)
        return 2
    if not rows:
        print('  [!] 没读到任何测量结果 —— iframe 可能被跨源策略拦住。')
        return 2
    print('\n  档位       scrollW  clientW  差  判定')
    for r in rows:
        print('  %-8s %7d %8d %+4d  %s'
              % ('%dpx' % r['width'], r['scroll_width'], r['client_width'],
                 r['delta'], '溢出' if r['overflow'] else 'ok'))
    if bad:
        print('\n  [X] %d 档有横向溢出（第 18 项不通过）' % len(bad))
        for r in bad:
            print('      %dpx 下内容需要 %dpx，可用 %dpx —— 多出 %dpx'
                  % (r['width'], r['scroll_width'], r['client_width'], r['delta']))
    else:
        print('\n  [OK] 四档均无横向溢出')
    print('  注：探针已隐藏滚动条，对应移动端（覆盖式滚动条）；桌面窄窗会多占 15px。')

    if shots:
        print('\n  截图（宽度 >= %d 才可信，更窄的会被裁剪）' % MIN_VIEWPORT)
        for w, info in sorted(shots.items()):
            if info['path']:
                print('    %-6s OK   %s' % ('%dpx' % w, info['path']))
            else:
                print('    %-6s 失败 %s' % ('%dpx' % w, info['error']))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())

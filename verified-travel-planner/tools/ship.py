#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键回归 —— 发布前 / 大改后跑一遍，把「九道闸门 + 基线」从人肉清单变成一条命令。

为什么要有它：东莞那一轮，这套检查是我**手敲**的七条命令，敲漏一条就带着问题
交付。闸门再多，没人一条不落地跑也是白搭——所以把清单固化成脚本，并且
**FAIL 就是退出码 2**，好让它在 CI 里也能当闸门用，而不是当一份「建议看看」的报告。

跑什么（阻断项 FAIL → 退出码 2）：
  1  unittest            闸门输入 / 退避行为 / 声明比对的机器断言
  2  validate_skill      自指校验（文档声称的能力 vs 代码事实）
  3  consistency         渲染产物 vs 基准骨架的版式指纹
  4  source_audit        每份事实源的证据标注纪律
  5  compact_check       每份事实源的双版本质量
  6  evaluate            每份 itinerary 的确定性可行性
  7  claim_audit         声明↔留痕比对：实采值必须如实进路书（内容真实第 1 层）
  8  echo_audit          来源回声核查：引用页里真有那个值（内容真实第 2 层；
                         无网络/风控页如实 SKIP/WARN，不假装查过）
  9  cross_check         独立源核查：双源不是互相抄的一家（内容真实第 3 层）
  10 渲染复现            由事实源重渲染，**与既有 HTML 逐字节比对**
                         （渲染器偷偷改了样式，这一条会先炸——比指纹更狠；
                         它是基线不算闸门，但同样 FAIL 即 2）

不阻断（只报出来）：
  ·  freshness          时效体检（按出发日判，本来就不阻断）
  ·  doctor             本机环境体检（缺 key / 缺 tzdata 属于环境问题，不是代码问题）

用法：
    python tools/ship.py                 # 全跑
    python tools/ship.py --quick         # 跳过渲染复现与体检（改文档时用）
    python tools/ship.py --list          # 只看识别到哪些示例，不跑
    python tools/ship.py --only 东莞     # 只跑某个示例的闸门（全局项仍跑）

零第三方依赖；退出码沿用本项目纪律：全绿 0，有 FAIL 2。
"""
from __future__ import annotations

import argparse
import atexit
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SKILL_ROOT.parent
_EXAMPLES = _REPO_ROOT / '产出示例'

# 事实源文件名形态：路书_东莞.json / 路书_成都_事实源.json
_FACTS_RE = re.compile(r'^路书_(?P<city>.+?)(?:_事实源)?\.json$')
_BADGE_RE = re.compile(r'\[[ABCD]\]')

# 硬闸门名册（validate_skill 的「硬闸门数」以本表长度为事实源）。
# 加闸门两处一起改：这里 + main() 的注册行 + 文件头清单。
# 渲染复现是基线不算闸门，不入册；freshness/doctor 本来就不阻断。
GATES = ('unittest', 'validate_skill', 'consistency', 'source_audit',
         'compact_check', 'evaluate', 'claim_audit', 'echo_audit',
         'cross_check')


def emit(line: str = '') -> None:
    print(line)


# ============================================================
# 示例识别
# ============================================================

def discover_examples() -> list:
    """扫 产出示例/ 及其一级子目录，把「事实源 + 配套产物」配成一组。"""
    found = []
    if not _EXAMPLES.exists():
        return found
    dirs = [_EXAMPLES] + sorted(p for p in _EXAMPLES.iterdir() if p.is_dir())
    for d in dirs:
        for f in sorted(d.glob('路书_*.json')):
            m = _FACTS_RE.match(f.name)
            if not m:
                continue
            city = m.group('city')
            htmls = sorted(d.glob('路书_%s*.html' % city))
            full_html = next((h for h in htmls if '精简版' not in h.name), None)
            compact_html = next((h for h in htmls if '精简版' in h.name), None)
            found.append({
                'city': city,
                'facts': f,
                'plan': d / ('final_plan_%s.json' % city),
                'itinerary': d / ('itinerary_%s.json' % city),
                'full_html': full_html,
                'compact_html': compact_html,
            })
    return found


# ============================================================
# 执行
# ============================================================

def fixture_info(ex: dict) -> dict:
    """示例是否**自述**为「渲染基线桩件」。

    有些示例（眼下是成都）存在的意义是**渲染基线**：它只有 3 天 × 3 槽、
    全文 0 枚徽章、也没有 compact 档，价值在版式指纹和逐字节复现，不在内容。
    拿 source_audit / compact_check 去打它，等于要求一份桩件具备交付级内容——
    补徽章会改产物字节，反而把基线毁了。

    判据取自**事实源自己的 meta 声明**，不是靠推断（推断 0 徽章=桩件 是危险的：
    一份真交付物漏标全部徽章时，必须 FAIL，不能被当成桩件放过去）。

    静默跳过是不允许的——所以这里返回声明，由调用方**打印出来**并在汇总里再列一次；
    标记过期（声明是桩件、实际却有徽章或双档）也要报出来。
    """
    text = ex['facts'].read_text(encoding='utf-8')
    try:
        meta = (json.loads(text).get('meta') or {})
    except ValueError:
        meta = {}
    is_fixture = meta.get('render_fixture') is True
    badges = len(_BADGE_RE.findall(text))
    compact = text.count('"compact"')
    return {
        'is_fixture': is_fixture,
        'reason': meta.get('render_fixture_reason') or '（事实源未写原因）',
        'badges': badges,
        'compact': compact,
        'stale': is_fixture and (badges > 0 or compact > 0),
    }


class Runner:
    def __init__(self, verbose: bool = False):
        self.rows = []
        self.verbose = verbose

    def run(self, name: str, args: list, blocking: bool = True,
            note: str = '') -> bool:
        proc = subprocess.run([sys.executable] + args,
                              capture_output=True, cwd=str(_SKILL_ROOT))
        ok = proc.returncode == 0
        self.rows.append({
            'name': name, 'ok': ok, 'code': proc.returncode,
            'blocking': blocking, 'note': note,
            'stdout': proc.stdout.decode('utf-8', 'replace'),
            'stderr': proc.stderr.decode('utf-8', 'replace'),
        })
        mark = 'OK  ' if ok else ('FAIL' if blocking else 'INFO')
        tail = ''
        if not ok:
            tail = self._first_problem(proc)
        emit('  [%s] %-28s %s' % (mark, name, tail))
        if not ok and self.verbose:
            for ln in (proc.stdout.decode('utf-8', 'replace').splitlines()
                       + proc.stderr.decode('utf-8', 'replace').splitlines())[-12:]:
                emit('        ' + ln)
        return ok

    @staticmethod
    def _first_problem(proc) -> str:
        """从输出里揪第一行像「原因」的话，别让人自己去翻几百行。"""
        text = (proc.stdout.decode('utf-8', 'replace')
                + proc.stderr.decode('utf-8', 'replace'))
        for ln in text.splitlines():
            s = ln.strip()
            if not s:
                continue
            if any(k in s for k in ('FAIL', 'Error', 'error', 'Traceback',
                                    'FATAL', '不合格', '未通过', 'X ')):
                return s[:70]
        return 'exit=%d' % proc.returncode


def render_reproducible(ex: dict, scratch: Path) -> tuple:
    """由事实源重渲染，与既有 HTML 逐字节比对。

    比版式指纹更硬：指纹只查骨架，这一条连一个字节的差异都不放过。
    渲染器动了样式/文案顺序，这里立刻红。
    """
    targets = [(ex['full_html'], False), (ex['compact_html'], True)]
    bad = []
    for html, compact in targets:
        if not html or not html.exists():
            continue
        dst = scratch / html.name
        args = ['tools/render_html.py', str(ex['facts']), '-o', str(dst)]
        if compact:
            args.append('--compact')
        proc = subprocess.run([sys.executable] + args,
                              capture_output=True, cwd=str(_SKILL_ROOT))
        if proc.returncode != 0:
            bad.append('%s 渲染失败(exit=%d)' % (html.name, proc.returncode))
            continue
        if not dst.exists():
            bad.append('%s 无产物' % html.name)
            continue
        if dst.read_bytes() != html.read_bytes():
            bad.append('%s 与既有产物不一致' % html.name)
    if not targets or all(not (t[0] and t[0].exists()) for t in targets):
        return None, '无 HTML 产物，跳过'
    return (not bad), ('；'.join(bad) if bad else '逐字节一致')


# ============================================================
# 主流程
# ============================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='一键回归：九道闸门 + 基线，FAIL 退出码 2')
    ap.add_argument('--quick', action='store_true',
                    help='跳过渲染复现与体检（只改文档时够用）')
    ap.add_argument('--only', help='只跑指定城市的示例闸门')
    ap.add_argument('--list', action='store_true', help='只看识别到哪些示例')
    ap.add_argument('--verbose', '-v', action='store_true', help='失败时展开输出')
    args = ap.parse_args(argv)

    examples = discover_examples()
    if args.only:
        examples = [e for e in examples if e['city'] == args.only]
        if not examples:
            emit('没有识别到城市「%s」的示例' % args.only)
            return 2

    if args.list:
        emit('识别到 %d 个示例：' % len(examples))
        for e in examples:
            emit('  %-6s 事实源 %s' % (e['city'], e['facts'].name))
            emit('         plan %s ｜ itinerary %s ｜ html %s / %s' % (
                '有' if e['plan'].exists() else '无',
                '有' if e['itinerary'].exists() else '无',
                e['full_html'].name if e['full_html'] else '无',
                e['compact_html'].name if e['compact_html'] else '无'))
        return 0

    emit('=' * 62)
    emit('一键回归 · %s' % ('quick' if args.quick else 'full'))
    emit('=' * 62)

    r = Runner(verbose=args.verbose)

    emit('[全局]')
    r.run('unittest', ['-m', 'unittest', 'discover', '-s', 'tests'])
    r.run('validate_skill 自指校验', ['tools/validate_skill.py'])
    htmls = [str(e['full_html']) for e in examples if e['full_html']]
    if htmls:
        r.run('consistency 版式指纹', ['tools/consistency.py'] + htmls)
    else:
        emit('  [ -- ] %-28s 无渲染产物' % 'consistency 版式指纹')

    fixtures = []
    # 内容真实第 2/3 层的共享抓取缓存：echo_audit 抓一次页面正文落盘，
    # cross_check 拿同一份去比转引——分两次抓既慢又对站点不礼貌。
    # 一次性数据，进程退出即清（这里不用 try/finally 缠住主流程，注册即忘）。
    echo_cache = Path(tempfile.mkdtemp(prefix='ship-echo-'))
    atexit.register(shutil.rmtree, echo_cache, True)
    for ex in examples:
        emit('[%s]' % ex['city'])
        fx = fixture_info(ex)
        if fx['is_fixture']:
            fixtures.append(ex['city'])
            emit('  [桩件] %-28s 自述为渲染基线桩件，跳过内容闸门：%s'
                 % (ex['city'], fx['reason'][:60]))
            if fx['stale']:
                emit('  [WARN] %-28s 声明是桩件，实际有 %d 枚徽章 / %d 处 compact——标记可能过期'
                     % ('标记过期？', fx['badges'], fx['compact']))
                r.rows.append({'name': '%s 桩件标记过期' % ex['city'],
                               'ok': False, 'code': 1, 'blocking': False,
                               'note': '', 'stdout': '', 'stderr': ''})
        else:
            sa = ['tools/source_audit.py', '--facts', str(ex['facts'])]
            if ex['plan'].exists():
                sa += ['--plan', str(ex['plan'])]
            r.run('source_audit 证据纪律', sa)

            cc = ['tools/compact_check.py', '--facts', str(ex['facts'])]
            if ex['full_html']:
                cc += ['--full-html', str(ex['full_html'])]
            if ex['compact_html']:
                cc += ['--compact-html', str(ex['compact_html'])]
            r.run('compact_check 双版本', cc)

        if ex['itinerary'].exists():
            r.run('evaluate 可行性',
                  ['tools/travel_planner.py', 'evaluate',
                   '--input', str(ex['itinerary'])])
        else:
            emit('  [ -- ] %-28s 无 itinerary' % 'evaluate 可行性')

        # 声明↔留痕比对（P1 v2）：有留痕就比对，没有就明说跳过——不静默。
        ca = ['tools/claim_audit.py', '--facts', str(ex['facts'])]
        has_evidence = ex['itinerary'].exists()
        if has_evidence:
            ca += ['--itinerary', str(ex['itinerary'])]
        nearby = sorted(ex['facts'].parent.glob('nearby_%s*.json' % ex['city']))
        for n in nearby:
            ca += ['--nearby', str(n)]
            has_evidence = True
        if has_evidence:
            r.run('claim_audit 声明比对', ca)
        else:
            emit('  [ -- ] %-28s 无留痕文件（itinerary/nearby 均缺）'
                 % 'claim_audit 声明比对')

        # 内容真实第 2/3 层：来源回声 + 独立源核查。两道闸门共享同一份
        # 抓取缓存（echo 抓回来的页面正文，cross 拿去比转引）——分两次
        # 抓既慢又不礼貌。没有线索卡的示例明说跳过，不静默。
        clues = sorted(ex['facts'].parent.glob('社媒线索卡_%s*.json' % ex['city']))
        if clues:
            ec = ['tools/echo_audit.py', '--clues', str(clues[0]),
                  '--facts', str(ex['facts']), '--cache', str(echo_cache)]
            r.run('echo_audit 来源回声', ec)
            xc = ['tools/cross_check.py', '--clues', str(clues[0]),
                  '--facts', str(ex['facts']), '--cache', str(echo_cache)]
            r.run('cross_check 独立源', xc)
        else:
            emit('  [ -- ] %-28s 无线索卡（社媒线索卡_<城市>.json 缺）'
                 % 'echo_audit 来源回声')
            emit('  [ -- ] %-28s 同上' % 'cross_check 独立源')

        if not args.quick:
            r.run('freshness 时效',
                  ['tools/freshness.py', str(ex['facts'])], blocking=False)

    if not args.quick:
        emit('[基线]')
        scratch = Path(tempfile.mkdtemp(prefix='ship-render-'))
        try:
            for ex in examples:
                ok, note = render_reproducible(ex, scratch)
                if ok is None:
                    emit('  [ -- ] %-28s %s' % (
                        '%s 渲染复现' % ex['city'], note))
                else:
                    r.rows.append({
                        'name': '%s 渲染复现' % ex['city'], 'ok': ok,
                        'code': 0 if ok else 1, 'blocking': True,
                        'note': note, 'stdout': '', 'stderr': ''})
                    emit('  [%s] %-28s %s' % (
                        'OK  ' if ok else 'FAIL',
                        '%s 渲染复现' % ex['city'], note))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        emit('[环境]')
        r.run('doctor 本机体检', ['tools/doctor.py'], blocking=False)

    # 汇总
    blocking_fail = [r_ for r_ in r.rows if r_['blocking'] and not r_['ok']]
    info_fail = [r_ for r_ in r.rows if not r_['blocking'] and not r_['ok']]
    emit('-' * 62)
    emit('阻断项 %d 项，FAIL %d ｜ 非阻断项异常 %d%s' % (
        len([r_ for r_ in r.rows if r_['blocking']]),
        len(blocking_fail), len(info_fail),
        ' ｜ 桩件豁免 %d 个（%s）—— 已在上面逐条打印，不是静默跳过'
        % (len(fixtures), '、'.join(fixtures)) if fixtures else ''))
    if blocking_fail:
        emit('')
        emit('FAIL（必须修，交付前不许带着走）：')
        for r_ in blocking_fail:
            emit('  · %s' % r_['name'])
    if info_fail:
        emit('')
        emit('非阻断（该看一眼，不拦交付）：')
        for r_ in info_fail:
            emit('  · %s' % r_['name'])
    emit('-' * 62)
    if blocking_fail:
        emit('结论：FAIL %d 项 —— 不许交付' % len(blocking_fail))
        return 2
    emit('结论：全绿 —— 可以交付')
    return 0


if __name__ == '__main__':
    sys.exit(main())

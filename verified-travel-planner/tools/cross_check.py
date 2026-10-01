#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cross_check.py — 独立源核查：多源交叉是「真的多个源」还是「互相抄的一家人」。

为什么需要它
------------
「[B] 双源印证」和三角验证的独立性判据，今天停在**域名不同**——
可中文旅行内容的大量「第二来源」是第一来源的转载/洗稿：携程攻略与
马蜂窝笔记互相抄， 十个站说同一句话，其实只有一个源头。域名去重挡不住
**跨域转载**。本工具补这一层（第 3 层）：把各来源**抓回来的页面正文**
做 shingle 重合比对，重合超阈值的两「源」判为同源——独立源计数随之降级。

三层真实链的位置
----------------
  claim_audit  声明 ↔ 采集留痕      （实采值如实进路书）
  echo_audit   声明 ↔ 来源网页      （页面上真有那个值）
  cross_check  来源 ↔ 来源 独立性    （本文件：双源是不是一家）

判定
----
  · 同一地点的「交叉印证」若恰恰只靠两个域、而这两域的页面正文重合
    ≥ SHINGLE_JACCARD → **转引充双源，FAIL**——[B] 徽章的地基是假的。
  · 三个以上独立域中只有两个互抄 → 仍有 ≥2 真独立源，WARN（点名）。
  · 同一地点不同来源给出**不同的数** → DISPUTED 并列摆出，**不裁决**
    （裁决是人读原话的事，见 evidence-rules.md「矛盾项并列」）。

不查（边界，写在这里是为了不被读成保证）
--------------------------------------
· 没有抓取缓存（先跑 echo_audit --cache）时只能做域名级独立性——
  内容级同源比对跳过，并**明说跳过**，不假装查过。
· 重合度低于阈值的高明洗稿、仅改写数字的转载——兜不住，归人工。
· 「独立」的定义是本工具的可操作近似（域名 + 正文重合），不等于
  采编独立；两个真独立站点同时转载同一通稿，仍会被算成两簇的边界。

用法
----
    python tools/cross_check.py --clues 社媒线索卡_X.json --cache <echo 缓存目录>
                                [--facts 路书_X.json] [--json]
退出码：0 = 无 FAIL ｜ 2 = 有 FAIL ｜ 1 = 用法/读取错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

#: 正文重合阈值：字符 4-gram Jaccard。两页四成以上四字片段重合，
#: 已不是「巧合用词」，是同文——0.7 取得偏保守，宁可少判不可错判。
SHINGLE_JACCARD = 0.7
SHINGLE_K = 4

_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '❌': '[X]', '✗': '[X]', '⚠': '[!]', '｜': '|',
})


def emit(line=''):
    try:
        print(line)
    except UnicodeEncodeError:
        try:
            print(line.translate(_SYM_FALLBACK))
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or 'ascii'
            print(line.encode(enc, 'replace').decode(enc))


def _norm_place(name: str) -> str:
    # 与 social_notes.triangulate 保持同一口径：只去空白，不做模糊合并。
    return re.sub(r'[\s\u3000]+', '', name or '').lower()


def shingles(text: str, k: int = SHINGLE_K) -> set:
    return {text[i:i + k] for i in range(0, max(0, len(text) - k + 1))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def load_cache(cache_dir: Path) -> dict:
    """读 echo_audit 的抓取缓存：{url: {status, text, …}}。"""
    out = {}
    if not cache_dir or not cache_dir.is_dir():
        return out
    for f in cache_dir.glob('*.json'):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            url = str(d.get('url') or '')
            if url:
                out[url] = d
        except ValueError:
            continue
    return out


def group_places(clues: dict) -> dict:
    """地点 → 去重后的来源列表（同 URL 只记一次；跨 note 的同 URL 合并）。"""
    places = {}
    seen = set()
    for note in clues.get('notes') or []:
        if not isinstance(note, dict):
            continue
        url = str(note.get('url') or '').strip()
        domain = urlparse(url).netloc.lower() or 'unknown'
        for ev in (note.get('place_evidence') or []):
            if not isinstance(ev, dict):
                continue
            name = str(ev.get('name') or '').strip()
            key = _norm_place(name)
            if not key:
                continue
            rec = places.setdefault(key, {
                'place': name, 'sources': {}, 'evidence': {}})
            if url and url not in rec['sources']:
                rec['sources'][url] = {
                    'domain': domain,
                    'title': str(note.get('title') or ''),
                    'text': str(ev.get('text') or ''),
                }
            for c in (note.get('claims') or []):
                if isinstance(c, dict):
                    t = str(c.get('text') or '').strip()
                    if t and (url, t) not in seen:
                        seen.add((url, t))
                        rec['evidence'].setdefault(url, []).append(t)
    return places


def pair_verdict(url_a: dict, url_b: dict, cache: dict) -> dict:
    """两个来源的内容级比对 verdict（基于缓存正文；无正文则 INDETERMINATE）。"""
    ta = cache.get(url_a) or {}
    tb = cache.get(url_b) or {}
    for side, t in (('A', ta), ('B', tb)):
        if not t:
            continue
        if t.get('status') != 'OK':
            return {'verdict': 'INDETERMINATE',
                    'note': '%s 侧页面未抓到正文（%s）'
                            % (side, t.get('status', '无缓存'))}
    if not ta or not tb:
        return {'verdict': 'INDETERMINATE', 'note': '缺抓取缓存（先跑 echo_audit --cache）'}
    j = jaccard(shingles(ta.get('text') or ''), shingles(tb.get('text') or ''))
    if j >= SHINGLE_JACCARD:
        return {'verdict': 'SAME_ORIGIN', 'jaccard': round(j, 3),
                'note': '正文重合 %.0f%%——跨域同文' % (j * 100)}
    return {'verdict': 'INDEPENDENT', 'jaccard': round(j, 3),
            'note': '正文重合 %.0f%%' % (j * 100)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='独立源核查：验证「双源印证」不是跨域转载（第3层）')
    ap.add_argument('--clues', required=True, help='社媒线索卡 JSON')
    ap.add_argument('--cache', help='echo_audit 的抓取缓存目录（内容级比对必需）')
    ap.add_argument('--facts', help='事实源 JSON（报告语境用，不参与判定）')
    ap.add_argument('--json', action='store_true', help='机读输出')
    a = ap.parse_args(argv)

    clues_path = Path(a.clues)
    if not clues_path.is_file():
        emit('线索卡不存在：%s' % clues_path)
        return 1
    try:
        clues = json.loads(clues_path.read_text(encoding='utf-8'))
    except ValueError as exc:
        emit('线索卡解析失败：%s' % exc)
        return 1

    cache = load_cache(Path(a.cache) if a.cache else None)
    places = group_places(clues)

    fails, warns, disputed, pairs = [], [], [], []
    for key, rec in sorted(places.items()):
        srcs = rec['sources']
        domains = {s['domain'] for s in srcs.values()}
        urls = sorted(srcs)
        # —— 内容级两两比对（有缓存才做）
        for i in range(len(urls)):
            for jx in range(i + 1, len(urls)):
                ua, ub = urls[i], urls[jx]
                if srcs[ua]['domain'] == srcs[ub]['domain']:
                    continue        # 同域本来就是一家，triangulate 已排除
                v = pair_verdict(ua, ub, cache)
                if v['verdict'] == 'INDETERMINATE' and not cache:
                    continue        # 整体无缓存时不再逐对刷屏
                pairs.append({'place': rec['place'], 'a': ua, 'b': ub, **v})
        # —— 转引充双源判定
        same = [p for p in pairs if p['place'] == rec['place']
                and p['verdict'] == 'SAME_ORIGIN']
        same_domains = set()
        for p in same:
            same_domains.add(urlparse(p['a']).netloc)
            same_domains.add(urlparse(p['b']).netloc)
        true_independent = len(domains - same_domains)
        if same and true_independent < 2:
            fails.append((rec['place'],
                          '交叉印证只靠 %d 个域，且其中两域正文重合超阈值'
                          '——[B] 的「双源」实为一家（真独立源 %d 个）'
                          % (len(domains), true_independent)))
        elif same:
            warns.append((rec['place'],
                          '%s 互为转载；剔除后仍有 %d 个真独立域'
                          % ('、'.join(sorted(same_domains)), true_independent)))
        # —— DISPUTED：同地不同数（并列，不裁决）
        nums_by_url = {}
        for url, texts in rec['evidence'].items():
            vals = set()
            for t in texts:
                for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(元|公里|米|分钟|小时)', t):
                    vals.add(m.group(0))
            if vals:
                nums_by_url[url] = vals
        if len(nums_by_url) >= 2:
            union = set().union(*nums_by_url.values())
            by_val = {}
            for url, vals in nums_by_url.items():
                for v in vals:
                    by_val.setdefault(v, []).append(url)
            if any(len(us) < len(nums_by_url) for us in by_val.values()):
                disputed.append((rec['place'], nums_by_url, len(nums_by_url)))

    result = {
        'clues': str(clues_path),
        'places_total': len(places),
        'pairs_checked': len(pairs),
        'fail': len(fails),
        'warn': len(warns),
        'disputed': len(disputed),
        'cache_entries': len(cache),
        'ok': not fails,
        'scope': ('域名级 + 正文重合级的独立源近似；「独立」不等于采编独立，'
                  '低重合洗稿兜不住。'),
        'not_checked': ('无缓存时的内容级同源（只能域名级）；语义等价改写；'
                        'DISPUTED 的对错裁决（机器并列，人工读原话）。'),
    }

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result['ok'] else 2

    emit('=' * 74)
    emit('独立源核查（cross_check v1）｜ 线索卡：%s ｜ 地点 %d 个 ｜ 缓存 %d 页'
         % (clues_path.name, len(places), len(cache)))
    emit('=' * 74)
    if not cache:
        emit('  [!!] 无抓取缓存——内容级同源比对**跳过**（不是通过）。')
        emit('       先跑：python tools/echo_audit.py --clues … --cache <目录>')
    for p in pairs:
        emit('  [%s] %s ｜ %s vs %s ｜ %s' % (
            {'SAME_ORIGIN': 'X ', 'INDEPENDENT': 'OK', 'INDETERMINATE': '? '}[
                p['verdict']],
            p['place'][:14],
            urlparse(p['a']).netloc[:24], urlparse(p['b']).netloc[:24],
            p.get('note', '')[:60]))
    for title, items in (('FAIL（转引充双源，[B] 地基是假的）', fails),
                         ('WARN（部分同源，仍够独立源数）', warns),
                         ('DISPUTED（同地不同数，并列待人工）', disputed)):
        if items:
            emit('-' * 74)
            emit('  %s：%d 处' % (title, len(items)))
            for row in items:
                if len(row) == 2:
                    emit('    [%s] %s' % (row[0], row[1]))
                else:
                    place, nums, nsrc = row
                    emit('    [%s] %d 个来源的数不一致：' % (place, nsrc))
                    for url, vals in sorted(nums.items()):
                        emit('        %-28s %s' % (urlparse(url).netloc[:28],
                                                   '、'.join(sorted(vals))))
    emit('-' * 74)
    if fails:
        emit('[X] FAIL %d 处——先把「双源」修成真双源（换来源或降级 [C·单源]）'
             % len(fails))
        return 2
    emit('[OK] 未发现转引充双源（WARN %d / DISPUTED %d——均不阻断，但要人看）'
         % (len(warns), len(disputed)))
    emit('    ⚠️ 独立源计数是可操作近似：域名不同 + 正文不重合 ≠ 采编独立。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

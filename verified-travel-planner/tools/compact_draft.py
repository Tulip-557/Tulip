#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
compact_draft.py — compact 半自动草稿（写作期助手，不是闸门）。

来历
----
2026-09-28 的暂缓项「P2 compact 半自动草稿」。当初的顾虑记录在案：
「自动压缩必丢约束数字（末班/停止入场），违背 C3 与『换写法而非压措辞』的
核心教训——收益 30% 但会坏掉证据链」。本工具按「消除顾虑」而不是「无视顾虑」
实现，三条铁规矩：

  ① 只出草案副产物（--out 指定的 JSON sidecar），**永不写事实源**——
     半自动的意思是：机器打底、人来核对、人自己合入，合入后 compact_check 终审。
  ② 刻意偏保守（宁长勿丢）：任何**带数字 / 带方括号 / 带粗体 / 含约束词**的段
     一律保留，可删的只有纯叙述注水段——且删掉的段连其中的数字一起列进
     dropped 清单供人工核对。C3 的教训变成结构保证：约束段根本不进删除通道。
  ③ 输出前跑不变量自检（徽章不丢失、约束数字不丢失），违者退出码 2——
     那说明本工具自己有 bug，先修工具再用。

它管不了精简率（C2/C5）：草案往往还偏长，压措辞是人的活——这正是
「换写法而非压措辞」教训的落点。本工具的价值是：**缺档单元一键起底，
且起底这一步不可能把约束数字或证据徽章弄丢。**

用法
----
    python tools/compact_draft.py --facts 路书_X.json                 # 摘要
    python tools/compact_draft.py --facts 路书_X.json --out 草案.json  # 出草案文件
    python tools/compact_draft.py --facts 路书_X.json --json          # 机读报告

退出码：0 = 草案已产出 ｜ 1 = 用法/读取错误 ｜ 2 = 不变量自检失败（工具 bug）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compact_check import CONSTRAINT_RE, OMITTED_MARK, collect, vis_len  # noqa: E402

BADGE_RE = re.compile(r'\[[ABCD](?:·[^\]]*)?\]')
NUM_RE = re.compile(r'\d+(?:\.\d+)?')
BR_RE = re.compile(r'<br\s*/?>')


def segments(full: str):
    """按 <br> 切大段、按句读切句，保持原文顺序。"""
    parts = []
    for block in BR_RE.split(full):
        parts.extend(p.strip() for p in re.split(r'(?<=[。；])', block) if p.strip())
    return parts


def _draftable(seg: str) -> bool:
    """保留判据：数字 / 方括号（徽章或注记）/ 粗体 / 约束词，命中任一即保留。"""
    return bool(re.search(r'\d', seg) or '[' in seg or '<b' in seg
                or CONSTRAINT_RE.search(seg))


def draft_one(full: str):
    """返回 (草案, dropped 清单)。保守抽取——见模块 docstring 的铁规矩 ②。"""
    keep, dropped = [], []
    for seg in segments(full):
        if _draftable(seg):
            keep.append(seg)
        else:
            dropped.append({'text': seg,
                            'numbers': NUM_RE.findall(seg)})
    draft = ''
    for seg in keep:
        draft += seg if seg[-1:] in ('。', '；', '！', '？') else seg + '。'
    return draft, dropped


def check_invariants(full: str, draft: str):
    """输出前自检：徽章不丢失、约束数字不丢失。返回违规清单（空 = 通过）。"""
    bad = []
    if BADGE_RE.search(full) and not BADGE_RE.search(draft):
        bad.append('C4 精神：full 有证据徽章，草案一个都没有')
    for seg in segments(full):
        if not CONSTRAINT_RE.search(seg):
            continue
        for n in NUM_RE.findall(seg):
            if n not in NUM_RE.findall(draft):
                bad.append('C3 精神：约束数字 %s（段：%s…）没活到草案'
                           % (n, seg[:24]))
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='compact 半自动草稿（写作期助手；不写事实源，不是闸门）')
    ap.add_argument('--facts', required=True, help='路书事实源 JSON')
    ap.add_argument('--out', help='草案 JSON 落盘路径（缺省只打印摘要）')
    ap.add_argument('--json', action='store_true', help='输出机读 JSON')
    a = ap.parse_args(argv)

    facts_path = Path(a.facts)
    if not facts_path.is_file():
        print('事实源不存在：%s' % facts_path)
        return 1
    facts_bytes = facts_path.read_bytes()
    facts = json.loads(facts_bytes.decode('utf-8'))

    items, omitted, already = [], [], 0
    violations = []
    for loc, full, _comp, dual, kind in collect(facts):
        if not full:
            continue
        if OMITTED_MARK in full:
            omitted.append(loc)
            continue
        if dual:
            already += 1
            continue
        draft, dropped = draft_one(full)
        violations += check_invariants(full, draft)
        items.append({
            'loc': loc, 'kind': kind,
            'full_len': vis_len(full), 'draft_len': vis_len(draft),
            'ratio': round(vis_len(draft) / max(vis_len(full), 1), 3),
            'badges_full': len(BADGE_RE.findall(full)),
            'badges_draft': len(BADGE_RE.findall(draft)),
            'draft': draft, 'dropped': dropped,
        })

    result = {
        'facts': str(facts_path),
        'units_total': len(items) + already + len(omitted),
        'units_draft': len(items),
        'units_already_dual': already,
        'units_omitted_marked': omitted,
        'invariant_violations': violations,
        'items': items,
        'note': ('草案仅供人工核对后自行合入事实源；本工具不写事实源；'
                 '合入后必须跑 compact_check 终审（C1-C5）——草案不等于达标。'),
    }

    if a.out:
        Path(a.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + '\n',
            encoding='utf-8')

    if facts_path.read_bytes() != facts_bytes:
        # 防御性：本工具没有任何写事实源的路径，这条是断言不是功能。
        print('[X] 事实源文件被改动——工具存在越界写入 bug，草案作废')
        return 2
    if violations:
        print('[X] 不变量自检失败 %d 处——草案可能丢徽章/约束数字，先修工具：'
              % len(violations))
        for v in violations:
            print('    %s' % v)
        return 2

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print('=' * 72)
    print('compact 半自动草稿 ｜ %s' % facts_path.name)
    print('=' * 72)
    print('  缺档单元 %d 个（已有双档 %d 跳过；声明不展开 %d 个）'
          % (len(items), already, len(omitted)))
    for it in items:
        print('  %-14s 草案 %d 字 / 全文 %d 字（%.0f%%）｜ 徽章 %d→%d ｜ 删段 %d'
              % (it['loc'], it['draft_len'], it['full_len'], it['ratio'] * 100,
                 it['badges_full'], it['badges_draft'], len(it['dropped'])))
        for d in it['dropped']:
            nums = ('｜ 含数字 ' + '、'.join(d['numbers'])) if d['numbers'] else ''
            print('      [删] %s%s' % (d['text'][:46], nums))
    if not items:
        print('  （没有缺档单元——所有单元都已写 compact 档）')
    print('-' * 72)
    if a.out:
        print('草案已写出：%s（%d 单元）' % (a.out, len(items)))
    print('提醒：%s' % result['note'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

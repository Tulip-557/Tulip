#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
review_trust.py — 口碑体检：评论区水军/推广的**信号**层（只出信号，不出结论）。

为什么需要它
------------
「只有去过的人才知道真实体验」——但评论区里混着推广与水军，且平台自己
都在持续治理（美团 2025 年处置违规评价 1161 万条），任何单一信号都会被
演化绕过。social_notes.py 已有单批内的水军信号（过短/空洞夸赞/雷同/账号
特征），本工具把体检扩到**整卡跨批**，并补四类机器可判的信号：

  · 批量复读短语   同一 4-gram 片段在 ≥3 条里出现——模板化文案的指纹
  · 推广标记       链接 / @店铺 / 团购 / 优惠券 / 私信加微信 / 电话号码
  · 立场-文本错配  stance=POSITIVE 却满是负面词（刷好评的典型形态），反之亦然
  · 同日爆发       同一核实日集中出现大量评价（活动式刷评的时间形态）

纪律（红线，与 social_notes 的 WATER_DISCLAIMER 完全一致）
--------------------------
  · **只出信号，不出结论**：命中 ≠ 水军，真实用户也可能命中；
    正确用法是「排后面看 / 标注存疑」，**不删除、不裁定、不改写**。
  · **不产出任何数字进路书**：评论里的金额/时刻/车次本来就被
    social_notes 的死线校验挡在路书外，本工具不改变这一点。
  · **不产出「调整后评分」**：把可疑剔除再算均值是 ReviewMeta 式做法，
    但那等于机器裁决了真假——与「只出信号」冲突，不做。
  · **LLM 盲区**：现有词表与统计信号都抓不住「LLM 生成的高质量变体
    好评」，这一点如实写进报告，不做能力夸大。

用法
----
    python tools/review_trust.py --clues 社媒线索卡_XX.json [--out 口碑体检_XX.json]
                                 [--json]
退出码：0 = 报告已产出（它不是闸门，不阻断交付）；1 = 用法/读取错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location('social_notes', _TOOLS / 'social_notes.py')
social_notes = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(social_notes)

_SYM_FALLBACK = str.maketrans({'✅': '[OK]', '❌': '[X]', '⚠': '[!]', '｜': '|'})


def emit(line=''):
    try:
        print(line)
    except UnicodeEncodeError:
        try:
            print(line.translate(_SYM_FALLBACK))
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or 'ascii'
            print(line.encode(enc, 'replace').decode(enc))


# ------------------------------------------------------------ 信号件

#: 推广行为标记（比 _PROMO_WORDS 更硬的「带货」信号）。
PROMO_ACT_RE = re.compile(
    r'https?://|@[^\s]{2,12}|团购|优惠券|折扣码|优惠码|私信|加微信|微信号|'
    r'公众号「|代订|代拍|戳我|点击链接|评论区置顶')

#: 负面词（立场错配判据用）——只做词面，不做语义。
NEGATIVE_WORDS = (
    '踩雷', '避雷', '别去', '不推荐', '失望', '差评', '坑', '脏', '吵',
    '态度差', '排队长', '排队两', '等了', '退票', '不值', '后悔')

#: 模板化复读的判据：同一 4-gram 在整批 ≥3 条出现才算（两条撞车是巧合）。
REPEAT_MIN_HITS = 3
REPEAT_K = 4
#: 常用虚词不成指纹——含任一虚词字的 4-gram 不计（「的话就是」「可以看看」类）。
REPEAT_STOP = set('的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动自己也你们去说好')


def _shingles(text: str, k: int = REPEAT_K) -> Counter:
    t = re.sub(r'\s+', '', text)
    return Counter(t[i:i + k] for i in range(len(t) - k + 1))


def repeat_phrases(reviews) -> list:
    """整批 4-gram 复读 Top：[(片段, 条数)]——模板化文案的指纹。"""
    holder = Counter()
    hit_reviews = Counter()
    for r in reviews:
        text = str(r.get('text') or '')
        grams = {g for g in _shingles(text)
                 if not any(ch in REPEAT_STOP for ch in g)}
        for g in grams:
            hit_reviews[g] += 1
    for g, n in hit_reviews.items():
        if n >= REPEAT_MIN_HITS:
            holder[g] = n
    return holder.most_common(8)


def promo_signals(text: str) -> list:
    hits = PROMO_ACT_RE.findall(text or '')
    return [h if isinstance(h, str) else h[0] for h in hits][:3]


def stance_mismatch(review) -> str:
    """立场-文本错配：POSITIVE 满是负面词 / NEGATIVE 却泛化夸赞。"""
    text = str(review.get('text') or '')
    stance = str(review.get('stance') or '').upper()
    neg_hits = [w for w in NEGATIVE_WORDS if w in text]
    if stance == 'POSITIVE' and len(neg_hits) >= 2:
        return '标 %s 却含 %d 个负面词（%s）——刷好评的典型形态之一' % (
            stance, len(neg_hits), '、'.join(neg_hits[:3]))
    if stance == 'NEGATIVE' and not neg_hits and any(
            w in text for w in social_notes._GENERIC_PRAISE):
        return '标 NEGATIVE 却只有泛化夸赞——疑似把好评伪装成差评的反向操作'
    return ''


def burst_days(pairs) -> list:
    """同日爆发：[(日期, 条数)]——同一核实日集中出现 ≥5 条评价。"""
    dates = Counter()
    for note, review in pairs:
        d = str(review.get('date') or note.get('checked_at') or '').strip()[:10]
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d):
            dates[d] += 1
    return [(d, n) for d, n in dates.most_common(3) if n >= 5]


# ------------------------------------------------------------ 主流程

def collect(clues: dict) -> list:
    """[(note, review)] 全卡拉平——雷同与复读要横向比，不能只看单批。"""
    out = []
    for note in clues.get('notes') or []:
        if not isinstance(note, dict):
            continue
        for review in (note.get('reviews') or []):
            if isinstance(review, dict):
                out.append((note, review))
    return out


def examine(clues: dict) -> dict:
    pairs = collect(clues)
    reviews = [r for _, r in pairs]
    report = {
        'reviews_total': len(reviews),
        'water_suspects': 0,
        'promo_hits': [],
        'mismatch_hits': [],
        'repeat_phrases': [],
        'burst_days': [],
        'signals': 'signal-only',
        'disclaimer': social_notes.WATER_DISCLAIMER,
        'llm_blindspot': ('现有词表与统计信号抓不住「LLM 生成的高质量变体好评」'
                          '——这一类只能靠来源层（第 2/3 层闸门）与人工。'),
        'ok': True,
        'scope': ('只出信号：近重复/复读/推广标记/立场错配/同日爆发/账号特征；'
                  '不裁定真假、不删评论、不产「调整后评分」。'),
        'not_checked': ('评论内容真假；LLM 生成的高质量变体文本；'
                        '平台已删评之外的隐藏水军。'),
    }
    # 复用 social_notes 的单批判定（过短/空洞/推广词/账号特征/批内雷同），
    # 但 peers 放大到**整卡**——跨批复制同样是指纹。
    flat = [r for _, r in pairs]
    for note, review in pairs:
        reasons = social_notes._review_water_signals(review, flat)
        if reasons:
            review['water_suspect'] = True
            review['water_reasons'] = reasons
            report['water_suspects'] += 1
        promo = promo_signals(str(review.get('text') or ''))
        if promo:
            review['promo_signal'] = promo
            report['promo_hits'].append('%.40s…：%s' % (
                str(review.get('text') or ''), '、'.join(promo)))
        mm = stance_mismatch(review)
        if mm:
            review['mismatch_signal'] = mm
            report['mismatch_hits'].append('%.40s…：%s' % (
                str(review.get('text') or ''), mm))
    report['repeat_phrases'] = ['%s（%d 条）' % (g, n)
                                for g, n in repeat_phrases(flat)]
    report['burst_days'] = ['%s（%d 条）' % (d, n)
                            for d, n in burst_days(pairs)]
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='口碑体检：评论区水军/推广信号（只出信号不出结论）')
    ap.add_argument('--clues', required=True, help='社媒线索卡 JSON（notes[].reviews）')
    ap.add_argument('--out', help='报告落盘路径（可选；默认只打印）')
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

    report = examine(clues)
    report['clues'] = str(clues_path)
    if a.out:
        try:
            Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                   encoding='utf-8')
            report.setdefault('saved_to', a.out)
        except OSError as exc:
            emit('报告落盘失败：%s' % exc)

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    emit('=' * 74)
    emit('口碑体检（review_trust v1）｜ %s ｜ 评论 %d 条'
         % (clues_path.name, report['reviews_total']))
    emit('=' * 74)
    if not report['reviews_total']:
        emit('  本卡 0 条评论——「未采到真人评价」本身要如实进路书，')
        emit('  不得表述为「游客普遍反映」（渲染侧已有同款机械提醒）。')
        return 0
    emit('  水军可疑（复用 social_notes 信号）：%d 条' % report['water_suspects'])
    for row in report['promo_hits'][:6]:
        emit('    [推广标记] %s' % row)
    for row in report['mismatch_hits'][:6]:
        emit('    [立场错配] %s' % row)
    for row in report['repeat_phrases'][:5]:
        emit('    [复读指纹] %s' % row)
    for row in report['burst_days']:
        emit('    [同日爆发] %s' % row)
    emit('-' * 74)
    emit('  %s' % report['disclaimer'])
    emit('  %s' % report['llm_blindspot'])
    emit('  用法：命中项「排后面看 / 标注存疑」；不删除、不裁定、不产调整后评分。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

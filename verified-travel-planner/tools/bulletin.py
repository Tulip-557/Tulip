#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bulletin.py — 行前公告扫描：把「最新消息」从随手翻变成有计划、有分级、可渲染的流程。

为什么需要它
------------
freshness.py 回答「动态数字旧不旧」，但感知不到**事件**：景区临时闭馆、
门票调价、道路管制、新开场馆、节庆活动——这些以「公告」形态出现在
官方渠道（政务网/景区官网/公众号），等临行才发现就晚了。本工具把这一层
做成三步（与 social_source → social_notes → 事实源 的既有管線同构）：

  --plan   生成 sweep 计划：按路书 POI × 事件关键词类，列出该查什么、去哪查
  --check  校验采集回来的公告条目（时戳/证据等级/死线约束），分级输出
  （渲染）  事实源 meta.bulletin → #overview 顶部「行前公告」条 + 调序提示

证据纪律（与 evidence-rules.md 完全一致，不另立规矩）
--------------------------
  · 公告属「营业时间/票价/开放状态」类 = **死线**：只认 [A]（官方页面，
    带查询时戳）。[C]/[D] 的「听说要闭馆」只是线索——标 error 退回，
    不得进路书，也不得据此改行程。
  · [B] 只在两个官方/权威源相互印证时给（如政务网 + 景区官网同说）。
  · 推荐联动只是**提示**：「XX 闭馆——当日行程建议核对备选」，
    改不改行程由人决定，本工具永不改行程。

不查（边界）
-----------
  · 本工具不联网抓取——sweep 的执行走阶段 3.5 的既有通道
    （官方发布 / 搜索索引 / 用户投喂），拿回来的条目在这里校验。
  · 不做持续监控（无服务端）：「临行前 3 天重跑一次」是 freshness 的节奏，
    公告扫描沿用同一节奏，见 SKILL.md 阶段 6。

用法
----
    python tools/bulletin.py --plan --facts 路书_XX.json
    python tools/bulletin.py --check 公告_XX.json --facts 路书_XX.json [--json]
退出码（--check）：0 = 条目全部合规 ｜ 2 = 有 error 级条目 ｜ 1 = 用法/读取错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

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


# ------------------------------------------------------------ 事件分类

#: 事件关键词类：sweep 计划按类生成查询；check 按类校验死线约束。
EVENT_CLASSES = {
    'CLOSURE':  ('闭馆', '关闭', '停开', '暂停开放', '停止开放', '检修'),
    'PRICE':    ('门票调整', '票价调整', '调价', '涨价', '降价', '免票政策', '免费开放'),
    'CONTROL':  ('管制', '限流', '预约', '交通管制', '封路', '限行'),
    'OPENING':  ('新开', '开放', '试运营', '重新开放', '恢复开放'),
    'EVENT':    ('活动', '灯会', '展览', '演出', '节庆', '市集'),
}
TYPE_RE = {k: re.compile('|'.join(v)) for k, v in EVENT_CLASSES.items()}

#: 死线类：CLOSURE / PRICE / CONTROL 触及「能不能去、多少钱、怎么走」，
#: 证据等级只认 [A]；OPENING / EVENT 是资讯类，[B]/[C] 可作线索。
DEADLINE_TYPES = ('CLOSURE', 'PRICE', 'CONTROL')

DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
#: 天标题里的噪音：日期段（9/29 周二）、天号（D1）
_TITLE_NOISE = re.compile(r'^D\d+\s*·\s*|\d{1,2}/\d{1,2}\s*周[一二三四五六日天][：:]?')
_TITLE_SPLIT = re.compile(r'[：:→、＋+]|——')


def extract_pois(facts: dict, plan_doc: dict = None) -> list:
    """路书的 POI 名册：final_plan 活动名优先，天标题拆分兜底。

    ⚠️ 不能用 `<b>加粗</b>` 抽取——实测东莞事实源里 158 个加粗串大多是
    「三人同行建议提前 40 分钟到站」这类强调短语，不是地名；拿它们去
    sweep 等于对着正文当/早餐/闭馆 搜「闭馆」，全是噪声。

    · final_plan.days[].activities[].name 是最准的名册（有 --final-plan 时）；
    · 兜底：days[].title 拆「D1 · 虎门：销烟池 → 炮台」→ [虎门, 销烟池, 炮台]
      （上海标题本身就是「城市规划展示馆 → 南京东路 → 外滩」的 POI 串）。
      滤掉含数字/价格/死线词的碎片——宁可少，不可把短语当地名。
    """
    seen, out = set(), []

    def add(name):
        name = name.strip(' 　。；，')
        if not (2 <= len(name) <= 14):
            return
        if re.search(r'\d|¥|￥|未核实|闭馆|估算|分钟|人均|车次', name):
            return
        if name not in seen:
            seen.add(name)
            out.append(name)

    if isinstance(plan_doc, dict):
        for day in (plan_doc.get('days') or []):
            for act in (day.get('activities') or []):
                if isinstance(act, dict):
                    add(str(act.get('name') or ''))
    for day in (facts.get('days') or []):
        title = str(day.get('title') or '')
        title = _TITLE_NOISE.sub('', title)
        for piece in _TITLE_SPLIT.split(title):
            add(piece)
    return out


def build_plan(facts: dict, plan_doc: dict = None) -> dict:
    """sweep 计划：POI × 事件类 的查询清单 + 渠道指引。"""
    pois = extract_pois(facts, plan_doc)
    queries = []
    for poi in pois:
        for cls, words in EVENT_CLASSES.items():
            for w in words[:2]:          # 每类取前两个代表词，控预算
                queries.append({'poi': poi, 'class': cls,
                                'query': '%s %s' % (poi, w)})
    return {
        'queries': queries,
        'query_budget': max(8, 3 * len(pois)),
        'channels': ['gov_official（政务网/文旅发布）[A]',
                     '景区/博物馆官网与官方公众号 [A]',
                     'search_index（搜索引擎索引）——只作线索 [C]'],
        'note': ('每条查询命中的公告都要带 URL 与查询日期；查不到公告本身也是'
                 '结论——「未查到变更」要记进 checked，不是白跑。'),
    }


#: 类优先级：预算不够时按「闭馆 > 调价 > 管制 > 新开 > 活动」轮转砍尾。
_CLASS_PRIORITY = {'CLOSURE': 0, 'PRICE': 1, 'CONTROL': 2, 'OPENING': 3,
                   'EVENT': 4}


def check_entries(path: Path, facts: dict, plan_doc: dict = None) -> dict:
    """校验公告条目。条目字段：poi/type/text/level/url/checked_at。"""
    d = json.loads(path.read_text(encoding='utf-8'))
    pois = set(extract_pois(facts, plan_doc))
    ok_rows, errors, warns = [], [], []
    for i, e in enumerate(d.get('entries') or [], 1):
        if not isinstance(e, dict):
            errors.append('第 %d 条不是对象' % i)
            continue
        poi = str(e.get('poi') or '').strip()
        etype = str(e.get('type') or '').strip().upper()
        text = str(e.get('text') or '').strip()
        level = str(e.get('level') or '').strip().upper()
        url = str(e.get('url') or '').strip()
        checked = str(e.get('checked_at') or '').strip()
        where = '第 %d 条（%s）' % (i, poi or '?')

        if etype not in EVENT_CLASSES:
            errors.append('%s type 非法（%s）——只能是 %s'
                          % (where, etype or '缺', '/'.join(EVENT_CLASSES)))
            continue
        if not text:
            errors.append('%s 缺 text' % where)
            continue
        if not DATE_RE.fullmatch(checked):
            errors.append('%s 缺合法查询时戳（YYYY-MM-DD）——公告没有时戳等于没查'
                          % where)
            continue
        if not url:
            warns.append('%s 无 URL——来源页可回访才能算 [A]，请补官方链接' % where)
        if poi and pois and not any(poi in p or p in poi for p in pois):
            warns.append('%s 不在路书 POI 名册里——确认是否拼错或属顺道点' % where)
        # 死线：闭馆/调价/管制只认 [A]
        if etype in DEADLINE_TYPES and level != 'A':
            errors.append('%s 属死线类（%s）但等级是 [%s]——公告类只认 [A]'
                          '（官方页面+时戳）；[C]/[D] 的传闻只能当线索去官方核实'
                          % (where, etype, level or '?'))
            continue
        # 文本与类型自洽：声明 PRICE 就该有价格信号
        if etype == 'PRICE' and not re.search(r'\d|免费|免票', text):
            warns.append('%s 声明调价但文本无数字——确认是否转述失真' % where)
        ok_rows.append({'poi': poi, 'type': etype, 'level': level, 'url': url,
                        'checked_at': checked, 'text': text})
    return {'file': str(path), 'entries': ok_rows, 'errors': errors,
            'warns': warns, 'ok': not errors,
            'scope': ('公告条目只校验「分类合法/时戳在场/死线等级够格」；'
                      '公告文本与页面的一致性由 echo_audit 对 URL 回查。'),
            'not_checked': ('公告页面本身的内容对错；未列出的事件；'
                            '「没扫到的渠道里有没有变更」。')}


def summary_for_render(res: dict) -> dict:
    """产出渲染侧要的摘要：meta.bulletin 里手填这份即可。"""
    rows = res['entries']
    by_type = {}
    for r in rows:
        by_type.setdefault(r['type'], []).append(r)
    items = []
    for r in rows:
        if r['type'] == 'CLOSURE':
            items.append({'poi': r['poi'], 'type': r['type'],
                          'text': r['text'][:60], 'checked_at': r['checked_at'],
                          'hint': '当日行程建议核对备选'})
        elif r['type'] in ('PRICE', 'CONTROL'):
            items.append({'poi': r['poi'], 'type': r['type'],
                          'text': r['text'][:60], 'checked_at': r['checked_at'],
                          'hint': '行前按官方口径复核'})
    return {'entries_total': len(rows),
            'by_type': {k: len(v) for k, v in sorted(by_type.items())},
            'items': items}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='行前公告扫描：官方渠道变更事件的计划/校验/分级（配合渲染）')
    ap.add_argument('--plan', action='store_true', help='生成 sweep 计划（需 --facts）')
    ap.add_argument('--check', help='校验采集回来的公告 JSON')
    ap.add_argument('--facts', help='路书事实源 JSON（POI 名册与日期来源）')
    ap.add_argument('--final-plan', dest='final_plan',
                    help='final_plan JSON（可选；活动名是最准的 POI 名册）')
    ap.add_argument('--json', action='store_true', help='机读输出')
    a = ap.parse_args(argv)

    if not a.plan and not a.check:
        ap.error('--plan 与 --check 至少给一个')
    if not a.facts:
        ap.error('--facts 必须给（POI 名册来源）')
    facts_path = Path(a.facts)
    if not facts_path.is_file():
        emit('事实源不存在：%s' % facts_path)
        return 1
    try:
        facts = json.loads(facts_path.read_text(encoding='utf-8'))
    except ValueError as exc:
        emit('事实源解析失败：%s' % exc)
        return 1
    plan_doc = None
    if a.final_plan:
        fp = Path(a.final_plan)
        if not fp.is_file():
            emit('final_plan 不存在：%s' % fp)
            return 1
        try:
            plan_doc = json.loads(fp.read_text(encoding='utf-8'))
        except ValueError as exc:
            emit('final_plan 解析失败：%s' % exc)
            return 1

    if a.plan:
        plan = build_plan(facts, plan_doc)
        if a.json:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            return 0
        emit('=' * 70)
        emit('行前公告 sweep 计划 ｜ POI %d 个 ｜ 查询 %d 条（预算 %d）'
             % (len(set(q['poi'] for q in plan['queries'])),
                len(plan['queries']), plan['query_budget']))
        emit('=' * 70)
        # 预算内按类优先级轮转展示：预算不够时每个 POI 都保住高优先类，
        # 而不是第一个 POI 把预算吃光、后面的地名一个查不了。
        shown = sorted(plan['queries'][:plan['query_budget'] * 3],
                       key=lambda q: (_CLASS_PRIORITY.get(q['class'], 9),))
        for q in shown[:plan['query_budget']]:
            emit('  [%-7s] %s' % (q['class'], q['query']))
        rest = len(plan['queries']) - plan['query_budget']
        if rest > 0:
            emit('  …另有 %d 条超出预算（EVENT 类靠后，按优先级砍尾）' % rest)
        emit('-' * 70)
        for c in plan['channels']:
            emit('  渠道：%s' % c)
        emit('  %s' % plan['note'])
        return 0

    # --check
    path = Path(a.check)
    if not path.is_file():
        emit('公告文件不存在：%s' % path)
        return 1
    try:
        res = check_entries(path, facts, plan_doc)
    except ValueError as exc:
        emit('公告文件解析失败：%s' % exc)
        return 1
    res['render_summary'] = summary_for_render(res)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res['ok'] else 2

    emit('=' * 70)
    emit('行前公告校验 ｜ %s ｜ 合规 %d 条' % (path.name, len(res['entries'])))
    emit('=' * 70)
    marks = {'CLOSURE': '闭馆', 'PRICE': '调价', 'CONTROL': '管制',
             'OPENING': '新开', 'EVENT': '活动'}
    for r in res['entries']:
        hint = dict((i['poi'], i.get('hint')) for i in res['render_summary']['items']).get(r['poi'])
        emit('  [A·%s] %s：%s' % (marks.get(r['type'], r['type']), r['poi'] or '—',
                                  r['text'][:52]))
        if hint:
            emit('        ↳ 提示：%s（提示不改行程，改不改由人定）' % hint)
    for w in res['warns']:
        emit('  [! ] %s' % w)
    for e in res['errors']:
        emit('  [X ] %s' % e)
    emit('-' * 70)
    if res['errors']:
        emit('[X] %d 条不合规——修条目（补时戳/补官方来源/降为线索）再交付' % len(res['errors']))
        return 2
    emit('[OK] 公告条目全部合规（WARN %d）｜ 渲染摘要已生成，手填进'
         ' meta.bulletin 即可上首屏' % len(res['warns']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

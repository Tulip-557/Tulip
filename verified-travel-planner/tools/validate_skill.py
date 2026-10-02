#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
validate_skill.py — skill 文档里「N 个 X」这类计数断言的自动校验。

为什么需要它
------------
自建 skill 最容易出的一类错，是**文档里的数字与代码事实不一致**：
写了「引擎 15 模块」实际 13 个，写了「契约文档 18 章」实际 9 章。
这类错不会被任何测试抓到（测试测的是代码，不是文档），
audit.py 也查不了（它只验路径与命令是否存在，脚本里自己声明了这一局限）。

本项目的立身之本是「每个数字都有来源」——那么**自己文档里的数字，首先必须能被机器核**。
本脚本把「数数」这一步机械化，让这类不一致在 CI 上直接失败，而不是等人肉去数。

查什么
------
逐条比对「文档断言值」与「代码/文件系统事实值」：

  断言                  事实来源
  ───────────────────  ──────────────────────────────────────────────
  引擎 N 模块            engine/travel_planner/*.py（不含 __init__.py）
  CLI N 命令             tools/travel_planner.py 中 sub.add_parser() 的调用数
  契约文档 N 章          references/data-contracts.md 的一级章节（「一、二、…」）
  references N 份        references/*.md 文件数
  交付自查 N 项          tools/ludbook_check.py 的 ITEMS 元数据长度
  机械判定 N 项          ITEMS 中 level ∉ {MANUAL, COND} 的个数
  测试用例 N 条          tests/*.py 的 def test_ 行数
  社媒渠道 N 条          tools/social_source.py 的 PROVIDERS 元素数
  人工维持 N 条          references/experience.md 的 [人工维持] 标注数
  规则 N 条 / 断言 M 处   本脚本自身（自指断言，见文末）

用法
----
    python validate_skill.py                 # 校验全部规则
    python validate_skill.py --json          # JSON 输出（供 CI 消费）
    python validate_skill.py --root <路径>    # 指定项目根（默认脚本上推两级）
    python validate_skill.py --scan          # 反向扫描：列出全部「N 个 X」断言，
                                             #   标出哪些已被规则覆盖、哪些需人工判断

退出码：0 = 全部一致 ｜ 1 = 有断言与事实不符 ｜ 2 = 执行出错

不查（须人工，见 skill-doc-code-audit 的人工清单）
--------------------------------------------------
· 讲上游 / 历史 / 示例的行里出现的数字（如「上游 12 个模块」「重建时 15 项修正全丢」），
  本脚本按上下文词**豁免**。豁免得对不对，仍须人工过一眼 —— `--scan` 会把它们列出来。
· 普通散文里的数字（「2 人同行」「3 天 2 晚」）不在规则内，不校验也不报错。
"""
from __future__ import annotations

import argparse
import ast
import glob
import hashlib
import io
import json
import os
import re
import sys

# ============================================================ 事实取值

ITEM_RE = re.compile(
    r"\(\s*'([①-⑳㉑-㉟])'\s*,\s*'([A-E])'\s*,\s*'([^']+)'\s*,\s*'(BLOCK|WARN|COND|MANUAL)'"
)


def _read(path):
    try:
        return io.open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return ''


def _skill_dir(root):
    return os.path.join(root, 'verified-travel-planner')


#: 2026-09-30 仓库说明双文件化：README.md = 对外版（GitHub 首页），WORKSPACE.md =
#: 工作区版（含公开范围表与待办）。规则扫描的「README.md」一律展开为两个文件——
#: 计数断言锚定在文件内容上，漏掉哪一个，那个文件里的数字就脱离机器看管。
#: 文件不存在时 _read 返回空串，自然跳过，不报错。
README_VARIANTS = ('README.md', 'WORKSPACE.md')


def _expand_readme(files):
    out = []
    for rel in files:
        if rel == 'README.md':
            out.extend(README_VARIANTS)
        else:
            out.append(rel)
    return out


def _parse_items(root):
    """解析 ludbook_check.py 的 ITEMS 元数据 —— 它是「覆盖声明」的事实来源。"""
    src = _read(os.path.join(_skill_dir(root), 'tools', 'ludbook_check.py'))
    return ITEM_RE.findall(src)


def fact_engine_modules(root):
    d = os.path.join(_skill_dir(root), 'engine', 'travel_planner')
    if not os.path.isdir(d):
        return None
    return len([f for f in os.listdir(d)
                if f.endswith('.py') and f != '__init__.py'])


def fact_cli_commands(root):
    p = os.path.join(_skill_dir(root), 'tools', 'travel_planner.py')
    src = _read(p)
    if not src:
        return None
    n = 0
    for line in src.splitlines():
        if line.strip().startswith('#'):
            continue                      # 注释里提到的不算
        if re.search(r'\bsub\.add_parser\(', line):
            n += 1
    return n


def fact_tool_scripts(root):
    d = os.path.join(_skill_dir(root), 'tools')
    if not os.path.isdir(d):
        return None
    return len([f for f in os.listdir(d) if f.endswith('.py')])


def fact_hard_gates(root):
    """ship.py 的 GATES 名册长度 —— 硬闸门数的唯一事实源。"""
    src = _read(os.path.join(_skill_dir(root), 'tools', 'ship.py'))
    if not src:
        return None
    m = re.search(r'^GATES\s*=\s*\(([^)]*)\)', src, re.M | re.S)
    if not m:
        return None
    return len(re.findall(r"['\"][^'\"]+['\"]", m.group(1)))


def fact_contract_chapters(root):
    p = os.path.join(_skill_dir(root), 'references', 'data-contracts.md')
    src = _read(p)
    if not src:
        return None
    return len(re.findall(r'^#\s*[一二三四五六七八九十]+、', src, re.M))


def fact_reference_docs(root):
    d = os.path.join(_skill_dir(root), 'references')
    if not os.path.isdir(d):
        return None
    return len([f for f in os.listdir(d) if f.endswith('.md')])


def fact_checklist_items(root):
    items = _parse_items(root)
    return len(items) if items else None


def fact_mechanical_items(root):
    items = _parse_items(root)
    if not items:
        return None
    return len([i for i in items if i[3] not in ('MANUAL', 'COND')])


def fact_skill_checklist(root):
    """SKILL.md 阶段 5 章节里实际列出的编号项数。

    这条是**文档内部自洽性**：若有人加了一项却忘了同步 ITEMS，
    两边会对不上 —— 而这个总数在 SKILL.md 和 ludbook_check.py 里各写了一遍。
    """
    src = _read(os.path.join(_skill_dir(root), 'SKILL.md'))
    if not src:
        return None
    lines = src.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if re.match(r'^###\s*阶段\s*5\s*·', line):
            start = i
            for j in range(i + 1, len(lines)):
                if re.match(r'^###\s*阶段\s*(5\.5|6)', lines[j]):
                    end = j
                    break
            break
    if start is None:
        return None
    return len([l for l in lines[start:end or len(lines)]
                if re.match(r'^\d+\.\s', l)])


def fact_example_figures(root):
    """成都示例 HTML 里的 figure 数（文档多处引用「4 张配图」）。"""
    src = _read(os.path.join(root, '产出示例', '路书_成都_渲染示例.html'))
    if not src:
        return None
    return len(re.findall(r'<figure', src))


def fact_engine_symbols(root):
    """engine/travel_planner/__init__.py 的 __all__ 长度。

    用 ast 静态解析而不是真 import —— 这条断言本来就是为了发现
    「改了引擎导出却忘了同步文档」，静态读一次最省事也最稳。
    """
    p = os.path.join(_skill_dir(root), 'engine', 'travel_planner', '__init__.py')
    src = _read(p)
    if not src:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == '__all__':
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return len(node.value.elts)
    return None


def fact_social_channels(root):
    """tools/social_source.py 里 PROVIDERS 的渠道条数。

    文档里那句「N 条渠道」是这套采集架构的对外承诺——
    加了渠道却忘了改文档，读者会按旧数字规划采集范围。
    """
    p = os.path.join(_skill_dir(root), 'tools', 'social_source.py')
    src = _read(p)
    if not src:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == 'PROVIDERS':
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return len(node.value.elts)
    return None


def _rules_len(src):
    """从一个工具的源码里数 `RULES = [...]` 的元素个数（事实值口径统一）。"""
    if not src:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == 'RULES':
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return len(node.value.elts)
    return None


def fact_source_audit_rules(root):
    """tools/source_audit.py 的 RULES 条数（源核验规则数）。

    2026-09-27 新增：源核验闸门上线后，「源核验 8 条规则」这句也成了对外承诺——
    加规则（或删规则）而不改文档，读者会按旧数字理解证据纪律的覆盖面。
    事实值取 RULES 表长度，而不是数 def rule_eN：表就是报告里逐条列的那份，
    改表即改覆盖面。
    """
    return _rules_len(_read(os.path.join(_skill_dir(root), 'tools', 'source_audit.py')))


def fact_compact_rules(root):
    """tools/compact_check.py 的 RULES 条数（精简版核验规则数）。

    2026-09-27 新增，与源核验同性质：闸门上线后「精简版 5 条规则」是对外承诺，
    加规则不改文档，读者会按旧数字理解精简版的把关范围。
    """
    return _rules_len(_read(os.path.join(_skill_dir(root), 'tools', 'compact_check.py')))


def fact_test_cases(root):
    """tests/*.py 里 def test_ 的定义数（= unittest 实跑用例数）。

    2026-09-30 加：实测抓到 tests/ 已扩到 49 条，README/AGENTS 仍写 19、
    记忆写 40 —— 三个数字三个样，而自指校验此前不覆盖这条。
    工程保障类数字与引擎模块数同性质：加了测试忘了改文档，读者会按旧数
    理解保障强度。口径取静态 def test_ 行数（与 unittest 计数一致），
    不真跑测试 —— 本工具只做静态事实读取，不执行项目代码。
    """
    d = os.path.join(_skill_dir(root), 'tests')
    if not os.path.isdir(d):
        return None
    n = 0
    for f in sorted(os.listdir(d)):
        if not f.endswith('.py'):
            continue
        for line in _read(os.path.join(d, f)).splitlines():
            s = line.strip()
            if s.startswith('def test_') or s.startswith('async def test_'):
                n += 1
    return n


def fact_retro_manual(root):
    """experience.md 里 `[人工维持]` 标注的条目数。

    2026-09-28 复盘协议（references/retro-protocol.md）设立时同步加这条：
    复盘出口三分支里，写不成机器判定的教训留在 experience.md 并标 [人工维持]，
    明示这是自觉区。这个数只升不降 = 规则化通道在堵塞——教训越积越多却没
    变成闸门。协议里有基线数，新增标注不同步基线，这里就会失败。
    """
    src = _read(os.path.join(_skill_dir(root), 'references', 'experience.md'))
    if not src:
        return None
    return len(re.findall(r'\[人工维持\]', src))


# ---------------------------------------------------------------- 上游归属
# 2026-09-28 加这一组，起因是一次真实的漏检：
# THIRD_PARTY_NOTICES.md 声明「原样保留 / 原样迁移」的 14 个上游文件里，**4 个其实被改过**
# （amap.py +221 行、diagnostics.py 241 行、feasibility.py +43 行、路书_基准骨架.html
# 的 CSS 改动还顺带把版式指纹从 9c3c6249a4 改成了 cd440ea068）。而当时 45/45 全绿——
# 自指校验压根不覆盖「上游归属声明 vs 实际文件」这一类。同一处漂移还在 LICENSE 里有第二份，
# 修了 THIRD_PARTY_NOTICES 却漏了 LICENSE。
#
# 所以这一组要查三件事：台账行数是否完整、台账里的哈希是否与磁盘一致、
# LICENSE 的标注是否与台账一致。**任何一处不符都必须 FAIL**——
# 这是一份要随公开仓被读的合规文件，「责任边界模糊」正是它存在的理由。

_LEDGER_ROW = re.compile(
    r'\| `([^`]+)` \| \*{0,2}(原样|已改动)\*{0,2} \| `([0-9a-f]{12})` \| (\d+) \|')
_LICENSE_LABEL = re.compile(r'([^\s|]+\.(?:py|html))\s+——\s*(\S+)')


def _ledger_rows(root):
    """解析 THIRD_PARTY_NOTICES.md 的归属核对表 → [(路径, 归属, md5, 字节)]。"""
    src = _read(os.path.join(_skill_dir(root), 'THIRD_PARTY_NOTICES.md'))
    return _LEDGER_ROW.findall(src)


def fact_upstream_asis_count(root):
    """台账里标「原样」的文件数——这些文件一个字节都不该动。"""
    return sum(1 for _, kind, _, _ in _ledger_rows(root) if kind == '原样')


def fact_upstream_ledger_mismatch(root):
    """台账里与磁盘不符的行数（含文件缺失）。声明是 0 行。"""
    skill = _skill_dir(root)
    bad = 0
    for rel, _kind, md5, size in _ledger_rows(root):
        path = os.path.join(skill, rel.replace('/', os.sep))
        try:
            with open(path, 'rb') as f:
                blob = f.read()
        except OSError:
            bad += 1
            continue
        if hashlib.md5(blob).hexdigest()[:12] != md5 or len(blob) != int(size):
            bad += 1
    return bad


def fact_upstream_label_mismatch(root):
    """LICENSE 的归属标注与台账不一致的处数（含两边文件清单对不上）。声明是 0 处。

    双向比对：LICENSE 列了台账没有的文件、台账列了 LICENSE 没写的文件，都算不一致——
    这正是 2026-09-28 那种「改了一处漏了另一处」的形状。
    """
    skill = _skill_dir(root)
    ledger = {rel: kind for rel, kind, _, _ in _ledger_rows(root)}
    src = _read(os.path.join(skill, 'LICENSE'))
    if not src:
        return len(ledger) or 1
    seen = {}
    for line in src.splitlines():
        m = _LICENSE_LABEL.search(line)
        if m:
            seen[m.group(1)] = '原样' if m.group(2).startswith('原样') else '已改动'
    return sum(1 for rel in set(ledger) | set(seen)
               if ledger.get(rel) != seen.get(rel))


def fact_description_len(root):
    """SKILL.md frontmatter 里 description 的字符数。

    客户端对它有长度上限（ZCode 是 1024，**超出整个技能被丢弃**）。
    写长了不会报错，只会让技能在那个客户端里静默消失 —— 最难查的一类故障，
    所以这个数值得盯着。
    """
    src = _read(os.path.join(_skill_dir(root), 'SKILL.md'))
    if not src:
        return None
    for line in src.split('\n'):
        if line.startswith('description:'):
            value = line[len('description:'):].strip().strip('"').strip("'")
            return len(value)
    return None


FACTS = {
    'engine_modules': fact_engine_modules,
    'cli_commands': fact_cli_commands,
    'contract_chapters': fact_contract_chapters,
    'reference_docs': fact_reference_docs,
    'checklist_items': fact_checklist_items,
    'mechanical_items': fact_mechanical_items,
    'skill_checklist': fact_skill_checklist,
    'example_figures': fact_example_figures,
    'engine_symbols': fact_engine_symbols,
    'description_len': fact_description_len,
    'social_channels': fact_social_channels,
    'retro_manual': fact_retro_manual,
    'test_cases': fact_test_cases,
}

# ============================================================ 规则表
# 每条规则 = 一组文档文件 + 一个锚定正则（捕获数字）+ 一个事实键。
# 正则必须**带上下文锚词**（如「引擎…模块」），否则会命中大量无关数字。
# skip_if：该行含这些词时判为「讲上游/历史」，豁免并单独列出，不计入失败。

_UPSTREAM = ('上游', '原样', '来源', '源自', '迁移自', '参考项目', '原项目', '原仓库')

RULES = [
    {
        'name': '引擎模块数',
        # 锚词要放宽：「引擎 13 模块」「检查引擎（13 模块）」「检查引擎（13 个模块）」都得覆盖。
        # 2026-09-27 它曾以「15」在四个文件里漂了不知多久。
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md'],
        'pattern': re.compile(r'引擎[^0-9\n]{0,8}(\d+)\s*个?模块'),
        'facts': {'engine/travel_planner/*.py 不含 __init__': fact_engine_modules},
        'skip_if': _UPSTREAM,
    },
    {
        'name': 'CLI 命令数',
        # 同一事实的四种写法：「统一 CLI，12 命令」「CLI（12 个命令）」
        # 「全部 12 个命令」「统一命令行入口（12 个命令）」
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md'],
        'pattern': re.compile(r'(?:CLI|命令行入口|命令入口)[^0-9\n]{0,8}(\d+)\s*个?命令|'
                              r'(?:看)?全部\s*(\d+)\s*个命令'),
        'facts': {'travel_planner.py 的 sub.add_parser() 数': fact_cli_commands},
        'skip_if': _UPSTREAM,
    },
    {
        'name': '契约文档章节数',
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md'],
        'pattern': re.compile(r'契约文档\s*(\d+)\s*章|文档\s*(\d+)\s*章契约'),
        'facts': {'data-contracts.md 一级章节（一、二、…）数': fact_contract_chapters},
        'skip_if': _UPSTREAM,
    },
    {
        'name': 'references 文档份数',
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md'],
        'pattern': re.compile(r'references[^\n]{0,26}?(\d+)\s*份'),
        'facts': {'references/*.md 文件数': fact_reference_docs},
        'skip_if': _UPSTREAM,
    },
    {
        'name': '交付自查总项数',
        # 两个独立事实交叉验证：ITEMS 元数据长度，与 SKILL.md 阶段 5 实际列出的编号项数。
        # 两边都写了一遍「26」——加一项只改一处，这里就会失败。
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md',
                  'verified-travel-planner/tools/ludbook_check.py'],
        'pattern': re.compile(r'(\d+)\s*项(?:机械核对|交付自查|自查)'),
        'facts': {'ludbook_check.py 的 ITEMS 长度': fact_checklist_items,
                  'SKILL.md 阶段 5 编号项数': fact_skill_checklist},
        'skip_if': _UPSTREAM + ('原为',),
    },
    {
        'name': '机械判定项数',
        'files': ['verified-travel-planner/SKILL.md',
                  'verified-travel-planner/tools/ludbook_check.py'],
        # 总项数是另一条事实，会变；这里的可选前缀不能写死 26，
        # 否则「机械判定 27 项里的 20 项」会被当成断言 27（写死过一次，实测被咬）。
        'pattern': re.compile(r'机械判定\s*(?:\d+\s*项里的\s*)?(\d+)\s*项|'
                              r'能机械判定的只有\s*(\d+)\s*项'),
        'facts': {'ITEMS 中 level 非 MANUAL/COND 的个数': fact_mechanical_items},
        'skip_if': _UPSTREAM,
    },
    {
        'name': '示例配图数',
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md'],
        'pattern': re.compile(r'成都配图\s*(\d+)\s*张|(\d+)\s*张配图'),
        'facts': {'成都示例 HTML 的 <figure> 数': fact_example_figures},
        # 2026-09-27 加「完整版 / 精简版」双渲染时撞出来的假阳性：
        # 新文案「精简版每天 1 张配图」讲的是**渲染策略**，不是成都示例有几张图，
        # 却被 `(\d+)\s*张配图` 逮住。**改闸门而不是改文案**——那句话本身是对的。
        'skip_if': _UPSTREAM + ('每天', '精简版', '折叠', '高清版'),
    },
    {
        'name': '引擎符号数',
        # 2026-09-27 加一个导出（detect_clients）就让这个数从 38 变成 39，
        # 而 README 两处都写着 38 —— 改代码导致文档数字漂移的活例子。
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md'],
        'pattern': re.compile(r'引擎\s*(\d+)\s*(?:个)?符号'),
        'facts': {'__init__.py 的 __all__ 长度': fact_engine_symbols},
        'skip_if': _UPSTREAM,
    },
    {
        'name': 'description 长度',
        'files': ['verified-travel-planner/references/client-compatibility.md'],
        'pattern': re.compile(r'`description`\s*\*\*(\d+)\s*字符\*\*'),
        'facts': {'SKILL.md 的 description 字符数': fact_description_len},
        'skip_if': (),
    },
    {
        'name': '社媒渠道数',
        # 「N 条渠道」是采集架构的对外承诺：加了渠道不改文档，
        # 读者会按旧数字规划这一趟的采集范围。
        # 2026-09-27 补：原规则只扫 SKILL.md 与 social-sources.md，且 pattern 只认
        # 「N 条渠道」——AGENTS.md 里「渠道能力矩阵 14 条」这种写法因此**漏检**过一次。
        # 两类写法都要认，且把 AGENTS.md / README.md 一并纳入扫描范围。
        'files': ['verified-travel-planner/SKILL.md',
                  'verified-travel-planner/references/social-sources.md',
                  'AGENTS.md', 'README.md'],
        'pattern': re.compile(r'(\d+)\s*条渠道'
                              r'|渠道(?:能力)?矩阵\s*[（(]?\s*(\d+)\s*条'),
        'facts': {'social_source.py 的 PROVIDERS 元素数': fact_social_channels},
        'skip_if': _UPSTREAM,
    },
    {
        'name': '源核验规则数',
        # 2026-09-27 源核验闸门（source_audit.py）上线时同步加这条：
        # 「源核验 8 条规则」是证据纪律的覆盖面承诺，与 ludbook 的项数同性质。
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md',
                  'verified-travel-planner/references/source-audit.md'],
        'pattern': re.compile(r'源核验\s*(\d+)\s*条'),
        'facts': {'source_audit.py 的 RULES 条数': fact_source_audit_rules},
        'skip_if': _UPSTREAM,
    },
    {
        'name': '精简版规则数',
        # 2026-09-27 精简版闸门（compact_check.py）上线时同步加：
        # 「精简版 5 条规则」是对外承诺的覆盖面，与源核验的 8 条同性质。
        # ⚠️ 写法上别只认一种：compact_check / 精简版核验 / 精简版 N 条规则 都要认。
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md',
                  'verified-travel-planner/references/dual-version.md'],
        'pattern': re.compile(r'精简版\s*核验[^。\n]{0,20}?(\d+)\s*条'
                              r'|compact_check[^。\n]{0,24}?(\d+)\s*条'
                              r'|精简版[^。\n]{0,16}?(\d+)\s*条规则'),
        'facts': {'compact_check.py 的 RULES 条数': fact_compact_rules},
        'skip_if': _UPSTREAM,
    },
    {
        'name': '工具脚本数',
        # tools/ 下 .py 文件数：README 目录树写「N 个命令行工具」。
        # 2026-10-01 前它是无守卫数字——echo/cross 上线时特意补上这条规则。
        'files': ['AGENTS.md', 'README.md'],
        'pattern': re.compile(r'(\d+)\s*个(?:命令行)?工具'),
        'facts': {'tools/*.py 文件数': fact_tool_scripts},
        'skip_if': _UPSTREAM,
    },
    {
        # 「N 道闸门」曾在「六道→七道」之间漂了 11 处没被发现，就因为这个数
        # 没有机器守卫。事实源是 ship.py 的 GATES 名册；中文数字（九道）与
        # 阿拉伯（9 道）都认。历史叙事（「成为第六道闸门」）走 skip_if 豁免。
        'name': '硬闸门数',
        'files': ['AGENTS.md', 'README.md', 'verified-travel-planner/SKILL.md',
                  'WORKSPACE.md', '.github/workflows/gates.yml'],
        'pattern': re.compile(r'([一二两三四五六七八九十\d]+)\s*道(?:硬)?闸门'),
        'facts': {'ship.py 的 GATES 名册长度': fact_hard_gates},
        'skip_if': _UPSTREAM + ('第六道', '第五道', '实战教训', '当时'),
    },
    {
        'name': '人工维持经验数',
        # 2026-09-28 复盘协议（references/retro-protocol.md）设立时同步加：
        # 「人工维持 N 条」是复盘出口的健康度基线——只升不降说明规则化通道堵塞。
        # ⚠️ retro-protocol.md 正文里讲机制时用「人工维持」不带数字，只有基线行带数字，
        #    否则散文里的数字会被本规则误捕。
        'files': ['verified-travel-planner/references/retro-protocol.md'],
        'pattern': re.compile(r'人工维持[^。。\n]{0,12}?(\d+)\s*条'),
        'facts': {'experience.md 的 [人工维持] 标注数': fact_retro_manual},
        'skip_if': _UPSTREAM,
    },
    {
        'name': '测试用例数',
        # 2026-09-30 实测抓到的漂移：tests/ 已扩到 49 条，README/AGENTS 仍写 19、
        # 记忆写 40 —— 三个数字三个样，而自指校验此前不覆盖这条。
        # ⚠️ 锚词认多种写法，否则改文案的瞬间规则就瞎了——与「渠道数」
        #   「工具自述」两次漏检同一个坑。已认的锚词：
        #   ① `tests/（N 条断言）`（旧写法）；②「N 条测试」（新写法）；
        #   ③ `| unittest | N 条机器断言 |`（README 闸门表写法）；
        #   ④ 目录树里 `tests/` 与「N 条断言」相隔较远的写法。
        # 2026-10-03 扩：③④ 是第三次同类漂移——README:92 写「88 条机器断言」、
        #   实为 107，长期漏网（--scan 把它列为未覆盖）。扩完实检断言 79→81。
        #   配套负向样本 tests/test_validate_skill_anchors.py（锁「认得全」与
        #   「不瞎吃」两端，规则被收窄回去时先红）。
        'files': ['AGENTS.md', 'README.md'],
        'pattern': re.compile(r'tests/[^0-9\n]{0,28}(\d+)\s*条(?:测试|断言)'
                              r'|测试套件[^0-9\n]{0,16}(\d+)\s*条(?:测试|断言)'
                              r'|unittest`?[^0-9\n]{0,12}(\d+)\s*条(?:机器)?断言'),
        'facts': {'tests/*.py 的 def test_ 行数': fact_test_cases},
        'skip_if': _UPSTREAM,
    },
    {
        # 2026-09-28 加：上游文件归属的机器核对（见上方「上游归属」注释块的漏检记录）。
        # ⚠️ 这三条**不能设 skip_if=_UPSTREAM** —— 声明行本身就含「原样」二字，
        #    设了会被当成「讲上游的历史叙述」而豁免，规则等于白写。
        'name': '上游原样文件数',
        'files': ['verified-travel-planner/THIRD_PARTY_NOTICES.md'],
        'pattern': re.compile(r'原样文件[^0-9\n]{0,4}(\d+)\s*个'),
        'facts': {'THIRD_PARTY_NOTICES.md 台账里标「原样」的行数': fact_upstream_asis_count},
    },
    {
        'name': '上游台账与磁盘不一致行数',
        # 声明值恒为 0。改过台账所列的任何文件（含「已改动」档）都要回来更新哈希，
        # 否则这里立刻非 0 → FAIL。
        'files': ['verified-travel-planner/THIRD_PARTY_NOTICES.md'],
        'pattern': re.compile(r'台账核对[^0-9\n]{0,10}?(\d+)\s*行'),
        'facts': {'台账 md5/字节与磁盘不符的行数（含文件缺失）': fact_upstream_ledger_mismatch},
    },
    {
        'name': 'LICENSE 归属标注不一致处数',
        # 声明值恒为 0。2026-09-28 的实况：THIRD_PARTY_NOTICES 修好了，LICENSE 里同一处
        # 漂移还在（4 个文件仍写着「原样」）——单文件检查抓不到这种，所以必须双向比对。
        'files': ['verified-travel-planner/THIRD_PARTY_NOTICES.md'],
        'pattern': re.compile(r'归属标注[^0-9\n]{0,10}?(\d+)\s*处'),
        'facts': {'LICENSE 标注与台账不一致的处数': fact_upstream_label_mismatch},
    },
]

# --scan 模式的通用断言正则（只用于「发现」，不用于判定）
SCAN_RE = re.compile(r'(\d+)\s*(项|个?模块|章|个?命令|份|张|条|处|人|天|个)')


# ============================================================ 自指断言
# 文档里对**本工具自身**的描述（「N 条规则覆盖 M 处断言」）同样会漂——
# 2026-09-27 实测：AGENTS.md 写着「7 条规则覆盖 31 处断言」，而脚本当时已是 9 条 / 32 处。
# 这类数两边都查不出来：audit.py 不管计数，本脚本的规则表里也不含自指。
# 那就把自己也纳入校验——规则数 = len(RULES)，断言数 = 本次实检数（不含自指）。

SELF_RE = re.compile(r'(\d+)\s*条规则\s*(?:覆盖|[/｜|]\s*)?\s*(\d+)\s*处断言')
# 2026-09-27 源核验上线时撞出来的漏检：原正则只认「N 条规则覆盖 M 处断言」，
# 而 README / portability 里写的是「12 条规则 / 44 处断言」——斜杠写法整条漏掉，
# 数字漂了也不报。**与「渠道数」那次同一个坑：写法一变，规则就瞎了。**
SELF_FILES = ('AGENTS.md', 'README.md', 'WORKSPACE.md',
              'verified-travel-planner/SKILL.md',
              'verified-travel-planner/references/portability.md',
              'verified-travel-planner/references/landscape.md')


def self_check(root, n_rules, n_checked):
    """校验「N 条规则覆盖 M 处断言」这类自述。

    期望值由入参给定，**不由本函数计算**——否则就成了自指循环。
    调用方必须先算好常规断言数，再拿它来查这一条。
    """
    out = []
    for rel in SELF_FILES:
        src = _read(os.path.join(root, rel.replace('/', os.sep)))
        if not src:
            continue
        for lineno, line in enumerate(src.splitlines(), 1):
            for m in SELF_RE.finditer(line):
                mr, mc = int(m.group(1)), int(m.group(2))
                out.append({
                    'rule': '工具自述',
                    'file': rel,
                    'line': lineno,
                    'asserted': '%d 条规则 / %d 处断言' % (mr, mc),
                    'facts': {'RULES 条数': n_rules,
                              '本次实检断言数': n_checked},
                    'status': 'ok' if (mr == n_rules and mc == n_checked)
                              else 'mismatch',
                    'text': line.strip()[:110],
                })
    return out


# ============================================================ 校验

def _first_group(match):
    for g in match.groups():
        if g:
            try:
                return int(g)
            except ValueError:
                v = _cn_to_int(g)
                if v is not None:
                    return v
    return None


#: 中文数字：文档里「九道闸门」写的是汉字，事实值是阿拉伯——两种都得认。
_CN_DIG = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9}


def _cn_to_int(s):
    if s.isdigit():
        return int(s)
    if '十' in s:
        head, _, tail = s.partition('十')
        t = _CN_DIG.get(head, 1) if head else 1
        return t * 10 + (_CN_DIG.get(tail, 0) if tail else 0)
    return _CN_DIG.get(s)


def validate(root):
    """返回 findings 列表，每项：规则名/文件/行号/断言值/各事实值/状态。"""
    findings = []
    for rule in RULES:
        facts = {label: fn(root) for label, fn in rule['facts'].items()}
        for rel in _expand_readme(rule['files']):
            path = os.path.join(root, rel.replace('/', os.sep))
            src = _read(path)
            if not src:
                continue
            for lineno, line in enumerate(src.splitlines(), 1):
                for m in rule['pattern'].finditer(line):
                    val = _first_group(m)
                    if val is None:
                        continue
                    skipped = any(w in line for w in rule.get('skip_if', ()))
                    ok = all(v == val for v in facts.values())
                    status = 'skipped' if skipped else ('ok' if ok else 'mismatch')
                    findings.append({
                        'rule': rule['name'],
                        'file': rel,
                        'line': lineno,
                        'asserted': val,
                        'facts': facts,
                        'status': status,
                        'text': line.strip()[:110],
                    })
    return findings


def scan(root):
    """反向扫描：列出全部「N 个 X」，标出是否被某条规则的正则覆盖。"""
    covered_pats = [r['pattern'] for r in RULES]
    files = []
    for pat in ('AGENTS.md', 'README.md', 'WORKSPACE.md',
                'verified-travel-planner/SKILL.md',
                'verified-travel-planner/tools/*.py',
                'verified-travel-planner/references/*.md'):
        files.extend(sorted(glob.glob(os.path.join(root, pat.replace('/', os.sep)))))
    out = []
    for path in files:
        rel = os.path.relpath(path, root).replace(os.sep, '/')
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for m in SCAN_RE.finditer(line):
                covered = any(p.search(line) for p in covered_pats)
                out.append({
                    'file': rel, 'line': lineno, 'assert': m.group(0),
                    'covered': covered, 'text': line.strip()[:100],
                })
    return out


def main():
    ap = argparse.ArgumentParser(
        description='skill 文档计数断言校验（文档里的「N 个 X」是否与代码事实一致）')
    ap.add_argument('--root', default=None, help='项目根目录（默认脚本上推两级）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--scan', action='store_true',
                    help='反向扫描全部「N 个 X」断言，标出未覆盖项（供人工核）')
    a = ap.parse_args()

    root = a.root or os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    if not os.path.isdir(root):
        print('项目根不存在：%s' % root)
        return 2
    if not os.path.isfile(os.path.join(_skill_dir(root), 'SKILL.md')):
        print('在 %s 下找不到 verified-travel-planner/SKILL.md —— '
              '请用 --root 指定项目根' % root)
        return 2

    if a.scan:
        rows = scan(root)
        un = [r for r in rows if not r['covered']]
        if a.json:
            print(json.dumps({'root': root, 'all': rows,
                              'uncovered': un}, ensure_ascii=False, indent=2))
            return 0
        print('=' * 78)
        print('反向扫描：全仓「N 个 X」断言 %d 处 ｜ 未被规则覆盖 %d 处'
              % (len(rows), len(un)))
        print('=' * 78)
        print('\n[未被规则覆盖 —— 需人工判断是否为需校验的断言]')
        if not un:
            print('    （无）')
        for r in un:
            print('    %-42s:%-5d %-6s | %s'
                  % (r['file'], r['line'], r['assert'], r['text'][:56]))
        print('\n[已被规则覆盖 %d 处，校验结果见常规运行]' % (len(rows) - len(un)))
        return 0

    findings = validate(root)
    checked = [f for f in findings if f['status'] in ('ok', 'mismatch')]
    skipped = [f for f in findings if f['status'] == 'skipped']

    # 自指断言放在最后：它的期望值就是上面算出的规则数与断言数，
    # 所以既不能参与计算，也不能计入 checked（那会自我循环）。
    self_findings = self_check(root, len(RULES), len(checked))
    findings = findings + self_findings
    bad = [f for f in findings if f['status'] == 'mismatch']

    if a.json:
        print(json.dumps({
            'root': root, 'rules': len(RULES),
            'checked': len(checked), 'mismatch': len(bad),
            'findings': findings, 'self': self_findings, 'ok': not bad,
        }, ensure_ascii=False, indent=2))
        return 1 if bad else 0

    print('=' * 78)
    print('skill 计数断言校验（文档里的「N 个 X」 vs 代码事实）')
    print('  root : %s' % root)
    print('  规则 %d 条 ｜ 实检断言 %d 处 ｜ 豁免（上游/历史）%d 处'
          % (len(RULES), len(checked), len(skipped)))
    print('=' * 78)

    # 按规则汇总
    by_rule = {}
    for f in checked:
        by_rule.setdefault(f['rule'], []).append(f)
    print()
    for rule in RULES:
        rows = by_rule.get(rule['name'], [])
        if not rows:
            print('  [·] %-18s 文档中未出现，跳过' % rule['name'])
            continue
        facts = rows[0]['facts']
        ok = all(all(v == r['asserted'] for v in facts.values()) for r in rows)
        mark = 'OK' if ok else 'X '
        vals = sorted({r['asserted'] for r in rows})
        print('  [%s] %-18s 文档=%s'
              % (mark, rule['name'], '/'.join(str(v) for v in vals)))
        for label, v in facts.items():
            print('       事实　%-40s = %s' % (label, v))
        for r in rows:
            wrong = [str(facts[k]) for k, v in facts.items() if v != r['asserted']]
            extra = ('  ← 应为 %s' % '/'.join(wrong)) if wrong else ''
            print('       %-44s:%-5d 断言 %s%s'
                  % (r['file'], r['line'], r['asserted'], extra))

    for r in self_findings:
        print('  [%s] %-18s 文档=%s'
              % ('OK' if r['status'] == 'ok' else 'X ', r['rule'], r['asserted']))
        for label, v in r['facts'].items():
            print('       事实　%-40s = %s' % (label, v))
        print('       %-44s:%-5d' % (r['file'], r['line']))

    if skipped:
        print('\n[·] 已豁免 %d 处（行内含「上游/来源/原样」等上下文词，讲的是别处的事）：'
              % len(skipped))
        for r in skipped[:8]:
            print('    %-42s:%-5d %s' % (r['file'], r['line'], r['text'][:56]))
        if len(skipped) > 8:
            print('    …另有 %d 处' % (len(skipped) - 8))

    print('\n' + '-' * 78)
    if bad:
        print('[X] %d 处断言与代码事实不符 —— 改文档或改代码，别让数字漂着' % len(bad))
        for f in bad:
            right = '/'.join('%s=%s' % (k, v) for k, v in f['facts'].items())
            print('    %s  %s:%d  文档写 %s，事实 %s'
                  % (f['rule'], f['file'], f['line'], f['asserted'], right))
    else:
        print('[OK] 全部 %d 处断言与代码事实一致' % len(checked))
    print('提醒：本脚本只校验**规则表列出的**断言。用 `--scan` 看还有哪些'
          '「N 个 X」未被覆盖，那些须人工判断。')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())

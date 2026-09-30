#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
audit.py — skill 文档与实现的一致性审计

为什么需要它
------------
自建 skill 最容易出的错不是代码写错，而是**文档说的东西实际不存在**：
删了脚本忘了改文档、改了字段名忘了同步说明、写了「跑这个命令」但那个命令从没实现。
这类错不会被任何测试抓到（测的是代码，不是文档），只有部署时才暴露。

本脚本把这类核对机械化。

用法
----
    python audit.py <skill目录>
    python audit.py <skill目录> --json
    python audit.py <skill目录> --runnable      # 额外尝试真跑各入口的 --help

查四件事
--------
  ① 文档引用的相对路径是否存在（tools/ assets/ references/ engine/ …）
  ② 文档里提到的脚本是否真实存在
  ③ 文档里出现的命令示例，其脚本文件是否存在
  ④ 文档里是否残留占位标记（TODO / FIXME / XXX / 待补）

**不查**（需要项目知识，无法通用化，见 SKILL.md 的人工清单）：
字段名与引擎是否相符、错误码表是否与实现一致、能力描述是否夸大。

退出码：0 = 未发现问题；1 = 有发现问题。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys

DOC_GLOBS = ('SKILL.md', 'README.md', 'THIRD_PARTY_NOTICES.md',
             'references/*.md', 'docs/*.md', '*.md')

# 文档里出现的、指向 skill 内部的路径
PATH_RE = re.compile(
    r'((?:tools|assets|references|engine|scripts|src|bin|lib|templates|prompts)'
    r'/[A-Za-z0-9_./\u4e00-\u9fff-]+)')
# 命令示例里的 python 调用
CMD_RE = re.compile(
    r'python[0-9.]*\s+"?([^\s"\']+\.py)"?')
# 占位标记
TODO_RE = re.compile(r'\b(TODO|FIXME|XXX|待补|待填)\b')

# 一行里出现这些词，说明它在描述上游/外部引用，那条路径不属于本 skill
UPSTREAM_HINTS = ('上游', '来源', '源自', '迁移自', '参考项目', '原项目', '原仓库',
                  'http://', 'https://', 'Copyright', '许可')

TRAIL = '。，；、）)`\'"…：:！？'


def collect_docs(root):
    out = []
    for pattern in DOC_GLOBS:
        for p in sorted(glob.glob(os.path.join(root, pattern))):
            if p not in out:
                out.append(p)
    return out


def read(path):
    try:
        return io.open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return ''


def rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, '/')


def audit(root, runnable=False):
    findings = {'missing_paths': [], 'missing_scripts': [],
                'bad_commands': [], 'todo_marks': []}
    docs = collect_docs(root)
    if not docs:
        findings['missing_paths'].append({'detail': '没找到任何文档（SKILL.md / README.md）'})
        return findings, docs

    seen_paths = {}
    for doc in docs:
        for line in read(doc).splitlines():
            # 描述上游/外部的行会天然引用别处的路径（如「迁移自上游 scripts/x.py」），
            # 那不是本 skill 的路径，跳过，否则全是误报
            if any(hint in line for hint in UPSTREAM_HINTS):
                continue
            for match in PATH_RE.finditer(line):
                ref = match.group(1).rstrip(TRAIL)
                seen_paths.setdefault(ref, set()).add(rel(root, doc))
            for match in CMD_RE.finditer(line):
                # 去掉 <SKILL_ROOT> 这类占位前缀，只留脚本相对路径
                raw = match.group(1).split('>')[-1].lstrip('/')
                seen_paths.setdefault(raw, set()).add(rel(root, doc))

    for ref in sorted(seen_paths):
        if '<' in ref or '>' in ref or ref.endswith('/'):
            continue                       # 占位符或目录引用，跳过
        target = os.path.join(root, ref.replace('/', os.sep))
        if not os.path.exists(target):
            findings['missing_paths'].append(
                {'ref': ref, 'cited_in': sorted(seen_paths[ref])})

    # ②③ 脚本是否存在 / 能否跑起来
    for doc in docs:
        for match in CMD_RE.finditer(read(doc)):
            raw = match.group(1).strip()
            candidate = raw.replace('<SKILL_ROOT>', root).replace('$SKILL_ROOT', root)
            if not os.path.isabs(candidate):
                base = os.path.dirname(os.path.join(root, rel(root, doc)))
                candidate = os.path.normpath(os.path.join(base, candidate))
                if not os.path.exists(candidate):
                    candidate = os.path.normpath(os.path.join(root, raw))
            if not os.path.exists(candidate):
                findings['bad_commands'].append(
                    {'script': raw, 'in': rel(root, doc)})
            elif runnable:
                try:
                    proc = subprocess.run([sys.executable, candidate, '--help'],
                                          capture_output=True, text=True, timeout=25)
                    if proc.returncode not in (0, 2):
                        findings['bad_commands'].append(
                            {'script': raw, 'in': rel(root, doc),
                             'detail': '--help 退出码 %s' % proc.returncode})
                except Exception as exc:
                    findings['bad_commands'].append(
                        {'script': raw, 'in': rel(root, doc),
                         'detail': '%s' % exc})

    # ④ 占位标记（只提示，不当错误：模板类文档本来就该有 TODO）
    for doc in docs:
        for lineno, line in enumerate(read(doc).splitlines(), 1):
            mark = TODO_RE.search(line)
            if mark:
                findings['todo_marks'].append(
                    {'file': rel(root, doc), 'line': lineno,
                     'text': line.strip()[:80]})
    return findings, docs


def main():
    ap = argparse.ArgumentParser(description='skill 文档与实现的一致性审计')
    ap.add_argument('skill_dir')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--runnable', action='store_true',
                    help='额外尝试真跑各脚本的 --help（较慢）')
    a = ap.parse_args()

    root = os.path.abspath(a.skill_dir)
    if not os.path.isdir(root):
        print('目录不存在：%s' % root)
        return 1

    findings, docs = audit(root, runnable=a.runnable)
    hard = (findings['missing_paths'] + findings['missing_scripts']
            + findings['bad_commands'])

    if a.json:
        print(json.dumps({'skill': root, 'docs': [rel(root, d) for d in docs],
                          'findings': findings, 'ok': not hard},
                         ensure_ascii=False, indent=2))
        return 1 if hard else 0

    print('=' * 78)
    print('skill 文档↔实现一致性审计')
    print('  %s' % root)
    print('  文档 %d 份' % len(docs))
    print('=' * 78)

    if findings['missing_paths']:
        print('\n[!] 文档引用了但盘上不存在的路径：')
        for item in findings['missing_paths']:
            print('    %-46s ← %s' % (item['ref'], ', '.join(item['cited_in'])))
    else:
        print('\n[OK] 文档引用的路径全部存在')

    if findings['bad_commands']:
        print('\n[!] 命令示例指向的脚本有问题：')
        for item in findings['bad_commands']:
            extra = ('（%s）' % item['detail']) if item.get('detail') else ''
            print('    %-46s ← %s %s' % (item['script'], item['in'], extra))
    else:
        print('[OK] 命令示例指向的脚本全部存在')

    if findings['todo_marks']:
        print('\n[·] 占位标记 %d 处（模板类文档本就是待填的，仅提示）：'
              % len(findings['todo_marks']))
        for item in findings['todo_marks'][:8]:
            print('    %s:%s  %s' % (item['file'], item['line'], item['text']))
        if len(findings['todo_marks']) > 8:
            print('    …另有 %d 处' % (len(findings['todo_marks']) - 8))

    print('\n' + '-' * 78)
    if hard:
        print('[X] 发现 %d 处不一致 —— 文档说了但实际没有，必须改文档或补实现'
              % len(hard))
    else:
        print('[OK] 未发现"文档说有、实际没有"的问题')
    print('提醒：字段名、错误码表、能力描述**本脚本查不了**，'
          '须按 SKILL.md 里的人工清单逐项核。')
    return 1 if hard else 0


if __name__ == '__main__':
    raise SystemExit(main())

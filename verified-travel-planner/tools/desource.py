#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
desource.py — 对外稿脱敏双轨化

为什么需要它：路书有两副面孔。给自己看的那份可以保留全部来源、账单细节、
内部代号；发给朋友或公开发布的那份必须干净。手工删来源最容易漏——曾经出过
"避雷 12 条转成分发版只剩 8 条"和"对外稿混进不该有的字眼"。

本脚本把"脱敏"变成一次可复现的机械操作：给一份规则 JSON，它生成两个产物并
逐条核对来源词是否归零。

用法：
    python desource.py rules.json

rules.json 结构：
{
  "stamp": "20260927",
  "files": [
    {
      "name": "宁波中秋",
      "source": "C:/.../路书_宁波_v1.html",
      "public_out": "C:/.../路书_宁波_对外版.html",
      "self_out": "C:/.../路书_宁波_自用版.html",
      "public_title": "宁波·中秋 出行路书",
      "self_title": "宁波·中秋 自用版路书（含情报原档）",
      "drop": ["六博主", "实探", "万赞", "某内部代号"],
      "replacements": [
        {"tag": "正餐段来源词", "mode": "exact",
         "old": "<span class=\"src\">点评+小红书双源核实</span>", "new": ""}
      ],
      "insert_intel_self": {
        "anchor": "<section id=\"boost\">",
        "md": "C:/.../美食情报卡_宁波.md",
        "title": "探店情报原档"
      }
    }
  ],
  "log_out": "desource_report.txt"
}

退出码：0 = 全部成功；1 = 有断言失败（源词残留 / 锚点未命中）
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

LOG = []


class DesourceError(RuntimeError):
    """脱敏校验未通过。

    为什么不用 `assert`：本脚本的全部价值就是「来源词必须归零」。`assert` 在
    `python -O` 下会被整条剥离——那时候校验静默失效，脚本会**照常写出一份没脱净的
    对外稿**，而且退出码 0、看起来一切正常。校验承担的是正确性，不是调试便利，
    所以必须是真异常。（2026-09-28 审查 L1/L3 项）
    """


_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '√': '[OK]',
    '❌': '[X]', '✗': '[X]', '×': '[X]',
    '→': '->', '■': '[*]', '●': '[*]', '·': '.',
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


def log(*a):
    line = " ".join(str(x) for x in a)
    LOG.append(line)
    emit(line)


def rep1(s, old, new, tag):
    """精确替换，必须命中恰好 1 处"""
    n = s.count(old)
    if n != 1:
        raise DesourceError("[%s] 期望精确命中 1 处，实际 %d 处" % (tag, n))
    log("  [OK] %s: 精确替换 1 处" % tag)
    return s.replace(old, new)


def resub1(s, pat, new, tag, flags=re.S):
    """正则替换，必须命中恰好 1 处"""
    rx = re.compile(pat, flags)
    n = len(rx.findall(s))
    if n != 1:
        raise DesourceError("[%s] 期望正则命中 1 处，实际 %d 处" % (tag, n))
    log("  [OK] %s: 正则替换 1 处" % tag)
    return rx.sub(new, s, count=1)


def esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def inl(t):
    t = esc(t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
    return t


def md_to_html(md):
    """极简 md -> html，够情报卡用即可"""
    out, i, open_card = [], 0, False
    L = md.split('\n')
    while i < len(L):
        st = L[i].strip()
        if not st:
            i += 1
            continue
        if st.startswith('# ') and not st.startswith('## '):
            i += 1
            continue
        if st.startswith('### '):
            out.append('<h4>%s</h4>' % inl(st[4:]))
            i += 1
            continue
        if st.startswith('## '):
            if open_card:
                out.append('</div>')
            out.append('<div class="ic-card"><h3 class="ic-h">%s</h3>' % inl(st[3:]))
            open_card = True
            i += 1
            continue
        if st.startswith('|'):
            rows = []
            while i < len(L) and L[i].strip().startswith('|'):
                rows.append(L[i].strip())
                i += 1
            cells = []
            for r in rows:
                c = [x.strip() for x in r.strip().strip('|').split('|')]
                if all(re.fullmatch(r':?-{2,}:?', x or '-') for x in c):
                    continue
                cells.append(c)
            if cells:
                out.append('<table><tr>' + ''.join('<th>%s</th>' % inl(x) for x in cells[0]) + '</tr>')
                for c in cells[1:]:
                    out.append('<tr>' + ''.join('<td>%s</td>' % inl(x) for x in c) + '</tr>')
                out.append('</table>')
            continue
        if st.startswith('- '):
            buf = []
            while i < len(L) and L[i].strip().startswith('- '):
                buf.append(L[i].strip()[2:])
                i += 1
            out.append('<ul>' + ''.join('<li>%s</li>' % inl(x) for x in buf) + '</ul>')
            continue
        out.append('<p>%s</p>' % inl(st))
        i += 1
    if open_card:
        out.append('</div>')
    return '\n'.join(out)


def run(rule, stamp):
    src = Path(rule['source'])
    if not src.exists():
        raise DesourceError("源文件不存在: %s" % src)
    s0 = src.read_text(encoding='utf-8')

    log("=" * 64)
    log("[*] %s  源文件 %s  %d 字符" % (rule.get('name', ''), src.name, len(s0)))

    result = {'name': rule.get('name', src.name), 'source_chars': len(s0)}
    drop = rule.get('drop', [])

    # ---- (1) 对外版（脱敏）----
    if rule.get('public_out'):
        log("--- 对外版脱敏 ---")
        s = s0
        s = resub1(s, r'<title>[^<]*</title>',
                   "<title>%s</title>" % rule.get('public_title', rule.get('name', '')),
                   '对外版·title')
        for r in rule.get('replacements', []):
            if r.get('mode') == 'exact':
                s = rep1(s, r['old'], r['new'], r['tag'])
            else:
                s = resub1(s, r['old'], r['new'], r['tag'])

        # 来源词归零检查（脱敏的全部价值所在——不通过就绝不写出）
        left = {k: s.count(k) for k in drop if s.count(k)}
        if left:
            raise DesourceError("对外版仍残留来源词：%s" % left)
        log("  [OK] 来源词残留检查：全部为 0（%s）" % '/'.join(drop)
            if drop else "  [OK] 无来源词需检查")

        # 字面转义残留
        for bad in ('\\n', '\\1'):
            if bad in s:
                raise DesourceError("对外版出现字面转义残留：%r" % bad)

        # 备份后写出
        bak = src.with_name(src.name + '.bak-%s' % stamp)
        if not bak.exists():
            shutil.copy2(src, bak)
            log("  -> 备份 %s" % bak.name)
        out = Path(rule['public_out'])
        out.write_text(s, encoding='utf-8')
        log("  -> 写出 %s（%d 字符，- %d）" % (out.name, len(s), len(s0) - len(s)))
        result['public_chars'] = len(s)
        result['removed'] = len(s0) - len(s)

    # ---- (2) 自用版（含情报原档）----
    ins = rule.get('insert_intel_self')
    if ins and rule.get('self_out'):
        log("--- 自用版叠加情报原档 ---")
        s = s0
        s = resub1(s, r'<title>[^<]*</title>',
                   "<title>%s</title>" % rule.get('self_title', rule.get('name', '')),
                   '自用版·title')
        md_path = Path(ins['md'])
        if not md_path.exists():
            raise DesourceError("情报卡 md 不存在: %s" % md_path)
        md_html = md_to_html(md_path.read_text(encoding='utf-8'))
        block = ('<section id="intel">\n  <h2>%s</h2>\n%s\n</section>\n'
                 % (ins.get('title', '情报原档'), md_html))
        s = rep1(s, ins['anchor'], block + ins['anchor'], '自用版·情报章节')
        for bad in ('\\n', '\\1'):
            if bad in s:
                raise DesourceError("自用版出现字面转义残留：%r" % bad)
        out = Path(rule['self_out'])
        out.write_text(s, encoding='utf-8')
        log("  -> 写出 %s（%d 字符）" % (out.name, len(s)))
        result['self_chars'] = len(s)

    return result


def main():
    ap = argparse.ArgumentParser(
        prog='desource.py',
        description='对外稿脱敏双轨化：从源路书派生出「对外版（脱敏）」与'
                    '「自用版（含情报原档）」，并断言来源词归零。',
        usage='%(prog)s rules.json')
    ap.add_argument('rules', metavar='rules.json', nargs='?',
                    help='规则配置 JSON 路径（缺省则打印帮助）')
    args = ap.parse_args()
    if not args.rules:
        ap.print_help()
        sys.exit(2)

    # 规则文件读不到 / 不是合法 JSON：给可读报错 + 退出码 2（原先直接甩 traceback）
    try:
        rules = json.loads(Path(args.rules).read_text(encoding='utf-8'))
    except OSError as exc:
        emit('❌ 读不到规则文件：%s' % exc)
        return 2
    except ValueError as exc:
        emit('❌ 规则文件不是合法 JSON：%s' % exc)
        return 2
    if not isinstance(rules, dict) or not rules.get('files'):
        emit('❌ 规则文件缺少非空的 files 列表：%s' % args.rules)
        return 2

    stamp = rules.get('stamp', 'bak')
    try:
        rep = [run(r, stamp) for r in rules['files']]
    except DesourceError as exc:
        emit('❌ 脱敏校验未通过：%s' % exc)
        emit('   未通过校验就不写出产物——宁可不发，也不发一份没脱净的对外稿。')
        Path(rules.get('log_out', 'desource_report.txt')).write_text(
            '\n'.join(LOG), encoding='utf-8')
        return 2
    except KeyError as exc:
        emit('❌ 规则文件缺字段：%s（每条 rule 至少要有 name / source）' % exc)
        return 2

    Path(rules.get('log_out', 'desource_report.txt')).write_text(
        '\n'.join(LOG), encoding='utf-8')
    emit('\n' + json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
doctor.py — 能力先测后报（本环境的旅行规划能力体检）

为什么需要它
------------
本 skill 的硬规矩之一是「能力先测后报」：不许在没确认数据源能不能用的前提下，
把推断出来的数字当成已核实的事实交给用户。这条规矩得先对自己用——先量一遍这台
机器的能力，再决定哪些项能取 [A]、哪些必须降级成 [D]。

本脚本只做**只读探测**，默认不联网；加 --live 才实测高德连通性。

用法
----
    python doctor.py [--json] [--live]

退出码：0 = 核心能力齐备；1 = 核心能力缺失（如时区数据库不可用）。
"""
import io
import os
import sys
import json
import platform
import argparse

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(SKILL_ROOT, 'engine')
sys.path.insert(0, ENGINE)

_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '❌': '[X]', '✗': '[X]',
    '⚠': '[!]', '·': '.', '→': '->',
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


def check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 9)
    return {
        'item': 'Python 解释器',
        'status': 'READY' if ok else 'MISSING',
        'detail': '%s（需 ≥3.9）' % platform.python_version(),
        'critical': True,
    }


def check_tzdata():
    """可行性检查要判营业时间与换乘余量，必须能解析真实时区。
    Windows 上 Python 不自带时区库，缺 tzdata 时 Asia/Shanghai 会解析失败。"""
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        tz = ZoneInfo('Asia/Shanghai')
        return {
            'item': '时区数据库',
            'status': 'READY',
            'detail': 'Asia/Shanghai 可解析（%s）' % datetime.now(tz).strftime('%Y-%m-%d %H:%M'),
            'critical': True,
        }
    except Exception as e:
        return {
            'item': '时区数据库',
            'status': 'DEGRADED',
            # 2026-09-30 实测：本机常有多个 Python，裸 shell 的 `python` 可能解析到
            # 另一个没装 tzdata 的解释器——闸门照样全绿，doctor 却报「核心能力缺失」，
            # 被误判成环境坏了。解释器路径放最前：ship/汇总输出会截断长文本，放后面看不见。
            'detail': ('出事解释器 = %s（v%s）。本机常装多个 Python，'
                       'tzdata 要装到报错里这一个身上，别装错身。'
                       '%s: %s。修法（用阿里源，清华源对 tzdata 查无此包）：'
                       'pip install tzdata -i https://mirrors.aliyun.com/pypi/simple/ '
                       '--trusted-host mirrors.aliyun.com') % (
                          sys.executable, platform.python_version(),
                          type(e).__name__, e),
            'critical': True,
        }


def check_engine():
    try:
        import travel_planner as tp
        return {
            'item': '检查引擎',
            'status': 'READY',
            'detail': '%d 个模块符号全部可用' % len(tp.__all__),
            'critical': True,
        }
    except Exception as e:
        return {
            'item': '检查引擎',
            'status': 'MISSING',
            'detail': '%s: %s' % (type(e).__name__, e),
            'critical': True,
        }


def check_amap_key():
    try:
        import travel_planner as tp
        st = tp.CredentialStore().status('amap')
        if st['status'] == 'CONFIGURED':
            return {
                'item': '高德地图 key',
                'status': 'READY',
                'detail': '来源 %s' % st.get('source'),
                'critical': False,
            }
        return {
            'item': '高德地图 key',
            'status': 'DEGRADED',
            'detail': '未配置——POI 与路线核验相关项须按规矩降级为 [D]，'
                      '不得用模型推断的坐标或车程冒充已核实',
            'critical': False,
        }
    except Exception as e:
        return {'item': '高德地图 key', 'status': 'MISSING',
                'detail': str(e), 'critical': False}


def check_assets():
    want = [
        ('assets/路书_基准骨架.html', '路书基准骨架'),
        ('assets/情报卡_基准骨架.html', '情报卡基准骨架'),
        ('assets/路书事实源模板.json', '事实源模板'),
        ('tools/travel_planner.py', '统一命令行入口'),
        ('tools/set_amap_key.py', '高德 key 配置工具'),
        ('tools/render_html.py', '渲染器'),
        ('tools/consistency.py', '版式一致性校验'),
        ('tools/ludbook_check.py', '内容交付自查'),
        ('tools/desource.py', '脱敏'),
    ]
    missing = [name for rel, name in want
               if not os.path.exists(os.path.join(SKILL_ROOT, rel))]
    return {
        'item': '随包资产',
        'status': 'READY' if not missing else 'MISSING',
        'detail': '到位 %d/%d' % (len(want) - len(missing), len(want))
                  + ('　缺：%s' % '、'.join(missing) if missing else ''),
        'critical': False,
    }


def _iter_py_files():
    """tools/ 与 engine/ 下的全部 .py —— 跨设备依赖扫描的对象。"""
    files = []
    for sub in ('tools', os.path.join('engine', 'travel_planner')):
        d = os.path.join(SKILL_ROOT, sub)
        if not os.path.isdir(d):
            continue
        files += [os.path.join(d, f) for f in sorted(os.listdir(d))
                  if f.endswith('.py')]
    return files


#: 本项目自己的模块（tools/ 与 engine/ 互引），不算第三方。
_LOCAL_MODULES = frozenset({
    'travel_planner', 'consistency', 'render_html', 'ludbook_check',
    'social_notes', 'social_source', 'social_login', 'doctor',
    'desource', 'shoot', 'validate_skill', 'set_amap_key',
})

#: 与操作系统绑定的能力（换台机器就可能不成立）。判据由 check_platform_deps 给。
_PLATFORM_BOUND = 'AUTH 档浏览器探针（tools/social_login.py）'


def _classify_imports():
    """静态扫出全部顶层 import，分「标准库 / 本项目 / 第三方」。

    为什么用**静态扫描**而不是 `pip list`：跨设备可复现问的是
    「别人拿到这份代码能不能直接跑」，判据是**代码里 import 了什么**，
    不是**我这台机器装了什么**。装了却没被 import 的包不该算依赖。
    """
    import ast
    import importlib.util
    import sysconfig
    stdlib_dir = sysconfig.get_paths()['stdlib'].replace('\\', '/').lower()

    seen = set()
    for path in _iter_py_files():
        try:
            with open(path, encoding='utf-8') as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    seen.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    seen.add(node.module.split('.')[0])

    std, third = [], []
    for mod in sorted(seen):
        if mod in _LOCAL_MODULES:
            continue
        origin = ''
        try:
            spec = importlib.util.find_spec(mod)
            origin = (spec.origin or '') if spec else ''
        except (ImportError, ValueError):
            origin = ''
        norm = origin.replace('\\', '/').lower()
        if not norm:                       # 内置 / 冻结模块，没有文件
            std.append(mod)
        elif 'site-packages' in norm or 'dist-packages' in norm:
            third.append(mod)
        else:                              # 标准库目录下
            std.append(mod)
    return std, third


def check_stdlib_only():
    """零第三方依赖是本项目**可复现性的地基**，所以它值得单独体检。"""
    std, third = _classify_imports()
    if third:
        return {
            'item': '第三方依赖',
            'status': 'DEGRADED',
            'detail': '发现 %s——他人须先 pip install 才能跑；'
                      '若非必需，建议改回标准库（本仓设计目标是零依赖）'
                      % '、'.join(third),
            'critical': False,
        }
    return {
        'item': '第三方依赖',
        'status': 'READY',
        'detail': '零第三方依赖（%d 个标准库模块）——'
                  'clone 下来直接跑，不需要 pip install' % len(std),
        'critical': False,
    }


def check_platform_deps():
    """与操作系统绑定的能力盘点——「换台机器还成不成立」就看这一项。

    ⚠️ 如实区分「已实测」与「没测过」：非 Windows 分支**没有真跑过**，
    所以它报 ⚠ 而不是 ✅。按铁律 1，没核实的东西不许写成已核实。
    """
    exe = None
    for p in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Google\Chrome\Application\chrome.exe'):
        if os.path.isfile(p):
            exe = p
            break
    if os.name == 'nt':
        return {
            'item': '平台绑定项',
            'status': 'READY' if exe else 'DEGRADED',
            'detail': ('Windows：核心闸门（渲染 / 30 项自查 / 可行性 / 一致性）'
                       '纯标准库，与平台无关；%s 本机实测可用%s'
                       % (_PLATFORM_BOUND, '（浏览器 %s）' % exe if exe
                          else '，但未找到 Edge / Chrome 可执行文件')),
            'critical': False,
        }
    return {
        'item': '平台绑定项',
        'status': 'INFO',
        'detail': ('非 Windows（%s）：核心闸门照常可用（纯标准库）；'
                   '但 %s 里的浏览器版本探测与 profile 占用探测调的是 Windows '
                   'PowerShell——**未在此平台实测过**，用 AUTH 档前先跑 --check 确认'
                   % (platform.system(), _PLATFORM_BOUND)),
        'critical': False,
    }


def check_local_state():
    """机器级私有状态盘点：这些**不在仓库里**，别人拿到代码必须自建。

    把它们显式报出来，是为了回答最常被问的那句——
    「我下载下来，是不是开箱就能跑完你的流程？」
    """
    base = os.path.join(os.path.expanduser('~'), '.verified-travel-planner')
    items = [
        ('登录态 profile', os.path.join(base, 'social-profile'),
         'AUTH 档扫码产物，**绝不入库**——他人须自己扫一次'),
        ('高德 key', os.path.join(base, 'credentials.json'),
         '机器级凭据，**绝不入库**——他人须自己配'),
    ]
    have = [name for name, p, _ in items if os.path.exists(p)]
    miss = [(name, note) for name, p, note in items if not os.path.exists(p)]
    detail = ('本机已有：%s。' % '、'.join(have)) if have else '本机暂无。'
    if miss:
        detail += '缺：%s。' % '；'.join('%s（%s）' % (n, note) for n, note in miss)
    return {'item': '机器级私有状态', 'status': 'INFO',
            'detail': detail + '核心闸门都不依赖它们。', 'critical': False}


def check_live_amap():
    """仅 --live 时执行：实测高德能否返回真实数据。"""
    try:
        import travel_planner as tp
        key = tp.CredentialStore().get('amap')
        client = tp.AmapClient(key)
        r = client.preflight()
        return {'item': '高德连通性（实测）', 'status': 'READY',
                'detail': 'preflight 通过', 'critical': False}
    except Exception as e:
        return {'item': '高德连通性（实测）', 'status': 'MISSING',
                'detail': '%s: %s' % (type(e).__name__, e), 'critical': False}


def main():
    ap = argparse.ArgumentParser(description='旅行规划能力体检（默认只读、不联网）')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--live', action='store_true', help='实测高德连通性（会联网）')
    a = ap.parse_args()

    rows = [check_python(), check_tzdata(), check_engine(),
            check_amap_key(), check_assets(),
            check_stdlib_only(), check_platform_deps(), check_local_state()]
    if a.live:
        rows.append(check_live_amap())

    failed_critical = [r for r in rows if r['critical'] and r['status'] != 'READY']

    if a.json:
        emit(json.dumps({'checks': rows, 'ok': not failed_critical},
                        ensure_ascii=False, indent=2))
    else:
        emit('=' * 84)
        emit('旅行规划能力体检　%s' % platform.node())
        emit('=' * 84)
        marks = {'READY': '✅', 'DEGRADED': '⚠', 'MISSING': '❌', 'INFO': 'ℹ'}
        for r in rows:
            emit('%s %-18s %s' % (marks.get(r['status'], '·'), r['item'], r['detail']))
        emit('-' * 84)
        if failed_critical:
            emit('❌ 核心能力缺失——先补齐再开工：%s'
                 % '、'.join(r['item'] for r in failed_critical))
        else:
            degraded = [r['item'] for r in rows if r['status'] == 'DEGRADED']
            emit('✅ 核心能力齐备。')
            if degraded:
                emit('⚠ 降级项：%s——相关结论必须按证据分级如实标 [D]，不许含糊过去。'
                     % '、'.join(degraded))

    sys.exit(1 if failed_critical else 0)


if __name__ == '__main__':
    main()

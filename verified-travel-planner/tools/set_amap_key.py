#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
set_amap_key.py — 跨平台的高德 key 配置工具

为什么需要它
------------
上游 `scripts/setup_amap_key.sh` 第一行就检查 `uname -s`，不是 darwin 直接 exit 1
（它走 macOS 钥匙串）。Windows / Linux 上那条路完全不可用——上游自己在
`references/client-compatibility.md` 里也写了「其他平台自行提供 AMAP_API_KEY」，
但没给工具。本文件补的就是这个缺口。

设计取舍
--------
1. **key 不回显、不进 shell 历史、不进对话记录**。交互式输入走 getpass；
   命令行 `--key` 参数虽然支持，但会留在 shell 历史里，所以默认不推荐。
2. **只用标准库，不调 subprocess**。权限收紧不由本脚本代劳，而是打印一条
   可选的 `icacls` 命令让使用者自己决定——保持与引擎一致的审计口径
   （全 skill 唯一的命令执行点仍是 diagnostics.py 的只读探测）。
3. **写入前备份**，且与已有凭据文件**合并**而非覆盖——那个文件可能还存着
   别的提供方的凭据，直接盖掉是数据丢失。
4. **验证要真验证**：`--verify` 会真发一次请求到 `restapi.amap.com`，
   并把高德的错误码翻译成「你该去改哪个选项」。这一步会消耗 1 次
   关键字搜索配额（个人认证仅 100 次/日，留意）。

用法
----
    python set_amap_key.py                    # 交互输入（推荐）
    python set_amap_key.py --status           # 看当前状态，不写不改
    python set_amap_key.py --verify           # 只验证已配置的 key
    python set_amap_key.py --remove           # 移除本地凭据文件里的 amap 键
    python set_amap_key.py --path P           # 指定凭据文件（测试 / 多环境）

退出码
------
    0 成功 ｜ 1 写入或读取出错 ｜ 2 验证未通过
"""
from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = TOOLS_DIR.parent
ENGINE_DIR = SKILL_ROOT / 'engine'
sys.path.insert(0, str(ENGINE_DIR))

# 中文 Windows 的 GBK 终端打不出部分符号会直接抛 UnicodeEncodeError 崩框。
# 降级成 ASCII 再打一次，别让配置报告因为一个符号打不出来。
_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '❌': '[X]', '✗': '[X]',
    '⚠': '[!]', '·': '.', '→': '->', '｜': '|', '✓': '[OK]',
})


def emit(text: str = '') -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            print(text.translate(_SYM_FALLBACK))
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or 'ascii'
            print(text.encode(enc, 'replace').decode(enc))


# ============================ 高德错误码 → 该改什么 ============================
# 用 info 枚举名（字符串）而不是数字码做键：枚举名稳定，数字码各文档版本有出入。
# 原始 infocode 一并打印出来，方便使用者自己去官方文档核。
AMAP_FIXES = {
    'INVALID_USER_KEY': [
        'Key 不正确或已失效。',
        '去控制台「应用管理 → 我的应用」重新复制整串 Key（32 位十六进制）。',
        '注意别把「安全密钥 Secret」当成 Key 抄进来——那是另一个字段。',
    ],
    'USERKEY_PLAT_NOMATCH': [
        '**这个 Key 绑定的服务平台不对**——最常见的坑。',
        '本 skill 走 restapi.amap.com（Web 服务 API），Key 的平台必须选「Web 服务」。',
        '选成 Web端(JS API) / Android / iOS 都调不通。',
        '一个 Key 只能绑一个平台：同时要 JS API 就再建一个 Key，别指望一个 Key 通吃。',
    ],
    'INVALID_USER_SIGNATURE': [
        '**你在控制台开了「数字签名」，但本 skill 不带 sig 参数**。',
        '回控制台把该 Key 的数字签名关掉（引擎里没有 md5 签名实现）。',
    ],
    'INVALID_USER_SCODE': [
        '同上：数字签名校验失败，去控制台关掉数字签名。',
    ],
    'DAILY_QUERY_OVER_LIMIT': [
        '当日配额用完了。免费额度每日 0 点重置，明天再用即可。',
        '若反复撞到这个：个人认证的关键字/周边搜索配额很低（100 次/日量级），',
        '做路书时收紧搜索次数（一次搜索多取结果、复用已采集的快照），或升企业认证。',
    ],
    'USER_DAILY_QUERY_OVER_LIMIT': ['同上：当日配额已用完，0 点重置。'],
    'ACCESS_TOO_FREQUENT': ['请求太频繁，稍等重试；批量查询时降低并发。'],
    'CUQPS_HAS_EXCEEDED_THE_LIMIT': ['QPS 超限（每秒请求数），降低并发重试。'],
    'QPS_HAS_EXCEEDED_THE_LIMIT': ['QPS 超限（每秒请求数），降低并发重试。'],
    'INVALID_USER_IP': [
        '**IP 白名单不匹配**。家庭宽带 IP 是动态的，设了白名单迟早调不通。',
        '回控制台把该 Key 的 IP 白名单**留空**。',
    ],
    'SERVICE_NOT_AVAILABLE': ['该服务未开通或未勾选对应权限。检查 Key 的可用服务列表。'],
    'INSUFFICIENT_PRIVILEGES': [
        '该接口你的认证级别无权调用。',
        '个人开发者认证不支持部分接口，改用个人认证可用的等价接口。',
    ],
    'USER_KEY_RECYCLED': ['这个 Key 已被删除或回收，重建一个。'],
}


def default_credential_file() -> Path:
    return Path(os.path.expanduser('~')) / '.verified-travel-planner' / 'credentials.json'


def mask(key: str) -> str:
    """只露首尾，中间打码。够确认抄对了没，又不足以被复用。"""
    key = key.strip()
    if len(key) <= 8:
        return key[:2] + '*' * max(0, len(key) - 2)
    return '%s%s%s  (%d 位)' % (key[:4], '*' * 12, key[-4:], len(key))


def read_store(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        # 文件坏了不等于要覆盖它 —— 里面可能有别的凭据，先停下来让人看。
        emit('[X] 凭据文件无法解析：%s' % path)
        emit('    %s: %s' % (type(exc).__name__, exc))
        emit('    已中止，未做任何改动。确认内容后重跑，或先手工备份并移走它。')
        sys.exit(1)
    if not isinstance(data, dict):
        emit('[X] 凭据文件顶层应为 JSON 对象，实际是 %s' % type(data).__name__)
        sys.exit(1)
    return data


def write_store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix('.json.bak-%s' % time.strftime('%Y%m%d%H%M%S'))
        shutil.copy2(str(path), str(backup))
        emit('    已备份原文件 -> %s' % backup.name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def restrict_hint(path: Path) -> None:
    """只提示、不代劳：保持「全 skill 唯一命令执行点是 diagnostics.py 只读探测」的审计口径。"""
    if sys.platform == 'win32':
        emit('')
        emit('    [!] key 是明文存的。想收紧到仅当前账户可读（可选，自己决定）：')
        emit('        icacls "%s" /inheritance:r /grant:r "%%USERNAME%%":F' % path)


def env_shadow_warning() -> None:
    """环境变量优先级高于文件。存在时会盖过刚写的文件，让人以为写失败了。"""
    for name in ('AMAP_API_KEY',):
        if str(os.environ.get(name, '') or '').strip():
            emit('')
            emit('    [!] 检测到环境变量 %s 已有值，它的优先级高于凭据文件。' % name)
            emit('        验证时会用环境变量那个而不是刚写的这个。要改用文件的话：')
            emit('        Windows: setx %s ""   然后重启 WorkBuddy' % name)
            emit('        或临时清掉：set %s=&' % name)


# ============================ 验证 ============================
def verify(key: str | None = None) -> int:
    """真发一次请求到高德。会消耗 1 次关键字搜索配额。"""
    try:
        from travel_planner.amap import AmapClient, AmapError
        from travel_planner.credentials import CredentialError, CredentialStore
    except Exception as exc:
        emit('[X] 引擎导入失败：%s: %s' % (type(exc).__name__, exc))
        return 1

    if key is None:
        try:
            key, source = CredentialStore().get_with_source('amap')
        except CredentialError as exc:
            emit('[X] 没有可用的 key。')
            emit('    %s' % str(exc).replace('\n', '\n    '))
            return 1
        emit('    读到 key 来源：%s' % source)

    emit('    正在真连 restapi.amap.com 查一次「北京 天安门」...')
    try:
        result = AmapClient(key).preflight()
    except AmapError as exc:
        emit('')
        emit('[X] 高德拒绝了这次请求：')
        emit('    %s' % exc)
        text = str(exc)
        # 从 "Amap rejected the request: INFO (infocode)" 里取出枚举名与原始码
        info, code = '', ''
        if ': ' in text and '(' in text:
            info = text.split(': ', 1)[1].rsplit('(', 1)[0].strip()
            code = text.rsplit('(', 1)[1].rstrip(')').strip()
        fixes = AMAP_FIXES.get(info)
        if fixes:
            emit('')
            emit('    这是什么问题、该改哪里：')
            for line in fixes:
                emit('      - %s' % line)
        else:
            emit('')
            emit('    未收录的错误。原始 info/infocode 见上，'
                 '可到 lbs.amap.com 的开发文档检索该 infocode。')
        if code:
            emit('')
            emit('    原始 infocode = %s' % code)
        return 2

    emit('')
    emit('[OK] 高德连通，返回 %d 条结果（%s）'
         % (result.get('result_count', 0), result.get('checked_at', '')))
    emit('     → POI 与路线相关项可以取 [A] 级证据了。')
    return 0


# ============================ 子命令 ============================
def cmd_status(path: Path) -> int:
    emit('凭据文件：%s' % path)
    emit('  存在：%s' % ('是' if path.exists() else '否'))
    if path.exists():
        data = read_store(path)
        keys = sorted(k for k in data if isinstance(data.get(k), str) and data[k].strip())
        emit('  已配置的键：%s' % (', '.join(keys) or '（无有效字符串值）'))
        if 'amap' in data and isinstance(data['amap'], str) and data['amap'].strip():
            emit('  amap：%s' % mask(data['amap']))
    emit('')
    emit('环境变量 AMAP_API_KEY：%s'
         % ('非空（优先级最高）' if str(os.environ.get('AMAP_API_KEY', '') or '').strip()
            else '空'))
    try:
        from travel_planner.credentials import CredentialStore
        st = CredentialStore(credential_file=str(path)).status('amap')
        emit('引擎实际读到：%s %s'
             % (st.get('status'), ('来源 %s' % st.get('source', '')) if st.get('source') else ''))
    except Exception as exc:
        emit('引擎读取失败：%s: %s' % (type(exc).__name__, exc))
    return 0


def cmd_remove(path: Path) -> int:
    if not path.exists():
        emit('凭据文件不存在，无需移除：%s' % path)
        return 0
    data = read_store(path)
    if 'amap' not in data and 'AMAP_API_KEY' not in data and 'amap-api-key' not in data:
        emit('凭据文件里没有 amap 相关键，未做改动。')
        return 0
    for k in ('amap', 'AMAP_API_KEY', 'amap-api-key'):
        data.pop(k, None)
    write_store(path, data)
    emit('[OK] 已从凭据文件移除 amap 键：%s' % path)
    return 0


def cmd_set(args, path: Path) -> int:
    key = args.key
    if key is None:
        emit('从高德控制台复制 Key 后粘贴（输入不回显、不进 shell 历史、不进对话记录）')
        emit('直接回车 = 放弃。')
        try:
            key = getpass.getpass('  AMAP API Key: ')
        except (KeyboardInterrupt, EOFError):
            emit('')
            emit('已取消，未做任何改动。')
            return 1
    key = (key or '').strip()
    if not key:
        emit('未提供 key，已取消。')
        return 1

    # 格式预检：高德 Key 是 32 位十六进制。只警告不拦截——官方改了格式不该把路堵死。
    looks_hex32 = len(key) == 32 and all(c in '0123456789abcdefABCDEF' for c in key)
    if not looks_hex32:
        emit('[!] 这串 key 不是常见的 32 位十六进制格式（长度 %d）。' % len(key))
        emit('    也可能没问题（官方格式若变更），但请再确认一次：')
        emit('    要的是「Key」，不是「安全密钥 Secret」。')
        if not args.force:
            emit('    确实要写入就加 --force 重跑。')
            return 1
        emit('    已加 --force，继续写入。')

    data = read_store(path)
    existed = isinstance(data.get('amap'), str) and data['amap'].strip()
    if existed and data['amap'].strip() == key:
        emit('    这个 key 与文件里现有的完全相同，无需重写。')
    else:
        if existed:
            emit('    将覆盖原有的 amap 键（旧值 %s）' % mask(data['amap']))
        data['amap'] = key
        write_store(path, data)
        emit('[OK] 已写入 %s' % path)
        emit('    amap = %s' % mask(key))
        restrict_hint(path)

    env_shadow_warning()

    if args.verify:
        emit('')
        emit('—— 立即验证 ——')
        return verify(key)

    emit('')
    emit('下一步：验证 key 真能用（会真连一次高德，消耗 1 次搜索配额）')
    emit('    python "%s" --verify' % Path(__file__).name)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description='配置高德 Web 服务 API key（跨平台；上游脚本仅支持 macOS）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--key', default=None,
                    help='直接提供 key。注意：会留在 shell 历史里，能用交互输入就别用它')
    ap.add_argument('--verify', action='store_true',
                    help='写入后立即真连高德验证（也消耗 1 次搜索配额）')
    ap.add_argument('--status', action='store_true', help='只看状态，不写不改')
    ap.add_argument('--remove', action='store_true', help='移除凭据文件里的 amap 键')
    ap.add_argument('--force', action='store_true', help='key 格式不像 32 位 hex 时也强行写入')
    ap.add_argument('--path', default=None, help='凭据文件路径（默认 ~/.verified-travel-planner/credentials.json）')
    a = ap.parse_args()

    path = Path(a.path) if a.path else default_credential_file()

    if a.status and a.remove:
        emit('--status 与 --remove 不能同时用。')
        return 1
    if a.status:
        return cmd_status(path)
    if a.remove:
        return cmd_remove(path)
    return cmd_set(a, path)


if __name__ == '__main__':
    sys.exit(main())

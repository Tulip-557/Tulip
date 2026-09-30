#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印块动效覆盖必须带 !important（回归锁）。

2026-09-30 实测：入场动效 `html.js section .card{opacity:0}` 特异性高于打印块的
`.card{opacity:1}`，未滚动到的区块「直接打印/导出 PDF」整块空白
（CDP 打印媒体仿真：东莞 9/13 区块隐形）。根治 = 打印块动效覆盖加 `!important`。
本测试锁两条：正向锁 !important 在场；反向锁动效本体不许带 !important
（它若也带，打印块就永远压不过，修复即失效）。
"""
import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SKELETON = SKILL / 'assets' / '路书_基准骨架.html'

# 打印块里的动效覆盖规则（主打印块是文件里最后一个 @media print）
OVERRIDE = '.card,.day,.tip,.wday,.sos{opacity:1!important;transform:none!important}'


class TestPrintCss(unittest.TestCase):
    def setUp(self):
        self.css = SKELETON.read_text(encoding='utf-8')
        self.block = self.css[self.css.rindex('@media print'):]

    def test_print_block_animation_override_has_important(self):
        self.assertIn(OVERRIDE, self.block,
                      '打印块的动效覆盖丢了 !important——直接打印/导 PDF 会丢正文')

    def test_animation_rule_itself_has_no_important(self):
        m = re.search(r'html\.js section \.card[^{}]*\{[^}]*opacity:0[^}]*\}',
                      self.css)
        self.assertIsNotNone(m, '未找到入场动效规则（选择器被改过？）')
        self.assertNotIn('!important', m.group(0),
                         '动效本体带了 !important，打印块将永远压不过它')


if __name__ == '__main__':
    unittest.main()

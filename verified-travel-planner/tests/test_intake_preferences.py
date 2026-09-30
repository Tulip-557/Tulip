# -*- coding: utf-8 -*-
"""intake.preferences（用户自报偏好）的校验负向样本。

把「个性化偏好」这条软字段的边界固化成断言：
- 只认 diet / pace / interests 三个键，未知键报错；
- diet / interests 接受字符串或字符串数组，数组元素不许为空串；
- pace 接受字符串或 {wake_time, nap} 对象，对象子键不许为空；
- 缺省不阻塞（进 assumptions，返回里可见），不追问不猜。

运行（零第三方依赖，标准库 unittest）：
    python -m unittest discover -s verified-travel-planner/tests -v
退出码：全绿 0，有 FAIL 1——与 evaluate / source_audit 同一纪律。
"""
import sys
import unittest
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILL_ROOT / 'engine'))

from travel_planner.intake import validate_trip_request  # noqa: E402


def _base():
    """一份必填项齐全的最小合法请求，作为各种 preferences 变体的底。"""
    return {
        "origin": "郑州",
        "destination": "上海",
        "start_date": "2026-09-29",
        "end_date": "2026-09-29",
        "travelers": 2,
        "budget_cny": 2000,
        "budget_scope": "PER_PERSON",
        "style": "balanced",
        "mobility": {"level": "MODERATE"},
        "browser_approval": {"xiaohongshu": "ANONYMOUS_ONLY", "ota": "ANONYMOUS_ONLY"},
    }


class PreferencesIntakeTest(unittest.TestCase):

    def test_absent_defaults_to_assumption(self):
        """缺 preferences 不阻塞，进 assumptions（软字段，追问不猜）。"""
        result = validate_trip_request(_base())
        self.assertIn(result["status"], ("READY", "NEEDS_CLARIFICATION"))
        self.assertTrue(any("偏好" in a for a in result["assumptions"]))

    def test_valid_full_preferences(self):
        """三键齐全、形态正确 → 无 error。"""
        req = _base()
        req["preferences"] = {
            "diet": ["不吃辣", "想吃本帮菜"],
            "pace": {"wake_time": "9 点前不起床", "nap": "午饭后要小睡"},
            "interests": ["建筑", "本地菜市场"],
        }
        result = validate_trip_request(req)
        self.assertEqual(result["errors"], [])

    def test_unknown_key_rejected(self):
        """未知键（如 foo）→ 报错，绝不静默放行。"""
        req = _base()
        req["preferences"] = {"foo": "bar"}
        result = validate_trip_request(req)
        self.assertTrue(any("unknown keys" in e for e in result["errors"]))

    def test_diet_accepts_string(self):
        req = _base()
        req["preferences"] = {"diet": "不吃辣"}
        result = validate_trip_request(req)
        self.assertEqual(result["errors"], [])

    def test_diet_rejects_non_string_type(self):
        """diet 给数字 → 报错（不是字符串也不是数组）。"""
        req = _base()
        req["preferences"] = {"diet": 123}
        result = validate_trip_request(req)
        self.assertTrue(any("diet" in e for e in result["errors"]))

    def test_diet_array_empty_string_rejected(self):
        req = _base()
        req["preferences"] = {"diet": ["", "不吃辣"]}
        result = validate_trip_request(req)
        self.assertTrue(any("diet" in e for e in result["errors"]))

    def test_pace_object_empty_subkey_rejected(self):
        req = _base()
        req["preferences"] = {"pace": {"wake_time": ""}}
        result = validate_trip_request(req)
        self.assertTrue(any("pace" in e for e in result["errors"]))

    def test_pace_accepts_string(self):
        req = _base()
        req["preferences"] = {"pace": "9 点前不起床"}
        result = validate_trip_request(req)
        self.assertEqual(result["errors"], [])

    def test_preferences_not_a_dict_rejected(self):
        req = _base()
        req["preferences"] = "不吃辣"
        result = validate_trip_request(req)
        self.assertTrue(any("preferences" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()

"""验真旅行规划器 · 确定性检查引擎

源码迁移自 tanweiping1012-source/travel-planner（MIT），并做 Windows 适配。
整个包**纯标准库、零第三方依赖**——所以「数字不骗人、排不通不给你」这条规矩
不依赖任何外部服务就能跑。

模块职责：
    timeutil     时区感知的时间解析与校验
    models       归一化数据模型（Location / Place / Route / Source）
    feasibility  确定性可行性检查（时间窗 / 换乘余量 / 营业时间 / 预算闭合）
    intake       需求采集校验（必填 / 冲突 / 可安全假设项）
    flight       机票报价校验（跨源一致性、时戳新鲜度）
    lodging      住宿报价校验（晚数推断、总价折算）
    rail         车次与接驳校验（席别归一、可用性汇总）
    research     目的地简报编译与内容校验
    geomatch     地理匹配度判别（防「查东京、得广西村庄」）
    amap         高德客户端（需自备 key；无 key 时相关项按规矩降级为 [D]）
    weather      天气实况与预报覆盖判定（窗口只有 4 天，属临行前能力）
    workflow     高德实况快照采集
    diagnostics  环境自检（doctor）
    credentials  凭据读取（环境变量 → 本地凭据文件 → macOS 钥匙串）

许可：MIT，见 skill 根目录 LICENSE；第三方声明见 THIRD_PARTY_NOTICES.md。
"""

from .amap import AmapClient, AmapError
from .credentials import (CredentialError, CredentialStore,
                          KeychainCredentialStore, PROVIDERS)
from .diagnostics import (
    build_doctor_report,
    default_data_dir,
    detect_client,
    detect_clients,
)
from .feasibility import evaluate_itinerary
from .flight import FlightDataError, offer_to_activity, validate_offers
from .geomatch import assess_geocode, coverage_hint
from .intake import validate_trip_request
from .lodging import (LodgingDataError, nights_between, normalize_offer,
                      total_for_stay)
from .lodging import validate_offers as validate_lodging_offers
from .models import Location, Place, Route, Source, to_dict
from .rail import (RailDataError, normalize_query_result, normalize_train,
                   parse_duration, select_trains, summarize_availability,
                   train_to_activity)
from .research import compile_destination_brief, validate_plan_content
from .timeutil import parse_datetime, require_aware
from .weather import PRE_DEPARTURE_LINE, assess_forecast
from .workflow import collect_amap_snapshot

__all__ = [
    # 可行性（本 skill 的判定核心）
    "evaluate_itinerary",
    # 需求采集
    "validate_trip_request",
    # 报价与车次校验
    "validate_offers",
    "validate_lodging_offers",
    "normalize_offer",
    "total_for_stay",
    "nights_between",
    "normalize_train",
    "normalize_query_result",
    "select_trains",
    "train_to_activity",
    "summarize_availability",
    "parse_duration",
    "offer_to_activity",
    # 内容与地理
    "compile_destination_brief",
    "validate_plan_content",
    "assess_geocode",
    "coverage_hint",
    # 实况数据（需 key）
    "AmapClient",
    "collect_amap_snapshot",
    "assess_forecast",
    "PRE_DEPARTURE_LINE",
    # 环境与凭据
    "build_doctor_report",
    "default_data_dir",
    "detect_client",
    "detect_clients",
    "CredentialStore",
    "KeychainCredentialStore",
    "PROVIDERS",
    # 模型与工具
    "Location",
    "Place",
    "Route",
    "Source",
    "to_dict",
    "parse_datetime",
    "require_aware",
    # 异常
    "AmapError",
    "CredentialError",
    "FlightDataError",
    "LodgingDataError",
    "RailDataError",
]

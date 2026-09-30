"""Credential access that keeps provider secrets outside prompts and source code.

迁移自 tanweiping1012-source/travel-planner（MIT），并做 Windows 适配。

上游只支持 macOS 钥匙串（用 subprocess 调 ``security`` 命令）。在 Windows 上那条
路径直接不可用，而本机主力是 Windows，所以改成三级回退；且**非 macOS 平台完全不
加载 subprocess**，保持引擎「零外部依赖」的性质：

    ① 环境变量        —— CI 与一次性使用首选，优先级最高
    ② 本地凭据文件    —— 跨平台，默认 ~/.verified-travel-planner/credentials.json
    ③ macOS 钥匙串    —— 仅 darwin，保持上游行为不变

任何一级拿到即返回，并把来源一并交回，便于 doctor 如实报告。
密钥永远不进提示词、不进产物、不进日志。
"""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

if sys.platform == "darwin":  # 仅 macOS 才需要，Windows/Linux 不加载
    import subprocess


class CredentialError(RuntimeError):
    """Raised when a provider credential cannot be loaded."""


@dataclass(frozen=True)
class CredentialSpec:
    service: str
    account: str
    environment_variable: str
    legacy_services: Sequence[str] = ()


PROVIDERS = {
    "amap": CredentialSpec(
        service="travel-planner-mvp",
        account="amap-api-key",
        environment_variable="AMAP_API_KEY",
        legacy_services=("trae-travel-planner",),
    ),
}

DEFAULT_CREDENTIAL_FILE = os.path.join(
    os.path.expanduser("~"), ".verified-travel-planner", "credentials.json"
)


class CredentialStore:
    """Load a provider credential from env → local file → (macOS) Keychain."""

    def __init__(
        self,
        environment: Optional[Mapping[str, str]] = None,
        credential_file: Optional[str] = None,
        command_runner=None,
    ):
        self._environment = environment if environment is not None else os.environ
        self._credential_file = credential_file or DEFAULT_CREDENTIAL_FILE
        self._command_runner = command_runner or (
            subprocess.run if sys.platform == "darwin" else None
        )

    # ---------- ① 环境变量 ----------
    def _from_environment(self, spec: CredentialSpec) -> Optional[str]:
        value = str(self._environment.get(spec.environment_variable, "") or "").strip()
        return value or None

    # ---------- ② 本地凭据文件 ----------
    def _from_file(self, spec: CredentialSpec) -> Optional[str]:
        path = self._credential_file
        if not path or not os.path.exists(path):
            return None
        try:
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialError("凭据文件无法读取（%s）：%s" % (path, exc)) from exc
        if not isinstance(data, dict):
            raise CredentialError("凭据文件顶层应为 JSON 对象：%s" % path)

        # 允许两种写法：{"amap": "key"} 或 {"AMAP_API_KEY": "key"}
        for key in ("amap", spec.account, spec.environment_variable):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    # ---------- ③ macOS 钥匙串 ----------
    def _from_keychain(self, spec: CredentialSpec) -> Optional[Tuple[str, str]]:
        if self._command_runner is None:
            return None
        for index, service in enumerate((spec.service, *spec.legacy_services)):
            try:
                result = self._command_runner(
                    ["security", "find-generic-password",
                     "-s", service, "-a", spec.account, "-w"],
                    check=True, capture_output=True, text=True,
                )
            except FileNotFoundError:
                return None
            except Exception:
                continue
            value = (result.stdout or "").strip()
            if value:
                source = "macos-keychain" if index == 0 else "macos-keychain-legacy"
                return value, source
        return None

    def get_with_source(self, provider: str) -> Tuple[str, str]:
        try:
            spec = PROVIDERS[provider]
        except KeyError as exc:
            raise CredentialError("不支持的凭据来源: %s" % provider) from exc

        value = self._from_environment(spec)
        if value:
            return value, "environment"

        value = self._from_file(spec)
        if value:
            return value, "file"

        keychain = self._from_keychain(spec)
        if keychain:
            return keychain

        raise CredentialError(
            "%s 的 API key 未配置。三选一：\n"
            "   ① 设环境变量 %s\n"
            "   ② 写入凭据文件 %s（形如 {\"amap\": \"你的key\"}）\n"
            "   ③ macOS 钥匙串（security add-generic-password -s travel-planner-mvp "
            "-a %s -w）" % (provider, spec.environment_variable,
                            self._credential_file, spec.account)
        )

    def get(self, provider: str) -> str:
        value, _source = self.get_with_source(provider)
        return value

    def status(self, provider: str) -> dict:
        try:
            _value, source = self.get_with_source(provider)
        except CredentialError as exc:
            return {"provider": provider, "status": "MISSING", "message": str(exc)}
        return {"provider": provider, "status": "CONFIGURED", "source": source}


# 上游类名向后兼容（上游 __init__ 与文档使用 KeychainCredentialStore）
KeychainCredentialStore = CredentialStore

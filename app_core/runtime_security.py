import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_guard_cb: Optional[Callable[[], None]] = None
_validator_cb: Optional[Callable[[str, str], bool]] = None


def configure_runtime_security(
    *,
    guard_cb: Optional[Callable[[], None]] = None,
    validator_cb: Optional[Callable[[str, str], bool]] = None,
) -> None:
    global _guard_cb, _validator_cb
    _guard_cb = guard_cb
    _validator_cb = validator_cb


def set_runtime_guard(guard_cb: Optional[Callable[[], None]]) -> None:
    global _guard_cb
    _guard_cb = guard_cb


def set_runtime_validator(validator_cb: Optional[Callable[[str, str], bool]]) -> None:
    global _validator_cb
    _validator_cb = validator_cb


def run_runtime_guard() -> None:
    guard_cb = _guard_cb
    if guard_cb is None:
        return
    guard_cb()


def run_runtime_validator(hw_id: str, key: str) -> bool:
    validator_cb = _validator_cb
    if validator_cb is None:
        return True
    try:
        return bool(validator_cb(hw_id, key))
    except Exception as exc:
        logger.error(f"运行时校验执行失败: {exc}", exc_info=True)
        return False

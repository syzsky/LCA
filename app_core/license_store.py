import base64
import hashlib
import logging
import os
import platform
from typing import Optional

from app_core.client_identity import get_hardware_id
from app_core.runtime_security import run_runtime_guard as _run_guard
from utils.app_paths import get_license_cache_path

logger = logging.getLogger(__name__)

LICENSE_FILE = get_license_cache_path()


def _build_dynamic_salt() -> str:
    _run_guard()
    salt_prefix = base64.b64decode("bGljZW5zZV9jYWNoZQ==").decode("utf-8")
    salt_year = base64.b64decode("MjAyNA==").decode("utf-8")
    system_info = f"{platform.machine()}{platform.processor()}"
    dynamic_part = hashlib.sha256(system_info.encode("utf-8")).hexdigest()[:8]
    return f"{salt_prefix}_{dynamic_part}_{salt_year}"


def encrypt_license_key(key: str, hardware_id: str) -> str:
    try:
        _run_guard()
        salt = _build_dynamic_salt()
        encryption_key = hashlib.sha256(f"{hardware_id}{salt}".encode("utf-8")).digest()

        encrypted_bytes = []
        for index, byte in enumerate(key.encode("utf-8")):
            mixed = byte ^ encryption_key[index % len(encryption_key)]
            mixed = ((mixed << 3) | (mixed >> 5)) & 0xFF
            mixed = mixed ^ (index & 0xFF)
            encrypted_bytes.append(mixed & 0xFF)

        encrypted_data = base64.b64encode(bytes(encrypted_bytes)).decode("utf-8")
        checksum = hashlib.sha256(f"{key}{hardware_id}".encode("utf-8")).hexdigest()[:8]
        return f"{encrypted_data}:{checksum}"
    except Exception as exc:
        logger.error(f"加密许可证密钥失败: {exc}")
        return ""


def decrypt_license_key(encrypted_key: str, hardware_id: str) -> Optional[str]:
    try:
        _run_guard()
        if ":" not in encrypted_key:
            return None

        encrypted_data, stored_checksum = encrypted_key.rsplit(":", 1)

        try:
            salt = _build_dynamic_salt()
            encryption_key = hashlib.sha256(f"{hardware_id}{salt}".encode("utf-8")).digest()
            encrypted_bytes = base64.b64decode(encrypted_data.encode("utf-8"))
            decrypted_bytes = []

            for index, byte in enumerate(encrypted_bytes):
                mixed = byte ^ (index & 0xFF)
                mixed = ((mixed >> 3) | (mixed << 5)) & 0xFF
                mixed = mixed ^ encryption_key[index % len(encryption_key)]
                decrypted_bytes.append(mixed & 0xFF)

            decrypted_key = bytes(decrypted_bytes).decode("utf-8")
            expected_checksum = hashlib.sha256(
                f"{decrypted_key}{hardware_id}".encode("utf-8")
            ).hexdigest()[:8]
            if stored_checksum == expected_checksum:
                return decrypted_key
        except Exception:
            pass

        try:
            salt = "license_cache_2024"
            encryption_key = hashlib.sha256(f"{hardware_id}{salt}".encode("utf-8")).digest()
            encrypted_bytes = base64.b64decode(encrypted_data.encode("utf-8"))
            decrypted_bytes = []

            for byte_index, byte in enumerate(encrypted_bytes):
                decrypted_bytes.append(byte ^ encryption_key[byte_index % len(encryption_key)])

            decrypted_key = bytes(decrypted_bytes).decode("utf-8")
            expected_checksum = hashlib.sha256(
                f"{decrypted_key}{hardware_id}".encode("utf-8")
            ).hexdigest()[:8]
            if stored_checksum == expected_checksum:
                logger.info("使用旧格式成功解密许可证缓存")
                return decrypted_key
        except Exception:
            pass

        logger.warning("许可证缓存解密失败，所有方法都无效")
        return None
    except Exception as exc:
        logger.warning(f"解密许可证密钥失败: {exc}")
        return None


def load_local_license() -> Optional[str]:
    if os.path.exists(LICENSE_FILE):
        try:
            with open(LICENSE_FILE, "r", encoding="utf-8") as file_obj:
                encrypted_key = file_obj.read().strip()
                if encrypted_key:
                    hardware_id = get_hardware_id()
                    if hardware_id:
                        decrypted_key = decrypt_license_key(encrypted_key, hardware_id)
                        if decrypted_key:
                            logger.info("从加密缓存成功加载许可证密钥")
                            return decrypted_key
                        logger.warning("解密许可证密钥失败，可能硬件ID已变更")
                    else:
                        logger.warning("无法获取硬件ID进行解密")
                else:
                    logger.warning(f"许可证缓存文件 {LICENSE_FILE} 为空")
        except Exception as exc:
            logger.error(f"读取许可证缓存文件失败: {exc}")
    else:
        logger.info(f"许可证缓存文件 {LICENSE_FILE} 不存在")
    return None


def has_local_license_cache() -> bool:
    try:
        return os.path.exists(LICENSE_FILE)
    except Exception:
        return False


def clear_local_license() -> bool:
    try:
        if not os.path.exists(LICENSE_FILE):
            return False
        os.remove(LICENSE_FILE)
        logger.info(f"已删除许可证缓存文件: {LICENSE_FILE}")
        return True
    except Exception as exc:
        logger.warning(f"删除许可证缓存文件失败: {exc}")
        return False


def save_local_license(key: str) -> None:
    try:
        hardware_id = get_hardware_id()
        if hardware_id:
            encrypted_key = encrypt_license_key(key, hardware_id)
            if encrypted_key:
                license_dir = os.path.dirname(LICENSE_FILE)
                if license_dir:
                    os.makedirs(license_dir, exist_ok=True)
                with open(LICENSE_FILE, "w", encoding="utf-8") as file_obj:
                    file_obj.write(encrypted_key)
                logger.info(f"许可证密钥已加密保存到 {LICENSE_FILE}")
            else:
                logger.error("加密许可证密钥失败")
        else:
            logger.error("无法获取硬件ID进行加密")
    except Exception as exc:
        logger.error(f"保存加密许可证缓存失败: {exc}")

import logging
import math
import threading

from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ControlCenterWorkflowOcrMixin:
    def _get_plugin_enabled_state_for_ocr(self, log_prefix: str):
        try:
            from app_core.plugin_bridge import is_plugin_enabled
            return bool(is_plugin_enabled())
        except ImportError:
            return False
        except Exception as e:
            logger.warning(
                f"\u3010{log_prefix}\u3011\u68c0\u67e5\u63d2\u4ef6\u6a21\u5f0f\u65f6\u53d1\u751f\u5f02\u5e38: {e}\uff0c\u8df3\u8fc7OCR\u76f8\u5173\u5904\u7406\u4ee5\u907f\u514d\u51b2\u7a81"
            )
            return None

    def _precreate_ocr_processes(self, valid_windows: list):
        plugin_enabled = self._get_plugin_enabled_state_for_ocr("OCR\u9884\u521b\u5efa")
        if plugin_enabled is None:
            return None
        if plugin_enabled:
            logger.info("\u3010OCR\u9884\u521b\u5efa\u3011\u68c0\u6d4b\u5230\u63d2\u4ef6\u6a21\u5f0f\u5df2\u542f\u7528\uff0c\u8df3\u8fc7OCR\u5b50\u8fdb\u7a0b\u521b\u5efa\uff08\u63d2\u4ef6\u6a21\u5f0f\u4f7f\u7528OLA\u63d2\u4ef6\u8fdb\u884cOCR\uff09")
            self.log_message("\u63d2\u4ef6\u6a21\u5f0f\uff1a\u8df3\u8fc7OCR\u5b50\u8fdb\u7a0b\u521b\u5efa")
            return None

        window_count = len(valid_windows)
        process_count = math.ceil(window_count / 3)
        logger.info(f"\u3010OCR\u9884\u521b\u5efa\u3011\u68c0\u6d4b\u5230 {window_count} \u4e2a\u6709\u6548\u7a97\u53e3\uff0c\u9700\u8981\u521b\u5efa {process_count} \u4e2aOCR\u8fdb\u7a0b")
        self.log_message(
            f"\u9884\u521b\u5efaOCR\u8fdb\u7a0b: {window_count}\u4e2a\u7a97\u53e3 -> {process_count}\u4e2a\u8fdb\u7a0b\uff08\u540e\u53f0\u6267\u884c\uff09"
        )

        def precreate_in_background():
            try:
                from services.multiprocess_ocr_pool import get_multiprocess_ocr_pool

                ocr_pool = get_multiprocess_ocr_pool()
                for index, window_data in enumerate(valid_windows, start=1):
                    if getattr(self, "_is_closing", False):
                        logger.info("\u3010OCR\u9884\u521b\u5efa\u3011\u68c0\u6d4b\u5230\u4e2d\u63a7\u7a97\u53e3\u5173\u95ed\uff0c\u505c\u6b62\u7ee7\u7eed\u9884\u521b\u5efa")
                        break
                    hwnd = window_data["hwnd"]
                    title = window_data["title"]
                    success = ocr_pool.preregister_window(title, hwnd)
                    if success:
                        logger.info(
                            f"\u3010OCR\u9884\u521b\u5efa\u3011\u7a97\u53e3 {index}/{window_count} \u6ce8\u518c\u6210\u529f: {title} (HWND: {hwnd})"
                        )
                    else:
                        logger.warning(
                            f"\u3010OCR\u9884\u521b\u5efa\u3011\u7a97\u53e3 {index}/{window_count} \u6ce8\u518c\u5931\u8d25: {title} (HWND: {hwnd})"
                        )
                logger.info(f"\u3010OCR\u9884\u521b\u5efa\u3011\u5b8c\u6210\uff0c\u5df2\u521b\u5efa {process_count} \u4e2aOCR\u8fdb\u7a0b")
            except Exception as e:
                logger.exception(f"\u3010OCR\u9884\u521b\u5efa\u3011\u5931\u8d25: {e}")

        precreate_thread = threading.Thread(
            target=precreate_in_background,
            daemon=True,
            name="OCR-Precreate",
        )
        precreate_thread.start()
        logger.info("\u3010OCR\u9884\u521b\u5efa\u3011\u540e\u53f0\u7ebf\u7a0b\u5df2\u542f\u52a8\uff0c\u4e0d\u963b\u585eUI")
        return precreate_thread

    def _force_cleanup_ocr_processes(self):
        plugin_enabled = self._get_plugin_enabled_state_for_ocr("OCR\u6e05\u7406")
        if plugin_enabled is None:
            return
        if plugin_enabled:
            logger.info("\u3010OCR\u6e05\u7406\u3011\u68c0\u6d4b\u5230\u63d2\u4ef6\u6a21\u5f0f\u5df2\u542f\u7528\uff0c\u8df3\u8fc7OCR\u5b50\u8fdb\u7a0b\u6e05\u7406\uff08\u63d2\u4ef6\u6a21\u5f0f\u672a\u521b\u5efaOCR\u5b50\u8fdb\u7a0b\uff09")
            return

        logger.info("\u3010OCR\u6e05\u7406\u3011\u5f00\u59cb\u5f3a\u5236\u5173\u95ed\u6240\u6709OCR\u5b50\u8fdb\u7a0b...")
        self.log_message("\u6b63\u5728\u5173\u95edOCR\u8fdb\u7a0b...")
        try:
            from services.multiprocess_ocr_pool import cleanup_ocr_services_on_stop

            cleanup_ocr_services_on_stop(deep_cleanup=True)
            logger.info("\u3010OCR\u6e05\u7406\u3011\u5df2\u5f3a\u5236\u5173\u95ed\u6240\u6709OCR\u5b50\u8fdb\u7a0b")
            self.log_message("OCR\u8fdb\u7a0b\u5df2\u5173\u95ed")
        except Exception as e:
            logger.exception(f"\u3010OCR\u6e05\u7406\u3011\u5173\u95edOCR\u5b50\u8fdb\u7a0b\u5931\u8d25: {e}")

    def _check_and_cleanup_ocr_if_all_done(self):
        plugin_enabled = self._get_plugin_enabled_state_for_ocr("OCR\u5ef6\u8fdf\u6e05\u7406")
        if plugin_enabled is None:
            return
        if plugin_enabled:
            logger.debug("\u3010OCR\u5ef6\u8fdf\u6e05\u7406\u3011\u63d2\u4ef6\u6a21\u5f0f\u5df2\u542f\u7528\uff0c\u8df3\u8fc7OCR\u8fdb\u7a0b\u6e05\u7406\u68c0\u67e5")
            return
        if self.is_any_task_running():
            return

        logger.info("\u3010OCR\u5ef6\u8fdf\u6e05\u7406\u3011\u6240\u6709\u4efb\u52a1\u5df2\u5b8c\u6210\uff0c\u542f\u52a830\u79d2\u5ef6\u8fdf\u6e05\u7406\u5b9a\u65f6\u5668")
        try:
            app = QApplication.instance()
            if not app:
                return
            main_windows = [w for w in app.topLevelWidgets() if hasattr(w, "task_state_manager")]
            if not main_windows:
                return
            main_window = main_windows[0]
            task_state_manager = getattr(main_window, "task_state_manager", None)
            if task_state_manager:
                task_state_manager.confirm_stopped()
                logger.info("\u3010OCR\u5ef6\u8fdf\u6e05\u7406\u3011\u5df2\u542f\u52a830\u79d2\u5ef6\u8fdf\u5b9a\u65f6\u5668\uff08\u4e2d\u63a7\u4efb\u52a1\u5b8c\u6210\uff09")
        except Exception as e:
            logger.warning(f"\u542f\u52a8OCR\u5ef6\u8fdf\u6e05\u7406\u5931\u8d25: {e}")

import logging

from PySide6.QtWidgets import QMessageBox

from utils.window_coordinate_common import center_window_on_widget_screen
from utils.window_activation_utils import show_and_activate_overlay

logger = logging.getLogger(__name__)


class MainWindowDialogMixin:
    def open_control_center(self):

        """打开中控软件窗口"""

        try:

            # 工具 关键修复：打开中控前验证窗口句柄是否仍然有效

            logger.info("准备打开中控，开始验证窗口句柄...")

            valid_windows = []

            invalid_windows = []

            import win32gui

            for window_info in self.bound_windows:

                window_title = window_info.get('title', '未知窗口')

                hwnd = window_info.get('hwnd')

                # 验证窗口句柄是否仍然有效

                try:

                    if hwnd and win32gui.IsWindow(hwnd):

                        # 窗口存在，只验证IsWindow，不验证MuMu Manager列表

                        class_name = win32gui.GetClassName(hwnd)

                        logger.info(f"验证窗口: {window_title} (HWND: {hwnd} = 0x{hwnd:08X}, 类名: {class_name})")

                        # 窗口有效

                        valid_windows.append(window_title)

                        logger.debug(f"窗口句柄有效: {window_title} (HWND: {hwnd})")

                    else:

                        # 窗口句柄无效（窗口已关闭）

                        invalid_windows.append(window_title)

                        logger.warning(f"窗口句柄无效: {window_title} (HWND: {hwnd}) - 窗口已关闭")

                except Exception as e:

                    logger.error(f"验证窗口句柄失败: {window_title} - {e}")

                    invalid_windows.append(window_title)

            # 显示验证结果

            logger.info(f"窗口句柄验证完成: 有效 {len(valid_windows)} 个, 无效 {len(invalid_windows)} 个")

            if invalid_windows:

                # 弹出警告

                reply = QMessageBox.warning(

                    self,

                    "窗口句柄验证警告",

                    f"以下窗口句柄已失效：\n\n" + "\n".join(f"  • {w}" for w in invalid_windows) +

                    f"\n\n请在全局设置中重新绑定这些窗口后再打开中控。\n\n" +

                    "是否仍要打开中控？（可能导致操作失败）",

                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

                    QMessageBox.StandardButton.No

                )

                if reply != QMessageBox.StandardButton.Yes:

                    logger.info("用户取消打开中控")

                    return

            # 导入中控窗口类

            from ui.control_center_parts.control_center import ControlCenterWindow

            # 创建中控窗口

            self.control_center = ControlCenterWindow(

                bound_windows=self.bound_windows,

                task_modules=self.task_modules,

                parent=self

            )

            # 显示中控窗口
            center_window_on_widget_screen(self.control_center, self)
            show_and_activate_overlay(self.control_center, log_prefix='中控窗口', focus=True)

            # 禁用主窗口的快捷键

            self._disable_main_window_hotkeys()

            # 监听中控窗口关闭事件

            self.control_center.destroyed.connect(self._on_control_center_closed)

            logging.info("中控软件已启动")

        except Exception as e:

            logging.error(f"启动中控软件失败: {e}")

            import traceback

            logging.error(traceback.format_exc())

            QMessageBox.warning(self, "错误", f"启动中控软件失败: {e}")

    def show_sponsor_dialog(self):

        """打开赞助对话框，显示微信和支付宝收款二维码（带防篡改保护）"""

        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget

        from PySide6.QtCore import Qt

        from PySide6.QtGui import QPixmap

        from utils.qrcode_security import QRCodeSecurityManager

        dialog = QDialog(self)

        dialog.setWindowTitle("赞助作者")

        dialog.setModal(True)

        dialog.setMinimumSize(600, 500)

        # 主布局

        main_layout = QVBoxLayout(dialog)

        main_layout.setSpacing(20)

        main_layout.setContentsMargins(30, 30, 30, 30)

        # 标题

        title_label = QLabel("感谢您的支持！❤️")

        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label.setObjectName("sponsor_title")

        # 增大标题字体

        title_font = title_label.font()

        title_font.setPointSize(16)

        title_font.setBold(True)

        title_label.setFont(title_font)

        main_layout.addWidget(title_label)

        # 说明文字

        desc_label = QLabel("如果这个工具对您有帮助，欢迎赞助支持项目持续开发")

        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        desc_label.setObjectName("sponsor_desc")

        main_layout.addWidget(desc_label)

        # 二维码容器

        qr_container = QWidget()

        qr_layout = QHBoxLayout(qr_container)

        qr_layout.setSpacing(40)

        # 微信二维码（从安全管理器加载）

        wechat_widget = QWidget()

        wechat_layout = QVBoxLayout(wechat_widget)

        wechat_layout.setSpacing(10)

        wechat_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        wechat_title = QLabel("微信赞助")

        wechat_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        wechat_title.setObjectName("wechat_title")

        wechat_layout.addWidget(wechat_title)

        wechat_qr = QLabel()

        wechat_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 从安全管理器加载微信二维码（带完整性验证）

        wechat_pixmap = QRCodeSecurityManager.get_wechat_qrcode()

        if not wechat_pixmap.isNull():

            wechat_qr.setPixmap(wechat_pixmap.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        else:

            wechat_qr.setText("微信收款码\n请配置二维码")

            wechat_qr.setObjectName("qr_placeholder")

        wechat_layout.addWidget(wechat_qr)

        qr_layout.addWidget(wechat_widget)

        # 支付宝二维码（从安全管理器加载）

        alipay_widget = QWidget()

        alipay_layout = QVBoxLayout(alipay_widget)

        alipay_layout.setSpacing(10)

        alipay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        alipay_title = QLabel("支付宝赞助")

        alipay_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        alipay_title.setObjectName("alipay_title")

        alipay_layout.addWidget(alipay_title)

        alipay_qr = QLabel()

        alipay_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 从安全管理器加载支付宝二维码（带完整性验证）

        alipay_pixmap = QRCodeSecurityManager.get_alipay_qrcode()

        if not alipay_pixmap.isNull():

            alipay_qr.setPixmap(alipay_pixmap.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        else:

            alipay_qr.setText("支付宝收款码\n请配置二维码")

            alipay_qr.setObjectName("qr_placeholder")

        alipay_layout.addWidget(alipay_qr)

        qr_layout.addWidget(alipay_widget)

        main_layout.addWidget(qr_container)

        # 关闭按钮

        close_btn = QPushButton("关闭")

        close_btn.clicked.connect(dialog.accept)

        close_btn.setProperty("class", "primary")

        close_btn_layout = QHBoxLayout()

        close_btn_layout.addStretch()

        close_btn_layout.addWidget(close_btn)

        close_btn_layout.addStretch()

        main_layout.addLayout(close_btn_layout)

        # 不再使用硬编码样式，让全局主题控制对话框样式

        # 对话框样式现在由 themes/dark.qss 和 themes/light.qss 统一管理

        center_window_on_widget_screen(dialog, self)

        dialog.exec()

        dialog.deleteLater()

    def open_variable_pool(self):

        """打开变量池管理对话框"""

        try:

            from ui.dialogs.variable_pool_dialog import VariablePoolDialog

            current_task_id = getattr(self, "_active_execution_task_id", None)

            if current_task_id is None:

                current_task_id = getattr(self, "_last_finished_task_id", None)

            if current_task_id is None and hasattr(self, "workflow_tab_widget") and self.workflow_tab_widget:

                try:

                    current_task_id = self.workflow_tab_widget.get_current_task_id()

                except Exception:

                    current_task_id = None

            dialog = VariablePoolDialog(

                self,

                parameter_panel=getattr(self, "parameter_panel", None),

                workflow_task_id=current_task_id,

            )

            center_window_on_widget_screen(dialog, self)

            dialog.exec()

        except Exception as e:

            logging.error(f"打开变量池对话框时出错: {e}")

            try:

                from ui.dialogs.custom_dialogs import ErrorWrapper

                ErrorWrapper.show_exception(

                    parent=self,

                    error=e,

                    title="变量池错误",

                    context="打开变量池"

                )

            except Exception as dialog_error:

                logging.error(f"显示错误对话框失败: {dialog_error}")

                try:

                    from PySide6.QtWidgets import QMessageBox

                    QMessageBox.critical(self, "错误", f"打开变量池失败: {e}\n\n{dialog_error}")

                except Exception:

                    pass

    def _get_configured_qq_groups(self):

        """从配置中读取并过滤有效QQ群链接。"""

        groups = self.config.get('qq_group_links', [])

        if not isinstance(groups, list):

            return []

        valid_groups = []

        for group in groups:

            if not isinstance(group, dict):

                continue

            group_name = str(group.get('name', '')).strip()

            group_id = str(group.get('id', '')).strip()

            group_url = str(group.get('url', '')).strip()

            if not group_name or not group_id or not group_url:

                continue

            valid_groups.append({

                'name': group_name,

                'id': group_id,

                'url': group_url,

            })

        return valid_groups

    def _show_qq_group_dialog(self):

        """显示QQ群对话框"""

        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

        dialog = QDialog(self)

        dialog.setWindowTitle("交流群")

        dialog.setMinimumWidth(300)

        layout = QVBoxLayout(dialog)

        # 标题

        title_label = QLabel("选择要加入的群：")

        title_label.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")

        layout.addWidget(title_label)

        qq_groups = self._get_configured_qq_groups()

        for group in qq_groups:

            group_btn = QPushButton(f"{group['name']}：{group['id']}")

            group_btn.setStyleSheet("padding: 10px; text-align: left;")

            group_btn.clicked.connect(

                lambda _checked=False, group_url=group['url']: self._open_qq_group(group_url, dialog)

            )

            layout.addWidget(group_btn)

        if not qq_groups:

            empty_label = QLabel("暂无可用群链接")

            empty_label.setStyleSheet("color: #666; padding: 8px 2px;")

            layout.addWidget(empty_label)

        # 关闭按钮

        close_btn = QPushButton("取消")

        close_btn.clicked.connect(dialog.reject)

        layout.addWidget(close_btn)

        center_window_on_widget_screen(dialog, self)

        dialog.exec()

        dialog.deleteLater()

    def _open_qq_group(self, url: str, dialog):

        """打开QQ群链接并关闭对话框"""

        import webbrowser

        webbrowser.open(url)

        dialog.accept()

    def _on_control_center_closed(self):

        """中控窗口关闭时的回调"""

        try:

            logger.info("中控窗口已关闭，恢复主窗口快捷键")

            # 重新注册主窗口的快捷键

            self._update_hotkeys()

        except Exception as e:

            logger.error(f"恢复主窗口快捷键失败: {e}")

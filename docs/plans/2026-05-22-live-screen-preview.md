# 实时画面预览 实施计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**目标：** 在浮动状态窗口中添加实时游戏画面预览，执行工作流时定时截图并显示

**架构：**
- 在 `FloatingStatusWindow` 的展开面板中添加画面预览标签页（与日志并列）
- 使用 `services/screenshot_pool.capture_window()` 定时截图
- 使用 QTimer 控制刷新频率（默认 1 秒/帧）
- hwnd 从主窗口的 `bound_windows` 获取

**涉及文件：**
- Modify: `ui/widgets/floating_status_window.py`
- Modify: `ui/main_window_parts/main_window_floating_status_mixin.py`

---

### Task 1: 在 FloatingStatusWindow 添加画面预览控件

**目标：** 在浮动窗口展开面板添加 QLabel 用于显示截图、切换按钮、和 QTimer

**文件：**
- Modify: `ui/widgets/floating_status_window.py`

**修改内容：**

在 `__init__` 中增加：
- `self._live_preview_enabled = False`
- `self._preview_timer = QTimer(self)` — 触发截图刷新
- `self._preview_label = QLabel("实时画面已关闭")` — 显示截图
- `self._btn_live_preview = QPushButton()` — 开关按钮

在 `_init_ui` 中：
- header 增加实时预览开关按钮（在展开按钮左边）
- expanded 区域增加 QStackedWidget 或 splitter，包含 log_panel 和 preview_panel
- preview_panel 包含 QLabel（显示截图）+ 刷新频率下拉框

新增方法：
- `_toggle_live_preview()` — 开关实时预览
- `_set_preview_interval(ms)` — 设置刷新间隔
- `_capture_and_display()` — 截图并显示（由 timer 触发）
- `_numpy_to_pixmap(np_array, max_width)` — 转换 numpy 截图到缩放后的 QPixmap
- `set_target_hwnd(hwnd)` — 设置要截图的窗口句柄
- `on_step_log` 不需要改
- `on_execution_finished` — 停掉 timer
- `on_execution_started` — 可选自动开启预览
- `stop_live_preview()` — 停止截图

常量调整：`EXPANDED_HEIGHT` 从 320 改为 480（给画面留空间）

**关键代码：**

```python
# numpy → QPixmap 转换（依赖：PIL.Image, cv2）
def _numpy_to_pixmap(self, frame: np.ndarray, max_width: int = 360) -> Optional[QPixmap]:
    try:
        import cv2
        from PIL import Image as PILImage
        height, width = frame.shape[:2]
        # 缩放保持比例
        scale = max_width / width
        new_w = max_width
        new_h = int(height * scale)
        # BGR → RGB → PIL → QPixmap
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if scale < 1.0:
            rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
        pil_img = PILImage.fromarray(rgb)
        from PySide6.QtGui import QImage
        data = pil_img.tobytes("raw", "RGB")
        qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGB888)
        return QPixmap.fromImage(qimg)
    except Exception as e:
        logger.warning(f"转换截图失败: {e}")
        return None
```

```python
# 截图并显示
def _capture_and_display(self):
    if not self._live_preview_enabled or not self._target_hwnd:
        return
    try:
        from services.screenshot_pool import capture_window
        frame = capture_window(self._target_hwnd, client_area_only=True, use_cache=False)
        if frame is not None:
            pixmap = self._numpy_to_pixmap(frame)
            if pixmap:
                self._preview_label.setPixmap(pixmap)
                self._preview_label.setFixedSize(pixmap.width(), pixmap.height())
    except Exception as e:
        logger.warning(f"实时截图失败: {e}")
```

```python
# toggle
def _toggle_live_preview(self):
    self._live_preview_enabled = not self._live_preview_enabled
    if self._live_preview_enabled and self._target_hwnd:
        self._btn_live_preview.setIcon(create_icon("visible", 14))
        self._btn_live_preview.setToolTip("关闭实时画面")
        self._preview_timer.start(1000)
    else:
        self._btn_live_preview.setIcon(create_icon("hidden", 14))
        self._btn_live_preview.setToolTip("开启实时画面")
        self._preview_timer.stop()
        self._preview_label.setText("实时画面已关闭")
        self._preview_label.setPixmap(QPixmap())
```

**UI 布局变化：**
```
HEADER: [●] [状态文字] [start][pause][stop][📷切换预览] [↕展开] [✕隐藏]
EXPANDED (480px):
  ┌─────────────────────────────────┐
  │ [日志 tab] [画面 tab]           │  ← tab bar
  ├─────────────────────────────────┤
  │                                 │
  │  画面区域 / 日志区域            │
  │                                 │
  └─────────────────────────────────┘
```

**注意：** 不要加 tab bar（太复杂），直接用 splitter 上下分：
- 上半：日志 (LogView)
- 下半：画面预览 (QLabel + 刷新频率)

---

### Task 2: 连接主窗口的 hwnd 到浮动窗口

**目标：** 当工作流执行时，浮动窗口能获取到绑定的游戏窗口句柄

**文件：**
- Modify: `ui/main_window_parts/main_window_floating_status_mixin.py`

**修改内容：**

在 `MainWindowFloatingStatusMixin` 中：
- `_init_floating_status_window` 之后，监听窗口绑定变化
- 在 `on_workflow_started` 或 `on_execution_started` 时，从 main_window 获取第一个绑定的 hwnd
- 通过 `self._floating.set_target_hwnd(hwnd)` 设置到浮动窗口

最简单的方式：在 `FloatingWindowController.on_workflow_started` 中获取 hwnd：

```python
def on_workflow_started(self):
    self._is_workflow_running = True
    self._floating.clear_logs()
    self._floating.on_execution_started()
    self._update_visibility()
    # 获取绑定的第一个窗口 hwnd
    hwnd = self._get_first_bound_hwnd()
    if hwnd:
        self._floating.set_target_hwnd(hwnd)
```

```python
def _get_first_bound_hwnd(self) -> Optional[int]:
    for window_info in getattr(self._main, 'bound_windows', []) or []:
        hwnd = int(window_info.get('hwnd', 0) or 0)
        if hwnd > 0:
            return hwnd
    return None
```

---

## 实施顺序

1. Task 1: 先在 floating_status_window.py 中添加所有画面预览控件和逻辑
2. Task 2: 再在 main_window_floating_status_mixin.py 中连接 hwnd

---

## 验证方式（用户测试）

1. 打开 LCA，绑定一个游戏窗口
2. 最小化主窗口 → 浮动窗口出现
3. 展开浮动窗口 → 看到日志上方/下方出现画面预览区域
4. 点击「实时画面」按钮 → 开始显示游戏画面刷新
5. 工作流停止 → 预览自动停掉
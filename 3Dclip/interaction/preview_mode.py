from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Callable
import vtk
import numpy as np
from PyQt5 import QtWidgets, QtCore, QtGui

from interaction.base_mode import BaseInteractionMode
from interaction.view import NoLeftDragCameraStyle
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager


Vec3 = Tuple[float, float, float]


class PreviewMode(BaseInteractionMode):
    """
    預覽模式互動管理，提供給 MainWindow 使用。
    - `toggle_selecting()`：切換「移動物體」模式
    - `toggle_marking()`：切換「切片滑動」模式
    - `clear_markers()`：清除預覽標記並還原顯示
    - `reset()`：只重置在預覽模式中移動過的切割結果物件
    - `commit()`：顯示透明度設定視窗
    - 依賴 `ObjectPropertyManager` 與 `Object3DManager` 管理場景資料與 3D actor
    """




    def __init__(
        self,
        interactor: vtk.vtkRenderWindowInteractor,
        renderer: vtk.vtkRenderer,
        prop_manager: ObjectPropertyManager,
        obj3d_manager: Object3DManager,
        main_window=None,
    ) -> None:
        super().__init__("preview", interactor)
        self._renderer = renderer
        self._prop_mgr = prop_manager
        self._obj3d_mgr = obj3d_manager
        self._main_window = main_window
        
        # UI 狀態
        self._selecting_enabled: bool = False  # 是否啟用移動物體模式
        self._marking_enabled: bool = False    # 是否啟用切片滑動模式
        self._slice_slider_mode: bool = False  # 是否啟用預覽切片滑動
        
        self._preview_slice_mgr = getattr(main_window, "preview_slice_mgr", None)

        # 拖曳狀態
        self._dragging: bool = False
        self._selected_actor: Optional[vtk.vtkActor] = None
        self._selected_obj_id: Optional[int] = None
        self._last_pos: Optional[Tuple[int, int]] = None
        self._last_world_pos: Optional[Vec3] = None
        
        # 拖曳互動器
        self._drag_interactor: Optional[vtk.vtkInteractorStyle] = None
        self._original_interactor_style: Optional[vtk.vtkInteractorStyle] = None
        
        # 回呼
        self.on_selected: Optional[Callable[[int, str, bool], None]] = None

        # 進入預覽模式時記錄的基準狀態
        self._baseline_opacity: Dict[int, float] = {}
        self._baseline_transform: Dict[int, vtk.vtkTransform] = {}

    # ---------------------------
    # 模式生命週期
    # ---------------------------

    def on_mode_enter(self) -> None:
        """進入預覽模式。"""
        print("[PreviewMode] 進入預覽模式", flush=True)
        
        # 先關閉拖曳與切片互動
        self._stop_drag_mode()
        self._set_sliders_enabled(False)
        
        # 重設狀態旗標
        self._selecting_enabled = False
        self._marking_enabled = False
        self._slice_slider_mode = False

        # 記錄目前場景狀態，供重置使用
        self._capture_preview_baseline()
        
        print("[PreviewMode] 已完成預覽模式初始化", flush=True)

    def on_mode_exit(self) -> None:
        """離開預覽模式。"""
        print("[PreviewMode] 離開預覽模式", flush=True)
        
        # 關閉拖曳與切片互動
        self._stop_drag_mode()
        self._set_sliders_enabled(False)
        
        # 重設狀態旗標
        self._selecting_enabled = False
        self._marking_enabled = False
        self._slice_slider_mode = False

    # ---------------------------
    # UI 操作 - 移動物體
    # ---------------------------

    def set_selecting_enabled(self, enabled: bool) -> None:
        """開啟或關閉預覽模式的移動物體功能。"""
        print(f"[PreviewMode] 移動物體模式：{enabled}")
        
        # 若狀態沒有改變則直接返回
        if enabled == self._selecting_enabled:
            return
            
        self._selecting_enabled = bool(enabled)
        
        if self._selecting_enabled:
            # 進入拖曳模式時關閉切片滑動
            self._marking_enabled = False
            self._slice_slider_mode = False
            self._set_sliders_enabled(False)
            self._start_drag_mode()
            self._append_to_terminal("已開啟移動物體模式，可拖曳切割後物件，按 ESC 可退出。\n")
        else:
            # 關閉拖曳模式
            self._stop_drag_mode()
            self._append_to_terminal("已關閉移動物體模式。\n")

    def toggle_selecting(self) -> None:
        """切換移動物體模式。"""
        self.set_selecting_enabled(not self._selecting_enabled)

    # ---------------------------
    # UI 操作 - 切片滑動
    # ---------------------------

    def set_marking_enabled(self, enabled: bool) -> None:
        """開啟或關閉預覽模式的切片滑動模式。"""
        print(f"[PreviewMode] 切片滑動模式：{enabled}")
        
        if enabled == self._marking_enabled:
            return
            
        self._marking_enabled = bool(enabled)
        
        if self._marking_enabled:
            # 進入切片滑動時關閉拖曳
            self._selecting_enabled = False
            self._slice_slider_mode = True
            self._stop_drag_mode()  # 確保拖曳模式已關閉
            # 啟用 preview slice
            if self._preview_slice_mgr:
                self._preview_slice_mgr.enter()
            self._set_sliders_enabled(True)
            self._append_to_terminal(
                "已開啟切片滑動模式。\n"
                "你可以拖動切片與調整預覽切面。\n"
                "按 ESC 可退出此模式。\n"
            )
        else:
            # 關閉切片滑動
            self._slice_slider_mode = False
            self._set_sliders_enabled(False)
            # 關閉 preview slice
            if self._preview_slice_mgr:
                self._preview_slice_mgr.exit()
            self._append_to_terminal("已關閉切片滑動模式。\n")

    def toggle_marking(self) -> None:
        """切換切片滑動模式。"""
        self.set_marking_enabled(not self._marking_enabled)

    # ---------------------------
    # UI 操作 - 套用設定
    # ---------------------------

    def commit(self) -> Optional[Tuple[List[int], List[int], List[int]]]:
        """
        顯示透明度設定視窗。
        """
        print("[PreviewMode] 套用預覽模式設定", flush=True)
        self._show_opacity_dialog()
        return [], [], []

    def _show_opacity_dialog(self):
        """顯示預覽模式的不透明度設定視窗。"""
        if not self._main_window:
            print("[PreviewMode] 找不到 main_window，無法開啟透明度設定視窗")
            return

        opacity_snapshot = self._snapshot_opacity_state()
        applied = False
        
        dialog = QtWidgets.QDialog(self._main_window)
        dialog.setWindowTitle("透明度設定")
        dialog.setModal(True)
        dialog.setFixedSize(600, 480)  # 放大對話框，避免元件太擠
        
        # 主版面
        main_layout = QtWidgets.QGridLayout(dialog)
        main_layout.setContentsMargins(25, 25, 25, 20)
        main_layout.setSpacing(12)
        main_layout.setColumnMinimumWidth(0, 100)
        main_layout.setColumnMinimumWidth(1, 350)
        main_layout.setColumnStretch(1, 1)
        
        # === 1. 標題 ===
        title_label = QtWidgets.QLabel("透明度設定")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 18px;
                font-weight: bold;
                color: #FFFFFF;
                background-color: #1F2A35;
                padding: 12px;
                border-radius: 6px;
            }
        """)
        title_label.setAlignment(QtCore.Qt.AlignCenter)
        title_label.setFixedHeight(45)
        main_layout.addWidget(title_label, 0, 0, 1, 2)
        
        # === 2. 套用對象 ===
        selector_label = QtWidgets.QLabel("套用對象")
        selector_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #E8EDF3;")
        selector_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        selector_label.setFixedHeight(35)
        main_layout.addWidget(selector_label, 1, 0)
        
        object_selector = QtWidgets.QComboBox()
        object_selector.setStyleSheet("""
            QComboBox {
                background-color: #1C232C;
                color: #E6EBF0;
                border: 1px solid #2E3A45;
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 14px;
                min-height: 35px;
            }
            QComboBox:hover {
                border: 1px solid #0078D7;
                background-color: #232B36;
            }
            QComboBox QAbstractItemView {
                background-color: #1F2731;
                color: #E6EBF0;
                selection-background-color: #0078D7;
                border: 1px solid #2E3A45;
                padding: 8px;
                font-size: 14px;
            }
        """)
        object_selector.addItem("全部物件 (All Objects)", "all")
        object_selector.addItem("原始物件 (Original)", "original")
        object_selector.addItem("切割結果 (Result)", "result")
        object_selector.addItem("已選取物件 (Selected)", "selected")
        object_selector.setFixedHeight(40)
        main_layout.addWidget(object_selector, 1, 1)
        
        # === 3. 透明度 ===
        slider_label = QtWidgets.QLabel("透明度")
        slider_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #E8EDF3;")
        slider_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        slider_label.setFixedHeight(40)
        main_layout.addWidget(slider_label, 2, 0)
        
        slider_container = QtWidgets.QHBoxLayout()
        slider_container.setSpacing(15)
        
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(100)  # 預設 100%
        slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        slider.setTickInterval(10)
        slider.setStyleSheet("""
            QSlider {
                height: 40px;
            }
            QSlider::groove:horizontal {
                background: #1E252E;
                height: 10px;
                border-radius: 5px;
                border: 1px solid #2E3A45;
            }
            QSlider::sub-page:horizontal {
                background: #2196F3;
                border-radius: 5px;
            }
            QSlider::handle:horizontal {
                background: #0078D7;
                width: 24px;
                height: 24px;
                margin: -7px 0;
                border-radius: 12px;
                border: 2px solid #8AB4F8;
            }
            QSlider::handle:horizontal:hover {
                background: #2196F3;
                border: 2px solid #FFFFFF;
            }
        """)
        slider_container.addWidget(slider)
        
        value_label = QtWidgets.QLabel("100%")
        value_label.setStyleSheet("""
            QLabel {
                color: #2196F3;
                font-size: 16px;
                font-weight: bold;
                background-color: #1F2731;
                padding: 8px 16px;
                border-radius: 4px;
                border: 1px solid #2E3A45;
                min-width: 80px;
                max-width: 80px;
                text-align: center;
            }
        """)
        value_label.setAlignment(QtCore.Qt.AlignCenter)
        value_label.setFixedSize(90, 40)
        slider.valueChanged.connect(lambda v: value_label.setText(f"{v}%"))
        slider_container.addWidget(value_label)
        
        main_layout.addLayout(slider_container, 2, 1)
        
        # === 4. 說明 ===
        hint_label = QtWidgets.QLabel("提示：0% = 完全透明，100% = 完全不透明")
        hint_label.setStyleSheet("""
            QLabel {
                color: #8AB4F8;
                font-size: 12px;
                font-style: italic;
                padding: 6px;
                background-color: #1A2028;
                border-radius: 4px;
            }
        """)
        hint_label.setAlignment(QtCore.Qt.AlignCenter)
        hint_label.setFixedHeight(32)
        main_layout.addWidget(hint_label, 3, 0, 1, 2)
        
        # === 5. 即時預覽 ===
        preview_checkbox = QtWidgets.QCheckBox("即時預覽")
        preview_checkbox.setStyleSheet("""
            QCheckBox {
                color: #E6EBF0;
                font-size: 14px;
                spacing: 12px;
                padding: 8px;
                background-color: #1A2028;
                border-radius: 4px;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
                border: 1px solid #2E3A45;
                border-radius: 4px;
                background-color: #1C232C;
            }
            QCheckBox::indicator:checked {
                background-color: #0078D7;
                border: 1px solid #8AB4F8;
            }
        """)
        preview_checkbox.setChecked(True)
        preview_checkbox.setFixedHeight(40)
        main_layout.addWidget(preview_checkbox, 4, 0, 1, 2)
        
        # === 6. 快速設定 ===
        preset_title = QtWidgets.QLabel("快速設定")
        preset_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFFFFF; padding: 3px;")
        main_layout.addWidget(preset_title, 5, 0, 1, 2)
        
        # === 7. 快速設定按鈕 ===
        preset_layout = QtWidgets.QHBoxLayout()
        preset_layout.setSpacing(10)
        preset_layout.setContentsMargins(0, 5, 0, 10)
        
        presets = [
            ("0% 完全透明", 0),
            ("25%", 25),
            ("50%", 50), 
            ("75%", 75),
            ("100% 完全不透明", 100),
        ]
        
        for text, value in presets:
            btn = QtWidgets.QPushButton(text)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #232B36;
                    color: #E6EBF0;
                    border: 1px solid #2E3A45;
                    border-radius: 4px;
                    padding: 6px 10px;
                    font-size: 13px;
                    min-width: 85px;
                    max-width: 100px;
                    min-height: 32px;
                }
                QPushButton:hover {
                    background-color: #2D3A46;
                    border: 1px solid #0078D7;
                    color: white;
                }
            """)
            btn.clicked.connect(lambda checked, v=value: slider.setValue(v))
            preset_layout.addWidget(btn)
        
        main_layout.addLayout(preset_layout, 6, 0, 1, 2)
        
        # === 8. 分隔線 ===
        line = QtWidgets.QFrame()
        line.setStyleSheet("""
            QFrame {
                background-color: #28313C;
                max-height: 1px;
                min-height: 1px;
                border: none;
                margin: 5px 0;
            }
        """)
        line.setFrameShape(QtWidgets.QFrame.HLine)
        main_layout.addWidget(line, 7, 0, 1, 2)
        
        # === 9. 操作按鈕 ===
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(25)
        button_layout.setContentsMargins(0, 5, 0, 0)
        
        btn_apply = QtWidgets.QPushButton("套用")
        btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #0078D7;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 30px;
                font-size: 14px;
                font-weight: bold;
                min-width: 120px;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #2196F3;
            }
        """)
        
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #2E3A45;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 30px;
                font-size: 14px;
                font-weight: bold;
                min-width: 120px;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #3B4A5A;
            }
        """)
        
        # 套用透明度
        def apply_opacity():
            nonlocal applied
            opacity = slider.value() / 100.0
            target = object_selector.currentData()
            self._apply_opacity(opacity, target)
            applied = True
            dialog.accept()

        btn_apply.clicked.connect(apply_opacity)
        btn_cancel.clicked.connect(dialog.reject)

        def rollback_if_needed():
            if not applied:
                self._restore_opacity_state(opacity_snapshot)

        dialog.rejected.connect(rollback_if_needed)
        
        # 即時預覽更新
        def update_opacity():
            if preview_checkbox.isChecked():
                opacity = slider.value() / 100.0
                target = object_selector.currentData()
                self._apply_opacity(opacity, target)
        
        slider.valueChanged.connect(update_opacity)
        object_selector.currentIndexChanged.connect(update_opacity)
        preview_checkbox.toggled.connect(lambda checked: update_opacity() if checked else None)
        
        button_layout.addStretch()
        button_layout.addWidget(btn_apply)
        button_layout.addWidget(btn_cancel)
        button_layout.addStretch()
        
        main_layout.addLayout(button_layout, 8, 0, 1, 2)
        
        # 設定對話框版面
        dialog.setLayout(main_layout)
        dialog.setFixedSize(600, 480)  # 固定大小
        
        # 開啟視窗時先套用 100%
        QtCore.QTimer.singleShot(50, lambda: self._apply_opacity(1.0, "all"))
        
        dialog.exec_()

    def _snapshot_opacity_state(self) -> Dict[int, float]:
        """記錄目前所有物件的不透明度狀態。"""
        return {obj.id: float(obj.opacity) for obj in self._prop_mgr.get_all_objects()}

    def _restore_opacity_state(self, opacity_snapshot: Dict[int, float]) -> None:
        """還原不透明度到屬性管理器、actor 與 UI。"""
        for obj_id, opacity in opacity_snapshot.items():
            so = self._prop_mgr.get_object(obj_id)
            if not so:
                continue

            self._prop_mgr.set_opacity(obj_id, opacity)
            actor = self._obj3d_mgr.get_actor(obj_id)
            if actor:
                actor.GetProperty().SetOpacity(opacity)

            if self._main_window:
                if so.kind == "original" and hasattr(self._main_window, 'object_list_widget'):
                    self._main_window.object_list_widget.refresh_from_manager(obj_id)
                if so.kind != "original" and hasattr(self._main_window, 'cut_list_widget'):
                    self._main_window.cut_list_widget.refresh_from_manager(obj_id)

        self._render()

    def _apply_opacity(self, opacity: float, target: str = "all"):
        """將不透明度套用到指定目標物件。"""
        obj_ids_modified = []
        
        if target == "all":
            # 全部物件
            for obj in self._prop_mgr.get_original_objects():
                # 1. 更新資料層
                self._prop_mgr.set_opacity(obj.id, opacity)
                
                # 2. 更新對應 Actor 的不透明度
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                    print(f"[PreviewMode] 已更新 Actor 不透明度：{opacity}")
                
                obj_ids_modified.append(obj.id)
            
            for obj in self._prop_mgr.get_result_objects():
                self._prop_mgr.set_opacity(obj.id, opacity)
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                obj_ids_modified.append(obj.id)
        
        elif target == "original":
            for obj in self._prop_mgr.get_original_objects():
                self._prop_mgr.set_opacity(obj.id, opacity)
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                obj_ids_modified.append(obj.id)
        
        elif target == "result":
            for obj in self._prop_mgr.get_result_objects():
                self._prop_mgr.set_opacity(obj.id, opacity)
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                obj_ids_modified.append(obj.id)
        
        elif target == "selected":
            selected_ids = list(self._prop_mgr.get_selected_objects())
            if selected_ids:
                for obj_id in selected_ids:
                    self._prop_mgr.set_opacity(obj_id, opacity)
                    obj_actor = self._obj3d_mgr.get_actor(obj_id)
                    if obj_actor:
                        obj_actor.GetProperty().SetOpacity(opacity)
                    obj_ids_modified.append(obj_id)
            elif self._selected_actor:
                # fallback: preview drag selection (for cut result actor)
                for obj in self._prop_mgr.get_all_objects():
                    obj_actor = self._obj3d_mgr.get_actor(obj.id)
                    if obj_actor == self._selected_actor:
                        self._prop_mgr.set_opacity(obj.id, opacity)
                        obj_actor.GetProperty().SetOpacity(opacity)
                        obj_ids_modified.append(obj.id)
                        break
        
        if not obj_ids_modified:
            self._append_to_terminal(f"沒有符合透明度目標的物件：{target}\n")
            return
        
        # 重新繪製畫面
        self._render()
        
        # 更新 UI 顯示
        if self._main_window:
            for obj_id in obj_ids_modified:
                obj = self._prop_mgr.get_object(obj_id)
                if obj.kind == "original":
                    if hasattr(self._main_window, 'object_list_widget'):
                        self._main_window.object_list_widget.refresh_from_manager(obj_id)
                else:
                    if hasattr(self._main_window, 'cut_list_widget'):
                        self._main_window.cut_list_widget.refresh_from_manager(obj_id)
        
        print(f"[PreviewMode] 已套用透明度 {opacity:.2f}，目標：{target}，物件數：{len(obj_ids_modified)}")

    def _set_selected_ids(self, obj_ids: List[int]) -> None:
        """同步 selected 狀態到資料層、UI 與 Actor 外觀。"""
        new_selected = set(obj_ids)
        prev_selected = set(self._prop_mgr.get_selected_objects())

        if new_selected == prev_selected:
            return

        to_clear = prev_selected - new_selected
        to_set = new_selected - prev_selected

        for obj_id in to_clear:
            self._prop_mgr.set_selected(obj_id, False)
            self._obj3d_mgr.update_actor_appearance(obj_id)
            if self._main_window:
                obj = self._prop_mgr.get_object(obj_id)
                if obj.kind == "original" and hasattr(self._main_window, 'object_list_widget'):
                    self._main_window.object_list_widget.refresh_from_manager(obj_id)
                if obj.kind != "original" and hasattr(self._main_window, 'cut_list_widget'):
                    self._main_window.cut_list_widget.refresh_from_manager(obj_id)

        for obj_id in to_set:
            self._prop_mgr.set_selected(obj_id, True)
            self._obj3d_mgr.update_actor_appearance(obj_id)
            if self._main_window:
                obj = self._prop_mgr.get_object(obj_id)
                if obj.kind == "original" and hasattr(self._main_window, 'object_list_widget'):
                    self._main_window.object_list_widget.refresh_from_manager(obj_id)
                if obj.kind != "original" and hasattr(self._main_window, 'cut_list_widget'):
                    self._main_window.cut_list_widget.refresh_from_manager(obj_id)

    # ---------------------------
    # 拖曳互動
    # ---------------------------

    def _start_drag_mode(self):
        """開始拖曳模式。"""
        print("[PreviewMode] 啟動拖曳模式")
        
        # 保存目前互動器樣式
        self._original_interactor_style = self._interactor.GetInteractorStyle()
        
        # 建立拖曳互動器
        if not self._drag_interactor:
            self._setup_drag_interactor()
        
        if self._drag_interactor:
            try:
                self._drag_interactor.SetDefaultRenderer(self._renderer)
            except Exception:
                pass
            try:
                self._drag_interactor.SetInteractor(self._interactor)
            except Exception:
                pass
            self._interactor.SetInteractorStyle(self._drag_interactor)
            print("[PreviewMode] 已切換到拖曳互動器")

    def _stop_drag_mode(self):
        """停止拖曳模式。"""
        print("[PreviewMode] 停止拖曳模式")
        
        # 還原原本的互動器樣式
        if self._original_interactor_style:
            self._interactor.SetInteractorStyle(self._original_interactor_style)
            print("[PreviewMode] 已還原原本的互動模式", flush=True)
        
        # 清除拖曳狀態
        self._dragging = False
        self._selected_actor = None
        self._selected_obj_id = None
        self._last_pos = None
        self._last_world_pos = None
        self._selecting_enabled = False
        
        # 重新繪製畫面
        self._render()

    def _setup_drag_interactor(self):
        """建立拖曳互動器。"""
        print("[PreviewMode] 正在建立拖曳互動器", flush=True)

        class DragInteractor(NoLeftDragCameraStyle):
            def __init__(outer_self, manager):
                super().__init__()
                outer_self.manager = manager
                outer_self._dragging = False
                outer_self._suppress_camera = False
                outer_self._selected_actor = None
                outer_self._last_pos = None
                outer_self._last_world_pos = None
                outer_self._original_color = None

            def OnLeftButtonDown(self):
                """處理左鍵按下，挑選可拖曳的切割結果物件。"""
                interactor = self.GetInteractor()
                x, y = interactor.GetEventPosition()
                self._last_pos = (x, y)
                
                # 使用 picker 取得滑鼠下的 actor
                picker = vtk.vtkPropPicker()
                if picker.Pick(x, y, 0, self.manager._renderer):
                    actor = picker.GetActor()
                    
                    if actor:
                        # 判斷是否為切割結果物件
                        result_obj_id = self.manager._get_obj_id_from_actor(actor)
                        is_result = False
                        if result_obj_id is not None:
                            try:
                                obj = self.manager._prop_mgr.get_object(result_obj_id)
                                is_result = (obj.kind == "result")
                            except Exception:
                                is_result = False

                        if is_result and result_obj_id is not None:
                            # 開始拖曳
                            self._dragging = True
                            self._suppress_camera = False
                            self._selected_actor = actor
                            self.manager._selected_obj_id = result_obj_id
                            self._last_world_pos = self.manager._display_to_world_on_camera_plane(
                                x, y, actor.GetCenter()
                            )

                            # 暫時變色以表示已被選取
                            self._original_color = actor.GetProperty().GetColor()
                            actor.GetProperty().SetColor(0, 0.8, 1)  # 拖曳中高亮
                            
                            print(f"[PreviewMode] 開始拖曳物件")
                            self.manager._append_to_terminal("開始拖曳切割結果物件\n")
                            if result_obj_id is not None:
                                self.manager._set_selected_ids([result_obj_id])
                        else:
                            # 點到非結果物件時，不進入拖曳
                            self._dragging = False
                            self._suppress_camera = True
                            self._selected_actor = None
                            self.manager._selected_obj_id = None
                            self._last_world_pos = None
                    else:
                        # 沒有 pick 到 actor
                        self._dragging = False
                        self._suppress_camera = False
                        self._selected_actor = None
                        self._last_world_pos = None
                        super().OnLeftButtonDown()
                else:
                    # 點到空白區域
                    self._dragging = False
                    self._suppress_camera = False
                    self._selected_actor = None
                    self.manager._selected_obj_id = None
                    self._last_world_pos = None
                    super().OnLeftButtonDown()
            
            def OnLeftButtonUp(self):
                """處理拖曳結束後的左鍵放開。"""
                if self._selected_actor and hasattr(self, '_original_color'):
                    # 還原顏色
                    self._selected_actor.GetProperty().SetColor(self._original_color)
                    print(f"[PreviewMode] 結束拖曳物件")
                    self.manager._append_to_terminal("已結束拖曳物件\n")
                
                suppress_camera = self._suppress_camera
                self._dragging = False
                self._suppress_camera = False
                self._selected_actor = None
                self.manager._selected_obj_id = None
                self._last_pos = None
                self._last_world_pos = None
                
                # 若不需抑制則交回原本互動器
                if not suppress_camera:
                    super().OnLeftButtonUp()
            
            def OnMouseMove(self):
                """拖曳滑鼠移動時更新物件位置。"""
                if self._dragging and self._selected_actor:
                    # 拖曳中的物件跟隨滑鼠移動
                    interactor = self.GetInteractor()
                    x, y = interactor.GetEventPosition()
                    
                    drag_world = self.manager._display_to_world_on_camera_plane(
                        x, y, self._selected_actor.GetCenter()
                    )
                    if self._last_world_pos is not None and drag_world is not None:
                        delta = np.array(drag_world) - np.array(self._last_world_pos)
                        if np.linalg.norm(delta) > 1e-6 and self.manager._selected_obj_id is not None:
                            obj = self.manager._prop_mgr.get_object(self.manager._selected_obj_id)
                            new_transform = vtk.vtkTransform()
                            new_transform.DeepCopy(obj.transform)
                            new_transform.PostMultiply()
                            new_transform.Translate(float(delta[0]), float(delta[1]), float(delta[2]))
                            self.manager._prop_mgr.set_transform(self.manager._selected_obj_id, new_transform)
                            self.manager._obj3d_mgr.update_actor_transform(self.manager._selected_obj_id)
                            if (
                                self.manager._main_window is not None
                                and hasattr(self.manager._main_window, "_apply_all_slice_clipping")
                            ):
                                self.manager._main_window._apply_all_slice_clipping()
                        self._last_world_pos = drag_world
                    elif drag_world is not None:
                        self._last_world_pos = drag_world
                    
                    self._last_pos = (x, y)
                    self.manager._render()
                else:
                    # 非拖曳時維持原本相機互動
                    if not self._suppress_camera:
                        super().OnMouseMove()
            
            def OnKeyPress(self):
                """處理按鍵事件，例如按 ESC 離開選取模式。"""
                key = self.GetInteractor().GetKeySym()
                if key == "Escape":
                    if self.manager._selecting_enabled:
                        self.manager.set_selecting_enabled(False)
                        self.manager._append_to_terminal("已退出移動物體模式\n")
                else:
                    super().OnKeyPress()
        
        self._drag_interactor = DragInteractor(self)
        try:
            self._drag_interactor.SetDefaultRenderer(self._renderer)
        except Exception:
            pass
        try:
            self._drag_interactor.SetInteractor(self._interactor)
        except Exception:
            pass
        print("[PreviewMode] 拖曳互動器建立完成", flush=True)

    def _get_obj_id_from_actor(self, actor: vtk.vtkProp3D) -> Optional[int]:
        obj_id = self._obj3d_mgr.get_obj_id_from_actor(actor)
        if obj_id is not None:
            return obj_id

        # preview cap actor 不在一般 mapping 中，這裡補做 fallback
        preview_caps = getattr(self._obj3d_mgr, "_preview_cap_actors", None)
        if preview_caps:
            for pid, pact in preview_caps.items():
                if pact is actor:
                    return pid
        return None

    # ---------------------------

    def _display_to_world_on_camera_plane(
        self,
        x: int,
        y: int,
        plane_origin: Vec3,
    ) -> Optional[Vec3]:
        camera = self._renderer.GetActiveCamera()
        if camera is None:
            return None

        position = np.array(camera.GetPosition(), dtype=float)
        focal_point = np.array(camera.GetFocalPoint(), dtype=float)
        normal = focal_point - position
        norm = np.linalg.norm(normal)
        if norm < 1e-8:
            return None
        normal = normal / norm

        self._renderer.SetDisplayPoint(float(x), float(y), 0.0)
        self._renderer.DisplayToWorld()
        p0 = self._renderer.GetWorldPoint()
        self._renderer.SetDisplayPoint(float(x), float(y), 1.0)
        self._renderer.DisplayToWorld()
        p1 = self._renderer.GetWorldPoint()

        if abs(p0[3]) < 1e-12 or abs(p1[3]) < 1e-12:
            return None

        ray_start = np.array((p0[0] / p0[3], p0[1] / p0[3], p0[2] / p0[3]), dtype=float)
        ray_end = np.array((p1[0] / p1[3], p1[1] / p1[3], p1[2] / p1[3]), dtype=float)
        ray_dir = ray_end - ray_start
        denom = float(np.dot(ray_dir, normal))
        if abs(denom) < 1e-8:
            return None

        origin = np.array(plane_origin, dtype=float)
        t = float(np.dot(origin - ray_start, normal) / denom)
        hit = ray_start + ray_dir * t
        return (float(hit[0]), float(hit[1]), float(hit[2]))

    # 切片滑動
    # ---------------------------

    def _set_sliders_enabled(self, enabled: bool):
        """啟用或停用預覽切片滑桿。"""
        if not self._main_window:
            return
            
        try:
            sliders = [
                self._main_window.ui.Sld_sagittal_5,
                self._main_window.ui.Sld_coronal_5,
                self._main_window.ui.Sld_axial_5
            ]
            
            for slider in sliders:
                if slider:
                    slider.setEnabled(enabled)
            
            # 更新按鈕文字
            if hasattr(self._main_window.ui, 'btn_mark'):
                btn_text = "停止標記" if enabled else "標記"
                self._main_window.ui.btn_mark.setText(btn_text)
                
        except Exception as e:
            print(f"[PreviewMode] 更新切片滑桿狀態失敗：{e}")

    # ---------------------------
    # 重置功能
    # ---------------------------

    def clear_markers(self) -> None:
        """清除預覽標記並還原預覽狀態。"""
        self.reset_presentation_view()

    def reset(self) -> None:
        """只還原預覽模式中被移動過的切割結果物件。"""
        print("[PreviewMode] 重置已移動的切割結果物件", flush=True)

        restored_ids: List[int] = []
        for obj in self._prop_mgr.get_result_objects():
            baseline = self._baseline_transform.get(obj.id)
            if baseline is None:
                continue
            self._prop_mgr.set_transform(obj.id, baseline)
            self._obj3d_mgr.update_actor_transform(obj.id)
            restored_ids.append(obj.id)

            if self._main_window and hasattr(self._main_window, "cut_list_widget"):
                self._main_window.cut_list_widget.refresh_from_manager(obj.id)

        if self._main_window is not None and hasattr(self._main_window, "_apply_all_slice_clipping"):
            self._main_window._apply_all_slice_clipping()
        else:
            self._render()

        self._append_to_terminal(f"已重置 {len(restored_ids)} 個已移動的切割結果物件\n")

    def _reset_opacity(self):
        """將所有 actor 的不透明度重設為 1.0。"""
        actors = self._renderer.GetActors()
        actors.InitTraversal()
        actor = actors.GetNextItem()
        while actor:
            actor.GetProperty().SetOpacity(1.0)
            actor = actors.GetNextItem()

    def _capture_preview_baseline(self) -> None:
        """記錄進入預覽模式時的基準狀態，用於重置。"""
        self._baseline_opacity = {}
        self._baseline_transform = {}

        for obj in self._prop_mgr.get_all_objects():
            self._baseline_opacity[obj.id] = float(obj.opacity)
            baseline_transform = vtk.vtkTransform()
            baseline_transform.DeepCopy(obj.transform)
            self._baseline_transform[obj.id] = baseline_transform

    def reset_presentation_view(self) -> None:
        """將預覽模式中的物件透明度與變換還原為基準狀態。"""
        print("[PreviewMode] 還原預覽模式狀態", flush=True)

        self._stop_drag_mode()
        self._set_sliders_enabled(False)
        self._selecting_enabled = False
        self._marking_enabled = False
        self._slice_slider_mode = False
        self._set_selected_ids([])

        for obj in self._prop_mgr.get_all_objects():
            obj_id = obj.id
            actor = self._obj3d_mgr.get_actor(obj_id)

            if obj_id in self._baseline_opacity:
                opacity = self._baseline_opacity[obj_id]
                self._prop_mgr.set_opacity(obj_id, opacity)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)

            if obj_id in self._baseline_transform:
                self._prop_mgr.set_transform(obj_id, self._baseline_transform[obj_id])
                self._obj3d_mgr.update_actor_transform(obj_id)

            if self._main_window:
                if obj.kind == "original" and hasattr(self._main_window, 'object_list_widget'):
                    self._main_window.object_list_widget.refresh_from_manager(obj_id)
                elif obj.kind != "original" and hasattr(self._main_window, 'cut_list_widget'):
                    self._main_window.cut_list_widget.refresh_from_manager(obj_id)

        self._render()
        self._append_to_terminal("預覽模式重置完成\n")

    # ---------------------------
    # 模式事件回呼
    # ---------------------------
    def on_key_press(self, key: str) -> bool:
        """處理鍵盤事件。"""
        # ESC 可退出互動模式
        if key == 'Escape':
            if self._selecting_enabled:
                self.set_selecting_enabled(False)
                return True
            elif self._slice_slider_mode:
                self.set_marking_enabled(False)
                return True
        return False

    def on_left_button_down(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if not self._selecting_enabled:
            return
        x, y = interactor.GetEventPosition()
        self._begin_drag_at(x, y)

    def on_left_button_up(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if not self._selecting_enabled:
            return
        self._finish_drag()

    def on_mouse_move(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if not self._selecting_enabled or not self._dragging or self._selected_actor is None:
            return
        x, y = interactor.GetEventPosition()
        self._drag_selected_to(x, y)

    # ---------------------------
    # 內部工具函式
    # ---------------------------

    def _begin_drag_at(self, x: int, y: int) -> None:
        picker = vtk.vtkPropPicker()
        if not picker.Pick(x, y, 0, self._renderer):
            self._finish_drag()
            return

        actor = picker.GetActor()
        if actor is None:
            self._finish_drag()
            return

        obj_id = self._get_obj_id_from_actor(actor)
        if obj_id is None:
            self._finish_drag()
            return

        try:
            obj = self._prop_mgr.get_object(obj_id)
        except Exception:
            self._finish_drag()
            return

        if obj.kind != "result":
            self._finish_drag()
            return

        self._dragging = True
        self._selected_actor = actor
        self._selected_obj_id = obj_id
        self._last_pos = (x, y)
        self._last_world_pos = self._display_to_world_on_camera_plane(x, y, actor.GetCenter())
        self._set_selected_ids([obj_id])

    def _drag_selected_to(self, x: int, y: int) -> None:
        if self._selected_actor is None or self._selected_obj_id is None:
            return

        drag_world = self._display_to_world_on_camera_plane(x, y, self._selected_actor.GetCenter())
        if self._last_world_pos is None or drag_world is None:
            self._last_pos = (x, y)
            self._last_world_pos = drag_world
            return

        delta = np.array(drag_world) - np.array(self._last_world_pos)
        if np.linalg.norm(delta) <= 1e-6:
            self._last_pos = (x, y)
            self._last_world_pos = drag_world
            return

        obj = self._prop_mgr.get_object(self._selected_obj_id)
        new_transform = vtk.vtkTransform()
        new_transform.DeepCopy(obj.transform)
        new_transform.PostMultiply()
        new_transform.Translate(float(delta[0]), float(delta[1]), float(delta[2]))
        self._prop_mgr.set_transform(self._selected_obj_id, new_transform)
        self._obj3d_mgr.update_actor_transform(self._selected_obj_id)

        if self._main_window is not None and hasattr(self._main_window, "_apply_all_slice_clipping"):
            self._main_window._apply_all_slice_clipping()
        else:
            self._render()

        self._last_pos = (x, y)
        self._last_world_pos = drag_world

    def _finish_drag(self) -> None:
        self._dragging = False
        self._selected_actor = None
        self._selected_obj_id = None
        self._last_pos = None
        self._last_world_pos = None

    def _render(self) -> None:
        """重新繪製 3D 視窗。"""
        rw = self._renderer.GetRenderWindow()
        if rw is not None:
            rw.Render()

    def _append_to_terminal(self, text: str):
        """將訊息輸出到主視窗終端區。"""
        if self._main_window and hasattr(self._main_window, '_append_terminal_text'):
            self._main_window._append_terminal_text(text)
        else:
            print(text, flush=True)

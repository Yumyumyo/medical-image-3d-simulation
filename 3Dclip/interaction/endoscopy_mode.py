from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple, Callable

import vtk

from PyQt5 import QtWidgets,QtCore

from interaction.base_mode import BaseInteractionMode
from interaction.view import NoLeftDragCameraStyle
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager
from utils.endoscope_camera import EndoscopeCamera


Vec3 = Tuple[float, float, float]


class EndoscopyMode(BaseInteractionMode):
    """
    互動模式模板（對齊 MainWindow 呼叫規格）：
    - toggle_selecting()：UI「選取物件」按鈕
    - toggle_marking()：UI「標記模式」按鈕
    - clear_markers()：UI「清除標記」按鈕
    - reset()：UI「重置」按鈕（若你有接）
    - commit()：UI「切割/套用」按鈕 & Enter
      如果有需要其他參數來跟我說，但通常是回傳這三個參數後: (changed_original_ids, new_result_ids, to_delete_result_ids)
        ，main_window就會更新右邊的UI，但是render要自己更新，可以去看一下其他cutmode的commit。
    - 很多呼叫物件的功能ObjectPropertyManager(資料層)和Object3DManager(3d顯示層)都有
        ，可以直接去看這兩個檔案，或是看plan_cut或tube_cut怎麼使用。
    """

    def __init__(
        self,
        interactor: vtk.vtkRenderWindowInteractor,
        renderer: vtk.vtkRenderer,
        prop_manager: ObjectPropertyManager,
        obj3d_manager: Object3DManager,
        main_window=None,  # 新增：用於透明度對話框
    ) -> None:
        super().__init__("endoscopy", interactor)
        self._renderer = renderer
        self._prop_mgr = prop_manager
        self._obj3d_mgr = obj3d_manager
        self._main_window = main_window  # 新增
        
        # 狀態標記
        self._selecting_enabled: bool = False
        self._marking_enabled: bool = False
        
        # 內視鏡相關
        self._endoscope_camera: Optional[EndoscopeCamera] = None
        self._surface_pick_mode: bool = False
        self._endoscope_active: bool = False
        self._original_camera_state = None
        self._move_speeds = [1.0, 5.0, 10.0, 20.0, 50.0]  # 多個速度檔位
        self._current_speed_index = 1  # 預設使用第二個速度（5.0）
        self._entry_marker_actor: Optional[vtk.vtkActor] = None

        # 外部插入模式（棒子）
        self._insert_mode: bool = False
        self._inserting: bool = False
        self._insert_base: Optional[Vec3] = None
        self._insert_base_display_z: Optional[float] = None
        self._rod_actor: Optional[vtk.vtkActor] = None
        self._rod_source: Optional[vtk.vtkCylinderSource] = None
        self._rod_radius: float = 1.5
        self._rod_min_length: float = 5.0
        self._rod_length: Optional[float] = None
        self._rod_style = NoLeftDragCameraStyle()
        self._style_before_insert_drag = None
        self._style_before_insert_mode = None
        self._dragging_rod = False
        self._dragging_view = False
        self._view_drag_last_xy: Optional[Tuple[int, int]] = None
        self._view_drag_sensitivity = 0.35
        self._hovering_rod = False
        self._rod_locked_style = vtk.vtkInteractorStyleUser()
        self._rod_anchor_point: Optional[Vec3] = None
        self._rod_ref_dir: Optional[Vec3] = None
        self._rod_p0: Optional[Vec3] = None
        self._rod_p1: Optional[Vec3] = None
        self._rod_drag_center: Optional[Vec3] = None
        self._rod_drag_radius: Optional[float] = None
        self._rod_drag_last_render_t = 0.0
        self._rod_drag_render_interval = 1.0 / 30.0
        self._picker = vtk.vtkCellPicker()
        if hasattr(self._picker, "SetTolerance"):
            self._picker.SetTolerance(0.005)
        self._picker.PickFromListOn()
        
        # 回調函數
        self.on_selected = None

    # ---------------------------
    # Mode lifecycle
    # ---------------------------
    def on_mode_enter(self) -> None:
        print("[EndoscopyMode] 進入內視鏡模式", flush=True)
        if self._insert_mode:
            self._rod_style.SetDefaultRenderer(self._renderer)
            self._rod_style.SetInteractor(self._interactor)
            self._interactor.SetInteractorStyle(self._rod_style)
            self._append_to_terminal("插入內視鏡模式：請點擊模型表面設定插入點\n")
        else:
            self._append_to_terminal("內視鏡模式（外部視角）\n")

    def on_mode_exit(self) -> None:
        print("[EndoscopyMode] 退出內視鏡模式", flush=True)
        if not self._insert_mode:
            self.deactivate_endoscope()
        self._clear_entry_marker()
        self._selecting_enabled = False
        self._marking_enabled = False
        self._append_to_terminal("內視鏡模式已關閉\n")

    def _init_endoscope_camera(self) -> None:
        """初始化內視鏡相機"""
        if not self._endoscope_camera:
            self._endoscope_camera = EndoscopeCamera(self._renderer)
            print("[EndoscopyMode] 內視鏡相機初始化完成", flush=True)

    def activate_endoscope(self) -> None:
        """啟動內視鏡模式"""
        if not self._endoscope_camera:
            self._init_endoscope_camera()
            
        # 保存原始相機狀態
        cam = self._renderer.GetActiveCamera()
        self._original_camera_state = {
            'position': cam.GetPosition(),
            'focal_point': cam.GetFocalPoint(),
            'view_up': cam.GetViewUp(),
            'view_angle': cam.GetViewAngle()
        }
        
        self._endoscope_camera.save_original_camera()
        self._endoscope_active = True
        print("[EndoscopyMode] 內視鏡模式已啟動", flush=True)

    def deactivate_endoscope(self) -> None:
        """關閉內視鏡模式"""
        if not self._original_camera_state:
            return
            
        # 恢復原始相機
        cam = self._renderer.GetActiveCamera()
        cam.SetPosition(self._original_camera_state['position'])
        cam.SetFocalPoint(self._original_camera_state['focal_point'])
        cam.SetViewUp(self._original_camera_state['view_up'])
        cam.SetViewAngle(self._original_camera_state['view_angle'])
        self._renderer.ResetCameraClippingRange()
        
        self._surface_pick_mode = False
        self._endoscope_active = False
        self._clear_entry_marker()
        self._render()
        print("[EndoscopyMode] 內視鏡模式已關閉", flush=True)

    # ---------------------------
    # UI actions
    # ---------------------------
    def set_selecting_enabled(self, enabled: bool) -> None:
        self._selecting_enabled = bool(enabled)
        print(f"[EndoscopyMode] selecting_enabled = {self._selecting_enabled}", flush=True)
        if self._selecting_enabled:
            self._marking_enabled = False
            self.enter_surface_selection()

    def toggle_selecting(self) -> None:
        """選取物件（在內視鏡模式下用於表面點選）"""
        self.set_selecting_enabled(not self._selecting_enabled)

    def set_marking_enabled(self, enabled: bool) -> None:
        self._marking_enabled = bool(enabled)
        print(f"[EndoscopyMode] marking_enabled = {self._marking_enabled}", flush=True)
        if self._marking_enabled:
            self._selecting_enabled = False
            self.take_snapshot()

    def toggle_marking(self) -> None:
        """標記模式（在內視鏡模式下用於拍照）"""
        self.set_marking_enabled(not self._marking_enabled)

    def enter_surface_selection(self) -> None:
        """進入表面點選模式"""
        self.activate_endoscope()
        self._surface_pick_mode = True
        self._append_to_terminal("請點擊模型表面選擇進入點\n")

    def enter_insertion_mode(self) -> None:
        """外部插入模式（不切換相機）"""
        self._insert_mode = True
        self._surface_pick_mode = True
        self._inserting = False
        if self._main_window is not None:
            try:
                self._main_window.setFocus()
                if hasattr(self._main_window, "vtk_widget"):
                    self._main_window.vtk_widget.setFocus()
            except Exception:
                pass
        if self._style_before_insert_mode is None:
            self._style_before_insert_mode = self._interactor.GetInteractorStyle()
        self._rod_style.SetDefaultRenderer(self._renderer)
        self._rod_style.SetInteractor(self._interactor)
        self._interactor.SetInteractorStyle(self._rod_style)
        self._append_to_terminal("插入內視鏡模式已啟動，請點擊模型表面設定插入點\n")

    def exit_insertion_mode(self) -> None:
        """退出外部插入模式（保留棒子）"""
        self._insert_mode = False
        self._surface_pick_mode = False
        self._inserting = False
        self._dragging_rod = False
        self._dragging_view = False
        self._view_drag_last_xy = None
        self._insert_base = None
        self._insert_base_display_z = None
        if self._style_before_insert_mode:
            self._interactor.SetInteractorStyle(self._style_before_insert_mode)
            self._style_before_insert_mode = None
        if self._style_before_insert_drag:
            self._interactor.SetInteractorStyle(self._style_before_insert_drag)
            self._style_before_insert_drag = None

    def take_snapshot(self) -> None:
        """拍攝當前視野畫面"""
        if not self._endoscope_camera:
            return
            
        try:
            # 實現截圖功能
            render_window = self._interactor.GetRenderWindow()
            w2i = vtk.vtkWindowToImageFilter()
            w2i.SetInput(render_window)
            w2i.Update()
            
            writer = vtk.vtkPNGWriter()
            writer.SetFileName("endoscope_snapshot.png")
            writer.SetInputData(w2i.GetOutput())
            writer.Write()
            
            self._append_to_terminal("畫面已保存為 endoscope_snapshot.png\n")
        except Exception as e:
            print(f"[EndoscopyMode] 拍照失敗: {e}", flush=True)

    def clear_markers(self) -> None:
        """清除標記（在內視鏡模式下用於退出內視鏡）"""
        self.deactivate_endoscope()
        self._clear_entry_marker()
        self._append_to_terminal("內視鏡已退出\n")

    def reset(self) -> None:
        """重置內視鏡設定"""
        self.deactivate_endoscope()
        self._clear_entry_marker()
        self._surface_pick_mode = False
        self._move_speed = 5.0
        self._rotation_speed = 5.0
        self._append_to_terminal("內視鏡設定已重置\n")

    # ---------------------------
    # 透明度調整功能（新增）
    # ---------------------------

    def commit(self) -> Optional[Tuple[List[int], List[int], List[int]]]:
        """
        內視鏡模式中的 commit: 開啟透明度調整對話框
        MainWindow 期待回傳 (changed_original_ids, new_result_ids, to_delete_result_ids)
        """
        print("[EndoscopyMode] commit: 開啟透明度調整", flush=True)
        
        # 開啟透明度調整對話框
        self._show_opacity_dialog()
        
        # 透明度調整不會產生新物件，回傳空列表
        return [], [], []

    def _apply_opacity(self, opacity: float, target: str = "all"):
        """套用透明度設定 - 同時更新資料層和顯示層"""
        obj_ids_modified = []
        
        print(f"[EndoscopyMode] 開始套用透明度 {opacity} 到 {target}")
        
        if target == "all":
            # 所有原始物件
            for obj in self._prop_mgr.get_original_objects():
                # 1. 更新資料層
                self._prop_mgr.set_opacity(obj.id, opacity)
                
                # 2. 直接更新 Actor
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                    print(f"[EndoscopyMode] 設定原始物件 {obj.id} 透明度為 {opacity}")
                
                obj_ids_modified.append(obj.id)
            
            # 所有切割物件
            for obj in self._prop_mgr.get_result_objects():
                self._prop_mgr.set_opacity(obj.id, opacity)
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                    print(f"[EndoscopyMode] 設定切割物件 {obj.id} 透明度為 {opacity}")
                obj_ids_modified.append(obj.id)
                            
        elif target == "original":
            # 只更新原始物件
            for obj in self._prop_mgr.get_original_objects():
                self._prop_mgr.set_opacity(obj.id, opacity)
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                    print(f"[EndoscopyMode] 設定原始物件 {obj.id} 透明度為 {opacity}")
                obj_ids_modified.append(obj.id)
                    
        elif target == "result":
            # 只更新切割物件
            for obj in self._prop_mgr.get_result_objects():
                self._prop_mgr.set_opacity(obj.id, opacity)
                actor = self._obj3d_mgr.get_actor(obj.id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                    print(f"[EndoscopyMode] 設定切割物件 {obj.id} 透明度為 {opacity}")
                obj_ids_modified.append(obj.id)
                    
        elif target == "selected":
            selected_ids = list(self._prop_mgr.get_selected_objects())
            if not selected_ids:
                print("[EndoscopyMode] 沒有任何已選取物件")
            for obj_id in selected_ids:
                self._prop_mgr.set_opacity(obj_id, opacity)
                actor = self._obj3d_mgr.get_actor(obj_id)
                if actor:
                    actor.GetProperty().SetOpacity(opacity)
                obj_ids_modified.append(obj_id)
        
        # 如果沒有找到任何物件，顯示提示訊息
        if not obj_ids_modified:
            self._append_to_terminal(f"警告：沒有找到任何{target}類型的物件可調整透明度\n")
            return
        
        # 強制重新渲染
        self._render()
        
        # === 更新 UI 列表 ===
        if self._main_window:
            for obj_id in obj_ids_modified:
                obj = self._prop_mgr.get_object(obj_id)
                if obj.kind == "original":
                    if hasattr(self._main_window, 'object_list_widget'):
                        self._main_window.object_list_widget.refresh_from_manager(obj_id)
                else:  # result
                    if hasattr(self._main_window, 'cut_list_widget'):
                        self._main_window.cut_list_widget.refresh_from_manager(obj_id)
            
            # 強制處理 UI 事件
            from PyQt5.QtWidgets import QApplication
            QApplication.processEvents()
        
        print(f"[EndoscopyMode] 已套用透明度: {opacity:.2f}, 目標: {target}, 物件數: {len(obj_ids_modified)}")


    def _show_opacity_dialog(self):
        """顯示透明度調整對話框 - 與預覽模式完全一致"""
        if not self._main_window:
            print("[EndoscopyMode] 無法開啟對話框：未設置 main_window")
            return

        opacity_snapshot = self._snapshot_opacity_state()
        applied = False
        
        dialog = QtWidgets.QDialog(self._main_window)
        dialog.setWindowTitle("透明度設定")
        dialog.setModal(True)
        dialog.setFixedSize(600, 480)
        
        # 主佈局 - 使用 grid layout 精確控制位置
        main_layout = QtWidgets.QGridLayout(dialog)
        main_layout.setContentsMargins(25, 25, 25, 20)
        main_layout.setSpacing(12)
        main_layout.setColumnMinimumWidth(0, 100)
        main_layout.setColumnMinimumWidth(1, 350)
        main_layout.setColumnStretch(1, 1)
        
        # === 1. 標題列 ===
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
        
        # === 2. 物件選擇 ===
        selector_label = QtWidgets.QLabel("套用至：")
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
        object_selector.addItem("所有物件 (All Objects)", "all")
        object_selector.addItem("原始物件 (Original)", "original")
        object_selector.addItem("切割物件 (Result)", "result")
        object_selector.addItem("目前已選取物件 (Selected)", "selected")
        object_selector.setFixedHeight(40)
        main_layout.addWidget(object_selector, 1, 1)
        
        # === 3. 透明度滑桿 ===
        slider_label = QtWidgets.QLabel("透明度：")
        slider_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #E8EDF3;")
        slider_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        slider_label.setFixedHeight(40)
        main_layout.addWidget(slider_label, 2, 0)
        
        slider_container = QtWidgets.QHBoxLayout()
        slider_container.setSpacing(15)
        
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(100)  # 預設100%不透明
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
        
        # === 4. 透明度說明 ===
        hint_label = QtWidgets.QLabel("※ 0% = 全透明（看不見），100% = 不透明（實心）")
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
        preview_checkbox = QtWidgets.QCheckBox("即時預覽透明度變化")
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
        
        # === 6. 快速設定標題 ===
        preset_title = QtWidgets.QLabel("快速設定：")
        preset_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFFFFF; padding: 3px;")
        main_layout.addWidget(preset_title, 5, 0, 1, 2)
        
        # === 7. 快速設定按鈕 ===
        preset_layout = QtWidgets.QHBoxLayout()
        preset_layout.setSpacing(10)
        preset_layout.setContentsMargins(0, 5, 0, 10)
        
        presets = [
            ("0% 全透明", 0),
            ("25%", 25),
            ("50%", 50), 
            ("75%", 75),
            ("100% 不透明", 100)
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
        
        # === 9. 按鈕區域（置中）===
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
        
        # 連接信號
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
        
        # 即時預覽功能
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
        
        # 設定對話框屬性
        dialog.setLayout(main_layout)
        dialog.setFixedSize(600, 480)
        
        # 預設套用一次透明度（100%）
        QtCore.QTimer.singleShot(50, lambda: self._apply_opacity(1.0, "all"))
        
        dialog.exec_()

    def _snapshot_opacity_state(self) -> Dict[int, float]:
        """記錄對話框開啟前的透明度，供取消時回復。"""
        return {obj.id: float(obj.opacity) for obj in self._prop_mgr.get_all_objects()}

    def _restore_opacity_state(self, opacity_snapshot: Dict[int, float]) -> None:
        """還原透明度到快照狀態（資料層 + 顯示層 + UI 列表）。"""
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

    # ---------------------------
    # Mouse events
    # ---------------------------
    def on_left_button_down(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        """處理左鍵點擊 - 用於表面點選"""
        if self._main_window is not None:
            try:
                self._main_window.setFocus()
                if hasattr(self._main_window, "vtk_widget"):
                    self._main_window.vtk_widget.setFocus()
            except Exception:
                pass
        if self._insert_mode:
            if self._rod_actor and self._is_mouse_over_rod(interactor):
                self._dragging_rod = True
                self._start_rod_drag()
                return
            if self._rod_actor is not None and self._rod_p0 is not None and self._rod_p1 is not None:
                self._dragging_view = True
                self._view_drag_last_xy = interactor.GetEventPosition()
                return
            self._handle_insert_surface_pick(interactor)
            return
        if self._surface_pick_mode and self._endoscope_camera:
            self._handle_surface_selection(interactor)

    def _handle_surface_selection(self, interactor: vtk.vtkRenderWindowInteractor) -> bool:
        """處理表面點選"""
        x, y = interactor.GetEventPosition()
        picker = vtk.vtkPropPicker()
        
        print(f"[EndoscopyMode] 嘗試在位置 ({x}, {y}) 拾取")
        
        if picker.Pick(x, y, 0, self._renderer):
            pick_position = picker.GetPickPosition()
            picked_actor = picker.GetActor()
            
            print(f"[EndoscopyMode] 成功拾取到表面點: {pick_position}")
            
            # 退出表面點選模式
            self._surface_pick_mode = False
            
            # 從表面點進入內視鏡
            if self._endoscope_camera:
                try:
                    self._endoscope_camera.enter_from_surface(pick_position)
                    self._set_entry_marker(pick_position)
                    self._append_to_terminal(f"成功進入內視鏡模式，進入點: {pick_position}\n")
                except Exception as e:
                    print(f"[EndoscopyMode] 進入內視鏡失敗: {e}")
                    self._append_to_terminal(f"進入內視鏡失敗: {e}\n")
                    return False
                
                self._render()
                return True
        else:
            print("[EndoscopyMode] 未拾取到任何表面點")
            self._append_to_terminal("未拾取到表面點，請確保點擊在模型上\n")
        
        return False

    def _handle_insert_surface_pick(self, interactor: vtk.vtkRenderWindowInteractor) -> bool:
        x, y = interactor.GetEventPosition()
        self._refresh_insert_pick_list()
        picked = self._picker.Pick(x, y, 0, self._renderer)
        if picked == 0:
            return False
        if self._picker.GetActor() is None:
            return False
        if self._picker.GetCellId() < 0:
            return False

        pick_position = self._picker.GetPickPosition()
        picked_actor = self._picker.GetActor()
        self._ensure_rod_actor()

        # 使用表面法向決定插入方向
        n = self._picker.GetPickNormal()
        nx, ny, nz = n[0], n[1], n[2]
        norm = (nx * nx + ny * ny + nz * nz) ** 0.5
        if norm < 1e-8:
            cam = self._renderer.GetActiveCamera()
            vx = pick_position[0] - cam.GetPosition()[0]
            vy = pick_position[1] - cam.GetPosition()[1]
            vz = pick_position[2] - cam.GetPosition()[2]
            vnorm = (vx * vx + vy * vy + vz * vz) ** 0.5
            if vnorm > 1e-8:
                nx, ny, nz = vx / vnorm, vy / vnorm, vz / vnorm
            else:
                nx, ny, nz = 0.0, 0.0, 1.0
        else:
            nx, ny, nz = nx / norm, ny / norm, nz / norm

        length = self._get_rod_length(picked_actor)
        p0 = (pick_position[0], pick_position[1], pick_position[2])
        p1 = (
            pick_position[0] + nx * length,
            pick_position[1] + ny * length,
            pick_position[2] + nz * length,
        )
        self._update_rod(p0, p1)

        self._inserting = False
        if self._style_before_insert_drag:
            self._interactor.SetInteractorStyle(self._style_before_insert_drag)
            self._style_before_insert_drag = None
        return True

    def on_key_press(self, interactor_or_key, key_sym: Optional[str] = None) -> bool:
        """處理鍵盤輸入"""
        key = interactor_or_key if key_sym is None else key_sym
        key_lower = key.lower()
        handled = True

        if not self._endoscope_camera:
            return False
        
        # 獲取當前速度
        current_speed = self._move_speeds[self._current_speed_index]
        
        # 速度控制
        if key in ['[', 'bracketleft']:
            if self._current_speed_index > 0:
                self._current_speed_index -= 1
                self._append_to_terminal(f"速度減慢: {self._move_speeds[self._current_speed_index]:.1f}\n")
            return True
        elif key in [']', 'bracketright']:
            if self._current_speed_index < len(self._move_speeds) - 1:
                self._current_speed_index += 1
                self._append_to_terminal(f"速度加快: {self._move_speeds[self._current_speed_index]:.1f}\n")
            return True
        
        # 移動控制
        if key_lower == 'w':
            self._endoscope_camera.move_forward(current_speed)
            self._append_to_terminal(f"向前移動 {current_speed:.1f}\n")
        elif key_lower == 's':
            self._endoscope_camera.move_backward(current_speed)
            self._append_to_terminal(f"向後移動 {current_speed:.1f}\n")
        elif key_lower == 'a':
            self._endoscope_camera.move_left(current_speed)
            self._append_to_terminal(f"向左移動 {current_speed:.1f}\n")
        elif key_lower == 'd':
            self._endoscope_camera.move_right(current_speed)
            self._append_to_terminal(f"向右移動 {current_speed:.1f}\n")
        elif key_lower == 'q':
            self._endoscope_camera.move_up(current_speed)
            self._append_to_terminal(f"向上移動 {current_speed:.1f}\n")
        elif key_lower == 'e':
            self._endoscope_camera.move_down(current_speed)
            self._append_to_terminal(f"向下移動 {current_speed:.1f}\n")
        # 旋轉控制
        elif key == 'Up':
            self._endoscope_camera.rotate(0, 5)
            self._append_to_terminal("向上旋轉\n")
        elif key == 'Down':
            self._endoscope_camera.rotate(0, -5)
            self._append_to_terminal("向下旋轉\n")
        elif key == 'Left':
            self._endoscope_camera.rotate(5, 0)
            self._append_to_terminal("向左旋轉\n")
        elif key == 'Right':
            self._endoscope_camera.rotate(-5, 0)
            self._append_to_terminal("向右旋轉\n")
        # 視野控制
        elif key_lower == 'f':
            current_fov = self._endoscope_camera.fov
            new_fov = 30 if current_fov > 45 else 60
            self._endoscope_camera.set_fov(new_fov)
            self._append_to_terminal(f"切換視野: {new_fov}度\n")
        # 退出內視鏡模式
        elif key == 'Escape':
            self.deactivate_endoscope()
            self._append_to_terminal("退出內視鏡模式\n")
        else:
            handled = False
            
        if handled:
            self._render()
            
        return handled

    def on_mouse_move(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._insert_mode and not self._dragging_rod:
            x, y = interactor.GetEventPosition()
            hovering = self._is_mouse_over_rod(interactor)
            if hovering != self._hovering_rod:
                self._hovering_rod = hovering
                if hovering:
                    interactor.SetInteractorStyle(self._rod_locked_style)
                else:
                    if self._style_before_insert_mode:
                        interactor.SetInteractorStyle(self._style_before_insert_mode)
            # fallthrough: only drag when pressed
        if self._insert_mode and self._dragging_rod:
            self._update_rod_drag(interactor)
            return
        if self._insert_mode and self._dragging_view:
            self._update_view_drag(interactor)
            return
        if not self._insert_mode or not self._inserting or self._insert_base is None:
            return
        if self._insert_base_display_z is None:
            return
        x, y = interactor.GetEventPosition()
        p1 = self._display_to_world(x, y, self._insert_base_display_z)
        if p1 is None:
            return
        self._update_rod(self._insert_base, (p1[0], p1[1], p1[2]))

    def on_left_button_up(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._insert_mode and self._dragging_rod:
            self._dragging_rod = False
            return
        if self._insert_mode and self._dragging_view:
            self._dragging_view = False
            self._view_drag_last_xy = None
            return
        if self._insert_mode and self._inserting:
            self._inserting = False
            if self._style_before_insert_drag:
                self._interactor.SetInteractorStyle(self._style_before_insert_drag)
                self._style_before_insert_drag = None

    # ---------------------------
    # Internal helpers
    # ---------------------------
    def _render(self) -> None:
        rw = self._renderer.GetRenderWindow()
        if rw is not None:
            rw.Render()

    def _display_to_world(self, x: int, y: int, display_z: float):
        renderer = self._renderer
        renderer.SetDisplayPoint(x, y, display_z)
        renderer.DisplayToWorld()
        wp = renderer.GetWorldPoint()
        if wp[3] == 0:
            return None
        return (wp[0] / wp[3], wp[1] / wp[3], wp[2] / wp[3])

    def _ensure_rod_actor(self) -> None:
        if self._rod_actor:
            return

        self._rod_source = vtk.vtkCylinderSource()
        self._rod_source.SetRadius(self._rod_radius)
        self._rod_source.SetHeight(1.0)
        self._rod_source.SetResolution(24)
        self._rod_source.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._rod_source.GetOutputPort())

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1.0, 0.2, 0.2)
        actor.GetProperty().SetOpacity(1.0)
        actor.GetProperty().SetLighting(False)
        actor.PickableOn()

        self._renderer.AddActor(actor)
        self._rod_actor = actor
        self._render()

    def _is_mouse_over_rod(self, interactor: vtk.vtkRenderWindowInteractor) -> bool:
        if self._rod_actor is None:
            return False
        x, y = interactor.GetEventPosition()
        picker = vtk.vtkPropPicker()
        if not picker.Pick(x, y, 0, self._renderer):
            return False
        return picker.GetActor() == self._rod_actor

    def _refresh_insert_pick_list(self) -> None:
        self._picker.InitializePickList()
        for actor in self._obj3d_mgr.all_actors():
            self._picker.AddPickList(actor)

    def _update_rod(self, p0: Vec3, p1: Vec3) -> None:
        if self._rod_actor is None:
            return

        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        dz = p1[2] - p0[2]
        length = (dx * dx + dy * dy + dz * dz) ** 0.5
        if length < self._rod_min_length:
            length = self._rod_min_length
            dx, dy, dz = 0.0, 0.0, 1.0

        # 方向向量
        vx, vy, vz = dx / length, dy / length, dz / length

        # 旋轉：從 z 軸轉到 v
        z_axis = (0.0, 0.0, 1.0)
        axis_x = z_axis[1] * vz - z_axis[2] * vy
        axis_y = z_axis[2] * vx - z_axis[0] * vz
        axis_z = z_axis[0] * vy - z_axis[1] * vx
        axis_norm = (axis_x * axis_x + axis_y * axis_y + axis_z * axis_z) ** 0.5

        import math
        dot = z_axis[0] * vx + z_axis[1] * vy + z_axis[2] * vz
        dot = max(-1.0, min(1.0, dot))
        angle = math.degrees(math.acos(dot))

        transform = vtk.vtkTransform()
        transform.PostMultiply()

        mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0, (p0[2] + p1[2]) / 2.0)
        transform.Translate(mid)

        if axis_norm > 1e-8:
            transform.RotateWXYZ(angle, axis_x / axis_norm, axis_y / axis_norm, axis_z / axis_norm)
        else:
            if vz < 0:
                transform.RotateWXYZ(180.0, 1.0, 0.0, 0.0)

        transform.Scale(1.0, 1.0, length)
        self._rod_actor.SetUserTransform(transform)
        self._rod_p0 = p0
        self._rod_p1 = p1
        self._rod_anchor_point = p0
        self._render_throttled()

    def _rotate_rod_by_key(self, key: str, step_deg: float = 3.0) -> None:
        if self._rod_p0 is None or self._rod_p1 is None:
            return
        cam = self._renderer.GetActiveCamera()
        view_up = cam.GetViewUp()
        focal = cam.GetFocalPoint()
        pos = cam.GetPosition()
        view_dir = (focal[0] - pos[0], focal[1] - pos[1], focal[2] - pos[2])
        # right = view_dir x view_up
        rx = view_dir[1] * view_up[2] - view_dir[2] * view_up[1]
        ry = view_dir[2] * view_up[0] - view_dir[0] * view_up[2]
        rz = view_dir[0] * view_up[1] - view_dir[1] * view_up[0]
        rnorm = (rx * rx + ry * ry + rz * rz) ** 0.5
        if rnorm < 1e-6:
            return
        rx, ry, rz = rx / rnorm, ry / rnorm, rz / rnorm
        unorm = (view_up[0] ** 2 + view_up[1] ** 2 + view_up[2] ** 2) ** 0.5
        if unorm < 1e-6:
            return
        ux, uy, uz = view_up[0] / unorm, view_up[1] / unorm, view_up[2] / unorm

        axis = (0.0, 0.0, 1.0)
        angle = step_deg
        if key == "Left":
            axis = (ux, uy, uz)
            angle = step_deg
        elif key == "Right":
            axis = (ux, uy, uz)
            angle = -step_deg
        elif key == "Up":
            axis = (rx, ry, rz)
            angle = step_deg
        elif key == "Down":
            axis = (rx, ry, rz)
            angle = -step_deg

        anchor = self._rod_anchor_point or self._rod_p0
        # current direction
        dx = self._rod_p1[0] - self._rod_p0[0]
        dy = self._rod_p1[1] - self._rod_p0[1]
        dz = self._rod_p1[2] - self._rod_p0[2]
        length = (dx * dx + dy * dy + dz * dz) ** 0.5
        if length < 1e-6:
            return
        vx, vy, vz = dx / length, dy / length, dz / length

        t = vtk.vtkTransform()
        t.PostMultiply()
        t.RotateWXYZ(angle, axis[0], axis[1], axis[2])
        v = t.TransformVector(vx, vy, vz)
        vx, vy, vz = v[0], v[1], v[2]
        p0 = anchor
        p1 = (anchor[0] + vx * length, anchor[1] + vy * length, anchor[2] + vz * length)
        self._update_rod(p0, p1)

    def _render_throttled(self) -> None:
        rw = self._renderer.GetRenderWindow()
        if rw is None:
            return
        now = time.time()
        if now - self._rod_drag_last_render_t < self._rod_drag_render_interval:
            return
        self._rod_drag_last_render_t = now
        rw.Render()

    def _start_rod_drag(self) -> None:
        if self._rod_p0 is None or self._rod_p1 is None:
            return
        anchor = self._rod_anchor_point or self._rod_p0
        free_end = self._rod_p1
        dx = free_end[0] - anchor[0]
        dy = free_end[1] - anchor[1]
        dz = free_end[2] - anchor[2]
        length = (dx * dx + dy * dy + dz * dz) ** 0.5
        if length < 1e-6:
            return
        self._rod_drag_center = anchor
        self._rod_drag_radius = length
        self._rod_ref_dir = (dx / length, dy / length, dz / length)

    def _update_rod_drag(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._rod_drag_center is None or self._rod_drag_radius is None:
            return
        x, y = interactor.GetEventPosition()
        p = self._screen_to_sphere_point(self._rod_drag_center, self._rod_drag_radius, x, y)
        if p is None:
            return
        cx, cy, cz = self._rod_drag_center
        vx, vy, vz = (p[0] - cx, p[1] - cy, p[2] - cz)
        norm = (vx * vx + vy * vy + vz * vz) ** 0.5
        if norm < 1e-6:
            return
        vx, vy, vz = vx / norm, vy / norm, vz / norm
        if self._rod_ref_dir is not None:
            hx, hy, hz = self._rod_ref_dir
            if (vx * hx + vy * hy + vz * hz) < 0:
                return
        length = self._rod_drag_radius
        p0 = (cx, cy, cz)
        p1 = (cx + vx * length, cy + vy * length, cz + vz * length)
        self._update_rod(p0, p1)

    def _update_view_drag(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._endoscope_camera is None:
            return
        current_xy = interactor.GetEventPosition()
        if self._view_drag_last_xy is None:
            self._view_drag_last_xy = current_xy
            return

        last_x, last_y = self._view_drag_last_xy
        x, y = current_xy
        dx = x - last_x
        dy = y - last_y
        self._view_drag_last_xy = current_xy

        if dx == 0 and dy == 0:
            return

        yaw = -dx * self._view_drag_sensitivity
        pitch = -dy * self._view_drag_sensitivity
        self._endoscope_camera.rotate(yaw, pitch)
        self._render_throttled()

    def _screen_to_sphere_point(self, center: Vec3, radius: float, x: int, y: int) -> Optional[Vec3]:
        if radius <= 1e-6:
            return None
        # display -> world near/far
        self._renderer.SetDisplayPoint(x, y, 0.0)
        self._renderer.DisplayToWorld()
        near_w = self._renderer.GetWorldPoint()
        if abs(near_w[3]) < 1e-6:
            return None
        p0 = (near_w[0] / near_w[3], near_w[1] / near_w[3], near_w[2] / near_w[3])

        self._renderer.SetDisplayPoint(x, y, 1.0)
        self._renderer.DisplayToWorld()
        far_w = self._renderer.GetWorldPoint()
        if abs(far_w[3]) < 1e-6:
            return None
        p1 = (far_w[0] / far_w[3], far_w[1] / far_w[3], far_w[2] / far_w[3])

        # ray-sphere intersection
        ox, oy, oz = p0
        dx, dy, dz = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        cx, cy, cz = center
        a = dx * dx + dy * dy + dz * dz
        b = 2.0 * (dx * (ox - cx) + dy * (oy - cy) + dz * (oz - cz))
        c = (ox - cx) ** 2 + (oy - cy) ** 2 + (oz - cz) ** 2 - radius * radius
        disc = b * b - 4.0 * a * c
        if disc < 0:
            return None
        import math
        t = (-b - math.sqrt(disc)) / (2.0 * a)
        if t < 0:
            t = (-b + math.sqrt(disc)) / (2.0 * a)
            if t < 0:
                return None
        return (ox + dx * t, oy + dy * t, oz + dz * t)

    def _get_rod_length(self, actor: Optional[vtk.vtkActor] = None) -> float:
        if self._rod_length is not None:
            return self._rod_length

        diag = 0.0
        if actor is not None:
            b = actor.GetBounds()
            if b:
                dx = b[1] - b[0]
                dy = b[3] - b[2]
                dz = b[5] - b[4]
                diag = (dx * dx + dy * dy + dz * dz) ** 0.5

        if diag <= 0:
            bounds = [0.0] * 6
            self._renderer.ComputeVisiblePropBounds(bounds)
            dx = bounds[1] - bounds[0]
            dy = bounds[3] - bounds[2]
            dz = bounds[5] - bounds[4]
            diag = (dx * dx + dy * dy + dz * dz) ** 0.5

        if diag <= 0:
            self._rod_length = max(self._rod_min_length, 20.0)
        else:
            length = diag * 0.08
            length = max(self._rod_min_length, length)
            length = min(length, diag * 0.5)
            self._rod_length = length
        return self._rod_length

    def _set_entry_marker(self, p_w: Vec3) -> None:
        """在內視鏡入口位置放置紅色小圓標記。"""
        self._clear_entry_marker()

        src = vtk.vtkSphereSource()
        src.SetRadius(1.5)
        src.SetThetaResolution(16)
        src.SetPhiResolution(16)
        src.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetPosition(p_w[0], p_w[1], p_w[2])
        actor.GetProperty().SetColor(1.0, 0.2, 0.2)
        actor.GetProperty().SetOpacity(1.0)
        actor.PickableOff()

        self._renderer.AddActor(actor)
        self._entry_marker_actor = actor
        self._render()

    def _clear_entry_marker(self) -> None:
        if self._entry_marker_actor:
            self._renderer.RemoveActor(self._entry_marker_actor)
            self._entry_marker_actor = None
        self._render()

    def _append_to_terminal(self, text: str):
        """發送訊息到終端顯示"""
        if self._main_window and hasattr(self._main_window, '_append_terminal_text'):
            self._main_window._append_terminal_text(text)
        else:
            print(text, flush=True)

    def get_status(self) -> dict:
        """獲取內視鏡狀態"""
        return {
            'endoscope_mode': self._endoscope_active,
            'surface_pick_mode': self._surface_pick_mode,
            'move_speed': self._move_speeds[self._current_speed_index],
            'camera_fov': self._endoscope_camera.fov if self._endoscope_camera else None
        }

    @property
    def endoscope_mode(self) -> bool:
        """提供 MainWindow 查詢目前是否在內視鏡顯示狀態。"""
        return self._endoscope_active

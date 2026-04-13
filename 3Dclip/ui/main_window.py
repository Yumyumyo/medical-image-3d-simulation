from __future__ import annotations
from typing import Optional, Dict, Any, List
import os
import sys
import json
from datetime import datetime
import colorsys
from functools import partial
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import QSize
from PyQt5.QtGui import QIcon, QPixmap

# VTK & QVTK
import vtk
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

# UI
from .untitled import Ui_Form
from ui.layout import LayoutManager
from ui.display_control import DisplayControl
from ui import untitled_rc

# Managers
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager
from manager.history_manager import HistoryManager
from manager.interaction_mode import InteractionModeManager
from manager.slice2D_manager import Slice2DManager
from manager.slice3D_manager import Slice3DManager
from manager.preview_slice_manager import PreviewSliceManager

# Interaction modes
from interaction.view import CameraMoveMode
from interaction.plane_cut_mode import PlaneCutMode
from interaction.line_cut_mode import LineCutMode
from interaction.tube_cut_mode import TubeCutMode
from interaction.endoscope_tube_mode import EndoscopeTubeMode
from interaction.endoscopy_mode import EndoscopyMode
from interaction.preview_mode import PreviewMode
from interaction.simple_cut_mode import SimpleCutMode

# UI widgets
from ui.widgets.object_list import ObjectListWidget
from ui.widgets.cut_list import CutListWidget
from ui.dialogs.property_dialog import PropertyDialog
from ui.dialogs.export_dialog import ExportDialog

# Utils
import random
from utils.file_loader import FileLoader
from utils.endoscope_camera import EndoscopeCamera

class _NoRotateOnPickStyle(vtk.vtkInteractorStyleTrackballCamera):
    def __init__(self, renderer, obj3d_mgr):
        super().__init__()
        self._renderer = renderer
        self._obj3d_mgr = obj3d_mgr
        self._picker = vtk.vtkPropPicker()
        self._block_rotation = False
        self._camera_drag_started = False
        self._pending_camera_drag = False
        self._press_pos = None
        self._drag_threshold = 4
        self._consume_next_empty_click = False

    def OnLeftButtonDown(self):
        interactor = self.GetInteractor()
        if interactor is not None:
            x, y = interactor.GetEventPosition()
            if self._picker.Pick(x, y, 0, self._renderer):
                actor = self._picker.GetActor()
                if actor is not None and self._obj3d_mgr.get_obj_id_from_actor(actor) is not None:
                    self._consume_next_empty_click = False
                    self._block_rotation = True
                    self._pending_camera_drag = False
                    self._press_pos = None
                    self._camera_drag_started = False
                    return
            if self._consume_next_empty_click:
                self._consume_next_empty_click = False
                self._block_rotation = False
                self._pending_camera_drag = False
                self._press_pos = None
                self._camera_drag_started = False
                return
        self._block_rotation = True
        self._camera_drag_started = False
        self._pending_camera_drag = False
        self._press_pos = None

    def OnMouseMove(self):
        if self._block_rotation:
            return
        if self._pending_camera_drag:
            if not (QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton):
                self.reset_interaction_state()
                return
            interactor = self.GetInteractor()
            if interactor is None or self._press_pos is None:
                self.reset_interaction_state()
                return
            x, y = interactor.GetEventPosition()
            dx = x - self._press_pos[0]
            dy = y - self._press_pos[1]
            if (dx * dx + dy * dy) < (self._drag_threshold * self._drag_threshold):
                return
            self._pending_camera_drag = False
            self._camera_drag_started = True
            super().OnLeftButtonDown()
        if not self._camera_drag_started:
            return
        if not (QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton):
            self.reset_interaction_state()
            return
        super().OnMouseMove()

    def OnLeftButtonUp(self):
        if self._block_rotation:
            self._block_rotation = False
            self._pending_camera_drag = False
            self._press_pos = None
            self._camera_drag_started = False
            return
        if self._pending_camera_drag:
            self._pending_camera_drag = False
            self._press_pos = None
            self._camera_drag_started = False
            return
        self._camera_drag_started = False
        self._press_pos = None
        super().OnLeftButtonUp()

    def reset_interaction_state(self):
        self._block_rotation = False
        self._camera_drag_started = False
        self._pending_camera_drag = False
        self._press_pos = None
        # 防止切模式時殘留的 Trackball 狀態讓鏡頭持續轉動
        for fn_name in ("EndRotate", "EndPan", "EndSpin", "EndDolly"):
            fn = getattr(self, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    def arm_empty_click_guard(self):
        self._consume_next_empty_click = True
from utils.terminal_display import TerminalDisplay

# 路徑配置
model_path="/Users/wangyuwen/Desktop/2/BAVM-OBJ"
nii_path="/Users/wangyuwen/Desktop/2/BAVM-OBJ/T1.nii.gz"

class MainWindow(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        # 1. 建立 UI
        self.ui = Ui_Form()
        self.ui.setupUi(self)
        
        # 2. 設定視窗基本屬性
        self.setWindowTitle("醫學影像切割系統")
        self.setGeometry(100, 100, 1440, 767)
        
        # 3. 設置終端顯示（重定向 print）
        self._setup_terminal()
        
        # 4. 初始化 VTK 場景
        self._setup_vtk()
        
        # 5. 載入 Layout Manager
        self.layout_mgr = LayoutManager(self)
        
        # 6. 初始化管理器和工具
        self._setup_managers()
        self._setup_widgets()
        self._setup_ui_elements()
        self._setup_connections()
        self._setup_slice_clip_controls()
        self._setup_endoscope_minimap()
        self._aligned_obj_ids: set[int] = set()
        # 預設不要自動套用 NIfTI 矩陣，避免物件被移動
        self._auto_align_obj_to_nifti = False

        # 切片裁切更新節流，避免滑桿拖曳時更新過於頻繁造成卡頓
        self._slice_clip_update_delay_ms = 16
        self._slice_clip_update_timer = QtCore.QTimer(self)
        self._slice_clip_update_timer.setSingleShot(True)
        self._slice_clip_update_timer.timeout.connect(self._apply_all_slice_clipping)
        
        # 7. 批次匯入物件和初始切面
        self._load_initial_data()

        # 初始歷史記錄（開啟後可以 Undo）
        if hasattr(self, 'history_mgr') and self.history_mgr and not self.history_mgr.undo_stack:
            self.history_mgr.push_state()
        self._update_undo_redo_buttons()

        # 8. 設定初始模式
        self.current_work_mode = "cutting"
        self._endoscopy_insert_enabled = False
        self._update_ui_for_work_mode()
        self._update_interaction_mode()
        self._prepare_first_viewport_click()
        
        # 9. 設置 Layout 規則
        self.layout_mgr.setup_layout_sizing()
        
        # 10. 顯示控制工具列
        self.display_control = DisplayControl(self)
        
        # 11. 初始化模式管理器
        self.presentation_manager = self.preview_mode
        self.endoscopy_manager = self.endoscopy_mode
        
        # 12. 確保焦點設置
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.vtk_widget.setFocusPolicy(QtCore.Qt.StrongFocus)
        
        # 13. 初始化切片勾選框
        QtCore.QTimer.singleShot(300, self.init_slice_checkboxes)

    # ======================================================================
    # 初始化方法
    # ======================================================================
    def _setup_terminal(self):
        """設置終端顯示"""
        self.terminal_display = TerminalDisplay()
        self.terminal_display.text_written.connect(self._append_terminal_text)
        sys.stdout = self.terminal_display

    def _setup_vtk(self):
        self.vtk_widget = QVTKRenderWindowInteractor(self.ui.render_3D_5)
        self.ui.render_3D_layout_5.addWidget(self.vtk_widget, 0, 0, 1, 1)

        self.mesh_renderer = vtk.vtkRenderer()
        self.mesh_renderer.SetBackground(0.1, 0.1, 0.1)

        render_window = self.vtk_widget.GetRenderWindow()
        render_window.AddRenderer(self.mesh_renderer)

        self.interactor = render_window.GetInteractor()
        self.interactor.Initialize()
        
        style = vtk.vtkInteractorStyleTrackballCamera()
        self.interactor.SetInteractorStyle(style)

        # 3D 視窗點擊拾取（交給 VTK 事件）
        self._viewport_picker = vtk.vtkPropPicker()

    def _reset_interactor_drag_state(self) -> None:
        if not hasattr(self, "interactor") or self.interactor is None:
            return
        style = self.interactor.GetInteractorStyle()
        if style is None:
            return
        if hasattr(style, "reset_interaction_state"):
            try:
                style.reset_interaction_state()
            except Exception:
                pass
        for fn_name in ("EndRotate", "EndPan", "EndSpin", "EndDolly"):
            fn = getattr(style, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    def _prepare_first_viewport_click(self) -> None:
        self._reset_interactor_drag_state()
        style = getattr(self, "_no_rotate_style", None)
        if style is not None and hasattr(style, "arm_empty_click_guard"):
            try:
                style.arm_empty_click_guard()
            except Exception:
                pass

    def _setup_managers(self):
        self.prop_mgr = ObjectPropertyManager()
        self.obj3d_mgr = Object3DManager(self.mesh_renderer, self.prop_mgr)
        # 自訂相機 style：點到物件時不旋轉
        self._no_rotate_style = _NoRotateOnPickStyle(self.mesh_renderer, self.obj3d_mgr)
        self._no_rotate_style.SetDefaultRenderer(self.mesh_renderer)
        self._no_rotate_style.SetInteractor(self.interactor)
        self.interactor.SetInteractorStyle(self._no_rotate_style)
        self._prepare_first_viewport_click()

        # Undo / Redo 管理器（需要在場景初始化後使用）
        self.history_mgr = HistoryManager(
            obj3d_mgr=self.obj3d_mgr,
            prop_mgr=self.prop_mgr,
            max_history=50,
            on_restored=self._on_history_restored,
        )
        # 空場景作為 baseline
        self.history_mgr.push_state()
        self._update_undo_redo_buttons()

        self.file_loader = FileLoader(self)
        self.file_loader.modelLoaded.connect(self.on_model_data_received)

        self.slice3D_mgr = Slice3DManager(self.mesh_renderer, self.interactor)
        
        self.preview_slice_mgr = PreviewSliceManager(
            self.slice3D_mgr,
            self.obj3d_mgr,
            self.vtkWidget,
            main_window=self,
        )

        self.slice2D_mgr = Slice2DManager(
            widget_sag=self.ui.render_sagittal_5,  
            widget_cor=self.ui.render_coronal_5,
            widget_axi=self.ui.render_axial_5,
            slider_sagittal=self.ui.Sld_sagittal_5,
            slider_coronal=self.ui.Sld_coronal_5,
            slider_axial=self.ui.Sld_axial_5,
            slice3D_manager=self.slice3D_mgr
        )
        
        self.mode_mgr = InteractionModeManager(self.interactor)
        self._register_interaction_modes()
        self._setup_keyboard_shortcuts()
        self.mode_mgr.register_single_click_callback(self._on_vtk_single_click)
        self.mode_mgr.register_left_button_down_filter(self._on_vtk_left_button_down_filter)

    def _register_interaction_modes(self):
        self.camera_mode = CameraMoveMode(self.interactor, self.mesh_renderer)
        if hasattr(self, "_no_rotate_style"):
            self.camera_mode._style = self._no_rotate_style
        self.mode_mgr.register_mode(self.camera_mode)

        self.simple_cut_mode = SimpleCutMode(
            interactor=self.interactor,
            renderer=self.mesh_renderer,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr,
        )
        self.mode_mgr.register_mode(self.simple_cut_mode)
        
        self.plane_cut_mode = PlaneCutMode(
            interactor=self.interactor,
            renderer=self.mesh_renderer,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr,
        )
        self.plane_cut_mode.on_selected = self._on_scene_selected
        self.mode_mgr.register_mode(self.plane_cut_mode)
        
        self.line_cut_mode = LineCutMode(
            interactor=self.interactor,
            renderer=self.mesh_renderer,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr,
        )
        self.line_cut_mode.on_selected = self._on_scene_selected
        self.mode_mgr.register_mode(self.line_cut_mode)
        
        self.tube_cut_mode = TubeCutMode(
            interactor=self.interactor,
            renderer=self.mesh_renderer,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr,
        )
        self.tube_cut_mode.on_selected = self._on_scene_selected
        self.mode_mgr.register_mode(self.tube_cut_mode)

        self.endoscope_tube_mode = EndoscopeTubeMode(
            interactor=self.interactor,
            renderer=self.mesh_renderer,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr,
        )
        self.endoscope_tube_mode.on_tube_updated = self._on_tube_updated
        self.mode_mgr.register_mode(self.endoscope_tube_mode)
        
        self.endoscopy_mode = EndoscopyMode(
            interactor=self.interactor,
            renderer=self.mesh_renderer,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr,
            main_window=self,
        )
        self.endoscopy_mode.on_selected = self._on_scene_selected
        self.mode_mgr.register_mode(self.endoscopy_mode)
        
        self.preview_mode = PreviewMode(
            interactor=self.interactor,
            renderer=self.mesh_renderer,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr,
            main_window=self,
        )
        self.preview_mode.on_selected = self._on_scene_selected
        self.mode_mgr.register_mode(self.preview_mode)
        
        self.mode_mgr.set_mode("camera")

    def _setup_keyboard_shortcuts(self):
        self.mode_mgr.register_key_callback("Return", self.on_apply_cut_clicked)
        self.mode_mgr.register_key_callback("KP_Enter", self.on_apply_cut_clicked)
        self.mode_mgr.register_key_callback("s", self.on_select_object_clicked)
        self.mode_mgr.register_key_callback("p", self.on_mark_mode_clicked)
        self.mode_mgr.register_key_callback("c", self.on_clear_marker_clicked)
        self.mode_mgr.register_key_callback("z", self.on_undo_clicked)
        self.mode_mgr.register_key_callback("y", self.on_redo_clicked)
        
        self.mode_mgr.register_key_callback("w", lambda: self._handle_endoscopy_key("w"))
        self.mode_mgr.register_key_callback("a", lambda: self._handle_endoscopy_key("a"))
        self.mode_mgr.register_key_callback("s", lambda: self._handle_endoscopy_key("s"))
        self.mode_mgr.register_key_callback("d", lambda: self._handle_endoscopy_key("d"))
        self.mode_mgr.register_key_callback("q", lambda: self._handle_endoscopy_key("q"))
        self.mode_mgr.register_key_callback("e", lambda: self._handle_endoscopy_key("e"))
        self.mode_mgr.register_key_callback("f", lambda: self._handle_endoscopy_key("f"))
        self.mode_mgr.register_key_callback("Escape", lambda: self._handle_endoscopy_key("Escape"))
        self.mode_mgr.register_key_callback("[", lambda: self._handle_endoscopy_key("["))
        self.mode_mgr.register_key_callback("]", lambda: self._handle_endoscopy_key("]"))
        self.mode_mgr.register_key_callback("i", lambda: self._handle_depth_key("i"))
        self.mode_mgr.register_key_callback("o", lambda: self._handle_depth_key("o"))

    def _handle_endoscopy_key(self, key: str):
        if self.current_work_mode == "endoscopy":
            return self.endoscopy_mode.on_key_press(key)
        return False

    def _handle_depth_key(self, key: str):
        if self.current_work_mode == "endoscopy" and self._endoscopy_insert_enabled:
            if hasattr(self, "endoscope_tube_mode") and self.endoscope_tube_mode.has_tube():
                step = self.endoscope_tube_mode.get_depth_step()
                delta = step if key == "i" else -step
                self.endoscope_tube_mode.adjust_depth(delta)
                return True
        # fallback: 讓原本 mode 接手（避免影響切割）
        mode = self.mode_mgr.current_mode()
        if mode is not None and hasattr(mode, "on_key_press"):
            try:
                return bool(mode.on_key_press(self.interactor, key))
            except TypeError:
                return bool(mode.on_key_press(key))
        return False

    def _setup_widgets(self):
        self.object_list_widget = ObjectListWidget(
            tree_widget=self.ui.importedFileList,
            prop_mgr=self.prop_mgr,
            obj3d_mgr=self.obj3d_mgr,
        )
        self.cut_list_widget = CutListWidget(
            tree_widget=self.ui.cutObjectsList,
            prop_mgr=self.prop_mgr,
            obj3d_mgr=self.obj3d_mgr,
        )
        self.object_list_widget.on_settings_requested = self.open_property_dialog_for_obj
        self.cut_list_widget.on_settings_requested = self.open_property_dialog_for_obj
        if hasattr(self, "plane_cut_mode") and self.plane_cut_mode is not None:
            self.plane_cut_mode.on_result_created = self.cut_list_widget.add_result

    def _setup_ui_elements(self):
        self._setup_icons()
        self._setup_tree_widgets()
        self._setup_combo_boxes()
        self._setup_sliders()
        self._setup_checkboxes()

    def _setup_icons(self):
        icon_configs = [
            (self.ui.btn_undo, "undo.png", "復原"),
            (self.ui.btn_redo, "redo.png", "重做"),
            (self.ui.btn_save, "save.png", "儲存"),
            (self.ui.btn_import, "import.png", "匯入模型"),
            (self.ui.btn_export, "export.png", "匯出"),
        ]
        
        for button, icon_name, button_name in icon_configs:
            if button:
                icon = self._load_button_icon(icon_name)
                if icon is not None:
                    button.setIcon(icon)
                else:
                    print(f"警告: 找不到 {button_name} 圖示 ({icon_name})")
                button.setIconSize(QSize(24, 24))
                button.setToolTip(button_name)

        # 這三顆是 icon-only 按鈕，給固定尺寸並移除 padding，避免圖示被擠壓或看起來像空白。
        for icon_only_btn in (self.ui.btn_undo, self.ui.btn_redo, self.ui.btn_save):
            if icon_only_btn:
                icon_only_btn.setText("")
                icon_only_btn.setIconSize(QSize(30, 30))
                icon_only_btn.setFixedSize(QSize(45, 45))
                icon_only_btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                icon_only_btn.setStyleSheet(
                    "QPushButton { padding: 0px; text-align: center; }"
                )

    def _load_button_icon(self, icon_name: str) -> Optional[QIcon]:
        """先嘗試 qrc，再退回本地檔案，避免 icon 因路徑問題無法顯示。"""
        candidate_paths = [
            f":/newPrefix/icon/{icon_name}",  # 與 untitled.qrc 的 <file>icon/... 對齊
            f":/newPrefix/{icon_name}",       # 舊路徑相容
            os.path.join(os.path.dirname(__file__), "icon", icon_name),
        ]

        for path in candidate_paths:
            # 注意：QIcon(path).isNull() 對不存在路徑不一定可靠，需用 QPixmap 驗證。
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                return QIcon(pixmap)

        return None

    def _setup_tree_widgets(self):
        if hasattr(self.ui, 'importedFileList'):
            self.ui.importedFileList.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.ui.importedFileList.customContextMenuRequested.connect(
                partial(self.show_import_context_menu, self.ui.importedFileList))
            
        if hasattr(self.ui, 'cutObjectsList'):
            self.ui.cutObjectsList.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.ui.cutObjectsList.customContextMenuRequested.connect(
                partial(self.show_cut_context_menu, self.ui.cutObjectsList))

    def _setup_combo_boxes(self):
        self.ui.cbx_workmode.clear()
        self.ui.cbx_workmode.addItem("切割模式")
        self.ui.cbx_workmode.addItem("預覽模式")
        self.ui.cbx_workmode.addItem("內視鏡模式")
        self.ui.cbx_workmode.setCurrentIndex(0)
        
        self.ui.cbx_cutmode.clear()
        self.ui.cbx_cutmode.addItem("Line")
        self.ui.cbx_cutmode.addItem("Plane")
        self.ui.cbx_cutmode.addItem("Tube")
        self.ui.cbx_cutmode.addItem("Simple")
        self.ui.cbx_cutmode.setCurrentIndex(0)

    def _setup_sliders(self):
        self.ui.Sld_sagittal_5.setRange(0, 100)
        self.ui.Sld_coronal_5.setRange(0, 100)
        self.ui.Sld_axial_5.setRange(0, 100)
        
        self.ui.Sld_sagittal_5.setValue(50)
        self.ui.Sld_coronal_5.setValue(50)
        self.ui.Sld_axial_5.setValue(50)

    def _setup_checkboxes(self):
        self.ui.checkBox_sagittal.setChecked(True)
        self.ui.checkBox_corona.setChecked(True)
        self.ui.checkBox_axial.setChecked(True)

    def _setup_connections(self):
        self.ui.cbx_workmode.currentTextChanged.connect(self.on_workmode_changed)
        self.ui.cbx_cutmode.currentTextChanged.connect(self.on_cutmode_changed)
        self.ui.cbx_switch.currentTextChanged.connect(self.on_view_changed)
        
        self.ui.btn_select.clicked.connect(self.on_select_object_clicked)
        self.ui.btn_mark.clicked.connect(self.on_mark_mode_clicked)
        self.ui.btn_cut.clicked.connect(self.on_apply_cut_clicked)
        self.ui.btn_clear.clicked.connect(self.on_clear_marker_clicked)
        self.ui.btn_reset.clicked.connect(self.on_reset_clicked)
        
        self.ui.btn_import.clicked.connect(self.on_import_clicked)
        self.ui.btn_export.clicked.connect(self.on_export_model)
        self.ui.btn_undo.clicked.connect(self.on_undo_clicked)
        self.ui.btn_redo.clicked.connect(self.on_redo_clicked)
        self.ui.btn_save.clicked.connect(self.on_save_clicked)
        
        self.ui.Sld_sagittal_5.valueChanged.connect(self.slice2D_mgr.update_sagittal)
        self.ui.Sld_coronal_5.valueChanged.connect(self.slice2D_mgr.update_coronal)
        self.ui.Sld_axial_5.valueChanged.connect(self.slice2D_mgr.update_axial)
        self.ui.Sld_sagittal_5.valueChanged.connect(
            lambda v: self._on_slice_slider_changed("sagittal", v)
        )
        self.ui.Sld_coronal_5.valueChanged.connect(
            lambda v: self._on_slice_slider_changed("coronal", v)
        )
        self.ui.Sld_axial_5.valueChanged.connect(
            lambda v: self._on_slice_slider_changed("axial", v)
        )

        if hasattr(self.ui, 'endoCamera'):
            self.ui.endoCamera.setCheckable(True)
            self.ui.endoCamera.setToolTip("插入內視鏡")
            self.ui.endoCamera.clicked.connect(self.on_insert_endoscope_clicked)

    def _setup_slice_clip_controls(self):
        # 0 = off, 1 = normal clip, 2 = inverted clip
        self._slice_clip_state: dict[str, int] = {}
        self._slice_clip_buttons = {}
        self._slice_clip_icons = {}

        if hasattr(self.ui, "pushButton"):
            self._slice_clip_buttons["sagittal"] = self.ui.pushButton
        if hasattr(self.ui, "pushButton_2"):
            self._slice_clip_buttons["coronal"] = self.ui.pushButton_2
        if hasattr(self.ui, "pushButton_3"):
            self._slice_clip_buttons["axial"] = self.ui.pushButton_3

        clip_icon = self._load_button_icon("slice_clip.png")
        if clip_icon is not None:
            base = clip_icon.pixmap(22, 22)
            self._slice_clip_icons = self._build_half_icons(base)
        for name, btn in self._slice_clip_buttons.items():
            btn.setCheckable(True)
            btn.setToolTip(f"{name} 切面裁切顯示")
            btn.clicked.connect(lambda checked, n=name: self._cycle_slice_clip(n))
            if clip_icon is not None:
                btn.setIcon(clip_icon)
            else:
                print("警告: 找不到 切片裁切圖示 (slice_clip.png)")
            btn.setText("")
            btn.setIconSize(QSize(22, 22))
            btn.setFixedSize(QSize(36, 36))
            btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            btn.setStyleSheet("QPushButton { padding: 0px; text-align: center; }")

    def _cycle_slice_clip(self, slice_name: str) -> None:
        state = self._slice_clip_state.get(slice_name, 0)
        # cycle: off -> normal -> inverted -> off
        state = (state + 1) % 3
        if state == 0 and slice_name in self._slice_clip_state:
            self._slice_clip_state.pop(slice_name, None)
        else:
            self._slice_clip_state[slice_name] = state
        self._sync_slice_clip_buttons()
        self._apply_all_slice_clipping()

    def _sync_slice_clip_buttons(self) -> None:
        for name, btn in self._slice_clip_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(self._slice_clip_state.get(name, 0) > 0)
            icons = self._slice_clip_icons
            if icons:
                state = self._slice_clip_state.get(name, 0)
                btn.setIcon(self._get_slice_clip_icon(name, state, icons))
            btn.blockSignals(False)

    def _on_slice_slider_changed(self, slice_name: str, value: int) -> None:
        if self._slice_clip_state.get(slice_name, 0) == 0:
            return
        self._schedule_slice_clip_update()

    def _schedule_slice_clip_update(self) -> None:
        if self._slice_clip_update_timer.isActive():
            self._slice_clip_update_timer.stop()
        self._slice_clip_update_timer.start(self._slice_clip_update_delay_ms)

    def _build_half_icons(self, base: QtGui.QPixmap) -> dict[str, QtGui.QIcon]:
        """用同一張 icon 生成 left/right/up/down 半邊版本"""
        w = base.width()
        h = base.height()
        full_icon = QtGui.QIcon(base)

        def make_half_lr(keep_left: bool) -> QtGui.QIcon:
            pm = QtGui.QPixmap(base)
            pm.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pm)
            rect = QtCore.QRect(0, 0, w // 2, h) if keep_left else QtCore.QRect(w // 2, 0, w - w // 2, h)
            painter.drawPixmap(rect, base, rect)
            painter.end()
            return QtGui.QIcon(pm)

        def make_half_ud(keep_top: bool) -> QtGui.QIcon:
            pm = QtGui.QPixmap(base)
            pm.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pm)
            rect = QtCore.QRect(0, 0, w, h // 2) if keep_top else QtCore.QRect(0, h // 2, w, h - h // 2)
            painter.drawPixmap(rect, base, rect)
            painter.end()
            return QtGui.QIcon(pm)

        return {
            "full": full_icon,
            "left": make_half_lr(True),
            "right": make_half_lr(False),
            "up": make_half_ud(True),
            "down": make_half_ud(False),
        }

    def _get_slice_clip_icon(
        self,
        axis: str,
        state: int,
        icons: dict[str, QtGui.QIcon],
    ) -> QtGui.QIcon:
        if state == 0:
            return icons["full"]

        # sagittal: first/second swapped
        if axis == "sagittal":
            return icons["right"] if state == 1 else icons["left"]

        # coronal: use up/down to represent front/back
        if axis == "coronal":
            return icons["up"] if state == 1 else icons["down"]

        # axial: first up, second down
        if axis == "axial":
            return icons["up"] if state == 1 else icons["down"]

        return icons["full"]

    def _apply_all_slice_clipping(self) -> None:
        planes = []
        sag_state = self._slice_clip_state.get("sagittal", 0)
        if sag_state > 0:
            plane = self.slice3D_mgr.get_slice_plane("sagittal", self.ui.Sld_sagittal_5.value())
            if plane:
                if sag_state == 2:
                    nx, ny, nz = plane.GetNormal()
                    plane.SetNormal(-nx, -ny, -nz)
                planes.append(plane)
        cor_state = self._slice_clip_state.get("coronal", 0)
        if cor_state > 0:
            plane = self.slice3D_mgr.get_slice_plane("coronal", self.ui.Sld_coronal_5.value())
            if plane:
                if cor_state == 2:
                    nx, ny, nz = plane.GetNormal()
                    plane.SetNormal(-nx, -ny, -nz)
                planes.append(plane)
        ax_state = self._slice_clip_state.get("axial", 0)
        if ax_state > 0:
            plane = self.slice3D_mgr.get_slice_plane("axial", self.ui.Sld_axial_5.value())
            if plane:
                if ax_state == 2:
                    nx, ny, nz = plane.GetNormal()
                    plane.SetNormal(-nx, -ny, -nz)
                planes.append(plane)

        if not planes:
            self.obj3d_mgr.clear_preview_clipping()
        else:
            self.obj3d_mgr.apply_preview_clipping(planes)
        self._render_3d()

    def _render_3d(self) -> None:
        rw = self.mesh_renderer.GetRenderWindow()
        if rw is not None:
            rw.Render()
        if hasattr(self, "_endo_vtk_widget") and self._endo_container.isVisible():
            self._endo_vtk_widget.GetRenderWindow().Render()

    def _load_initial_data(self):
        self.file_loader.batch_load_from_path(model_path)
        self.slice2D_mgr.load_nifti(nii_path)
        # 重新載入影像後，強制刷新裁切平面，避免舊平面殘留導致不同步
        self._sync_slice_clip_offset()
        self._apply_all_slice_clipping()

    def _sync_slice_clip_offset(self) -> None:
        """若模型與影像中心有固定偏移，補正裁切平面位置"""
        if not hasattr(self, "slice3D_mgr") or self.slice3D_mgr is None:
            return
        if not hasattr(self, "mesh_renderer") or self.mesh_renderer is None:
            return
        if self.slice3D_mgr.image_data is None:
            return

        # 純影像座標模式：不做 offset/scale/flip，直接用影像矩陣
        if getattr(self, "_auto_align_obj_to_nifti", False):
            self.slice3D_mgr.set_clip_offset((0.0, 0.0, 0.0))
            self.slice3D_mgr.set_clip_center(None)
            self.slice3D_mgr.set_clip_scale((1.0, 1.0, 1.0))
            self.slice3D_mgr.set_clip_axis_flip({"sagittal": False, "coronal": False, "axial": False})
            self.slice3D_mgr.set_use_image_matrix_for_clipping(True)
            return

        bounds = [0.0] * 6
        self.mesh_renderer.ComputeVisiblePropBounds(bounds)
        if any(b == 0.0 for b in bounds):
            # 沒有有效的 mesh bounds
            self.slice3D_mgr.set_clip_offset((0.0, 0.0, 0.0))
            return

        mesh_center = (
            (bounds[0] + bounds[1]) * 0.5,
            (bounds[2] + bounds[3]) * 0.5,
            (bounds[4] + bounds[5]) * 0.5,
        )

        img_bounds, img_center = self._get_image_world_bounds_and_center()
        if img_center is None or img_bounds is None:
            return

        offset = (
            mesh_center[0] - img_center[0],
            mesh_center[1] - img_center[1],
            mesh_center[2] - img_center[2],
        )
        self.slice3D_mgr.set_clip_offset(offset)
        self.slice3D_mgr.set_clip_center(img_center)

        # 尺度補正（中心點一致但滑桿越移越偏，通常是尺度不同）
        img_size = (
            max(1e-6, img_bounds[1] - img_bounds[0]),
            max(1e-6, img_bounds[3] - img_bounds[2]),
            max(1e-6, img_bounds[5] - img_bounds[4]),
        )
        mesh_size = (
            max(1e-6, bounds[1] - bounds[0]),
            max(1e-6, bounds[3] - bounds[2]),
            max(1e-6, bounds[5] - bounds[4]),
        )
        scale = (
            mesh_size[0] / img_size[0],
            mesh_size[1] / img_size[1],
            1.0,
        )
        self.slice3D_mgr.set_clip_scale(scale)

        # 依據影像矩陣方向修正裁切方向（避免滑桿方向相反）
        flips = self._compute_clip_axis_flip()
        if flips is not None:
            self.slice3D_mgr.set_clip_axis_flip(flips)

        # 整合模式：sagittal/coronal 用補正；axial 只用影像矩陣 + 微調
        self.slice3D_mgr.set_clip_axis_matrix_usage(
            {"sagittal": True, "coronal": True, "axial": True}
        )
        self.slice3D_mgr.set_clip_axis_offset_scale_usage(
            {"sagittal": True, "coronal": True, "axial": False}
        )
        self.slice3D_mgr.set_clip_axis_bias({"axial": -0.3})

        # 若影像有旋轉，但模型看起來是軸對齊，則不要使用影像矩陣來切割
        self._sync_clip_matrix_usage()

    def _sync_clip_matrix_usage(self) -> None:
        if self.slice3D_mgr is None or self.slice3D_mgr.image_matrix is None:
            return
        m = self.slice3D_mgr.image_matrix
        # 判斷是否有明顯旋轉
        dir_i = (m.GetElement(0, 0), m.GetElement(1, 0), m.GetElement(2, 0))
        dir_j = (m.GetElement(0, 1), m.GetElement(1, 1), m.GetElement(2, 1))
        dir_k = (m.GetElement(0, 2), m.GetElement(1, 2), m.GetElement(2, 2))

        def dot(a, b):
            return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

        rotated = (
            abs(dot(dir_i, (1.0, 0.0, 0.0))) < 0.98
            or abs(dot(dir_j, (0.0, 1.0, 0.0))) < 0.98
            or abs(dot(dir_k, (0.0, 0.0, 1.0))) < 0.98
        )

        has_obj = False
        try:
            for obj in self.prop_mgr.get_original_objects():
                if obj.name.lower().endswith(".obj"):
                    has_obj = True
                    break
        except Exception:
            has_obj = False

        if rotated and has_obj:
            self.slice3D_mgr.set_use_image_matrix_for_clipping(False)
        else:
            self.slice3D_mgr.set_use_image_matrix_for_clipping(True)

    def _compute_clip_axis_flip(self) -> Optional[dict[str, bool]]:
        if self.slice3D_mgr is None or self.slice3D_mgr.image_matrix is None:
            return None
        m = self.slice3D_mgr.image_matrix
        # 取矩陣 columns 作為 i/j/k 在世界座標的方向
        dir_i = (m.GetElement(0, 0), m.GetElement(1, 0), m.GetElement(2, 0))
        dir_j = (m.GetElement(0, 1), m.GetElement(1, 1), m.GetElement(2, 1))
        dir_k = (m.GetElement(0, 2), m.GetElement(1, 2), m.GetElement(2, 2))

        def dot(a, b):
            return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

        flips = {
            "sagittal": dot(dir_i, (1.0, 0.0, 0.0)) < 0.0,
            "coronal": dot(dir_j, (0.0, 1.0, 0.0)) < 0.0,
            "axial": dot(dir_k, (0.0, 0.0, 1.0)) < 0.0,
        }
        return flips

    def _get_image_world_bounds_and_center(self) -> tuple[Optional[tuple[float, float, float, float, float, float]], Optional[tuple[float, float, float]]]:
        if self.slice3D_mgr is None or self.slice3D_mgr.image_data is None:
            return None, None
        img = self.slice3D_mgr.image_data
        extent = img.GetExtent()
        matrix = self.slice3D_mgr.image_matrix

        # 取 8 個 corner 算 world bounds（避免方向矩陣導致偏移）
        corners = []
        for i in (extent[0], extent[1]):
            for j in (extent[2], extent[3]):
                for k in (extent[4], extent[5]):
                    if matrix is not None:
                        p = [float(i), float(j), float(k), 1.0]
                        pw = [0.0, 0.0, 0.0, 0.0]
                        matrix.MultiplyPoint(p, pw)
                        if abs(pw[3]) < 1e-8:
                            corners.append((pw[0], pw[1], pw[2]))
                        else:
                            corners.append((pw[0] / pw[3], pw[1] / pw[3], pw[2] / pw[3]))
                    else:
                        origin = img.GetOrigin()
                        spacing = img.GetSpacing()
                        corners.append(
                            (
                                origin[0] + i * spacing[0],
                                origin[1] + j * spacing[1],
                                origin[2] + k * spacing[2],
                            )
                        )

        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        zs = [p[2] for p in corners]
        bounds = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
        center = ((bounds[0] + bounds[1]) * 0.5, (bounds[2] + bounds[3]) * 0.5, (bounds[4] + bounds[5]) * 0.5)
        return bounds, center

    # ======================================================================
    # 切片勾選框處理
    # ======================================================================
    def init_slice_checkboxes(self):
        """初始化切片顯示勾選框狀態"""
        self.ui.checkBox_sagittal.stateChanged.connect(
            lambda state: self.toggle_slice_visibility('sagittal', state == QtCore.Qt.Checked)
        )
        self.ui.checkBox_corona.stateChanged.connect(
            lambda state: self.toggle_slice_visibility('coronal', state == QtCore.Qt.Checked)
        )
        self.ui.checkBox_axial.stateChanged.connect(
            lambda state: self.toggle_slice_visibility('axial', state == QtCore.Qt.Checked)
        )
        
        QtCore.QTimer.singleShot(200, self._initial_slice_visibility_sync)
        
    def _initial_slice_visibility_sync(self):
        """初始同步切片可見性"""
        self.toggle_slice_visibility('sagittal', True)
        self.toggle_slice_visibility('coronal', True)
        self.toggle_slice_visibility('axial', True)

    def _set_slice_checkbox_states(self, visibility_map):
        checkbox_map = {
            'sagittal': getattr(self.ui, 'checkBox_sagittal', None),
            'coronal': getattr(self.ui, 'checkBox_corona', None),
            'axial': getattr(self.ui, 'checkBox_axial', None),
        }
        for name, checked in visibility_map.items():
            checkbox = checkbox_map.get(name)
            if checkbox is None:
                continue
            old_state = checkbox.blockSignals(True)
            checkbox.setChecked(bool(checked))
            checkbox.blockSignals(old_state)
        
    def toggle_slice_visibility(self, slice_type, visible):
        """切換指定切片在3D場景中的可見性"""
        if not hasattr(self, 'slice3D_mgr') or self.slice3D_mgr is None:
            print(f"警告: slice3D_mgr 尚未初始化")
            return False
            
        success = False
        if slice_type == 'sagittal':
            success = self.slice3D_mgr.set_slice_visibility('sagittal', visible)
        elif slice_type == 'coronal':
            success = self.slice3D_mgr.set_slice_visibility('coronal', visible)
        elif slice_type == 'axial':
            success = self.slice3D_mgr.set_slice_visibility('axial', visible)
        
        if success:
            status = "顯示" if visible else "隱藏"
            print(f"3D場景 {slice_type} 切片已{status}")
            
        return success
        
    def get_slice_visibility_status(self):
        """取得當前所有切片可見性狀態"""
        if not hasattr(self, 'slice3D_mgr') or self.slice3D_mgr is None:
            return {}
        return self.slice3D_mgr.get_slice_visibility()
        
    def show_all_slices(self):
        """顯示所有切片"""
        if not hasattr(self, 'slice3D_mgr') or self.slice3D_mgr is None:
            return False
        self.slice3D_mgr.show_all_slices()
        self.ui.checkBox_sagittal.setChecked(True)
        self.ui.checkBox_corona.setChecked(True)
        self.ui.checkBox_axial.setChecked(True)
        print("所有3D切片已顯示")
        return True
        
    def hide_all_slices(self):
        """隱藏所有切片"""
        if not hasattr(self, 'slice3D_mgr') or self.slice3D_mgr is None:
            return False
        self.slice3D_mgr.hide_all_slices()
        self.ui.checkBox_sagittal.setChecked(False)
        self.ui.checkBox_corona.setChecked(False)
        self.ui.checkBox_axial.setChecked(False)
        print("所有3D切片已隱藏")
        return True

    def update_slice_ranges_from_2d(self):
        """從2D管理器更新3D切片範圍"""
        if not hasattr(self, 'slice2D_mgr') or not hasattr(self, 'slice3D_mgr'):
            return
        ranges = self.slice2D_mgr.get_slice_ranges()
        if not ranges:
            return
        if hasattr(self.ui, 'Sld_sagittal_5'):
            self.ui.Sld_sagittal_5.setRange(ranges['sagittal'][0], ranges['sagittal'][1])
            self.ui.Sld_coronal_5.setRange(ranges['coronal'][0], ranges['coronal'][1])
            self.ui.Sld_axial_5.setRange(ranges['axial'][0], ranges['axial'][1])

    # ======================================================================
    # 事件處理方法
    # ======================================================================
    def _append_terminal_text(self, text: str):
        if text.strip():
            cursor = self.ui.textBrowser.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            cursor.insertText(text)
            self.ui.textBrowser.setTextCursor(cursor)
            self.ui.textBrowser.ensureCursorVisible()

    def _on_vtk_single_click(self) -> None:
        if not hasattr(self, "_viewport_picker") or self._viewport_picker is None:
            return
        if not self.mesh_renderer:
            return

        pos = self.interactor.GetEventPosition()
        if not pos:
            return

        x, y = int(pos[0]), int(pos[1])
        if self._viewport_picker.Pick(x, y, 0, self.mesh_renderer) == 0:
            return

        actor = self._viewport_picker.GetActor()
        if actor is None:
            return

        obj_id = self.obj3d_mgr.get_obj_id_from_actor(actor)
        if obj_id is None:
            return

        so = self.prop_mgr.get_object(obj_id)
        if so.kind == "original":
            item = self.object_list_widget.find_item(obj_id)
            self._flash_list_item(self.object_list_widget.tree, item)
        elif so.kind == "result":
            item = self.cut_list_widget.find_item(obj_id)
            self._flash_list_item(self.cut_list_widget.tree, item)

    def _flash_list_item(
        self,
        tree: QtWidgets.QTreeWidget,
        item: Optional[QtWidgets.QTreeWidgetItem],
        duration_ms: int = 1000,
    ) -> None:
        if tree is None or item is None:
            return

        # 清理前一次殘留的高亮，避免黃底卡住
        prev_item = getattr(self, "_flash_item", None)
        prev_brushes = getattr(self, "_flash_brushes", None)
        if prev_item is not None and prev_brushes is not None and prev_item is not item:
            for col, brush in zip(range(tree.columnCount()), prev_brushes):
                prev_item.setBackground(col, brush)
            prev_item.setSelected(False)

        tree.setFocus()
        tree.setCurrentItem(item)
        tree.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)

        cols = list(range(tree.columnCount()))
        original_brushes = [item.background(col) for col in cols]
        self._flash_item = item
        self._flash_brushes = original_brushes

        # 只保留單擊選取效果，不做黃底高亮
        item.setSelected(True)

        token = getattr(self, "_flash_token", 0) + 1
        self._flash_token = token

        def clear_selection():
            if getattr(self, "_flash_token", 0) != token:
                return
            if item.treeWidget() is None:
                return
            item.setSelected(False)
            if getattr(self, "_flash_item", None) is item:
                self._flash_item = None
                self._flash_brushes = None

        QtCore.QTimer.singleShot(duration_ms, clear_selection)

    def _on_vtk_left_button_down_filter(self) -> bool:
        if not hasattr(self, "_viewport_picker") or self._viewport_picker is None:
            return False
        if not self.mesh_renderer:
            return False
        pos = self.interactor.GetEventPosition()
        if not pos:
            return False
        x, y = int(pos[0]), int(pos[1])

        # 先讓當前 mode 有機會攔截，例如 plane_cut 的 ROI / handle
        current_mode = self.mode_mgr.current_mode()
        if current_mode is not None and hasattr(current_mode, "wants_to_capture_mouse"):
            try:
                if current_mode.wants_to_capture_mouse(x, y):
                    return True
            except Exception:
                pass

        if self._viewport_picker.Pick(x, y, 0, self.mesh_renderer) == 0:
            return False
        actor = self._viewport_picker.GetActor()
        if actor is None:
            return False
        return self.obj3d_mgr.get_obj_id_from_actor(actor) is not None

    def _on_scene_selected(self, obj_id: int, kind: str, selected: bool) -> None:
        if kind == "original":
            self.object_list_widget.refresh_from_manager(obj_id)
        elif kind == "result":
            self.cut_list_widget.refresh_from_manager(obj_id)

    def on_model_data_received(self, name, poly_data):
        random_color = (random.random(), random.random(), random.random())
        obj_id = self.prop_mgr.create_original(name, poly_data, color=random_color)
        self.obj3d_mgr.spawn_actor(obj_id)
        self.object_list_widget.disable_item_changed()
        self.object_list_widget.add_object(obj_id)
        self.object_list_widget.enable_item_changed()

    def _align_all_obj_to_nifti(self) -> None:
        # 先把之前對齊過的物件恢復為 identity，避免重複套矩陣
        for obj_id in list(self._aligned_obj_ids):
            try:
                obj = self.prop_mgr.get_object(obj_id)
                obj.transform.Identity()
                self.obj3d_mgr.update_actor_transform(obj_id)
            except Exception:
                pass
        self._aligned_obj_ids.clear()
        try:
            for obj in self.prop_mgr.get_original_objects():
                self._align_obj_to_nifti(obj.id)
        except Exception:
            return

    def _align_obj_to_nifti(self, obj_id: int) -> None:
        if not self.slice3D_mgr or self.slice3D_mgr.image_matrix is None:
            return
        if obj_id in self._aligned_obj_ids:
            return
        obj = self.prop_mgr.get_object(obj_id)
        if not obj.name.lower().endswith(".obj"):
            return

        if self._obj_looks_aligned(obj_id):
            return

        # 安全設定矩陣，避免 circular reference
        mat = vtk.vtkMatrix4x4()
        mat.DeepCopy(self.slice3D_mgr.image_matrix)
        obj.transform.Identity()
        obj.transform.SetMatrix(mat)
        self.obj3d_mgr.update_actor_transform(obj_id)
        self._aligned_obj_ids.add(obj_id)

    def _obj_looks_aligned(self, obj_id: int) -> bool:
        """判斷 OBJ 是否已經和 NIfTI 大致對齊，避免重複套矩陣。"""
        img_bounds, img_center = self._get_image_world_bounds_and_center()
        if img_bounds is None or img_center is None:
            return False
        actor = self.obj3d_mgr.get_actor(obj_id)
        if actor is None:
            return False
        b = actor.GetBounds()
        if b is None:
            return False
        obj_center = ((b[0] + b[1]) * 0.5, (b[2] + b[3]) * 0.5, (b[4] + b[5]) * 0.5)
        dx = obj_center[0] - img_center[0]
        dy = obj_center[1] - img_center[1]
        dz = obj_center[2] - img_center[2]
        img_diag = ((img_bounds[1]-img_bounds[0])**2 + (img_bounds[3]-img_bounds[2])**2 + (img_bounds[5]-img_bounds[4])**2) ** 0.5
        if img_diag <= 1e-6:
            return False
        # 中心距離很小就視為已對齊
        if (dx*dx + dy*dy + dz*dz) ** 0.5 < img_diag * 0.1:
            return True
        return False
        self.vtk_widget.GetRenderWindow().Render()

        # 將載入動作記錄到 History（可復原）
        if hasattr(self, 'history_mgr') and self.history_mgr:
            self.history_mgr.push_state()
            self._update_undo_redo_buttons()

        self._append_terminal_text(f"已載入模型: {name}\n")

    def on_import_item_changed(self, item, column):
        if column == 0:
            obj_id = item.data(0, QtCore.Qt.UserRole)
            if obj_id is None:
                obj_id = item.data(1, QtCore.Qt.UserRole)
            
            if obj_id is not None:
                visible = item.checkState(0) == QtCore.Qt.Checked
                self.prop_mgr.set_visible(obj_id, visible)
                self.obj3d_mgr.set_visibility(obj_id, visible)
                self.vtk_widget.GetRenderWindow().Render()
                
        elif column == 2:
            obj_id = item.data(0, QtCore.Qt.UserRole)
            if obj_id is None:
                obj_id = item.data(1, QtCore.Qt.UserRole)
            
            if obj_id is not None:
                self.prop_mgr.rename(obj_id, item.text(2))

    def on_cut_item_changed(self, item, column):
        if column == 0:
            obj_id = item.data(0, QtCore.Qt.UserRole)
            if obj_id is None:
                obj_id = item.data(1, QtCore.Qt.UserRole)
            
            if obj_id is not None:
                visible = item.checkState(0) == QtCore.Qt.Checked
                self.prop_mgr.set_visible(obj_id, visible)
                self.obj3d_mgr.set_visibility(obj_id, visible)
                self.vtk_widget.GetRenderWindow().Render()

    def show_import_context_menu(self, tree_widget, pos):
        item = tree_widget.itemAt(pos)
        if not item:
            return
            
        menu = QtWidgets.QMenu(self)
        delete_action = menu.addAction("刪除匯入物件")
        rename_action = menu.addAction("重新命名")
        
        action = menu.exec_(tree_widget.viewport().mapToGlobal(pos))
        
        if action == delete_action:
            # 從 COL_NAME (索引 2) 取得存放在 UserRole 的 obj_id
            obj_id = item.data(2, QtCore.Qt.UserRole)
            
            if obj_id is not None:
                # 1. 從 3D 渲染層移除 Actor
                self.obj3d_mgr.remove_actor(obj_id)
                
                # 2. 從資料層刪除 SceneObject
                self.prop_mgr.delete_object(obj_id)
                
                # 3. 呼叫 ObjectListWidget 內建的方法移除 UI 項目
                self.object_list_widget.remove_object(obj_id)
                
                # 4. 重要：立即重新渲染 3D 視窗，物件才會消失
                self.vtk_widget.GetRenderWindow().Render()

                # 5. 記錄到歷史紀錄
                if hasattr(self, 'history_mgr') and self.history_mgr:
                    self.history_mgr.push_state()
                    self._update_undo_redo_buttons()
                    
        elif action == rename_action:
            # 直接進入編輯模式
            tree_widget.editItem(item, 2)

    def show_cut_context_menu(self, tree_widget, pos):
        item = tree_widget.itemAt(pos)
        if not item:
            return
            
        menu = QtWidgets.QMenu(self)
        delete_action = menu.addAction("刪除切割物件")
        
        action = menu.exec_(tree_widget.viewport().mapToGlobal(pos))
        
        if action == delete_action:
            # 同樣從 COL_NAME (索引 2) 取得 obj_id
            obj_id = item.data(2, QtCore.Qt.UserRole)
            
            if obj_id is not None:
                # 1. 移除 Actor
                self.obj3d_mgr.remove_actor(obj_id)
                
                # 2. 刪除資料
                self.prop_mgr.delete_object(obj_id)
                
                # 3. 呼叫 CutListWidget 內建的方法移除 UI 項目
                self.cut_list_widget.remove_result(obj_id)
                
                # 4. 重新渲染畫面
                self.vtk_widget.GetRenderWindow().Render()

                # 5. 記錄到歷史紀錄
                if hasattr(self, 'history_mgr') and self.history_mgr:
                    self.history_mgr.push_state()
                    self._update_undo_redo_buttons()

    # ======================================================================
    # 工作模式切換
    # ======================================================================
    def on_workmode_changed(self, text: str) -> None:
        prev_mode = self.current_work_mode
        mode_map = {
            "切割模式": "cutting",
            "預覽模式": "presentation",
            "內視鏡模式": "endoscopy"
        }
        
        if text in mode_map:
            if hasattr(self, 'mode_mgr'):
                current = self.mode_mgr.current_mode()
                if hasattr(current, 'set_selecting_enabled'):
                    current.set_selecting_enabled(False)
                if hasattr(current, 'set_marking_enabled'):
                    current.set_marking_enabled(False)
            
            self.current_work_mode = mode_map[text]
            if prev_mode == "endoscopy" and self._endoscopy_insert_enabled:
                self._endoscopy_insert_enabled = False
                if hasattr(self, "endoscope_tube_mode"):
                    self.endoscope_tube_mode.set_selecting_enabled(False)
            self._update_ui_for_work_mode()
            self._update_interaction_mode()
            self._append_terminal_text(f"切換到 {text}\n")
            self._sync_button_states()

        # solve 切換過內視鏡和預覽模式後 slice 的 slider 無法使用
        if self.interactor:
            style = getattr(self, "_no_rotate_style", vtk.vtkInteractorStyleTrackballCamera())
            self.interactor.SetInteractorStyle(style)
            self.interactor.Initialize()
            self._prepare_first_viewport_click()

        # 2. 確保 UI 組件重新啟用
        self.ui.Sld_sagittal_5.setEnabled(True)
        self.ui.Sld_coronal_5.setEnabled(True)
        self.ui.Sld_axial_5.setEnabled(True)
        
        # 3. 讓主視窗重新獲得焦點
        self.setFocus()
        print(f"[Debug] 模式已切換為 {text}，Slider 已重設焦點。")

    def _update_ui_for_work_mode(self):
        is_cutting = (self.current_work_mode == "cutting")
        is_endoscopy = (self.current_work_mode == "endoscopy")
        
        self.ui.label_16.setVisible(is_cutting)
        self.ui.label.setText("工作模式選擇")
        
        self.ui.cbx_cutmode.setVisible(is_cutting)
        self.ui.cbx_cutmode.setEnabled(is_cutting)
        
        # 內視鏡模式也允許打開視角下拉選單（避免看起來「被換掉/失效」）
        self.ui.cbx_switch.setEnabled(True)
        if is_endoscopy:
            self.ui.cbx_switch.blockSignals(True)
            self.ui.cbx_switch.setCurrentIndex(0)
            self.ui.cbx_switch.blockSignals(False)

        # 右側內視鏡視窗只在真的有可顯示的內視鏡畫面時才顯示。
        self._set_endoscope_minimap_large(is_endoscopy and self._endo_enabled)
        if not is_endoscopy and hasattr(self.ui, "frame_node_7"):
            self.ui.frame_node_7.setVisible(True)
            for w in (self.ui.importedFileList, self.ui.cutObjectsList,
                      self.ui.label_12, self.ui.label_13,
                      self.ui.btn_import, self.ui.btn_export):
                if w:
                    w.setVisible(True)
            if hasattr(self, "_endo_container"):
                self._endo_container.setVisible(False)
        
        self._update_buttons_for_work_mode()
        self._update_button_styles()

    def _update_buttons_for_work_mode(self):
        cut_mode = self.ui.cbx_cutmode.currentText().lower()
        config = {}

        if self.current_work_mode == "cutting" and cut_mode == "simple":
            config = {
                "btn_select": ("上/下", "切換上下切割顯示"),
                "btn_mark": ("左/右", "切換左右切割顯示"),
                "btn_cut": ("前/後", "切換前後切割顯示"),
                "btn_clear": ("重製", "重製切割顯示"),
                "btn_reset": ("重置", "重置所有設定"),
            }
        elif self.current_work_mode == "cutting":
            config = {
                "btn_select": ("選取", "選取物件進行切割"),
                "btn_mark": ("標記", "標記切割位置"),
                "btn_cut": ("切割", "執行切割"),
                "btn_clear": ("清標記", "清除所有標記"),
                "btn_reset": ("重置", "重置切割設定"),
            }
        elif self.current_work_mode == "presentation":
            config = {
                "btn_select": ("移動物體", "可自由移動物體"),
                "btn_mark": ("切片滑動", "進入切片滑動模式"),
                "btn_cut": ("透明化", "調整選取物件透明度"),
                "btn_clear": ("清設定", "重置所有顯示設定"),
                "btn_reset": ("重置", "重置顯示設定"),
            }
        elif self.current_work_mode == "endoscopy":
            config = {
                "btn_select": ("插入內視鏡", "放置管子"),
                "btn_mark": ("", ""),
                "btn_cut": ("透明化", "調整選取物件透明度"),
                "btn_clear": ("退出內視鏡", "退出內視鏡模式"),
                "btn_reset": ("重置", "重置內視鏡設定"),
            }

        for btn_name, (text, tooltip) in config.items():
            btn = getattr(self.ui, btn_name, None)
            if btn:
                btn.setText(text)
                btn.setToolTip(tooltip)
                btn.setEnabled(True)
                btn.setVisible(True)

        # 內視鏡只保留三顆：插入內視鏡、透明化、退出內視鏡
        if self.current_work_mode == "endoscopy":
            for btn_name in ("btn_mark", "btn_reset"):
                btn = getattr(self.ui, btn_name, None)
                if btn:
                    btn.setEnabled(False)
                    btn.setVisible(False)

        self._sync_button_states()

    def _sync_button_states(self):
        if self.current_work_mode == "cutting":
            # 從 simple 切回其他 cut mode 時，先恢復切割模式的按鈕文字/顯示狀態
            self._reset_cutting_button_labels()
        current_mode = self.mode_mgr.current_mode()

        if self._is_simple_cut_mode():
            self._apply_simple_cut_button_labels()
            return

        # 如果是內視鏡模式，確保標記按鈕是禁用且隱藏的
        if self.current_work_mode == "endoscopy":
            self.ui.btn_mark.setEnabled(False)
            self.ui.btn_mark.setVisible(False)
            self.ui.btn_reset.setEnabled(False)
            self.ui.btn_reset.setVisible(False)
            return
        
        if hasattr(current_mode, '_selecting_enabled') and current_mode._selecting_enabled:
            if self.current_work_mode == "cutting":
                if self._is_plane_cut_mode():
                    self.ui.btn_select.setText("停止止血鉗標記")
                else:
                    self.ui.btn_select.setText("停止選取")
            elif self.current_work_mode == "presentation":
                self.ui.btn_select.setText("停止拖拽")
        else:
            if self.current_work_mode == "presentation":
                self.ui.btn_select.setText("移動物體")
            elif self.current_work_mode == "cutting":
                if self._is_plane_cut_mode():
                    self.ui.btn_select.setText("止血鉗標記")
                    self.ui.btn_select.setToolTip("止血鉗標記")
                else:
                    self.ui.btn_select.setText("選取")
                    self.ui.btn_select.setToolTip("選取物件進行切割")
        
        if hasattr(current_mode, '_marking_enabled') and current_mode._marking_enabled:
            if self.current_work_mode == "presentation":
                self.ui.btn_mark.setText("退出切片")
            elif self.current_work_mode == "endoscopy":
                self.ui.btn_mark.setText("拍照中...")
            elif self.current_work_mode == "cutting":
                self.ui.btn_mark.setText("標記中")
        else:
            config = {
                "cutting": ("標記", "標記切割位置"),
                "presentation": ("切片滑動", "進入切片滑動模式"),
                "endoscopy": ("拍照", "截取當前視野畫面")
            }
            if self.current_work_mode in config:
                text, tooltip = config[self.current_work_mode]
                self.ui.btn_mark.setText(text)
                self.ui.btn_mark.setToolTip(tooltip)

    def _is_simple_cut_mode(self) -> bool:
        return (
            self.current_work_mode == "cutting"
            and hasattr(self.ui, "cbx_cutmode")
            and self.ui.cbx_cutmode.currentText().lower() == "simple"
        )

    def _is_plane_cut_mode(self) -> bool:
        if self.current_work_mode != "cutting":
            return False
        if not hasattr(self.ui, "cbx_cutmode"):
            return False
        if self.ui.cbx_cutmode.currentText().lower() != "plane":
            return False
        # 進一步用 mode name 保護，避免工作模式不同步時誤改字樣
        if hasattr(self, "mode_mgr") and self.mode_mgr is not None:
            return self.mode_mgr.get_current_mode_name() == "plane_cut"
        return True

    def _apply_simple_cut_button_labels(self) -> None:
        # 1: 上/下, 2: 左/右, 3: 前/後, 4: 隱藏, 5: 重置
        self.ui.btn_select.setText("上/下")
        self.ui.btn_select.setToolTip("切換軸向裁切")
        self.ui.btn_mark.setText("左/右")
        self.ui.btn_mark.setToolTip("切換矢狀裁切")
        self.ui.btn_cut.setText("前/後")
        self.ui.btn_cut.setToolTip("切換冠狀裁切")
        self.ui.btn_clear.setEnabled(False)
        self.ui.btn_clear.setVisible(False)
        self.ui.btn_reset.setText("重置")
        self.ui.btn_reset.setToolTip("重置切割設定")

    def _reset_cutting_button_labels(self) -> None:
        if self.current_work_mode != "cutting":
            return

        config = {
            "btn_select": ("選取", "選取物件進行切割"),
            "btn_mark": ("標記", "標記切割位置"),
            "btn_cut": ("切割", "執行切割"),
            "btn_clear": ("清標記", "清除所有標記"),
            "btn_reset": ("重置", "重置切割設定"),
        }

        for btn_name, (text, tooltip) in config.items():
            btn = getattr(self.ui, btn_name, None)
            if not btn:
                continue
            btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setEnabled(True)
            btn.setVisible(True)

    def _update_undo_redo_buttons(self):
        """更新 Undo/Redo 按鈕可用狀態。"""
        if not hasattr(self, 'history_mgr') or self.history_mgr is None:
            return
        self.ui.btn_undo.setEnabled(self.history_mgr.can_undo())
        self.ui.btn_redo.setEnabled(self.history_mgr.can_redo())

    def _on_history_restored(self):
        """當 HistoryManager 還原場景時，重新建立 UI list。"""
        # 重新建立列表（包含 original + result）
        self.object_list_widget.disable_item_changed()
        self.cut_list_widget.disable_item_changed()

        self.object_list_widget.tree.clear()
        self.cut_list_widget.tree.clear()

        for so in self.prop_mgr.get_original_objects():
            self.object_list_widget.add_object(so.id)

        for so in self.prop_mgr.get_result_objects():
            self.cut_list_widget.add_result(so.id)

        # 重新同步一次名稱/狀態以避免 UI 舊 callback 影響
        for so in self.prop_mgr.get_all_objects():
            if so.kind == 'original':
                self.object_list_widget.refresh_from_manager(so.id)
            else:
                self.cut_list_widget.refresh_from_manager(so.id)

        self.object_list_widget.enable_item_changed()
        self.cut_list_widget.enable_item_changed()

        self._update_undo_redo_buttons()
        # 確保畫面更新
        self.vtk_widget.GetRenderWindow().Render()

    def _update_button_styles(self):
        color_map = {
            "cutting": {
                "bg": "#e0e0e0",
                "text": "#333333",
                "border": "#b0b0b0",
            },
            "presentation": {
                "bg": "#e8f4f8",
                "text": "#2c5282",
                "border": "#90cdf4",
            },
            "endoscopy": {
                "bg": "#f0f8e8",
                "text": "#2d5a27",
                "border": "#9ae6b4",
            }
        }
        
        if self.current_work_mode in color_map:
            colors = color_map[self.current_work_mode]
            
            style = f"""
                QPushButton {{
                    background-color: {colors['bg']};
                    color: {colors['text']};
                    border: 1px solid {colors['border']};
                    padding: 6px 12px;
                    border-radius: 3px;
                    font-weight: normal;
                    font-size: 12px;
                    min-height: 24px;
                }}
                QPushButton:hover {{
                    background-color: {self._lighten_color(colors['bg'], 10)};
                    border: 1px solid {self._darken_color(colors['border'], 20)};
                }}
                QPushButton:pressed {{
                    background-color: {self._darken_color(colors['bg'], 10)};
                }}
            """
            
            for btn_name in ['btn_select', 'btn_mark', 'btn_cut', 'btn_clear', 'btn_reset']:
                btn = getattr(self.ui, btn_name, None)
                if btn:
                    btn.setStyleSheet(style)

    def _update_interaction_mode(self):
        if self.current_work_mode == "cutting":
            cut_mode = self.ui.cbx_cutmode.currentText().lower()
            if cut_mode == "simple":
                self.mode_mgr.set_mode("simple_cut")
            elif cut_mode == "plane":
                self.mode_mgr.set_mode("plane_cut")
            elif cut_mode == "line":
                self.mode_mgr.set_mode("line_cut")
            elif cut_mode == "tube":
                self.mode_mgr.set_mode("tube_cut")
            else:
                self.mode_mgr.set_mode("camera")
                
        elif self.current_work_mode == "endoscopy":
            self.mode_mgr.set_mode("endoscopy")
            
        elif self.current_work_mode == "presentation":
            self.mode_mgr.set_mode("preview")
        self._prepare_first_viewport_click()

    def on_cutmode_changed(self, text: str) -> None:
        if self.current_work_mode != "cutting":
            return
            
        mode_map = {
            "simple": "simple_cut",
            "plane": "plane_cut",
            "line": "line_cut",
            "tube": "tube_cut"
        }

        mode_name = mode_map.get(text.lower(), "camera")
        self.mode_mgr.set_mode(mode_name)
        self._prepare_first_viewport_click()
        self._update_buttons_for_work_mode()  # 更新按鍵文本
        self._append_terminal_text(f"切割模式: {text}\n")
        self._sync_button_states()

    def on_view_changed(self, text: str) -> None:
        if text == "【請選擇視角】":
            return

        if self.current_work_mode == "endoscopy":
            # 內視鏡進入中就先不切外部視角，避免打亂內視鏡相機
            if hasattr(self, "endoscopy_manager") and self.endoscopy_manager and self.endoscopy_manager.endoscope_mode:
                self._append_terminal_text("內視鏡已啟動，暫不支援外部視角切換\n")
                return
            
        cam = self.mesh_renderer.GetActiveCamera()
        foc = cam.GetFocalPoint()
        d = 500
            
        view_actions = {
            "Posterior": ((foc[0], foc[1]-d, foc[2]), (0, 0, 1)),
            "Anterior": ((foc[0], foc[1]+d, foc[2]), (0, 0, 1)),
            "Right": ((foc[0]+d, foc[1], foc[2]), (0, 0, 1)),
            "Left": ((foc[0]-d, foc[1], foc[2]), (0, 0, 1)),
            "Superior": ((foc[0], foc[1], foc[2]+d), (0, 1, 0)),
            "Inferior": ((foc[0], foc[1], foc[2]-d), (0, -1, 0))
        }
            
        if text in view_actions:
            pos, view_up = view_actions[text]
            cam.SetPosition(pos)
            cam.SetViewUp(view_up)
                
        self.mesh_renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()
        self._append_terminal_text(f"視角: {text}\n")

    # ======================================================================
    # 按鈕動作處理
    # ======================================================================
    def on_import_clicked(self) -> None:
        # 詢問用戶匯入方式：工作檔或物件檔
        msg = QtWidgets.QMessageBox()
        msg.setWindowTitle("匯入方式")
        msg.setText("選擇匯入方式：")
        workfile_btn = msg.addButton("工作檔 (.scene)", QtWidgets.QMessageBox.AcceptRole)
        objfile_btn = msg.addButton("物件檔 (.obj)", QtWidgets.QMessageBox.AcceptRole)
        cancel_btn = msg.addButton("取消", QtWidgets.QMessageBox.RejectRole)
        msg.exec_()

        if msg.clickedButton() == workfile_btn:
            # 匯入工作檔
            file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "選擇工作檔",
                "",
                "工作檔 (*.scene)"
            )
            if file_path:
                success = self.history_mgr.load_scene_from_file(file_path)
                if success:
                    self._append_terminal_text(f"已匯入工作檔: {file_path}\n")
                    # 更新 UI
                    self._on_history_restored()
                    # 重置相機視角
                    self.mesh_renderer.ResetCamera()
                    self.mesh_renderer.Render()
                else:
                    self._append_terminal_text(f"匯入工作檔失敗: {file_path}\n")
                    QtWidgets.QMessageBox.critical(self, "匯入失敗", f"無法匯入工作檔:\n{file_path}")
        elif msg.clickedButton() == objfile_btn:
            # 匯入物件檔
            self.file_loader.import_model()
        elif msg.clickedButton() == cancel_btn:
            return

    def _import_folder_with_metadata(self, folder: str) -> bool:
        metadata_path = os.path.join(folder, 'scene_metadata.json')
        if not os.path.exists(metadata_path):
            return False

        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            objects = data.get('objects', [])
            if not objects:
                return False

            for item in objects:
                obj_file = item.get('obj_file')
                if not obj_file:
                    continue

                obj_path = os.path.join(folder, obj_file)
                if not os.path.exists(obj_path):
                    print(f"[Import] OBJ 不存在: {obj_path}")
                    continue

                poly_data = self.file_loader._read_obj(obj_path)
                if not poly_data:
                    continue

                name = str(item.get('name') or os.path.splitext(os.path.basename(obj_file))[0])
                color = tuple(item.get('color', (0.8, 0.8, 0.8)))
                opacity = float(item.get('opacity', 1.0))
                visible = bool(item.get('visible', True))
                group = str(item.get('group', 'default'))

                obj_id = self.prop_mgr.create_original(name, poly_data, color=color, opacity=opacity, group=group)
                self.obj3d_mgr.spawn_actor(obj_id)

                if not visible:
                    self.prop_mgr.set_visible(obj_id, False)
                    self.obj3d_mgr.set_visibility(obj_id, False)

                self.object_list_widget.disable_item_changed()
                self.object_list_widget.add_object(obj_id)
                self.object_list_widget.enable_item_changed()

            self.vtk_widget.GetRenderWindow().Render()
            if hasattr(self, 'history_mgr') and self.history_mgr:
                self.history_mgr.push_state()
                self._update_undo_redo_buttons()

            self._append_terminal_text(f"已批次匯入資料夾(含 metadata): {folder}\n")
            return True

        except Exception as e:
            self._append_terminal_text(f"資料夾匯入失敗: {e}\n")
            return False

    def _import_folder_obj_only(self, folder: str) -> bool:
        obj_files = [f for f in os.listdir(folder) if f.lower().endswith('.obj')]
        if not obj_files:
            return False

        for obj_file in obj_files:
            obj_path = os.path.join(folder, obj_file)
            poly_data = self.file_loader._read_obj(obj_path)
            if not poly_data:
                continue

            name = os.path.splitext(obj_file)[0]
            color = (random.random(), random.random(), random.random())
            obj_id = self.prop_mgr.create_original(name, poly_data, color=color)
            self.obj3d_mgr.spawn_actor(obj_id)

            self.object_list_widget.disable_item_changed()
            self.object_list_widget.add_object(obj_id)
            self.object_list_widget.enable_item_changed()

        self.vtk_widget.GetRenderWindow().Render()
        if hasattr(self, 'history_mgr') and self.history_mgr:
            self.history_mgr.push_state()
            self._update_undo_redo_buttons()

        self._append_terminal_text(f"已批次匯入資料夾(OBJ 只有): {folder}\n")
        return True
    def _estimate_tube_depth(self) -> float:
        bounds = [0.0] * 6
        self.mesh_renderer.ComputeVisiblePropBounds(bounds)
        dx = bounds[1] - bounds[0]
        dy = bounds[3] - bounds[2]
        dz = bounds[5] - bounds[4]
        diag = (dx * dx + dy * dy + dz * dz) ** 0.5
        if diag <= 0:
            return 80.0
        return max(30.0, diag * 0.2)

    def _setup_endoscope_minimap(self) -> None:
        self._endo_enabled = False
        # 使用右側列表區塊作為小地圖容器（需要時才顯示）
        self._endo_container = QtWidgets.QWidget(self.ui.frame_node_7)
        self._endo_container.setObjectName("endo_minimap_container")
        self._endo_container.setVisible(False)
        self._endo_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        container_layout = QtWidgets.QVBoxLayout(self._endo_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        self._endo_vtk_widget = QVTKRenderWindowInteractor(self._endo_container)
        self._endo_vtk_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        container_layout.addWidget(self._endo_vtk_widget)

        # 放到右側框的 layout 裡，讓它填滿
        if hasattr(self.ui, "gridLayout_59"):
            self.ui.gridLayout_59.addWidget(self._endo_container, 0, 0, 1, 1)

        self._endo_renderer = vtk.vtkRenderer()
        self._endo_renderer.SetBackground(0.05, 0.05, 0.05)
        self._endo_camera = vtk.vtkCamera()
        self._endo_renderer.SetActiveCamera(self._endo_camera)

        endo_rw = self._endo_vtk_widget.GetRenderWindow()
        endo_rw.AddRenderer(self._endo_renderer)
        self._endo_vtk_widget.Initialize()

        # 右側內視鏡小地圖：完全阻擋滑鼠/鍵盤事件，避免任何操作影響視角
        self._endo_vtk_widget.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        self._endo_vtk_widget.setFocusPolicy(QtCore.Qt.NoFocus)
        self._endo_container.setFocusPolicy(QtCore.Qt.NoFocus)
        self._endo_container.installEventFilter(self)
        self._endo_vtk_widget.installEventFilter(self)

    def _set_endoscope_minimap_large(self, enabled: bool) -> None:
        if not hasattr(self, "_endo_renderer"):
            return
        if hasattr(self.ui, "frame_node_7"):
            if enabled:
                # 隱藏右側列表，顯示小地圖
                for w in (self.ui.importedFileList, self.ui.cutObjectsList,
                          self.ui.label_12, self.ui.label_13,
                          self.ui.btn_import, self.ui.btn_export):
                    if w:
                        w.setVisible(False)
                self.ui.frame_node_7.setVisible(True)
                self._endo_container.setVisible(True)
            else:
                # 內視鏡未插入時：右側整塊隱藏，主視窗變大
                self._endo_container.setVisible(False)
                self.ui.frame_node_7.setVisible(False)
        self._render_3d()

    def _sync_endoscope_renderer_props(self) -> None:
        for actor in self.obj3d_mgr.all_actors():
            if not self._endo_renderer.HasViewProp(actor):
                self._endo_renderer.AddActor(actor)

        # 不在小地圖中顯示管子，避免看到圓形內壁
        tube_actor = self.endoscope_tube_mode.get_tube_actor() if hasattr(self, "endoscope_tube_mode") else None
        if tube_actor and self._endo_renderer.HasViewProp(tube_actor):
            self._endo_renderer.RemoveActor(tube_actor)

    def _on_tube_updated(self, p0, p1) -> None:
        if hasattr(self, "slice2D_mgr") and self.slice2D_mgr is not None:
            self.slice2D_mgr.update_endoscope_tube(p0, p1)

        if p0 is None or p1 is None:
            self._endo_enabled = False
            if hasattr(self._endo_renderer, "SetDraw"):
                self._endo_renderer.SetDraw(0)
            self._set_endoscope_minimap_large(False)
            self._render_3d()
            return

        self._endo_enabled = True
        if hasattr(self._endo_renderer, "SetDraw"):
            self._endo_renderer.SetDraw(1)
        self._set_endoscope_minimap_large(True)

        self._sync_endoscope_renderer_props()

        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        dz = p1[2] - p0[2]
        length = (dx * dx + dy * dy + dz * dz) ** 0.5
        if length < 1e-6:
            return

        # 內視鏡視角：取「較靠近中心」的端點當作鏡頭位置
        bounds = [0.0] * 6
        self.mesh_renderer.ComputeVisiblePropBounds(bounds)
        cx = (bounds[0] + bounds[1]) * 0.5
        cy = (bounds[2] + bounds[3]) * 0.5
        cz = (bounds[4] + bounds[5]) * 0.5

        def dist2(a, b, c, p):
            return (p[0]-a)**2 + (p[1]-b)**2 + (p[2]-c)**2

        d0 = dist2(cx, cy, cz, p0)
        d1 = dist2(cx, cy, cz, p1)
        p_in, p_out = (p0, p1) if d0 < d1 else (p1, p0)

        # 視線方向朝向中心（更像內視鏡往內看）
        vx = cx - p_in[0]
        vy = cy - p_in[1]
        vz = cz - p_in[2]
        vnorm = (vx * vx + vy * vy + vz * vz) ** 0.5
        if vnorm > 1e-6:
            vx, vy, vz = vx / vnorm, vy / vnorm, vz / vnorm
        else:
            vx, vy, vz = (p_out[0] - p_in[0]) / length, (p_out[1] - p_in[1]) / length, (p_out[2] - p_in[2]) / length

        cam_pos = (p_in[0] + vx * length * 0.02, p_in[1] + vy * length * 0.02, p_in[2] + vz * length * 0.02)
        focal = (p_in[0] + vx * length * 0.5, p_in[1] + vy * length * 0.5, p_in[2] + vz * length * 0.5)

        up = (0.0, 1.0, 0.0)
        dot = abs(vx * up[0] + vy * up[1] + vz * up[2])
        if dot > 0.95:
            up = (1.0, 0.0, 0.0)

        self._endo_camera.SetPosition(cam_pos)
        self._endo_camera.SetFocalPoint(focal)
        self._endo_camera.SetViewUp(*up)
        self._endo_camera.SetClippingRange(max(0.1, length * 0.01), max(10.0, length * 4.0))
        self._render_3d()

    def on_select_object_clicked(self) -> None:
        if self.current_work_mode == "endoscopy":
            if not self._endoscopy_insert_enabled:
                self._endoscopy_insert_enabled = True
                self.mode_mgr.set_mode("endoscope_tube")
                self.endoscope_tube_mode.set_initial_centered(True)
                self.endoscope_tube_mode.set_depth(self._estimate_tube_depth())
                self.endoscope_tube_mode.set_selecting_enabled(True)
                self.ui.btn_select.setText("停止插入")
                self._set_endoscope_minimap_large(False)
            else:
                self._endoscopy_insert_enabled = False
                self.endoscope_tube_mode.set_selecting_enabled(False)
                self.endoscope_tube_mode.set_initial_centered(True)
                self.mode_mgr.set_mode("camera")
                self.ui.btn_select.setText("插入內視鏡")
                self._set_endoscope_minimap_large(False)
            return

        cut_mode = self.ui.cbx_cutmode.currentText().lower()
        if self.current_work_mode == "cutting" and cut_mode == "simple":
            # Simple cut: 上/下
            if hasattr(self, 'simple_cut_mode'):
                self.simple_cut_mode.set_cut_direction("up")
            return
        if self.current_work_mode == "presentation":
            self._initialize_presentation_manager()
            if hasattr(self, "mode_mgr") and self.mode_mgr is not None:
                if self.mode_mgr.get_current_mode_name() != "preview":
                    self.mode_mgr.set_mode("preview")
            if self.presentation_manager:
                self.presentation_manager.toggle_selecting()
            self._sync_button_states()
            return
        else:
            # 正常選取邏輯
            mode = self.mode_mgr.current_mode()
            if hasattr(mode, 'toggle_selecting'):
                mode.toggle_selecting()
                self._sync_button_states()

    def on_mark_mode_clicked(self) -> None:
        cut_mode = self.ui.cbx_cutmode.currentText().lower()
        if self.current_work_mode == "cutting" and cut_mode == "simple":
            # Simple cut: 左/右
            if hasattr(self, 'simple_cut_mode'):
                self.simple_cut_mode.set_cut_direction("right")
        else:
            # 原有邏輯
            if self.current_work_mode == "endoscopy":
                return
        
            mode = self.mode_mgr.current_mode()
            
            if self.current_work_mode == "presentation":
                self._initialize_presentation_manager()
                self.presentation_manager.toggle_marking()
            elif self.current_work_mode == "endoscopy":
                self._initialize_endoscopy_manager()
                if not self.endoscopy_manager.endoscope_mode:
                    self.endoscopy_manager.activate_endoscope()
                self.endoscopy_manager.take_snapshot()
            elif hasattr(mode, 'toggle_marking'):
                mode.toggle_marking()
                self._sync_button_states()
            else:
                print(f"[UI] 當前模式 {mode.name} 不支援標記功能")

    def on_apply_cut_clicked(self):
        cut_mode = self.ui.cbx_cutmode.currentText().lower()
        if self.current_work_mode == "cutting" and cut_mode == "simple":
            # Simple cut: 前/後
            if hasattr(self, 'simple_cut_mode'):
                self.simple_cut_mode.set_cut_direction("front")
        else:
            # 原有邏輯
            mode = self.mode_mgr.current_mode()
            
            if self.current_work_mode == "presentation":
                # 預覽模式：透明度調整
                self._initialize_presentation_manager()  
                if self.presentation_manager:
                    self.presentation_manager.commit()  
                    
            elif self.current_work_mode == "endoscopy":
                # 內視鏡模式：透明度調整
                self._initialize_endoscopy_manager()  
                if self.endoscopy_manager:
                    if not self.endoscopy_manager._endoscope_camera:
                        self.endoscopy_manager.activate_endoscope()
                    self.endoscopy_manager.commit()  

                    
            elif hasattr(mode, 'commit'):
                # 切割模式：執行切割
                ret = mode.commit()
                if not ret: 
                    return
                
                changed_ids, new_result_ids, to_delete_ids = ret
                
                for oid in changed_ids:
                    self.object_list_widget.disable_item_changed()
                    if self.prop_mgr.get_object(oid).kind == "original":
                        self.object_list_widget.refresh_from_manager(oid)
                    self.object_list_widget.enable_item_changed()
                
                for oid in to_delete_ids:
                    self.cut_list_widget.remove_result(oid)
                    self.obj3d_mgr.remove_actor(oid)
                    self.prop_mgr.delete_object(oid)            
                
                for rid in new_result_ids:
                    self.cut_list_widget.disable_item_changed()
                    self.cut_list_widget.add_result(rid)
                    self.cut_list_widget.enable_item_changed()
                    self.cut_list_widget.refresh_from_manager(rid)
                
                # 記錄操作歷史（Undo/Redo）
                if hasattr(self, 'history_mgr') and self.history_mgr:
                    self.history_mgr.push_state()
                    self._update_undo_redo_buttons()
                
                self._append_terminal_text("切割完成\n")
                self._render_3d()
            else:
                print(f"[UI] 當前模式 {mode.name} 不支援切割功能")

    def on_clear_marker_clicked(self) -> None:
        cut_mode = self.ui.cbx_cutmode.currentText().lower()
        if self.current_work_mode == "cutting" and cut_mode == "simple":
            # Simple cut: 重製
            if hasattr(self, 'simple_cut_mode'):
                self.simple_cut_mode.reset()
        else:
            # 原有邏輯
            mode = self.mode_mgr.current_mode()
            
            if self.current_work_mode == "presentation":
                self._initialize_presentation_manager()
                self.presentation_manager.reset_presentation_view()
            elif self.current_work_mode == "endoscopy":
                self._restore_endoscopy_preinsert_view()
            elif hasattr(mode, 'clear_markers'):
                mode.clear_markers()
                self._sync_button_states()
                self._append_terminal_text("已清除所有標記\n")

    def _restore_endoscopy_preinsert_view(self) -> None:
        self._initialize_endoscopy_manager()

        self._endoscopy_insert_enabled = False

        if hasattr(self, "endoscope_tube_mode") and self.endoscope_tube_mode:
            self.endoscope_tube_mode.set_selecting_enabled(False)
            self.endoscope_tube_mode.clear_markers()
            self.endoscope_tube_mode.set_initial_centered(True)

        if self.endoscopy_manager:
            self.endoscopy_manager.exit_insertion_mode()
            self.endoscopy_manager.deactivate_endoscope()

        self._endo_enabled = False
        if hasattr(self, "_endo_renderer") and hasattr(self._endo_renderer, "SetDraw"):
            self._endo_renderer.SetDraw(0)
        self._set_endoscope_minimap_large(False)

        self.mode_mgr.set_mode("camera")
        if hasattr(self.ui, "btn_select"):
            self.ui.btn_select.setText("插入內視鏡")

        self._render_3d()
        self._append_terminal_text("已退出內視鏡並恢復到插入前畫面\n")

    def on_reset_clicked(self) -> None:
        cut_mode = self.ui.cbx_cutmode.currentText().lower()
        if self.current_work_mode == "cutting" and cut_mode == "simple":
            # Simple cut: 重製 (同上)
            if hasattr(self, 'simple_cut_mode'):
                self.simple_cut_mode.reset()
        elif self.current_work_mode == "presentation":
            self._initialize_presentation_manager()
            current_mode = self.mode_mgr.current_mode()
            if hasattr(current_mode, 'reset'):
                current_mode.reset()
            self._sync_button_states()
            return
        else:
            # 原有邏輯
            """
            重置功能：清空當前所有物件，並從路徑重新匯入原始模型。
            此動作會被視為一個新的歷史紀錄，因此使用者可以 Undo 回到重置前。
            """
            # 1. 讓當前模式清理標記 (例如 PlaneCut 的預覽面)
            current_mode = self.mode_mgr.current_mode()
            if hasattr(current_mode, 'reset'):
                current_mode.reset()

            # 2. 清除當前所有資料與渲染器中的 Actor
            # 取得目前所有物件 ID
            all_ids = [obj.id for obj in self.prop_mgr.get_all_objects()]
            
            for obj_id in all_ids:
                # 從渲染層移除
                self.obj3d_mgr.remove_actor(obj_id)
                # 從資料層移除
                self.prop_mgr.delete_object(obj_id)
            
            # 3. 清空 UI 列表
            self.object_list_widget.tree.clear()
            self.cut_list_widget.tree.clear()

        # 4. 重新從路徑載入原始物件
        # 使用您定義好的 model_path
        self._append_terminal_text(f"正在從路徑重新匯入初始模型...\n")
        self.file_loader.batch_load_from_path(model_path)

        # 5. 畫面重繪
        self.vtk_widget.GetRenderWindow().Render()

        # 6. 【關鍵】將這個「重置後的狀態」存入歷史紀錄
        # 這樣使用者按下 Undo 就能回到「重置前」刪除或切割過的樣子
        if hasattr(self, 'history_mgr') and self.history_mgr:
            self.history_mgr.push_state()
            self._update_undo_redo_buttons()

        self._append_terminal_text("場景已重置：原始模型已重新載入。\n")

    def on_undo_clicked(self):
        if hasattr(self, 'history_mgr') and self.history_mgr:
            self.history_mgr.undo()
            self._update_undo_redo_buttons()
        self._append_terminal_text("復原操作\n")

    def on_redo_clicked(self):
        if hasattr(self, 'history_mgr') and self.history_mgr:
            self.history_mgr.redo()
            self._update_undo_redo_buttons()
        self._append_terminal_text("重做操作\n")

    def on_save_clicked(self):
        # 儲存為單一工作檔 (.scene)
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "儲存工作檔",
            "",
            "工作檔 (*.scene)"
        )

        if not file_path:
            self._append_terminal_text("使用者取消儲存\n")
            return

        try:
            if hasattr(self, 'history_mgr') and self.history_mgr:
                # 儲存所有物件的狀態（包括隱藏的）
                saved = self.history_mgr.save_scene_to_file(file_path, include_hidden=True)
                if saved:
                    self._append_terminal_text(f"已儲存工作檔: {file_path}\n")
                    QtWidgets.QMessageBox.information(self, "儲存完成", f"已儲存工作檔至:\n{file_path}")
                else:
                    raise RuntimeError("儲存過程失敗")
            else:
                raise RuntimeError("HistoryManager 尚未初始化")
        except Exception as e:
            self._append_terminal_text(f"儲存失敗：{e}\n")
            QtWidgets.QMessageBox.critical(self, "儲存失敗", f"儲存工作檔時發生錯誤:\n{e}")

    def on_export_model(self) -> None:
        dialog = ExportDialog(self, prop_manager=self.prop_mgr, obj3d_manager=self.obj3d_mgr)
        dialog.exec_()  # 顯示對話框，阻塞直到關閉


    def open_property_dialog_for_obj(self, obj_id: int) -> None:
        so = self.prop_mgr.get_object(obj_id)
        dlg = PropertyDialog(so, main_window=self)
        dlg.exec_()
        self.object_list_widget.refresh_from_manager(obj_id)
        self.cut_list_widget.refresh_from_manager(obj_id)
        self._append_terminal_text(f"編輯物件屬性: {so.name}\n")

    def toggle_endoscope_mode(self):
        self._initialize_endoscopy_manager()
        if self.endoscopy_manager.endoscope_mode:
            self.endoscopy_manager.deactivate_endoscope()
        else:
            self.endoscopy_manager.activate_endoscope()

    def on_insert_endoscope_clicked(self, checked: bool) -> None:
        self._initialize_endoscopy_manager()
        if checked:
            self._insert_mode_prev = self.mode_mgr.get_current_mode_name()
            self.mode_mgr.set_mode("endoscopy")
            self.endoscopy_manager.enter_insertion_mode()
        else:
            self.endoscopy_manager.exit_insertion_mode()
            prev = getattr(self, "_insert_mode_prev", None)
            if prev:
                self.mode_mgr.set_mode(prev)
            else:
                self.mode_mgr.set_mode("camera")

    def _initialize_presentation_manager(self):
        # 預覽模式實例已在 _register_interaction_modes 建立並註冊，
        # 這裡統一指向同一個實例，避免操作到分身導致狀態不同步。
        self.presentation_manager = self.preview_mode

    def _initialize_endoscopy_manager(self):
        # 內視鏡模式實例已在 _register_interaction_modes 建立並註冊，
        # 統一使用同一個實例，避免 UI 按鈕操作到分身。
        self.endoscopy_manager = self.endoscopy_mode

    # ======================================================================
    # 視窗事件處理
    # ======================================================================
    def resizeEvent(self, event):
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(50, self.layout_mgr.adjust_dynamic_layout)
        
        if hasattr(self, 'display_control'):
            self.display_control.on_window_resize()

    '''def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(100, self.adjust_view_sizes)

    def closeEvent(self, event):
        sys.stdout = sys.__stdout__
           
        super().closeEvent(event)'''


    # ======================================================================
    # 工具方法
    # ======================================================================
    def adjust_view_sizes(self):
        if hasattr(self, 'vtk_widget') and self.vtk_widget:
            self.vtk_widget.GetRenderWindow().Render()

    def _lighten_color(self, hex_color, percent=10):
        return self._adjust_color_brightness(hex_color, percent)
    
    def _darken_color(self, hex_color, percent=10):
        return self._adjust_color_brightness(hex_color, -percent)
    
    def _adjust_color_brightness(self, hex_color, percent):
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 3:
            hex_color = ''.join([c*2 for c in hex_color])
            
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        v = max(0, min(1, v * (1 + percent/100)))
        
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        
        return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'

    def keyPressEvent(self, event):
        if self.mode_mgr.handle_key_press(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if hasattr(self, "_endo_container") and self._endo_container and self._endo_container.isVisible():
            if obj is getattr(self, "_endo_container", None) or obj is getattr(self, "_endo_vtk_widget", None):
                et = event.type()
                if et in (
                    QtCore.QEvent.MouseButtonPress,
                    QtCore.QEvent.MouseButtonRelease,
                    QtCore.QEvent.MouseButtonDblClick,
                    QtCore.QEvent.MouseMove,
                    QtCore.QEvent.Wheel,
                    QtCore.QEvent.ContextMenu,
                    QtCore.QEvent.KeyPress,
                    QtCore.QEvent.KeyRelease,
                    QtCore.QEvent.Gesture,
                    QtCore.QEvent.TouchBegin,
                    QtCore.QEvent.TouchEnd,
                    QtCore.QEvent.TouchUpdate,
                ):
                    return True
        return super().eventFilter(obj, event)

    # ======================================================================
    # 屬性訪問器
    # ======================================================================
    @property
    def vtkWidget(self):
        return self.vtk_widget
    
    @property
    def ren(self):
        return self.mesh_renderer
    
    @property
    def iren(self):
        return self.interactor

from __future__ import annotations

from typing import Optional, Callable

from PyQt5 import QtWidgets, QtCore

from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager
from ui.dialogs.property_dialog import PropertyDialog


class CutListWidget:
    """
    右下「切割物件列表」（Result Objects List）。

    顯示的對象：SceneObject.kind == "result" 的物件。

    欄位：
        0: 顯示   (checkbox → SceneObject.visible)
        1: 選取   (checkbox → SceneObject.selected)
        2: 物件名稱 (文字，可改名)
        3: 透明度 (QSlider 0~100 → opacity 0.0~1.0)
        4: 設定   (QPushButton "..."，點擊後呼叫 on_settings_requested(obj_id))
    """

    COL_VISIBLE = 0
    COL_SELECTED = 1
    COL_NAME = 2
    COL_OPACITY = 3
    COL_SETTINGS = 4

    # 欄位寬度設定
    COLUMN_WIDTHS = {
        COL_VISIBLE: 52,    # 顯示
        COL_SELECTED: 52,   # 選取
        COL_NAME: 110,      # 物件名稱
        COL_OPACITY: 110,   # 透明度
        COL_SETTINGS: 52,   # 設定
    }

    def __init__(
        self,
        tree_widget: QtWidgets.QTreeWidget,
        prop_mgr: ObjectPropertyManager,
        obj3d_mgr: Object3DManager,
    ) -> None:

        self.tree = tree_widget
        self.prop_mgr = prop_mgr
        self.obj3d_mgr = obj3d_mgr

        # 由 MainWindow 指定的 callback：on_settings_requested(obj_id: int) -> None
        self.on_settings_requested: Optional[Callable[[int], None]] = None

        # 欄位與標題
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["顯示", "選取", "物件名稱", "透明度", "設定"])
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        
        # 設定欄位寬度
        self._setup_column_widths()
        
        self.tree.setIconSize(QtCore.QSize(16, 16))

        # 綁事件
        self.tree.itemChanged.connect(self._on_item_changed)

    def _setup_column_widths(self):
        """設定各欄位的寬度"""
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(44)
        for col in range(self.tree.columnCount()):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.Interactive)

        for col, width in self.COLUMN_WIDTHS.items():
            self.tree.setColumnWidth(col, width)

    # ======================================================================
    # 避免複寫
    # ======================================================================

    def enable_item_changed(self) -> None:
        # 避免重複 connect
        try:
            self.tree.itemChanged.disconnect(self._on_item_changed)
        except TypeError:
            pass
        self.tree.itemChanged.connect(self._on_item_changed)

    def disable_item_changed(self) -> None:
        try:
            self.tree.itemChanged.disconnect(self._on_item_changed)
        except TypeError:
            pass
    # ======================================================================
    # 新增 result 物件
    # ======================================================================

    def add_result(self, obj_id: int) -> None:
        """
        將一個 kind='result' 的 SceneObject 加入列表。
        （由 PlaneCutMode 或其他邏輯在 create_result 後呼叫）
        """
        so = self.prop_mgr.get_object(obj_id)
        if so.kind != "result":
            # 如果你希望也能加 original，可以拿掉這個檢查
            return

        item = QtWidgets.QTreeWidgetItem(self.tree)

        # 存 obj_id 在 UserRole
        item.setData(self.COL_NAME, QtCore.Qt.UserRole, obj_id)

        # checkbox / selectable
        item.setFlags(
            item.flags()
            | QtCore.Qt.ItemIsSelectable
            | QtCore.Qt.ItemIsEnabled
        )

        # 顯示 / 選取欄（置中）
        visible_widget = self._build_centered_checkbox(so.visible, obj_id, "visible")
        selected_widget = self._build_centered_checkbox(so.selected, obj_id, "selected")
        self.tree.setItemWidget(item, self.COL_VISIBLE, visible_widget)
        self.tree.setItemWidget(item, self.COL_SELECTED, selected_widget)

        # 名稱
        item.setText(self.COL_NAME, so.name)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)

        # 透明度 slider
        slider_widget = self._build_centered_slider(int(so.opacity * 100), obj_id)
        self.tree.setItemWidget(item, self.COL_OPACITY, slider_widget)

        # 設定按鈕
        btn_widget = self._build_centered_settings_button(obj_id)
        self.tree.setItemWidget(item, self.COL_SETTINGS, btn_widget)

    # ======================================================================
    # item 改變（顯示 / 選取 / 名稱）
    # ======================================================================

    def _on_item_changed(self, item: QtWidgets.QTreeWidgetItem, col: int) -> None:
        obj_id = item.data(self.COL_NAME, QtCore.Qt.UserRole)
        if obj_id is None:
            return

        # 名稱修改
        if col == self.COL_NAME:
            new_name = item.text(self.COL_NAME)
            self.prop_mgr.rename(obj_id, new_name)
            return

    # ======================================================================
    # 透明度 slider
    # ======================================================================

    def _on_opacity_slider_changed(self, value: int) -> None:
        slider = self.tree.sender()
        if not isinstance(slider, QtWidgets.QSlider):
            return

        obj_id = slider.property("obj_id")
        if obj_id is None:
            return

        opacity = max(0.0, min(1.0, value / 100.0))
        self.prop_mgr.set_opacity(obj_id, opacity)
        self.obj3d_mgr.update_actor_appearance(obj_id)

    # ======================================================================
    # 設定按鈕：交給 main_window 的 callback
    # ======================================================================

    def _setup_dialog(self,obj_id: int) -> None:
        dlg = PropertyDialog(
            obj_id=obj_id,
            prop_manager=self.prop_mgr,
            obj3d_manager=self.obj3d_mgr
        )
        # 執行對話框
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.refresh_from_manager(obj_id)

    def _on_settings_clicked(self) -> None:
        btn = self.tree.sender()
        if not isinstance(btn, QtWidgets.QPushButton):
            return
        
        obj_id = btn.property("obj_id")
        if obj_id is None:
            return
        
        self._setup_dialog(int(obj_id))

    def _build_centered_settings_button(self, obj_id: int) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self.tree)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        btn = QtWidgets.QPushButton("⚙", container)
        btn.setFixedSize(32, 32)
        btn.setStyleSheet(
            "font-size: 20px; padding: 0px; font-family: 'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji';"
        )
        btn.setProperty("obj_id", obj_id)
        btn.clicked.connect(self._on_settings_clicked)
        layout.addWidget(btn)
        return container

    # ======================================================================
    # 工具函式：找 item / 移除 / 重新整理
    # ======================================================================

    def find_item(self, obj_id: int) -> Optional[QtWidgets.QTreeWidgetItem]:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(self.COL_NAME, QtCore.Qt.UserRole) == obj_id:
                return item
        return None

    def remove_result(self, obj_id: int) -> None:
        """
        從列表中移除某個 result 物件（當 SceneObject 被 delete_object 後）。
        """
        item = self.find_item(obj_id)
        if item is None:
            return
        idx = self.tree.indexOfTopLevelItem(item)
        self.tree.takeTopLevelItem(idx)

    def clear(self) -> None:
        """清空整個切割結果列表（不會刪 SceneObject，只清 UI）。"""
        self.tree.clear()

    def refresh_from_manager(self, obj_id: int) -> None:
        """
        依照 ObjectPropertyManager 的資料重新更新這一列。
        """
        item = self.find_item(obj_id)
        if item is None:
            return

        so = self.prop_mgr.get_object(obj_id)

        # 名稱
        item.setText(self.COL_NAME, so.name)

        # 顯示 / 選取
        visible_cb = self._get_checkbox(item, self.COL_VISIBLE)
        if isinstance(visible_cb, QtWidgets.QCheckBox):
            visible_cb.blockSignals(True)
            visible_cb.setChecked(bool(so.visible))
            visible_cb.blockSignals(False)

        selected_cb = self._get_checkbox(item, self.COL_SELECTED)
        if isinstance(selected_cb, QtWidgets.QCheckBox):
            selected_cb.blockSignals(True)
            selected_cb.setChecked(bool(so.selected))
            selected_cb.blockSignals(False)

        # 透明度 slider
        slider = self._get_slider(item, self.COL_OPACITY)
        if isinstance(slider, QtWidgets.QSlider):
            slider.blockSignals(True)
            slider.setValue(int(so.opacity * 100))
            slider.blockSignals(False)

    def _build_centered_checkbox(self, checked: bool, obj_id: int, role: str) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self.tree)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        cb = QtWidgets.QCheckBox(container)
        cb.setChecked(bool(checked))
        cb.setProperty("obj_id", obj_id)
        cb.setProperty("role", role)
        cb.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(cb)
        return container

    def _build_centered_slider(self, value: int, obj_id: int) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self.tree)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, container)
        slider.setRange(0, 100)
        slider.setSingleStep(1)
        slider.setPageStep(10)
        slider.setValue(int(value))
        slider.setFixedHeight(18)
        slider.setProperty("obj_id", obj_id)
        slider.valueChanged.connect(self._on_opacity_slider_changed)
        layout.addWidget(slider)
        return container

    def _get_checkbox(self, item: QtWidgets.QTreeWidgetItem, col: int) -> Optional[QtWidgets.QCheckBox]:
        widget = self.tree.itemWidget(item, col)
        if widget is None:
            return None
        return widget.findChild(QtWidgets.QCheckBox)

    def _get_slider(self, item: QtWidgets.QTreeWidgetItem, col: int) -> Optional[QtWidgets.QSlider]:
        widget = self.tree.itemWidget(item, col)
        if widget is None:
            return None
        return widget.findChild(QtWidgets.QSlider)

    def _on_checkbox_changed(self, state: int) -> None:
        cb = self.tree.sender()
        if not isinstance(cb, QtWidgets.QCheckBox):
            return
        obj_id = cb.property("obj_id")
        role = cb.property("role")
        if obj_id is None or role not in ("visible", "selected"):
            return

        checked = (state == QtCore.Qt.Checked)
        if role == "visible":
            self.prop_mgr.set_visible(obj_id, checked)
        else:
            self.prop_mgr.set_selected(obj_id, checked)
        self.obj3d_mgr.update_actor_appearance(obj_id)

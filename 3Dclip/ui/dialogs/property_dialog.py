# property_dialog.py (先做成：只要能開啟，不依賴 main_window)

from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QGroupBox, QColorDialog
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager

class PropertyDialog(QDialog):
    def __init__(self, obj_id: int, prop_manager: ObjectPropertyManager, obj3d_manager: Object3DManager):
        super().__init__()
        self.obj_id = obj_id
        self.prop_manager = prop_manager
        self.obj3d_manager = obj3d_manager
        # 直接從 prop_manager 拿到該物件的資料
        self.scene_object = self.prop_manager.get_object(obj_id)
        self.setWindowTitle(f"屬性設定 - {getattr(self.scene_object, 'name', '')}")
        self.setModal(True)
        self.resize(350, 300)
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.WindowCloseButtonHint)
        self.setup_ui()


    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        top_group = QGroupBox("基本設定")
        top_layout = QHBoxLayout(top_group)
        top_layout.setSpacing(15)  # 增加間距
        top_layout.setContentsMargins(10, 10, 10, 10)

        # 鎖定
        lock_layout = QVBoxLayout()
        lock_layout.setSpacing(5)
        lock_layout.setAlignment(QtCore.Qt.AlignCenter)
        lock_layout.addWidget(QLabel("鎖定"))
        self.locked_cb = QCheckBox()
        self.locked_cb.setChecked(bool(getattr(self.scene_object, "locked", False)))
        self.locked_cb.stateChanged.connect(self.on_locked_changed)
        lock_layout.addWidget(self.locked_cb)
        top_layout.addLayout(lock_layout)
    
        # 分隔線
        line1 = QtWidgets.QFrame()
        line1.setFrameShape(QtWidgets.QFrame.VLine)
        line1.setFrameShadow(QtWidgets.QFrame.Sunken)
        top_layout.addWidget(line1)

        # 顏色
        color_layout = QVBoxLayout()
        color_layout.setSpacing(5)
        color_layout.setAlignment(QtCore.Qt.AlignCenter)
        color_layout.addWidget(QLabel("顏色"))
        self.color_btn = QtWidgets.QPushButton()
        self.color_btn.setFixedSize(50, 30)
        qcol = QtGui.QColor(int(self.scene_object.color[0]*255), 
                           int(self.scene_object.color[1]*255), 
                           int(self.scene_object.color[2]*255))
        self.color_btn.setStyleSheet(f"background-color:{qcol.name()}; border:1px solid #333; border-radius: 3px;")
        self.color_btn.clicked.connect(self.on_color_picked)
        color_layout.addWidget(self.color_btn)
        top_layout.addLayout(color_layout)

        # 分隔線
        line2 = QtWidgets.QFrame()
        line2.setFrameShape(QtWidgets.QFrame.VLine)
        line2.setFrameShadow(QtWidgets.QFrame.Sunken)
        top_layout.addWidget(line2)

        # 群組
        group_layout = QVBoxLayout()
        group_layout.setSpacing(5)
        group_layout.setAlignment(QtCore.Qt.AlignCenter)
        group_layout.addWidget(QLabel("群組"))
        self.group_edit = QtWidgets.QLineEdit(str(getattr(self.scene_object, "group", "default")))
        self.group_edit.setFixedWidth(120)
        self.group_edit.setAlignment(QtCore.Qt.AlignCenter)
        group_layout.addWidget(self.group_edit)
        top_layout.addLayout(group_layout)

        layout.addWidget(top_group)

        # 位置設定
        current_pos = self.scene_object.transform.GetPosition()
        position_group = QGroupBox("位置")
        position_layout = QHBoxLayout(position_group)
        position_layout.setSpacing(10)  # 增加間距
        position_layout.setContentsMargins(10, 10, 10, 10)
        
        for i, axis in enumerate(['X', 'Y', 'Z']):
            axis_layout = QVBoxLayout()
            axis_layout.setSpacing(5)
            axis_layout.setAlignment(QtCore.Qt.AlignCenter)
            axis_layout.addWidget(QLabel(axis))
            spinbox = QtWidgets.QDoubleSpinBox()
            spinbox.setRange(-500, 500)
            spinbox.setDecimals(1)
            spinbox.setValue(current_pos[i]) 
            spinbox.setFixedWidth(70)
            spinbox.valueChanged.connect(self.update_transform_from_ui)
            setattr(self, f'pos_{axis.lower()}', spinbox)
            axis_layout.addWidget(spinbox)
            position_layout.addLayout(axis_layout)
        
        layout.addWidget(position_group)

        # 旋轉設定
        current_rot = self.scene_object.transform.GetOrientation()
        rotation_group = QGroupBox("旋轉")
        rotation_layout = QHBoxLayout(rotation_group)
        rotation_layout.setSpacing(10)  # 增加間距
        rotation_layout.setContentsMargins(10, 10, 10, 10)
        
        for i, axis in enumerate(['X', 'Y', 'Z']):
            axis_layout = QVBoxLayout()
            axis_layout.setSpacing(5)
            axis_layout.setAlignment(QtCore.Qt.AlignCenter)
            axis_layout.addWidget(QLabel(axis))
            spinbox = QtWidgets.QDoubleSpinBox()
            spinbox.setRange(-180, 180)
            spinbox.setDecimals(0)
            spinbox.setSuffix("°")
            spinbox.setValue(current_rot[i])
            spinbox.setFixedWidth(70)
            spinbox.valueChanged.connect(self.update_transform_from_ui)
            setattr(self, f'rot_{axis.lower()}', spinbox)
            axis_layout.addWidget(spinbox)
            rotation_layout.addLayout(axis_layout)
        
        layout.addWidget(rotation_group)

        # 按鈕
        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.apply_btn = QtWidgets.QPushButton("套用")
        self.apply_btn.setFixedWidth(80)
        self.close_btn = QtWidgets.QPushButton("關閉")
        self.close_btn.setFixedWidth(80)
        self.apply_btn.clicked.connect(self.accept)
        self.close_btn.clicked.connect(self.reject)

        btns.addStretch()
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.close_btn)
        btns.addStretch()
        layout.addLayout(btns)

    def on_locked_changed(self, state):
        # 使用 prop_manager 的方法來更新狀態，符合架構規範
        is_locked = (state == QtCore.Qt.Checked)
        self.prop_manager.set_locked(self.obj_id, is_locked)

    def on_color_picked(self):
        curr = self.scene_object.color
        old_color = QtGui.QColor(int(curr[0]*255), int(curr[1]*255), int(curr[2]*255))
        new_color = QColorDialog.getColor(old_color, self, "選擇顏色")
        if new_color.isValid():
            rgb = (new_color.red()/255.0, new_color.green()/255.0, new_color.blue()/255.0)
            self.prop_manager.set_color(self.obj_id, rgb)
            self.color_btn.setStyleSheet(f"background-color:{new_color.name()}; border:1px solid #333;")
            self.obj3d_manager.update_actor_appearance(self.obj_id)
            

    def update_transform_from_ui(self):
        """直接操作資料層物件，並通知渲染層更新"""
        # 1. 取得 UI 數值
        tx, ty, tz = self.pos_x.value(), self.pos_y.value(), self.pos_z.value()
        rx, ry, rz = self.rot_x.value(), self.rot_y.value(), self.rot_z.value()

        # 2. 修改資料層中的 transform 物件
        t = self.scene_object.transform
        t.Identity()
        t.Translate(tx, ty, tz)
        t.RotateZ(rz)
        t.RotateX(rx)
        t.RotateY(ry)

        # 3. 呼叫渲染管理器的 API 同步 3D 畫面
        #self.obj3d_manager.update_actor_transform(self.obj_id)
        
        # 4. 觸發 RenderWindow 刷新 (如果 obj3D_manager 沒有內建 render 呼叫)
        rw = self.obj3d_manager._renderer.GetRenderWindow()
        if rw:
            rw.Render()
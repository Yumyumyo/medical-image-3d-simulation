# export_dialog.py
import os
import vtk
import numpy as np
import nibabel as nib
from datetime import datetime
from PyQt5 import QtWidgets, QtCore, QtGui
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager


class ExportDialog(QtWidgets.QDialog):
    """匯出對話框 - 支援 OBJ/NIfTI，可預覽和修改檔案名稱"""
    
    def __init__(self, parent ,prop_manager: ObjectPropertyManager, obj3d_manager: Object3DManager):
        super().__init__(parent)
        self._prop_manager = prop_manager
        self._obj3d_manager = obj3d_manager
        self.parent = parent
        self.file_items = []  # 儲存檔案項目
        
        self.setup_ui()
        self.update_file_list()
        
    def setup_ui(self):
        self.setWindowTitle("匯出設定")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(10)
        
        # === 第一部分：匯出類型 ===
        type_group = QtWidgets.QGroupBox("匯出類型")
        type_layout = QtWidgets.QVBoxLayout()
        
        # 類型選項
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems([
            "所有物件",
            "選取的物件", 
            "切割結果",
        ])
        self.type_combo.currentIndexChanged.connect(self.update_file_list)
        
        type_layout.addWidget(self.type_combo)
        type_group.setLayout(type_layout)
        main_layout.addWidget(type_group)
        
        # === 第二部分：格式選擇 ===
        format_group = QtWidgets.QGroupBox("匯出格式")
        format_layout = QtWidgets.QHBoxLayout()
        
        self.format_objs = QtWidgets.QCheckBox("OBJ")
        self.format_objs.setChecked(True)
        self.format_objs.stateChanged.connect(self.update_file_list)

        self.format_nii = QtWidgets.QCheckBox("NIfTI (.nii.gz)")
        self.format_nii.setChecked(True)
        self.format_nii.setVisible(False)
        self.format_nii.stateChanged.connect(self.update_file_list)
        
        format_layout.addWidget(self.format_objs)
        format_layout.addStretch()
        
        format_group.setLayout(format_layout)
        main_layout.addWidget(format_group)
        
        # === 第三部分：檔案列表 ===
        list_group = QtWidgets.QGroupBox("匯出檔案列表")
        list_layout = QtWidgets.QVBoxLayout()
        
        # 標題行
        header_widget = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header_widget)
        header_layout.setContentsMargins(5, 0, 5, 0)
        
        name_label = QtWidgets.QLabel("檔案名稱")
        name_label.setMinimumWidth(200)
        format_label = QtWidgets.QLabel("格式")
        format_label.setFixedWidth(80)
        
        header_layout.addWidget(name_label)
        header_layout.addWidget(format_label)
        header_layout.addStretch()
        
        list_layout.addWidget(header_widget)
        
        # 檔案列表（使用 Scroll Area）
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(200)
        
        self.list_container = QtWidgets.QWidget()
        self.list_layout = QtWidgets.QVBoxLayout(self.list_container)
        self.list_layout.setSpacing(5)
        self.list_layout.setContentsMargins(5, 5, 5, 5)
        
        scroll_area.setWidget(self.list_container)
        list_layout.addWidget(scroll_area)
        
        # 全選/取消全選按鈕
        select_buttons = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("全選")
        select_all_btn.clicked.connect(self.select_all_files)
        deselect_all_btn = QtWidgets.QPushButton("取消全選")
        deselect_all_btn.clicked.connect(self.deselect_all_files)
        
        select_buttons.addWidget(select_all_btn)
        select_buttons.addWidget(deselect_all_btn)
        select_buttons.addStretch()
        
        list_layout.addLayout(select_buttons)
        list_group.setLayout(list_layout)
        main_layout.addWidget(list_group)
        
        # === 第四部分：輸出路徑 ===
        path_group = QtWidgets.QGroupBox("輸出路徑")
        path_layout = QtWidgets.QVBoxLayout()
        
        # 路徑選擇
        path_row = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        
        # 預設路徑
        default_path = os.path.join(
            os.path.expanduser("~"), 
            "Desktop", 
            f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self.path_edit.setText(default_path)
        self.path_edit.textChanged.connect(self.update_file_previews)
        
        path_button = QtWidgets.QPushButton("瀏覽...")
        path_button.clicked.connect(self.browse_path)
        
        path_row.addWidget(self.path_edit)
        path_row.addWidget(path_button)
        path_layout.addLayout(path_row)
        
        path_group.setLayout(path_layout)
        main_layout.addWidget(path_group)
        
        # === 第五部分：進度條 ===
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)
        
        # === 第六部分：按鈕 ===
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept_export)
        button_box.rejected.connect(self.reject)
        
        main_layout.addWidget(button_box)
        self.setLayout(main_layout)
    
    def update_file_list(self):
        """更新檔案列表"""
        # 清除現有項目
        for item in self.file_items:
            item['widget'].deleteLater()
        self.file_items.clear()
        
        # 取得選取的格式
        formats = []
        if self.format_objs.isChecked():
            formats.append('obj')
        
        if not formats:
            # 顯示提示
            label = QtWidgets.QLabel("請選擇至少一種匯出格式")
            label.setAlignment(QtCore.Qt.AlignCenter)
            self.list_layout.addWidget(label)
            return
        
        # 根據匯出類型取得物件列表
        objects = self.get_objects_to_export()
        if not objects:
            # 顯示提示
            label = QtWidgets.QLabel("沒有可匯出的物件")
            label.setAlignment(QtCore.Qt.AlignCenter)
            self.list_layout.addWidget(label)
            return
        
        # 為每個物件和格式建立項目
        for obj_info in objects:
            base_name = obj_info['name']
            
            for fmt in formats:
                # 建立項目 widget
                item_widget = QtWidgets.QWidget()
                item_layout = QtWidgets.QHBoxLayout(item_widget)
                item_layout.setContentsMargins(5, 2, 5, 2)
                
                # 勾選框
                checkbox = QtWidgets.QCheckBox()
                checkbox.setChecked(True)
                checkbox.setFixedWidth(20)
                
                # 檔案名稱編輯框
                name_edit = QtWidgets.QLineEdit()
                safe_name = self.sanitize_filename(base_name)
                name_edit.setText(safe_name)
                name_edit.setMinimumWidth(200)
                
                # 副檔名標籤
                ext_label = QtWidgets.QLabel(f".{fmt}")
                ext_label.setFixedWidth(80)
                
                # 檔案預覽路徑
                preview_label = QtWidgets.QLabel("")
                preview_label.setStyleSheet("color: #666;")
                preview_label.setWordWrap(True)
                
                # 添加到布局
                item_layout.addWidget(checkbox)
                item_layout.addWidget(name_edit)
                item_layout.addWidget(ext_label)
                item_layout.addWidget(preview_label)
                item_layout.addStretch()
                
                # 儲存項目資訊
                item_info = {
                    'widget': item_widget,
                    'checkbox': checkbox,
                    'name_edit': name_edit,
                    'format': fmt,
                    'ext_label': ext_label,
                    'preview_label': preview_label,
                    'object_info': obj_info
                }
                self.file_items.append(item_info)
                
                # 添加到列表
                self.list_layout.addWidget(item_widget)
                
                # 連接信號
                name_edit.textChanged.connect(
                    lambda text, item=item_info: self.update_file_preview(item)
                )
        
        # 添加彈性空間
        self.list_layout.addStretch()
        
        # 更新檔案預覽
        self.update_file_previews()
    
    def _is_identity_transform(self, t: vtk.vtkTransform) -> bool:
        if t is None:
            return True
        m = t.GetMatrix()
        for r in range(4):
            for c in range(4):
                v = m.GetElement(r, c)
                if r == c:
                    if abs(v - 1.0) > 1e-12:
                        return False
                else:
                    if abs(v) > 1e-12:
                        return False
        return True


    def _get_export_polydata(self, so) -> vtk.vtkPolyData:
        """
        匯出用 polydata：優先把「畫面上的最終 actor 變換」烘焙進去，
        讓 OBJ 匯入後位置不會跑掉。
        """
        poly = getattr(so, "polydata", None)
        if poly is None or not isinstance(poly, vtk.vtkPolyData) or poly.GetNumberOfPoints() == 0:
            return vtk.vtkPolyData()

        # 1) 最準：用 actor 的總矩陣（包含 userTransform / position / orientation / scale）
        if getattr(self, "_obj3d_manager", None) is not None:
            try:
                actor = self._obj3d_manager.get_actor(so.id)
            except Exception:
                actor = None

            if actor is not None:
                m = actor.GetMatrix()
                t = vtk.vtkTransform()
                t.SetMatrix(m)

                tf = vtk.vtkTransformPolyDataFilter()
                tf.SetInputData(poly)
                tf.SetTransform(t)
                tf.Update()

                out_poly = vtk.vtkPolyData()
                out_poly.ShallowCopy(tf.GetOutput())
                return out_poly

        # 2) fallback：只用資料層 transform
        t = getattr(so, "transform", None)
        if t is None or self._is_identity_transform(t):
            return poly

        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetInputData(poly)
        tf.SetTransform(t)
        tf.Update()

        out_poly = vtk.vtkPolyData()
        out_poly.ShallowCopy(tf.GetOutput())
        return out_poly



    def get_objects_to_export(self):
        """取得要匯出的物件列表（新架構：改用 ObjectPropertyManager API）"""
        objects = []
        if not hasattr(self, "_prop_manager") or self._prop_manager is None:
            return objects

        export_type = self.type_combo.currentText()

        # 1) 取得 SceneObject 清單
        if export_type == "所有物件":
            src = self._prop_manager.get_all_objects()
        elif export_type == "選取的物件":
            ids = self._prop_manager.get_selected_objects()
            src = [self._prop_manager.get_object(i) for i in ids]
        elif export_type == "切割結果":
            src = self._prop_manager.get_result_objects()
        else:
            src = []

        # 2) 轉成舊 UI 期待的 dict 結構
        for i, so in enumerate(src):
            obj_name = getattr(so, "name", f"object_{i}")
            poly = self._get_export_polydata(so)

            objects.append({
                "actor": None,   # 保留 key，避免其他流程期待它存在
                "obj_id": so.id,
                "name": obj_name,
                "poly": poly
            })

        return objects

    
    def update_file_previews(self):
        """更新所有檔案的預覽路徑"""
        base_path = self.path_edit.text().strip()
        if not base_path:
            return
        
        for item in self.file_items:
            self.update_file_preview(item)
    
    def update_file_preview(self, item):
        """更新單一檔案的預覽路徑"""
        base_path = self.path_edit.text().strip()
        if not base_path:
            return
        
        file_name = item['name_edit'].text().strip()
        if not file_name:
            return
        
        # 清理檔案名稱
        safe_name = self.sanitize_filename(file_name)
        
        # 建立完整路徑
        full_path = os.path.join(base_path, f"{safe_name}.{item['format']}")
        
        # 顯示路徑（縮短版本）
        display_path = full_path
        if len(display_path) > 60:
            display_path = "..." + display_path[-57:]
        
        item['preview_label'].setText(display_path)
    
    def select_all_files(self):
        """全選所有檔案"""
        for item in self.file_items:
            item['checkbox'].setChecked(True)
    
    def deselect_all_files(self):
        """取消全選所有檔案"""
        for item in self.file_items:
            item['checkbox'].setChecked(False)
    
    def browse_path(self):
        """選擇輸出路徑"""
        export_type = self.type_combo.currentText()
        
        # 選擇資料夾
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "選擇輸出資料夾", self.path_edit.text()
            )
        
        if path:
            self.path_edit.setText(path)
            self.update_file_previews()
    
    def get_export_items(self):
        """取得要匯出的項目列表"""
        items = []
        base_path = self.path_edit.text().strip()
        
        if not base_path:
            return items
        
        for item in self.file_items:
            if item['checkbox'].isChecked():
                file_name = item['name_edit'].text().strip()
                if file_name:
                    safe_name = self.sanitize_filename(file_name)
                    
                    full_path = os.path.join(base_path, f"{safe_name}.{item['format']}")
                    
                    items.append({
                        'actor': item['object_info']['actor'],
                        'poly': item['object_info']['poly'],
                        'name': safe_name,
                        'format': item['format'],
                        'path': full_path
                    })
        
        return items
    
    def ask_overwrite(self, file_path):
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setWindowTitle("檔案已存在")
        msg.setText(f"檔案已存在：\n{file_path}")
        msg.setInformativeText("是否要覆蓋？")

        overwrite_btn = msg.addButton("覆蓋", QtWidgets.QMessageBox.AcceptRole)
        overwrite_all_btn = msg.addButton("全部覆蓋", QtWidgets.QMessageBox.YesRole)
        skip_btn = msg.addButton("略過", QtWidgets.QMessageBox.RejectRole)
        cancel_btn = msg.addButton("取消匯出", QtWidgets.QMessageBox.DestructiveRole)

        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == overwrite_btn:
            return "overwrite"
        elif clicked == overwrite_all_btn:
            return "overwrite_all"
        elif clicked == skip_btn:
            return "skip"
        else:
            return "cancel"

    
    def accept_export(self):
        """執行匯出"""
        export_type = self.type_combo.currentText()
        output_path = self.path_edit.text().strip()
        
       
        if not output_path:
            QtWidgets.QMessageBox.warning(self, "警告", "請選擇輸出路徑")
            return
        
        # 檢查是否有選取的項目
        export_items = self.get_export_items()
        if not export_items:
            QtWidgets.QMessageBox.warning(self, "警告", "請選擇至少一個要匯出的檔案")
            return
        
        try:
            self.progress_bar.setVisible(True)
            self.progress_bar.setMaximum(len(export_items))
            self.progress_bar.setValue(0)
            
            overwrite_all = False
            success_count = 0
            
            for i, item in enumerate(export_items):
                overwrite = overwrite_all
                try:
                    # 更新進度
                    self.progress_bar.setValue(i)
                    QtWidgets.QApplication.processEvents()
                    
                    print(f"匯出 {i+1}/{len(export_items)}: {item['name']}.{item['format']}")
                    
                    # 檢查是否已有同名檔案
                    if os.path.exists(item['path']) and not overwrite_all:
                        choice = self.ask_overwrite(item['path'])

                        if choice == "cancel":
                            self.progress_bar.setVisible(False)
                            return
                        elif choice == "skip":
                            continue
                        elif choice == "overwrite":
                            overwrite = True
                        elif choice == "overwrite_all":
                            overwrite = True
                            overwrite_all = True

                    # 根據格式選擇匯出方法
                    if item['format'] == 'obj':
                        success = self.export_vtk_to_obj_reliable(
                            item['poly'],
                            item['path'],
                            overwrite=overwrite
                        )
                    else:
                        success = False

                    
                    if success:
                        success_count += 1
                        
                except Exception as e:
                    print(f"匯出失敗 {item['name']}: {e}")
            
            self.progress_bar.setValue(len(export_items))
          
            # 顯示結果
            if success_count > 0:
                QtWidgets.QMessageBox.information(
                    self, "成功", 
                    f"成功匯出 {success_count}/{len(export_items)} 個檔案"
                )
                self.accept()
            else:
                QtWidgets.QMessageBox.warning(
                    self, "失敗", 
                    "沒有成功匯出任何檔案"
                )
            
        except Exception as e:
            self.progress_bar.setVisible(False)
            QtWidgets.QMessageBox.critical(
                self, "錯誤", f"匯出失敗: {str(e)}"
            )
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """清理檔案名稱"""
        import re
        # 移除不合法字元
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # 移除多餘空格
        filename = re.sub(r'\s+', '_', filename.strip())
        return filename
    
    @staticmethod
    def export_vtk_to_obj(polydata: vtk.vtkPolyData, file_path: str, overwrite: bool = False) -> bool:
        """匯出 OBJ 檔（原 export_manager 版本搬進來）"""
        if os.path.exists(file_path) and not overwrite:
            return False

        # 舊版有印 actor user transform（但這裡 polydata 不會有 user transform，保留相容）
        actor_transform = getattr(polydata, 'GetUserTransform', lambda: None)()
        if actor_transform:
            print(f"[DEBUG] actor user transform 矩陣:\n{actor_transform.GetMatrix()}")

        try:
            writer = vtk.vtkOBJWriter()
            writer.SetFileName(file_path)
            writer.SetInputData(polydata)
            writer.Update()
            writer.Write()
            return True
        except Exception as e:
            print(f"OBJ 匯出失敗: {e}")
            return False
        
    @staticmethod   
    def export_vtk_to_nifti(polydata: vtk.vtkPolyData, file_path: str, overwrite: bool = False) -> bool:
        """匯出 NIfTI (.nii.gz)（原 export_manager 版本搬進來：示範用空 volume）"""
        if os.path.exists(file_path) and not overwrite:
            return False

        try:
            bounds = polydata.GetBounds()
            dims = [
                int(bounds[1] - bounds[0] + 1),
                int(bounds[3] - bounds[2] + 1),
                int(bounds[5] - bounds[4] + 1),
            ]

            volume = np.zeros(dims, dtype=np.float32)
            # TODO: 可將 polydata 實際 rasterize 成 volume（舊版也未實作）

            affine = np.eye(4)
            nifti_img = nib.Nifti1Image(volume, affine)
            nib.save(nifti_img, file_path)
            return True
        except Exception as e:
            print(f"NIfTI 匯出失敗: {e}")
            return False
    @staticmethod
    def export_vtk_to_obj_reliable(polydata: vtk.vtkPolyData, file_path: str, overwrite: bool = False) -> bool:
        """較穩定的 OBJ 匯出流程。"""
        if os.path.exists(file_path) and not overwrite:
            return False

        print(f"\n[匯出] {os.path.basename(file_path)}")

        if polydata is None:
            print("錯誤: polydata 為 None")
            return False

        num_points = polydata.GetNumberOfPoints()
        num_polys = polydata.GetNumberOfPolys()
        print(f"點數: {num_points:,}, 面數: {num_polys:,}")

        if num_points == 0:
            print("錯誤: 沒有點數據")
            return False

        return ExportDialog._write_obj_manually_reliable(polydata, file_path)

    @staticmethod
    def _write_obj_manually_reliable(polydata: vtk.vtkPolyData, file_path: str) -> bool:
        """手動寫入 OBJ 檔案。"""
        try:
            print("手動寫入 OBJ 檔案...")

            points = polydata.GetPoints()
            if points is None:
                return False

            num_points = points.GetNumberOfPoints()
            num_polys = polydata.GetNumberOfPolys()

            with open(file_path, "w", encoding="utf-8") as f:
                f.write("# Generated by 3D Cutting Tool\n")
                f.write(f"# Points: {num_points:,}\n")
                f.write(f"# Faces: {num_polys:,}\n\n")

                print(f"寫入 {num_points:,} 個頂點...")
                batch_size = 50000
                for batch_start in range(0, num_points, batch_size):
                    batch_end = min(batch_start + batch_size, num_points)
                    batch_lines = []
                    for i in range(batch_start, batch_end):
                        point = points.GetPoint(i)
                        batch_lines.append(f"v {point[0]:.6f} {point[1]:.6f} {point[2]:.6f}")
                    f.write("\n".join(batch_lines) + "\n")

                f.write("\n")

                if num_polys > 0:
                    print(f"寫入 {num_polys:,} 個面...")
                    polys = polydata.GetPolys()
                    polys.InitTraversal()
                    cell = vtk.vtkIdList()

                    batch_lines = []
                    face_count = 0
                    while polys.GetNextCell(cell):
                        num_ids = cell.GetNumberOfIds()
                        if num_ids >= 3:
                            indices = [str(cell.GetId(j) + 1) for j in range(num_ids)]
                            batch_lines.append(f"f {' '.join(indices)}")
                            face_count += 1

                        if len(batch_lines) >= 10000:
                            f.write("\n".join(batch_lines) + "\n")
                            batch_lines = []

                    if batch_lines:
                        f.write("\n".join(batch_lines) + "\n")

                    print(f"完成: {face_count:,} 個面")
                else:
                    print("警告: 沒有面數據")

            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                print(f"匯出成功: {size:,} bytes")
                return True

            return False
        except Exception as e:
            print(f"手動寫入失敗: {e}")
            import traceback
            traceback.print_exc()
            return False

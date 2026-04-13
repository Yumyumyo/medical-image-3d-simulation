# new_cut/utils/file_loader.py
import os
import vtk
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QFileDialog

class FileLoader(QObject):
    """
    專門負責檔案載入與數據轉換的類別。
    不直接操作 UI 或 Actor，而是透過信號發送 vtkPolyData。
    """
    # 定義信號：(物件名稱, vtkPolyData)
    modelLoaded = pyqtSignal(str, vtk.vtkPolyData)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent

    def batch_load_from_path(self, folder_path):
        """
        啟動時批次載入指定資料夾內的所有模型與影像。
        """
        if not os.path.exists(folder_path):
            print(f"[FileLoader] 路徑不存在: {folder_path}")
            return

        supported_extensions = (".obj", ".nii", ".nii.gz")
        
        for root, _, files in os.walk(folder_path):
            for f in files:
                if f.lower().endswith(supported_extensions):
                    full_path = os.path.join(root, f)
                    self._process_and_emit(full_path)

    def import_model(self):
        """
        手動匯入功能，供 UI 按鈕呼叫。
        """
        path, _ = QFileDialog.getOpenFileName(
            self.parent_window, 
            "選擇模型或影像檔案", 
            "", 
            "Supported Files (*.obj *.nii *.nii.gz);;Mesh Files (*.obj);;NIfTI Files (*.nii *.nii.gz)"
        )
        if path:
            self._process_and_emit(path)

    def _process_and_emit(self, path):
        """
        內部處理邏輯：判斷格式並轉換為 PolyData。
        """
        try:
            name = os.path.basename(path)
            poly_data = None

            if path.lower().endswith(".obj"):
                poly_data = self._read_obj(path)
            elif path.lower().endswith((".nii", ".nii.gz")):
                poly_data = self._read_nifti_to_mesh(path)

            if poly_data:
                # 發送信號通知 MainWindow
                self.modelLoaded.emit(name, poly_data)
                print(f"[FileLoader] 成功載入: {name}")

        except Exception as e:
            print(f"[FileLoader] 載入失敗 {path}: {e}")

    # file_loader.py 內部處理
    def _read_obj(self, path):
        reader = vtk.vtkOBJReader()
        reader.SetFileName(path)
        reader.Update()
        '''
        poly = reader.GetOutput()

        transform = vtk.vtkTransform()
        transform.Translate(96, 107.5, 138.8) 
        
        transformFilter = vtk.vtkTransformPolyDataFilter()
        transformFilter.SetInputData(poly)
        transformFilter.SetTransform(transform)
        transformFilter.Update()
        
        return transformFilter.GetOutput()'''
        return reader.GetOutput()

    def _read_nifti_to_mesh(self, path):
        """
        讀取 NIfTI 並透過變換矩陣對齊世界座標，確保與 OBJ 同一空間。
        """
        reader = vtk.vtkNIFTIImageReader()
        reader.SetFileName(path)
        reader.Update()
        
        image_data = reader.GetOutput()
        
        # 取得 SForm 矩陣（NIfTI 定義的實體世界座標矩陣）
        s_matrix = reader.GetSFormMatrix()
        q_matrix = reader.GetQFormMatrix()
        matrix = s_matrix if s_matrix else q_matrix

        # 1. 提取等值面
        scalar_range = image_data.GetScalarRange()
        mc = vtk.vtkMarchingCubes()
        mc.SetInputData(image_data)
        mc.ComputeNormalsOn()
        mc.SetValue(0, (scalar_range[0] + scalar_range[1]) / 2)
        mc.Update()
        
        # 2. 強制套用變換矩陣至 Mesh 點位
        if matrix:
            # 建立一個 vtkMatrix4x4 的副本，避免直接引用 reader 的內部矩陣
            new_matrix = vtk.vtkMatrix4x4()
            new_matrix.DeepCopy(matrix)
            
            transform = vtk.vtkTransform()
            transform.SetMatrix(new_matrix)
            
            # 使用 Filter 直接變換 PolyData 的點座標
            tf = vtk.vtkTransformPolyDataFilter()
            tf.SetInputConnection(mc.GetOutputPort())
            tf.SetTransform(transform)
            tf.Update()
            return tf.GetOutput()
        
        return mc.GetOutput()
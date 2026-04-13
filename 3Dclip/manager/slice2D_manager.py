# new_cut/manager/slice2D_manager.py
from __future__ import annotations
import vtk
import os
from PyQt5 import QtWidgets, QtCore
from manager.slice3D_manager import Slice3DManager
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

class _SliceViewportBlocker(QtCore.QObject):
    def eventFilter(self, obj, event):
        blocked_events = {
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseButtonRelease,
            QtCore.QEvent.MouseButtonDblClick,
            QtCore.QEvent.MouseMove,
            QtCore.QEvent.Wheel,
            QtCore.QEvent.ContextMenu,
        }
        if event.type() in blocked_events:
            return True
        return super().eventFilter(obj, event)

class Slice2DManager:
    def __init__(self, 
                 widget_sag: QtWidgets.QWidget, 
                 widget_cor: QtWidgets.QWidget, 
                 widget_axi: QtWidgets.QWidget,
                 slider_sagittal: QtWidgets.QSlider,
                 slider_coronal: QtWidgets.QSlider,
                 slider_axial: QtWidgets.QSlider,
                 nii_file: str = "",
                 slice3D_manager= Slice3DManager) -> None:
        
        # 1. 封裝視窗與滑桿引用
        self.containers = {'sagittal': widget_sag, 'coronal': widget_cor, 'axial': widget_axi}
        self.sliders = {'sagittal': slider_sagittal, 'coronal': slider_coronal, 'axial': slider_axial}
        self.slice3D_manager = slice3D_manager
        
        self.viewers: dict[str, vtk.vtkImageViewer2] = {}
        self.vtk_widgets: dict[str, QVTKRenderWindowInteractor] = {}
        self.image_data: vtk.vtkImageData | None = None
        self._world_to_ijk_matrix: vtk.vtkMatrix4x4 | None = None
        self._locked_styles: dict[str, vtk.vtkInteractorStyleUser] = {}
        self._viewport_blockers: dict[str, _SliceViewportBlocker] = {}
        self._slider_connected: set[str] = set()
        self._tube_line_sources: dict[str, vtk.vtkLineSource] = {}
        self._tube_filters: dict[str, vtk.vtkTubeFilter] = {}
        self._tube_line_actors: dict[str, vtk.vtkActor] = {}
        self._tube_world_points: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
        self._tube_radius: float = 1.5

        # 2. 初始化 VTK 視窗元件並嵌入介面 (解決 AttributeError)
        for name, container in self.containers.items():
            # 在普通的 QWidget 內部建立 VTK 視窗
            vtk_widget = QVTKRenderWindowInteractor(container)
            
            # 建立佈局確保 VTK 視窗填滿容器
            if container.layout() is None:
                layout = QtWidgets.QVBoxLayout(container)
                layout.setContentsMargins(0, 0, 0, 0)
                container.setLayout(layout)
            container.layout().addWidget(vtk_widget)
            
            self.vtk_widgets[name] = vtk_widget
            vtk_widget.Initialize()

            # Keep the 2D slice view static when the viewport is clicked.
            blocker = _SliceViewportBlocker(vtk_widget)
            vtk_widget.installEventFilter(blocker)
            self._viewport_blockers[name] = blocker

            locked_style = vtk.vtkInteractorStyleUser()
            interactor = vtk_widget.GetRenderWindow().GetInteractor()
            interactor.SetInteractorStyle(locked_style)
            self._locked_styles[name] = locked_style

        # 3. 如果有預設路徑，直接載入
        if nii_file and os.path.exists(nii_file):
            self.load_nifti(nii_file)

    def load_nifti(self, path: str):
        """讀取影像並自動設定 UI 綁定"""
        self.viewers.clear()

        reader = vtk.vtkNIFTIImageReader()
        reader.SetFileName(path)
        reader.Update()
        self.image_data = reader.GetOutput()

        print("[Slice2DManager] slice3D_manager =", self.slice3D_manager)
        print("[Slice2DManager] slice3D_manager type =", type(self.slice3D_manager))

        
        
        s_matrix = reader.GetSFormMatrix()
        q_matrix = reader.GetQFormMatrix()
        matrix = s_matrix if s_matrix else q_matrix
        if matrix is not None:
            self._world_to_ijk_matrix = vtk.vtkMatrix4x4()
            vtk.vtkMatrix4x4.Invert(matrix, self._world_to_ijk_matrix)
        else:
            self._world_to_ijk_matrix = None

        # 同步 3D 場景
        if self.slice3D_manager:
            self.slice3D_manager.set_image_data(self.image_data, matrix)

        # 設定 2D Viewers
        orientations = {
            'sagittal': vtk.vtkImageViewer2.SLICE_ORIENTATION_YZ,
            'coronal': vtk.vtkImageViewer2.SLICE_ORIENTATION_XZ,
            'axial': vtk.vtkImageViewer2.SLICE_ORIENTATION_XY
        }

        extent = self.image_data.GetExtent()
        for name, orient in orientations.items():
            viewer = vtk.vtkImageViewer2()
            viewer.SetRenderWindow(self.vtk_widgets[name].GetRenderWindow())
            viewer.SetInputData(self.image_data)
            viewer.SetSliceOrientation(orient)
            viewer.GetWindowLevel().SetWindow(1500)
            viewer.GetWindowLevel().SetLevel(750)
            viewer.GetRenderer().GetActiveCamera().ParallelProjectionOn()
            
            # 設定滑桿範圍與初值
            axis_idx = 0 if name == 'sagittal' else 1 if name == 'coronal' else 2
            slider = self.sliders[name]
            min_idx = extent[axis_idx * 2]
            max_idx = extent[axis_idx * 2 + 1]
            initial_slice = (min_idx + max_idx) // 2
            slider.setRange(min_idx, max_idx)
            slider.setValue(initial_slice)
            viewer.SetSlice(initial_slice)
            
            # 綁定訊號 (MainWindow 不用寫這段)
            update_func = getattr(self, f"update_{name}")
            if name not in self._slider_connected:
                slider.valueChanged.connect(update_func)
                self._slider_connected.add(name)
            
            viewer.Render()
            viewer.GetRenderer().ResetCamera()
            self._ensure_tube_overlay(name, viewer)
            self.viewers[name] = viewer
            self._refresh_tube_overlay(name)
            viewer.Render()

    def update_sagittal(self, val):
        if 'sagittal' in self.viewers:
            self.viewers['sagittal'].SetSlice(val)
            self._refresh_tube_overlay('sagittal')
            self.viewers['sagittal'].Render()
            if self.slice3D_manager: self.slice3D_manager.update_sagittal_plane(val)

    def update_coronal(self, val):
        if 'coronal' in self.viewers:
            self.viewers['coronal'].SetSlice(val)
            self._refresh_tube_overlay('coronal')
            self.viewers['coronal'].Render()
            if self.slice3D_manager: self.slice3D_manager.update_coronal_plane(val)

    def update_axial(self, val):
        if 'axial' in self.viewers:
            self.viewers['axial'].SetSlice(val)
            self._refresh_tube_overlay('axial')
            self.viewers['axial'].Render()
            if self.slice3D_manager: self.slice3D_manager.update_axial_plane(val)

    def update_endoscope_tube(self, p0_world, p1_world):
        if p0_world is None or p1_world is None:
            self._tube_world_points = None
            for name, actor in self._tube_line_actors.items():
                actor.SetVisibility(False)
                if name in self.viewers:
                    self.viewers[name].Render()
            return

        self._tube_world_points = (
            (float(p0_world[0]), float(p0_world[1]), float(p0_world[2])),
            (float(p1_world[0]), float(p1_world[1]), float(p1_world[2])),
        )
        for name in self.viewers:
            self._refresh_tube_overlay(name)
            self.viewers[name].Render()

    def _ensure_tube_overlay(self, name: str, viewer: vtk.vtkImageViewer2) -> None:
        if name in self._tube_line_actors:
            return

        line_source = vtk.vtkLineSource()
        line_source.SetPoint1(0.0, 0.0, 0.0)
        line_source.SetPoint2(0.0, 0.0, 0.0)

        tube_filter = vtk.vtkTubeFilter()
        tube_filter.SetInputConnection(line_source.GetOutputPort())
        tube_filter.SetRadius(self._tube_radius)
        tube_filter.SetNumberOfSides(20)
        tube_filter.CappingOn()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(tube_filter.GetOutputPort())

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.95, 0.2, 0.2)
        actor.GetProperty().SetLighting(False)
        actor.SetVisibility(False)

        viewer.GetRenderer().AddActor(actor)
        self._tube_line_sources[name] = line_source
        self._tube_filters[name] = tube_filter
        self._tube_line_actors[name] = actor

    def _refresh_tube_overlay(self, name: str) -> None:
        actor = self._tube_line_actors.get(name)
        line_source = self._tube_line_sources.get(name)
        viewer = self.viewers.get(name)
        if actor is None or line_source is None or viewer is None:
            return

        if self._tube_world_points is None:
            actor.SetVisibility(False)
            return

        p0_ijk = self._world_to_ijk_point(self._tube_world_points[0])
        p1_ijk = self._world_to_ijk_point(self._tube_world_points[1])

        line_source.SetPoint1(p0_ijk[0], p0_ijk[1], p0_ijk[2])
        line_source.SetPoint2(p1_ijk[0], p1_ijk[1], p1_ijk[2])
        line_source.Modified()
        actor.SetVisibility(True)

    def _world_to_ijk_point(self, point):
        if self._world_to_ijk_matrix is None:
            return (float(point[0]), float(point[1]), float(point[2]))

        x, y, z = float(point[0]), float(point[1]), float(point[2])
        mat = self._world_to_ijk_matrix
        w = (
            mat.GetElement(3, 0) * x
            + mat.GetElement(3, 1) * y
            + mat.GetElement(3, 2) * z
            + mat.GetElement(3, 3)
        )
        if abs(w) < 1e-8:
            w = 1.0

        return (
            (mat.GetElement(0, 0) * x + mat.GetElement(0, 1) * y + mat.GetElement(0, 2) * z + mat.GetElement(0, 3)) / w,
            (mat.GetElement(1, 0) * x + mat.GetElement(1, 1) * y + mat.GetElement(1, 2) * z + mat.GetElement(1, 3)) / w,
            (mat.GetElement(2, 0) * x + mat.GetElement(2, 1) * y + mat.GetElement(2, 2) * z + mat.GetElement(2, 3)) / w,
        )

# new_cut/manager/slice3D_manager.py
from __future__ import annotations
import time
import vtk

class Slice3DManager:
    def __init__(self, renderer: vtk.vtkRenderer, iren: vtk.vtkRenderWindowInteractor = None) -> None:
        self.renderer = renderer
        self.iren = iren
        self.slice_actors = {}
        self.slice_names = ['sagittal', 'coronal', 'axial']
        self.orientation_widget = None
        self.image_data: vtk.vtkImageData | None = None
        self.image_matrix: vtk.vtkMatrix4x4 | None = None
        self._clip_offset = (0.0, 0.0, 0.0)
        self._clip_center: tuple[float, float, float] | None = None
        self._clip_scale = (1.0, 1.0, 1.0)
        self._clip_axis_flip = {"sagittal": False, "coronal": False, "axial": False}
        self._use_image_matrix_for_clipping = True
        self._clip_axis_use_matrix = {"sagittal": True, "coronal": True, "axial": True}
        self._clip_axis_apply_offset_scale = {"sagittal": True, "coronal": True, "axial": True}
        self._clip_axis_bias = {"sagittal": 0.0, "coronal": 0.0, "axial": 0.0}
        self._last_render_t = 0.0
        self._render_interval = 1.0 / 30.0
        
        # 如果有交互器，立即添加方向立方體
        if self.iren:
            self.add_orientation_cube()
            print("方向立方體已添加")

    def set_image_data(self, image: vtk.vtkImageData, matrix: vtk.vtkMatrix4x4 = None) -> None:
        """建立切片 Actor 並初始化至中點位置"""
        self.image_data = image
        self.image_matrix = matrix
        # 移除現有的切片
        for actor in self.slice_actors.values():
            self.renderer.RemoveViewProp(actor)
        self.slice_actors.clear()

        # 獲取影像範圍並計算中點
        extent = image.GetExtent()
        mid_points = [
            (extent[0] + extent[1]) // 2,
            (extent[2] + extent[3]) // 2,
            (extent[4] + extent[5]) // 2
        ]

        print(f"影像範圍: {extent}")
        print(f"初始切片位置: {mid_points}")

        directions = {'sagittal': 0, 'coronal': 1, 'axial': 2}
        for name, axis in directions.items():
            mapper = vtk.vtkImageSliceMapper()
            mapper.SetInputData(image)
            mapper.SetSliceNumber(mid_points[axis])
            
            if axis == 0:
                mapper.SetOrientationToX()
            elif axis == 1:
                mapper.SetOrientationToY()
            else:
                mapper.SetOrientationToZ()
            
            actor = vtk.vtkImageSlice()
            actor.SetMapper(mapper)
            actor.name = name
            
            actor.GetProperty().SetColorWindow(1500)
            actor.GetProperty().SetColorLevel(750)
            
            if matrix:
                actor.SetUserMatrix(matrix)
                
            self.renderer.AddViewProp(actor)
            self.slice_actors[name] = actor
        
        self.renderer.ResetCamera()
        
        if self.renderer.GetRenderWindow():
            self.renderer.GetRenderWindow().Render()

    def set_clip_offset(self, offset: tuple[float, float, float]) -> None:
        """設定裁切平面位移（用於資料集座標中心偏移時的補正）"""
        if offset is None:
            self._clip_offset = (0.0, 0.0, 0.0)
            return
        self._clip_offset = (float(offset[0]), float(offset[1]), float(offset[2]))

    def set_clip_center(self, center: tuple[float, float, float] | None) -> None:
        """設定裁切平面縮放的中心點"""
        if center is None:
            self._clip_center = None
            return
        self._clip_center = (float(center[0]), float(center[1]), float(center[2]))

    def set_clip_scale(self, scale: tuple[float, float, float]) -> None:
        """設定裁切平面縮放倍率（用於資料集尺度不一致補正）"""
        if scale is None:
            self._clip_scale = (1.0, 1.0, 1.0)
            return
        self._clip_scale = (float(scale[0]), float(scale[1]), float(scale[2]))

    def set_use_image_matrix_for_clipping(self, enabled: bool) -> None:
        """是否使用 NIfTI 的矩陣方向來產生裁切平面"""
        self._use_image_matrix_for_clipping = bool(enabled)

    def set_clip_axis_matrix_usage(self, usage: dict[str, bool]) -> None:
        """各軸是否使用影像矩陣"""
        if not usage:
            return
        for name in ("sagittal", "coronal", "axial"):
            if name in usage:
                self._clip_axis_use_matrix[name] = bool(usage[name])

    def set_clip_axis_offset_scale_usage(self, usage: dict[str, bool]) -> None:
        """各軸是否套用 offset/scale"""
        if not usage:
            return
        for name in ("sagittal", "coronal", "axial"):
            if name in usage:
                self._clip_axis_apply_offset_scale[name] = bool(usage[name])

    def set_clip_axis_bias(self, bias: dict[str, float]) -> None:
        """各軸裁切平面沿法向的微調偏移（世界座標）"""
        if not bias:
            return
        for name in ("sagittal", "coronal", "axial"):
            if name in bias:
                self._clip_axis_bias[name] = float(bias[name])

    def set_clip_axis_flip(self, flips: dict[str, bool]) -> None:
        """設定各軸裁切方向是否翻轉"""
        if not flips:
            self._clip_axis_flip = {"sagittal": False, "coronal": False, "axial": False}
            return
        for name in ("sagittal", "coronal", "axial"):
            if name in flips:
                self._clip_axis_flip[name] = bool(flips[name])

    def update_sagittal_plane(self, index):
        if 'sagittal' in self.slice_actors:
            self.slice_actors['sagittal'].GetMapper().SetSliceNumber(index)
            self._render_throttled()

    def update_coronal_plane(self, index):
        if 'coronal' in self.slice_actors:
            self.slice_actors['coronal'].GetMapper().SetSliceNumber(index)
            self._render_throttled()

    def update_axial_plane(self, index):
        if 'axial' in self.slice_actors:
            self.slice_actors['axial'].GetMapper().SetSliceNumber(index)
            self._render_throttled()

    def update_slices(self, sag_index=None, cor_index=None, ax_index=None):
        if sag_index is not None:
            self.update_sagittal_plane(sag_index)
        if cor_index is not None:
            self.update_coronal_plane(cor_index)
        if ax_index is not None:
            self.update_axial_plane(ax_index)

    def _render_throttled(self) -> None:
        rw = self.renderer.GetRenderWindow()
        if rw is None:
            return
        now = time.time()
        if now - self._last_render_t < self._render_interval:
            return
        self._last_render_t = now
        rw.Render()

    def get_slice_ranges(self):
        ranges = {}
        for name, actor in self.slice_actors.items():
            mapper = actor.GetMapper()
            if mapper:
                ranges[name] = mapper.GetSliceRange()
        return ranges

    def get_slice_visibility(self, slice_name=None):
        if not self.slice_actors:
            return {}
        if slice_name:
            if slice_name in self.slice_actors:
                return self.slice_actors[slice_name].GetVisibility()
            return False
        visibility = {}
        for name, actor in self.slice_actors.items():
            visibility[name] = actor.GetVisibility()
        return visibility

    def set_slice_visibility(self, slice_name, visible):
        """設置特定切片可見性"""
        if slice_name in self.slice_actors:
            self.slice_actors[slice_name].SetVisibility(visible)
            if self.renderer.GetRenderWindow():
                self.renderer.GetRenderWindow().Render()
            return True
        return False

    def set_slice_visibility_by_name(self, slice_name, visible):
        """根據名稱設置切片可見性（別名方法）"""
        return self.set_slice_visibility(slice_name, visible)

    def toggle_slice_visibility(self, slice_type, visible=None):
        if slice_type == 'all':
            slices = self.slice_actors.values()
        else:
            if slice_type not in self.slice_actors:
                return
            slices = [self.slice_actors[slice_type]]
        
        for slice_actor in slices:
            if visible is None:
                current_visibility = slice_actor.GetVisibility()
                slice_actor.SetVisibility(not current_visibility)
            else:
                slice_actor.SetVisibility(visible)
        
        if self.renderer.GetRenderWindow():
            self.renderer.GetRenderWindow().Render()

    def show_all_slices(self):
        for actor in self.slice_actors.values():
            actor.SetVisibility(True)
        if self.renderer.GetRenderWindow():
            self.renderer.GetRenderWindow().Render()

    def hide_all_slices(self):
        for actor in self.slice_actors.values():
            actor.SetVisibility(False)
        if self.renderer.GetRenderWindow():
            self.renderer.GetRenderWindow().Render()

    def add_orientation_cube(self):
        """添加方向指示立方體"""
        if not self.iren:
            print("警告：無法添加方向立方體，缺少交互器")
            return
            
        cube = vtk.vtkAnnotatedCubeActor()
        cube.SetXPlusFaceText("R")
        cube.SetXMinusFaceText("L")
        cube.SetYPlusFaceText("A")
        cube.SetYMinusFaceText("P")
        cube.SetZPlusFaceText("S")
        cube.SetZMinusFaceText("I")
        
        cube.GetCubeProperty().SetColor(0.8, 0.8, 0.8)
        cube.GetTextEdgesProperty().SetColor(0, 0, 0)
        cube.GetTextEdgesProperty().SetLineWidth(1.5)
        
        # 創建方向標記組件
        self.orientation_widget = vtk.vtkOrientationMarkerWidget()
        self.orientation_widget.SetOrientationMarker(cube)
        self.orientation_widget.SetInteractor(self.iren)
        self.orientation_widget.SetViewport(0.8, 0.0, 1.0, 0.2)
        self.orientation_widget.SetEnabled(1)
        self.orientation_widget.InteractiveOff()
        
        print("方向立方體已成功添加到場景")

    def get_orientation_widget(self):
        return self.orientation_widget

    def set_iren(self, iren: vtk.vtkRenderWindowInteractor):
        """設置交互器（用於後期添加方向立方體）"""
        self.iren = iren
        if not self.orientation_widget and self.iren:
            self.add_orientation_cube()

    def get_slice_actors(self):
        return self.slice_actors

    def get_slice_actor(self, slice_name):
        return self.slice_actors.get(slice_name)

    def get_slice_plane(self, slice_name: str, index: int) -> vtk.vtkPlane | None:
        """回傳指定切片在世界座標中的 clipping plane。"""
        if self.image_data is None:
            return None

        extent = self.image_data.GetExtent()
        if self._clip_axis_flip.get(slice_name, False):
            min_i = extent[0] if slice_name == "sagittal" else extent[2] if slice_name == "coronal" else extent[4]
            max_i = extent[1] if slice_name == "sagittal" else extent[3] if slice_name == "coronal" else extent[5]
            index = min_i + max_i - index
        center_i = (extent[0] + extent[1]) / 2.0
        center_j = (extent[2] + extent[3]) / 2.0
        center_k = (extent[4] + extent[5]) / 2.0

        if slice_name == "sagittal":
            p_ijk = (index, center_j, center_k)
            n_ijk = (1.0, 0.0, 0.0)
        elif slice_name == "coronal":
            p_ijk = (center_i, index, center_k)
            n_ijk = (0.0, 1.0, 0.0)
        elif slice_name == "axial":
            p_ijk = (center_i, center_j, index)
            n_ijk = (0.0, 0.0, 1.0)
        else:
            return None

        use_matrix = self._use_image_matrix_for_clipping and self._clip_axis_use_matrix.get(slice_name, True)
        if self.image_matrix is not None and use_matrix:
            p = [p_ijk[0], p_ijk[1], p_ijk[2], 1.0]
            p_w = [0.0, 0.0, 0.0, 0.0]
            self.image_matrix.MultiplyPoint(p, p_w)

            n = [n_ijk[0], n_ijk[1], n_ijk[2], 0.0]
            n_w = [0.0, 0.0, 0.0, 0.0]
            self.image_matrix.MultiplyPoint(n, n_w)
            nx, ny, nz = n_w[0], n_w[1], n_w[2]
        else:
            origin = self.image_data.GetOrigin()
            spacing = self.image_data.GetSpacing()
            p_w = [
                origin[0] + p_ijk[0] * spacing[0],
                origin[1] + p_ijk[1] * spacing[1],
                origin[2] + p_ijk[2] * spacing[2],
                1.0,
            ]
            nx, ny, nz = n_ijk

        norm = (nx * nx + ny * ny + nz * nz) ** 0.5
        if norm < 1e-8:
            return None

        # scale about image center if provided
        if self._clip_axis_apply_offset_scale.get(slice_name, True) and self._clip_center is not None:
            cx, cy, cz = self._clip_center
            sx, sy, sz = self._clip_scale
            p_w = [
                cx + (p_w[0] - cx) * sx,
                cy + (p_w[1] - cy) * sy,
                cz + (p_w[2] - cz) * sz,
                p_w[3],
            ]

        plane = vtk.vtkPlane()
        ox, oy, oz = self._clip_offset
        if self._clip_axis_apply_offset_scale.get(slice_name, True):
            plane.SetOrigin(p_w[0] + ox, p_w[1] + oy, p_w[2] + oz)
        else:
            plane.SetOrigin(p_w[0], p_w[1], p_w[2])
        plane.SetNormal(nx / norm, ny / norm, nz / norm)
        bias = self._clip_axis_bias.get(slice_name, 0.0)
        if abs(bias) > 1e-6:
            plane.SetOrigin(
                plane.GetOrigin()[0] + (nx / norm) * bias,
                plane.GetOrigin()[1] + (ny / norm) * bias,
                plane.GetOrigin()[2] + (nz / norm) * bias,
            )
        return plane

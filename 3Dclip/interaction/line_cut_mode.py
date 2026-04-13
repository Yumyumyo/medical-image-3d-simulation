from typing import List, Optional, Tuple
import vtk
import numpy as np

from interaction.base_mode import BaseInteractionMode
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager

class LineCutMode(BaseInteractionMode):
    """
    線性切割模式 (Self-contained version)：
    - 包含所有切割運算邏輯，不依賴外部 geometry utils。
    - 流程：標記 (P0 -> P1) -> 計算視角平面 -> 預覽 -> Commit。
    """
    def __init__(
        self,
        interactor: vtk.vtkRenderWindowInteractor,
        renderer: vtk.vtkRenderer,
        prop_manager: ObjectPropertyManager,
        obj3d_manager: Object3DManager,
    ) -> None:
        super().__init__("line_cut", interactor)
        self._renderer = renderer
        self._prop_mgr = prop_manager
        self._obj3d_mgr = obj3d_manager

        # --- 狀態旗標 ---
        self._marking_enabled: bool = False   # 是否允許畫線
        self._selecting_enabled: bool = False # 是否允許選取物件
        self._dragging: bool = False          # 是否正在拖曳畫線

        # --- 幾何資料 ---
        self.p0: Optional[np.ndarray] = None  # 起點 (World)
        self.p1: Optional[np.ndarray] = None  # 終點 (World)
        self._p0_display_z: Optional[float] = None  # 起點對應螢幕深度，用於 drag 無 pick 時反投影
        
        # 計算出的切割平面 (World)
        self._plane_origin_w: Optional[Tuple[float, float, float]] = None
        self._plane_normal_w: Optional[Tuple[float, float, float]] = None
        
        # --- 預覽用 Actors ---
        self._preview_actor_pos: Optional[vtk.vtkActor] = None
        self._preview_actor_neg: Optional[vtk.vtkActor] = None
        self._helper_line_actor: Optional[vtk.vtkActor] = None
        self._helper_plane_actor: Optional[vtk.vtkActor] = None
        
        # --- 工具 ---
        self._picker = vtk.vtkCellPicker()
        self._picker.SetTolerance(0.0005)

        # callback
        self.on_selected = None

    # =========================================================
    # Mode Lifecycle
    # =========================================================
    def on_mode_enter(self) -> None:
        print("[LineCutMode] Enter", flush=True)
        self.reset_status()

    def on_mode_exit(self) -> None:
        print("[LineCutMode] Exit", flush=True)
        self.reset_status()
        self._marking_enabled = False
        self._selecting_enabled = False

    def reset_status(self) -> None:
        """重置所有暫存狀態與預覽"""
        self._clear_previews()
        self.p0 = None
        self.p1 = None
        self._p0_display_z = None
        self._dragging = False
        self._plane_origin_w = None
        self._plane_normal_w = None
        self._render()

    def _display_to_world(self, x: int, y: int, display_z: float) -> Optional[np.ndarray]:
        """Display -> World 轉換（含深度），方便在 drag 時模擬 p1"""
        renderer = self._renderer
        renderer.SetDisplayPoint(x, y, display_z)
        renderer.DisplayToWorld()
        wp = renderer.GetWorldPoint()
        if wp[3] == 0:
            return None
        return np.array([wp[0] / wp[3], wp[1] / wp[3], wp[2] / wp[3]])

    def _clear_previews(self):
        if self._preview_actor_pos:
            self._renderer.RemoveActor(self._preview_actor_pos)
            self._preview_actor_pos = None
        if self._preview_actor_neg:
            self._renderer.RemoveActor(self._preview_actor_neg)
            self._preview_actor_neg = None
        if self._helper_line_actor:
            self._renderer.RemoveActor(self._helper_line_actor)
            self._helper_line_actor = None
        if self._helper_plane_actor:
            self._renderer.RemoveActor(self._helper_plane_actor)
            self._helper_plane_actor = None

    # =========================================================
    # UI Actions (Called by MainWindow)
    # =========================================================
    def set_selecting_enabled(self, enabled: bool) -> None:
        self._selecting_enabled = bool(enabled)
        print(f"[LineCutMode] selecting_enabled = {self._selecting_enabled}", flush=True)
        if self._selecting_enabled:
            self._marking_enabled = False

    def toggle_selecting(self) -> None:
        self.set_selecting_enabled(not self._selecting_enabled)

    def toggle_marking(self):
        self._marking_enabled = not self._marking_enabled
        if self._marking_enabled:
            self._selecting_enabled = False
            self.reset_status()
        print(f"[LineCutMode] Marking: {self._marking_enabled}", flush=True)

    def clear_markers(self):
        self.reset_status()
    
    def reset(self) -> None:
        # reset = 清 marker + 清 plane
        self.clear_markers()
        
        

    # =========================================================
    # Mouse Interactions
    # =========================================================
    def on_left_button_down(self, interactor: vtk.vtkRenderWindowInteractor):
        # 1. 選取模式
        if self._selecting_enabled:
            x, y = interactor.GetEventPosition()
            picked = self._picker.Pick(x, y, 0, self._renderer)
            print(f"[LineCutMode] selecting_enabled, pick result={picked} at {x},{y}", flush=True)
            if picked == 0:
                return

            actor = self._picker.GetActor()
            if actor is None:
                print("[LineCutMode] pick actor is None", flush=True)
                return

            obj_id = self._obj3d_mgr.get_obj_id_from_actor(actor)
            print(f"[LineCutMode] picked actor belongs to obj_id={obj_id}", flush=True)
            if obj_id is None:
                return

            so = self._prop_mgr.get_object(obj_id)
            new_state = (not bool(so.selected))

            print(f"[LineCutMode] toggling selection for obj {obj_id} -> {new_state}", flush=True)
            self._prop_mgr.set_selected(obj_id, new_state)
            self._obj3d_mgr.update_actor_appearance(obj_id)
            self._render()

            if callable(self.on_selected):
                self.on_selected(obj_id, so.kind, new_state)
            return

        # 2. 畫線模式 (開始)
        if self._marking_enabled:
            x, y = interactor.GetEventPosition()
            if self._picker.Pick(x, y, 0, self._renderer):
                self._dragging = True
                self.p0 = np.array(self._picker.GetPickPosition())

                # 記錄 p0 的 Display Z，用於後續 mouse_move 無 pick 時反投影.
                self._renderer.SetWorldPoint(self.p0[0], self.p0[1], self.p0[2], 1.0)
                self._renderer.WorldToDisplay()
                dp = self._renderer.GetDisplayPoint()
                self._p0_display_z = float(dp[2])

                # 隱藏舊預覽，準備畫新的
                self._clear_previews()
                print(f"[LineCutMode] Start Drag: {self.p0} display_z={self._p0_display_z}", flush=True)
            return

    def on_mouse_move(self, interactor: vtk.vtkRenderWindowInteractor):
        # 拖曳中：更新終點 -> 計算平面 -> 更新預覽
        if self._dragging and self._marking_enabled and self.p0 is not None:
            x, y = interactor.GetEventPosition()
            p1_world: Optional[np.ndarray] = None

            if self._picker.Pick(x, y, 0, self._renderer):
                p1_world = np.array(self._picker.GetPickPosition())
            elif self._p0_display_z is not None:
                p1_world = self._display_to_world(x, y, self._p0_display_z)

            if p1_world is not None:
                if np.linalg.norm(p1_world - self.p0) < 1e-4:
                    # 允許微小位移; 避免訊號噪聲造成預覽抖動。
                    return

                self.p1 = p1_world
                # 計算
                self._compute_plane_from_view()
                # 預覽
                self._update_preview()
                # 助手
                self._update_helper_actors()
                self._render()

    def on_left_button_up(self, interactor: vtk.vtkRenderWindowInteractor):
        if self._dragging:
            self._dragging = False
            print("[LineCutMode] End Drag. Ready to commit.", flush=True)
            # 最後一次補一下輔助視覺和計算值
            if self.p0 is not None and self.p1 is not None:
                self._compute_plane_from_view()
                self._update_preview()
                self._update_helper_actors()
                self._render()

    def _handle_selection_click(self, interactor):
        x, y = interactor.GetEventPosition()
        if self._picker.Pick(x, y, 0, self._renderer):
            actor = self._picker.GetActor()
            obj_id = self._obj3d_mgr.get_obj_id_from_actor(actor)
            if obj_id is not None:
                so = self._prop_mgr.get_object(obj_id)
                new_state = not so.selected
                self._prop_mgr.set_selected(obj_id, new_state)
                self._obj3d_mgr.update_actor_appearance(obj_id)
                self._render()
                if callable(self.on_selected):
                    self.on_selected(obj_id, so.kind, new_state)

    # =========================================================
    # Core Logic: Commit
    # =========================================================
    def commit(self) -> Optional[Tuple[List[int], List[int], List[int]]]:
        """
        執行切割：
        1. 檢查是否有計算好的平面 (origin, normal)。
        2. 取得選取物件。
        3. 轉換座標 (World -> Local)。
        4. 執行 Clip。
        5. 更新 Manager (隱藏舊的，建立新的)。
        """
        # debug/help information
        print("[LineCutMode] commit called", flush=True)

        # if we already have p0/p1 but the plane wasn't computed (e.g. mouse-up
        # happened without a pick), try to calculate it now so the user doesn't
        # have to drag again.
        if (self._plane_origin_w is None or self._plane_normal_w is None) and \
           (self.p0 is not None and self.p1 is not None):
            self._compute_plane_from_view()
            print("[LineCutMode] computed plane on commit", flush=True)

        if self._plane_origin_w is None or self._plane_normal_w is None:
            if self.p0 is not None and self.p1 is not None:
                self._compute_plane_from_view()
            if self._plane_origin_w is None or self._plane_normal_w is None:
                print("[LineCutMode] No plane defined. Ignore commit.", flush=True)
                return None

        selected_ids = list(self._prop_mgr.get_selected_objects())
        if not selected_ids:
            print("[LineCutMode] No object selected.", flush=True)
            return None

        changed_ids = []
        new_result_ids = []
        delete_ids = []

        for obj_id in selected_ids:
            obj = self._prop_mgr.get_object(obj_id)
            if not obj.visible:
                continue

            # 轉 Local
            o_local, n_local = self._world_plane_to_local(
                obj.transform, self._plane_origin_w, self._plane_normal_w
            )
            
            # 切割 (使用內部運算邏輯)
            poly_pos, poly_neg = self._clip_polydata_two_sides(obj.polydata, o_local, n_local)

            # 建立結果物件
            created_this_obj: List[int] = []
            # 正側
            if poly_pos and poly_pos.GetNumberOfPoints() > 0:
                rid = self._prop_mgr.create_result(
                    parent_id=obj_id,
                    polydata=poly_pos,
                    name=f"{obj.name}_pos",
                    inherit_transform=True,
                )
                created_this_obj.append(rid)
                new_result_ids.append(rid)
            # 反側
            if poly_neg and poly_neg.GetNumberOfPoints() > 0:
                rid = self._prop_mgr.create_result(
                    parent_id=obj_id,
                    polydata=poly_neg,
                    name=f"{obj.name}_neg",
                    inherit_transform=True,
                )
                created_this_obj.append(rid)
                new_result_ids.append(rid)

            # 先把新物件加到場景（避免你刪舊 actor 後畫面不更新）
            for rid in created_this_obj:
                self._obj3d_mgr.spawn_actor(rid)

            # 處理原始物件
            if obj.kind == "original":
                self._prop_mgr.set_selected(obj_id, False)
                self._prop_mgr.set_visible(obj_id, False)
                self._obj3d_mgr.update_actor_appearance(obj_id)
                changed_ids.append(obj_id)
            elif obj.kind == "result":
                # 若切的是已經切過的 result，通常直接刪除舊的
                delete_ids.append(obj_id)

        # 切割完重置狀態
        self.reset_status()
        # make sure the window is updated (PlaneCutMode does similar)
        rw = self._renderer.GetRenderWindow()
        if rw is not None:
            rw.Render()

        print(f"[LineCutMode] Commit Done. New results: {new_result_ids}", flush=True)
        return changed_ids, new_result_ids, delete_ids

    # =========================================================
    # Internal Calculation Methods (幾何運算區)
    # =========================================================

    def _compute_plane_from_view(self):
        """
        根據 p0, p1 (螢幕上的線) 與 相機視角 (View Vector)，
        計算出切割平面的法向量。
        Normal = (P1 - P0) x ViewDirection
        """
        if self.p0 is None or self.p1 is None:
            return

        line_vec = self.p1 - self.p0
        dist = np.linalg.norm(line_vec)
        if dist < 1e-6:
            return

        camera = self._renderer.GetActiveCamera()
        view_vec = np.array(camera.GetDirectionOfProjection()) # Camera 看的方向

        # 外積算出平面法向量
        normal = np.cross(line_vec, view_vec)
        
        # 正規化
        norm_val = np.linalg.norm(normal)
        if norm_val < 1e-6:
            # 線段可能與視線共線；改用 camera 上向量或右向量避免 degenerate
            up_vec = np.array(camera.GetViewUp())
            normal = np.cross(line_vec, up_vec)
            norm_val = np.linalg.norm(normal)
            if norm_val < 1e-6:
                right_vec = np.cross(view_vec, up_vec)
                right_vec = right_vec / np.linalg.norm(right_vec) if np.linalg.norm(right_vec) > 0 else np.array([1.0, 0.0, 0.0])
                normal = np.cross(line_vec, right_vec)
                norm_val = np.linalg.norm(normal)
                if norm_val < 1e-6:
                    return

        normal /= norm_val

        self._plane_origin_w = tuple(self.p0) # 平面通過起點
        self._plane_normal_w = tuple(normal)

    def _world_plane_to_local(
        self,
        obj_transform: vtk.vtkTransform,
        origin_w: Tuple[float, float, float],
        normal_w: Tuple[float, float, float]
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        """將世界座標平面轉為物件 Local 座標"""
        inv = vtk.vtkTransform()
        inv.DeepCopy(obj_transform)
        inv.Inverse()

        o_local = inv.TransformPoint(origin_w)
        n_local = inv.TransformVector(normal_w) # 向量變換只受旋轉縮放影響，不受位移影響

        # 正規化 n_local
        n_np = np.array(n_local)
        nm = np.linalg.norm(n_np)
        if nm > 0:
            n_np /= nm
        
        return (
            (float(o_local[0]), float(o_local[1]), float(o_local[2])),
            (float(n_np[0]), float(n_np[1]), float(n_np[2]))
        )

    def _clip_polydata_two_sides(
        self,
        polydata: vtk.vtkPolyData,
        origin_local: Tuple[float, float, float],
        normal_local: Tuple[float, float, float],
        generate_faces: bool = True
    ) -> Tuple[Optional[vtk.vtkPolyData], Optional[vtk.vtkPolyData]]:
        """
        核心演算法：使用 vtkClipClosedSurface 切割並補面。
        """
        # 1. 確保三角化 (ClipClosedSurface 需要)
        tri = vtk.vtkTriangleFilter()
        tri.SetInputData(polydata)
        tri.Update()

        normal = np.array(normal_local)

        # 內部函式：執行單邊切割
        def run_clip(n_vec):
            plane = vtk.vtkPlane()
            plane.SetOrigin(origin_local)
            plane.SetNormal(n_vec)

            planes = vtk.vtkPlaneCollection()
            planes.AddItem(plane)

            clipper = vtk.vtkClipClosedSurface()
            clipper.SetInputData(tri.GetOutput())
            clipper.SetClippingPlanes(planes)
            if generate_faces:
                clipper.GenerateFacesOn()    # 重要：補面
            else:
                clipper.GenerateFacesOff()
            clipper.SetTolerance(1e-6)
            clipper.Update()

            res = clipper.GetOutput()
            if res.GetNumberOfPoints() == 0:
                return None
            return self._post_process_poly(res)

        out_pos = run_clip(normal)
        out_neg = run_clip(-normal) # 反向法向量切出另一半

        return out_pos, out_neg

    def _post_process_poly(self, poly: vtk.vtkPolyData) -> vtk.vtkPolyData:
        """清理資料：移除孤立點、統一法向量"""
        clean = vtk.vtkCleanPolyData()
        clean.SetInputData(poly)
        clean.Update()

        # 再次三角化確保結構
        tri = vtk.vtkTriangleFilter()
        tri.SetInputConnection(clean.GetOutputPort())
        tri.Update()

        # 重算法向量 (讓光影正確)
        norm = vtk.vtkPolyDataNormals()
        norm.SetInputConnection(tri.GetOutputPort())
        norm.AutoOrientNormalsOn() # 自動調整正反面
        norm.ConsistencyOn()
        norm.Update()

        out = vtk.vtkPolyData()
        out.DeepCopy(norm.GetOutput())
        return out

    # =========================================================
    # Internal Render/Preview Helpers
    # =========================================================
    def _update_preview(self):
        """產生預覽 (不寫入 Manager，僅視覺顯示)"""
        # 只預覽「第一個」選取的物件，避免效能太差
        selected_ids = list(self._prop_mgr.get_selected_objects())
        if not selected_ids or self._plane_origin_w is None:
            return

        target_id = selected_ids[0]
        obj = self._prop_mgr.get_object(target_id)
        
        # 轉 Local
        o_local, n_local = self._world_plane_to_local(
            obj.transform, self._plane_origin_w, self._plane_normal_w
        )
        
        # 試切 (預覽時採用不補面以加速)
        poly_pos, poly_neg = self._clip_polydata_two_sides(obj.polydata, o_local, n_local, generate_faces=False)
        
        self._update_preview_actor(poly_pos, is_pos=True, transform=obj.transform)
        self._update_preview_actor(poly_neg, is_pos=False, transform=obj.transform)

        # 顯示輔助線與切割平面（即時操作輔助）
        self._update_helper_actors()

    def _update_preview_actor(self, poly, is_pos, transform):
        if poly is None:
            return

        actor = self._preview_actor_pos if is_pos else self._preview_actor_neg
        
        # 建立 actor (如果還沒建)
        if actor is None:
            actor = vtk.vtkActor()
            self._renderer.AddActor(actor)
            if is_pos: self._preview_actor_pos = actor
            else: self._preview_actor_neg = actor
        
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)
        
        actor.SetMapper(mapper)
        actor.SetUserTransform(transform)
        
        # 預覽顏色 (紅/藍區分)
        color = (0.9, 0.4, 0.4) if is_pos else (0.4, 0.4, 0.9)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(0.85)
        actor.SetPickable(False) # 預覽物件不可被點選
        actor.SetVisibility(True)

    def _render(self):
        self._renderer.GetRenderWindow().Render()

    def _update_helper_actors(self):
        if self.p0 is None or self.p1 is None:
            return

        # 線段輔助器
        line_source = vtk.vtkLineSource()
        line_source.SetPoint1(*self.p0)
        line_source.SetPoint2(*self.p1)
        line_source.Update()

        if self._helper_line_actor is None:
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(line_source.GetOutput())
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(1.0, 1.0, 0.3)
            actor.GetProperty().SetLineWidth(4)
            actor.GetProperty().SetOpacity(0.9)
            actor.SetPickable(False)
            self._renderer.AddActor(actor)
            self._helper_line_actor = actor
        else:
            self._helper_line_actor.GetMapper().SetInputData(line_source.GetOutput())

        # 平面輔助器（基於 line 距離與當前切面法向）
        if self._plane_normal_w is not None:
            try:
                # using intermediate plane geometry in local XY then transform
                dist = np.linalg.norm(self.p1 - self.p0)
                if dist < 1e-6:
                    dist = 1.0
                size = max(dist, 1.0)

                plane = vtk.vtkPlaneSource()
                plane.SetOrigin(-size, -size, 0)
                plane.SetPoint1(size, -size, 0)
                plane.SetPoint2(-size, size, 0)
                plane.SetResolution(1, 1)
                plane.Update()

                cx = float((self.p0[0] + self.p1[0]) * 0.5)
                cy = float((self.p0[1] + self.p1[1]) * 0.5)
                cz = float((self.p0[2] + self.p1[2]) * 0.5)

                transform = vtk.vtkTransform()
                transform.Translate(cx, cy, cz)
                view_up = np.array([0.0, 0.0, 1.0])
                normal = np.array(self._plane_normal_w)
                normal = normal / np.linalg.norm(normal)
                axis = np.cross(view_up, normal)
                norm_axis = np.linalg.norm(axis)
                if norm_axis > 1e-6:
                    axis = axis / norm_axis
                    angle = np.degrees(np.arccos(np.dot(view_up, normal)))
                    transform.RotateWXYZ(angle, axis.tolist())

                if self._helper_plane_actor is None:
                    mapper = vtk.vtkPolyDataMapper()
                    mapper.SetInputData(plane.GetOutput())
                    actor = vtk.vtkActor()
                    actor.SetMapper(mapper)
                    actor.GetProperty().SetColor(0.6, 0.8, 1.0)
                    actor.GetProperty().SetOpacity(0.25)
                    actor.GetProperty().SetRepresentationToSurface()
                    actor.SetUserTransform(transform)
                    actor.SetPickable(False)
                    self._renderer.AddActor(actor)
                    self._helper_plane_actor = actor
                else:
                    self._helper_plane_actor.GetMapper().SetInputData(plane.GetOutput())
                    self._helper_plane_actor.SetUserTransform(transform)

            except Exception:
                pass

    def on_key_press(self, interactor: vtk.vtkRenderWindowInteractor, key_sym: str) -> bool:
        # Esc: 取消當前標記並還原
        if key_sym == 'Escape':
            self.reset_status()
            return True

        # Enter/Return: 立即 commit line_cut
        if key_sym in ('Return', 'Enter'):
            self.commit()
            return True

        return False

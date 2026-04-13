from typing import List, Optional, Tuple
from PyQt5 import QtWidgets, QtCore
import vtk
from interaction.base_mode import BaseInteractionMode
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager

Vec3 = Tuple[float, float, float]
UV2 = Tuple[float, float]


class PlaneCutMode(BaseInteractionMode):
    """
    第一版 plane cut：
    - selecting 模式：在已選取物件表面放置 result 圓錐
    - marking 模式：
        1) 前 3 點在物件表面決定切割平面
        2) 平面建立後，顯示可視化預覽平面
        3) 在預覽平面上自動生成可拖曳的矩形 ROI
        4) commit 時只對矩形 ROI 對應的區域做局部 plane cut
    """

    def __init__(
        self,
        interactor: vtk.vtkRenderWindowInteractor,
        renderer: vtk.vtkRenderer,
        prop_manager: ObjectPropertyManager,
        obj3d_manager: Object3DManager,
    ) -> None:
        super().__init__("plane_cut", interactor)
        self._renderer = renderer
        self._prop_mgr = prop_manager
        self._obj3d_mgr = obj3d_manager

        self._marking_enabled: bool = False
        self._selecting_enabled: bool = False

        self.on_selected = None
        self.on_result_created = None

        self._picked_points_w: List[Vec3] = []
        self._marker_actors: List[vtk.vtkActor] = []

        self._picker = vtk.vtkCellPicker()
        self._picker.SetTolerance(0.0005)

        self._plane_origin_w: Optional[Vec3] = None
        self._plane_normal_w: Optional[Vec3] = None
        self._plane_u_w: Optional[Vec3] = None
        self._plane_v_w: Optional[Vec3] = None
        self._plane_size_u: float = 0.0
        self._plane_size_v: float = 0.0

        self._plane_preview_actor: Optional[vtk.vtkActor] = None
        self._roi_outline_actor: Optional[vtk.vtkActor] = None
        self._roi_fill_actor: Optional[vtk.vtkActor] = None
        self._roi_handle_actors: List[vtk.vtkActor] = []

        self._roi_u_min: float = 0.0
        self._roi_u_max: float = 0.0
        self._roi_v_min: float = 0.0
        self._roi_v_max: float = 0.0
        self._roi_ready: bool = False

        self._drag_mode: Optional[str] = None  # None | "move" | "handle"
        self._active_handle_index: Optional[int] = None
        self._drag_start_uv: Optional[UV2] = None
        self._drag_start_bounds: Optional[Tuple[float, float, float, float]] = None

        self._handle_pick_radius_px: float = 16.0
        self._plane_pick_margin_uv: float = 0.02

    # ---------------------------
    # Mode lifecycle
    # ---------------------------
    def on_mode_enter(self) -> None:
        print("[PlaneCutMode] enter", flush=True)
        self.clear_markers()

    def on_mode_exit(self) -> None:
        print("[PlaneCutMode] exit", flush=True)
        self.clear_markers()
        self._marking_enabled = False
        self._selecting_enabled = False

    # ---------------------------
    # UI actions
    # ---------------------------
    def set_marking_enabled(self, enabled: bool) -> None:
        self._marking_enabled = bool(enabled)
        print(f"[PlaneCutMode] marking_enabled = {self._marking_enabled}", flush=True)
        if self._marking_enabled:
            self._selecting_enabled = False

    def toggle_marking(self) -> None:
        self.set_marking_enabled(not self._marking_enabled)

    def set_selecting_enabled(self, enabled: bool) -> None:
        self._selecting_enabled = bool(enabled)
        print(f"[PlaneCutMode] selecting_enabled = {self._selecting_enabled}", flush=True)
        if self._selecting_enabled:
            self._marking_enabled = False

    def toggle_selecting(self) -> None:
        self.set_selecting_enabled(not self._selecting_enabled)

    def clear_markers(self) -> None:
        for a in self._marker_actors:
            try:
                self._renderer.RemoveActor(a)
            except Exception:
                pass
        self._marker_actors.clear()

        self._clear_plane_preview()
        self._clear_roi_preview()

        self._picked_points_w.clear()
        self._plane_origin_w = None
        self._plane_normal_w = None
        self._plane_u_w = None
        self._plane_v_w = None
        self._plane_size_u = 0.0
        self._plane_size_v = 0.0

        self._roi_u_min = self._roi_u_max = 0.0
        self._roi_v_min = self._roi_v_max = 0.0
        self._roi_ready = False

        self._clear_drag_state()
        self._render()
        print("[PlaneCutMode] markers cleared", flush=True)

    def reset(self) -> None:
        self.clear_markers()
        print("[PlaneCutMode] reset", flush=True)

    # ---------------------------
    # Mouse capture helpers
    # ---------------------------
    def wants_to_capture_mouse(self, x: int, y: int) -> bool:
        """給 main_window 的 filter 用，點到 ROI / handle 時鎖住相機左鍵旋轉。"""
        if not self._marking_enabled:
            return False
        if self._plane_origin_w is None or not self._roi_ready:
            return False
        hit_type, _ = self._hit_test_roi_elements(x, y)
        return hit_type is not None

    # ---------------------------
    # Mouse events
    # ---------------------------
    def on_left_button_down(self, interactor: vtk.vtkRenderWindowInteractor):
        if self._selecting_enabled:
            self._handle_cone_placement(interactor)
            return

        if not self._marking_enabled:
            return

        # plane ready 後：只操作 ROI
        if self._plane_origin_w is not None and self._roi_ready:
            if self._try_start_roi_drag(interactor):
                return
            print("[PlaneCutMode] plane already ready; drag ROI handles or ROI body", flush=True)
            return

        # 前 3 點：從模型表面取點定義 plane
        x, y = interactor.GetEventPosition()
        ok = self._picker.Pick(x, y, 0, self._renderer)
        if ok == 0:
            print("[PlaneCutMode] pick failed", flush=True)
            return

        actor = self._picker.GetActor()
        if actor is None:
            print("[PlaneCutMode] pick actor is None", flush=True)
            return

        obj_id = self._obj3d_mgr.get_obj_id_from_actor(actor)
        selected_ids = set(self._prop_mgr.get_selected_objects())
        if not selected_ids:
            print("[PlaneCutMode] no selected object -> ignore pick", flush=True)
            return
        if obj_id is None or obj_id not in selected_ids:
            print(f"[PlaneCutMode] picked obj_id={obj_id} not selected -> ignore", flush=True)
            return

        p = self._picker.GetPickPosition()
        pw = (float(p[0]), float(p[1]), float(p[2]))
        self._picked_points_w.append(pw)
        self._add_marker(pw)
        print(f"[PlaneCutMode] plane point {len(self._picked_points_w)}/3: {pw}", flush=True)

        if len(self._picked_points_w) == 3:
            self._compute_plane_from_3_points()
            if self._plane_origin_w is not None and self._plane_normal_w is not None:
                self._setup_plane_preview_and_default_roi()
                print("[PlaneCutMode] plane ready, ROI rectangle created on preview plane", flush=True)
        elif len(self._picked_points_w) > 3:
            print("[PlaneCutMode] more than 3 plane points, auto reset", flush=True)
            self.clear_markers()

    def on_mouse_move(self, interactor: vtk.vtkRenderWindowInteractor):
        if self._drag_mode is None:
            return

        # 保險：若左鍵已放開但 release 事件沒收乾淨，立刻停止拖曳
        if not (QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton):
            self._clear_drag_state()
            return

        x, y = interactor.GetEventPosition()
        hit_w = self._display_to_plane_world(x, y)
        if hit_w is None:
            return

        uv = self._world_to_plane_uv(hit_w)
        if uv is None or self._drag_start_uv is None or self._drag_start_bounds is None:
            return

        su_min, su_max, sv_min, sv_max = self._drag_start_bounds
        du = uv[0] - self._drag_start_uv[0]
        dv = uv[1] - self._drag_start_uv[1]

        if self._drag_mode == "move":
            self._roi_u_min = su_min + du
            self._roi_u_max = su_max + du
            self._roi_v_min = sv_min + dv
            self._roi_v_max = sv_max + dv
        elif self._drag_mode == "handle" and self._active_handle_index is not None:
            self._apply_handle_drag(self._active_handle_index, uv, self._drag_start_bounds)

        self._clamp_roi_bounds()
        self._update_roi_preview()
        self._render()

    def on_left_button_up(self, interactor: vtk.vtkRenderWindowInteractor):
        self._clear_drag_state()

    # ---------------------------
    # Commit
    # ---------------------------
    def commit(self) -> Optional[Tuple[List[int], List[int], List[int]]]:
        if self._plane_origin_w is None or self._plane_normal_w is None:
            print("[PlaneCutMode] commit ignored: plane not ready (need 3 points)", flush=True)
            return None

        selected_ids = list(self._prop_mgr.get_selected_objects())
        if not selected_ids:
            print("[PlaneCutMode] commit ignored: no selected objects", flush=True)
            return None

        fill_cut_surface = self._ask_fill_cut_surface()
        if fill_cut_surface is None:
            print("[PlaneCutMode] commit canceled by user", flush=True)
            return None

        roi_points_w = self._get_roi_world_points()
        use_local_roi = len(roi_points_w) >= 3

        changed_original_ids: List[int] = []
        new_result_ids: List[int] = []
        to_delete_results: List[int] = []

        for obj_id in selected_ids:
            obj = self._prop_mgr.get_object(obj_id)
            kind = getattr(obj, "kind", None)
            if kind not in ("original", "result"):
                print(f"[PlaneCutMode] obj_id={obj_id} kind={kind} unsupported -> skip", flush=True)
                continue

            base_actor = self._obj3d_mgr.get_actor(obj_id)
            if base_actor is None:
                print(f"[PlaneCutMode] obj_id={obj_id} has no actor -> skip", flush=True)
                continue

            origin_local, normal_local = self._world_plane_to_object_local(
                obj.transform,
                self._plane_origin_w,
                self._plane_normal_w,
            )

            if use_local_roi:
                roi_points_local = [self._world_point_to_object_local(obj.transform, p_w) for p_w in roi_points_w]
                poly_a, poly_b = self._clip_polydata_local_roi(
                    obj.polydata,
                    origin_local,
                    normal_local,
                    roi_points_local,
                    fill_cut_surface=fill_cut_surface,
                )
            else:
                poly_a, poly_b = self._clip_polydata_two_sides(
                    obj.polydata,
                    origin_local,
                    normal_local,
                    fill_cut_surface=fill_cut_surface,
                )

            if (poly_a is None or poly_a.GetNumberOfPoints() == 0) and (poly_b is None or poly_b.GetNumberOfPoints() == 0):
                print(f"[PlaneCutMode] obj_id={obj_id} clip result empty -> skip", flush=True)
                continue

            created_this_obj: List[int] = []
            if poly_a is not None and poly_a.GetNumberOfPoints() > 0:
                rid1 = self._prop_mgr.create_result(parent_id=obj_id, polydata=poly_a, name=f"{obj.name}_partA", inherit_transform=True)
                created_this_obj.append(rid1)
                new_result_ids.append(rid1)
            if poly_b is not None and poly_b.GetNumberOfPoints() > 0:
                rid2 = self._prop_mgr.create_result(parent_id=obj_id, polydata=poly_b, name=f"{obj.name}_partB", inherit_transform=True)
                created_this_obj.append(rid2)
                new_result_ids.append(rid2)

            for rid in created_this_obj:
                self._obj3d_mgr.spawn_actor(rid)

            if kind == "original":
                self._prop_mgr.set_selected(obj_id, False)
                self._prop_mgr.set_visible(obj_id, False)
                self._obj3d_mgr.update_actor_appearance(obj_id)
                changed_original_ids.append(obj_id)
            elif kind == "result":
                to_delete_results.append(obj_id)

        self._render()
        self.clear_markers()

        print(
            f"[PlaneCutMode] commit done: changed={changed_original_ids}, "
            f"new_results={new_result_ids}, delete_results={to_delete_results}, "
            f"fill_cut_surface={fill_cut_surface}",
            flush=True,
        )
        return changed_original_ids, new_result_ids, to_delete_results

    # ---------------------------
    # Dialog
    # ---------------------------
    def _ask_fill_cut_surface(self) -> Optional[bool]:
        msg = QtWidgets.QMessageBox()
        msg.setWindowTitle("切割選項")
        msg.setText("此次切割是否要補齊切面？")
        msg.setIcon(QtWidgets.QMessageBox.Question)

        yes_btn = msg.addButton("要", QtWidgets.QMessageBox.AcceptRole)
        no_btn = msg.addButton("不要", QtWidgets.QMessageBox.DestructiveRole)
        cancel_btn = msg.addButton("取消", QtWidgets.QMessageBox.RejectRole)

        msg.exec_()
        clicked = msg.clickedButton()
        if clicked == yes_btn:
            return True
        if clicked == no_btn:
            return False
        return None

    # ---------------------------
    # Cone placement
    # ---------------------------
    def _handle_cone_placement(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        x, y = interactor.GetEventPosition()
        ok = self._picker.Pick(x, y, 0, self._renderer)
        if ok == 0:
            print("[PlaneCutMode] selecting pick failed", flush=True)
            return

        actor = self._picker.GetActor()
        if actor is None:
            print("[PlaneCutMode] selecting actor is None", flush=True)
            return

        obj_id = self._obj3d_mgr.get_obj_id_from_actor(actor)
        if obj_id is None:
            print("[PlaneCutMode] selecting obj_id is None", flush=True)
            return

        selected_ids = set(self._prop_mgr.get_selected_objects())
        if not selected_ids or obj_id not in selected_ids:
            print(f"[PlaneCutMode] picked obj_id={obj_id} not selected -> ignore cone placement", flush=True)
            return

        p = self._picker.GetPickPosition()
        pick_w = (float(p[0]), float(p[1]), float(p[2]))
        new_result_id = self._create_cone_result_at_pick(obj_id, pick_w)
        if new_result_id is None:
            return

        if callable(self.on_result_created):
            try:
                self.on_result_created(new_result_id)
            except Exception as e:
                print(f"[PlaneCutMode] on_result_created failed: {e}", flush=True)

        if callable(self.on_selected):
            try:
                self.on_selected(new_result_id, "result", 1)
            except Exception as e:
                print(f"[PlaneCutMode] on_selected(result) failed: {e}", flush=True)

        self._render()

    def _create_cone_result_at_pick(self, parent_obj_id: int, pick_world: Vec3) -> Optional[int]:
        try:
            parent_obj = self._prop_mgr.get_object(parent_obj_id)
        except Exception as e:
            print(f"[PlaneCutMode] create cone result failed: {e}", flush=True)
            return None

        tip_local = self._world_point_to_object_local(parent_obj.transform, pick_world)
        view_up_world = self._get_camera_view_up_world()
        up_local = self._world_vector_to_object_local(parent_obj.transform, view_up_world)
        up_local = self._normalize(up_local, fallback=(0.0, 0.0, 1.0))

        cone_height = 5.0
        cone_radius = 2.0
        direction_local = (-up_local[0], -up_local[1], -up_local[2])
        center_local = (
            tip_local[0] - direction_local[0] * (cone_height * 0.5),
            tip_local[1] - direction_local[1] * (cone_height * 0.5),
            tip_local[2] - direction_local[2] * (cone_height * 0.5),
        )

        src = vtk.vtkConeSource()
        src.SetCenter(center_local[0], center_local[1], center_local[2])
        src.SetDirection(direction_local[0], direction_local[1], direction_local[2])
        src.SetHeight(cone_height)
        src.SetRadius(cone_radius)
        src.SetResolution(24)
        src.CappingOn()
        src.Update()

        poly = vtk.vtkPolyData()
        poly.DeepCopy(src.GetOutput())
        poly = self._post_process_polydata(poly)

        rid = self._prop_mgr.create_result(
            parent_id=parent_obj_id,
            polydata=poly,
            name=f"{parent_obj.name}_cone",
            color=(1.0, 0.6, 0.15),
            opacity=1.0,
            inherit_transform=True,
        )
        self._obj3d_mgr.spawn_actor(rid)
        return rid

    # ---------------------------
    # Preview / ROI interaction
    # ---------------------------
    def _setup_plane_preview_and_default_roi(self) -> None:
        if self._plane_origin_w is None or self._plane_normal_w is None:
            return

        u_w, v_w = self._make_plane_basis(self._plane_normal_w)
        self._plane_u_w = u_w
        self._plane_v_w = v_w

        size = self._estimate_plane_size_from_selected_objects()
        self._plane_size_u = size
        self._plane_size_v = size
        self._create_plane_preview_actor()

        self._roi_u_min = -size * 0.18
        self._roi_u_max = size * 0.18
        self._roi_v_min = -size * 0.12
        self._roi_v_max = size * 0.12
        self._roi_ready = True
        self._update_roi_preview()
        self._render()

    def _estimate_plane_size_from_selected_objects(self) -> float:
        bounds = [0.0] * 6
        any_valid = False
        for obj_id in self._prop_mgr.get_selected_objects():
            actor = self._obj3d_mgr.get_actor(obj_id)
            if actor is None:
                continue
            b = actor.GetBounds()
            if b is None:
                continue
            if not any_valid:
                bounds[:] = list(b)
                any_valid = True
            else:
                bounds[0] = min(bounds[0], b[0]); bounds[1] = max(bounds[1], b[1])
                bounds[2] = min(bounds[2], b[2]); bounds[3] = max(bounds[3], b[3])
                bounds[4] = min(bounds[4], b[4]); bounds[5] = max(bounds[5], b[5])

        if not any_valid:
            return 30.0
        dx = bounds[1] - bounds[0]
        dy = bounds[3] - bounds[2]
        dz = bounds[5] - bounds[4]
        diag = max((dx * dx + dy * dy + dz * dz) ** 0.5, 10.0)
        return diag * 0.65

    def _create_plane_preview_actor(self) -> None:
        self._clear_plane_preview()
        pts = self._get_plane_preview_corners_world()
        poly = self._make_quad_polydata(pts)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.25, 0.55, 0.95)
        actor.GetProperty().SetOpacity(0.16)
        actor.GetProperty().SetLighting(False)
        actor.GetProperty().SetRepresentationToSurface()
        actor.PickableOff()
        self._renderer.AddActor(actor)
        self._plane_preview_actor = actor

    def _update_roi_preview(self) -> None:
        self._clear_roi_preview()
        if not self._roi_ready:
            return

        corners_w = self._get_roi_world_points()
        if len(corners_w) != 4:
            return

        fill_poly = self._make_quad_polydata(corners_w)
        fill_mapper = vtk.vtkPolyDataMapper()
        fill_mapper.SetInputData(fill_poly)
        fill_actor = vtk.vtkActor()
        fill_actor.SetMapper(fill_mapper)
        fill_actor.GetProperty().SetColor(1.0, 0.85, 0.15)
        fill_actor.GetProperty().SetOpacity(0.10)
        fill_actor.GetProperty().SetLighting(False)
        fill_actor.PickableOff()
        self._renderer.AddActor(fill_actor)
        self._roi_fill_actor = fill_actor

        outline_poly = self._make_polyline_polydata(corners_w, closed=True)
        outline_mapper = vtk.vtkPolyDataMapper()
        outline_mapper.SetInputData(outline_poly)
        outline_actor = vtk.vtkActor()
        outline_actor.SetMapper(outline_mapper)
        outline_actor.GetProperty().SetColor(1.0, 0.9, 0.1)
        outline_actor.GetProperty().SetLineWidth(2.4)
        outline_actor.GetProperty().SetLighting(False)
        outline_actor.PickableOff()
        self._renderer.AddActor(outline_actor)
        self._roi_outline_actor = outline_actor

        #handle_radius = max(min(self._plane_size_u, self._plane_size_v) * 0.018, 0.18)
        for p in corners_w:
            src = vtk.vtkSphereSource()
            src.SetRadius(1)
            src.SetThetaResolution(18)
            src.SetPhiResolution(18)
            src.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(src.GetOutputPort())
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.SetPosition(*p)
            actor.GetProperty().SetColor(1.0, 0.35, 0.2)
            actor.GetProperty().SetLighting(False)
            actor.PickableOff()
            self._renderer.AddActor(actor)
            self._roi_handle_actors.append(actor)

    def _try_start_roi_drag(self, interactor: vtk.vtkRenderWindowInteractor) -> bool:
        x, y = interactor.GetEventPosition()
        hit_type, hit_index = self._hit_test_roi_elements(x, y)
        if hit_type is None:
            return False

        hit_w = self._display_to_plane_world(x, y)
        if hit_w is None:
            return False
        uv = self._world_to_plane_uv(hit_w)
        if uv is None:
            return False

        self._drag_start_uv = uv
        self._drag_start_bounds = (self._roi_u_min, self._roi_u_max, self._roi_v_min, self._roi_v_max)
        self._active_handle_index = hit_index
        self._drag_mode = "handle" if hit_type == "handle" else "move"
        return True

    def _hit_test_roi_elements(self, x: int, y: int) -> Tuple[Optional[str], Optional[int]]:
        if not self._roi_ready:
            return None, None

        corners_w = self._get_roi_world_points()
        if len(corners_w) != 4:
            return None, None

        # 先測 handle
        for i, p in enumerate(corners_w):
            disp = self._world_to_display(p)
            if disp is None:
                continue
            dx = disp[0] - x
            dy = disp[1] - y
            if (dx * dx + dy * dy) ** 0.5 <= self._handle_pick_radius_px:
                return "handle", i

        hit_w = self._display_to_plane_world(x, y)
        if hit_w is None:
            return None, None
        uv = self._world_to_plane_uv(hit_w)
        if uv is None:
            return None, None

        if self._roi_u_min <= uv[0] <= self._roi_u_max and self._roi_v_min <= uv[1] <= self._roi_v_max:
            return "move", None

        return None, None

    def _apply_handle_drag(
        self,
        handle_index: int,
        uv: UV2,
        start_bounds: Tuple[float, float, float, float],
    ) -> None:
        su_min, su_max, sv_min, sv_max = start_bounds
        min_size = max(min(self._plane_size_u, self._plane_size_v) * 0.03, 0.35)

        if handle_index == 0:  # bottom-left
            self._roi_u_min = min(uv[0], su_max - min_size)
            self._roi_v_min = min(uv[1], sv_max - min_size)
            self._roi_u_max = su_max
            self._roi_v_max = sv_max
        elif handle_index == 1:  # bottom-right
            self._roi_u_max = max(uv[0], su_min + min_size)
            self._roi_v_min = min(uv[1], sv_max - min_size)
            self._roi_u_min = su_min
            self._roi_v_max = sv_max
        elif handle_index == 2:  # top-right
            self._roi_u_max = max(uv[0], su_min + min_size)
            self._roi_v_max = max(uv[1], sv_min + min_size)
            self._roi_u_min = su_min
            self._roi_v_min = sv_min
        elif handle_index == 3:  # top-left
            self._roi_u_min = min(uv[0], su_max - min_size)
            self._roi_v_max = max(uv[1], sv_min + min_size)
            self._roi_u_max = su_max
            self._roi_v_min = sv_min

    def _clamp_roi_bounds(self) -> None:
        mu = self._plane_size_u * 0.5 - self._plane_size_u * self._plane_pick_margin_uv
        mv = self._plane_size_v * 0.5 - self._plane_size_v * self._plane_pick_margin_uv
        self._roi_u_min = max(self._roi_u_min, -mu)
        self._roi_u_max = min(self._roi_u_max, mu)
        self._roi_v_min = max(self._roi_v_min, -mv)
        self._roi_v_max = min(self._roi_v_max, mv)

        min_size = max(min(self._plane_size_u, self._plane_size_v) * 0.03, 0.35)
        if self._roi_u_max - self._roi_u_min < min_size:
            c = 0.5 * (self._roi_u_min + self._roi_u_max)
            self._roi_u_min = c - min_size * 0.5
            self._roi_u_max = c + min_size * 0.5
        if self._roi_v_max - self._roi_v_min < min_size:
            c = 0.5 * (self._roi_v_min + self._roi_v_max)
            self._roi_v_min = c - min_size * 0.5
            self._roi_v_max = c + min_size * 0.5

    def _get_plane_preview_corners_world(self) -> List[Vec3]:
        hu = self._plane_size_u * 0.5
        hv = self._plane_size_v * 0.5
        return [
            self._plane_uv_to_world((-hu, -hv)),
            self._plane_uv_to_world(( hu, -hv)),
            self._plane_uv_to_world(( hu,  hv)),
            self._plane_uv_to_world((-hu,  hv)),
        ]

    def _get_roi_world_points(self) -> List[Vec3]:
        if not self._roi_ready:
            return []
        return [
            self._plane_uv_to_world((self._roi_u_min, self._roi_v_min)),
            self._plane_uv_to_world((self._roi_u_max, self._roi_v_min)),
            self._plane_uv_to_world((self._roi_u_max, self._roi_v_max)),
            self._plane_uv_to_world((self._roi_u_min, self._roi_v_max)),
        ]

    def _plane_uv_to_world(self, uv: UV2) -> Vec3:
        o = self._plane_origin_w
        u = self._plane_u_w
        v = self._plane_v_w
        if o is None or u is None or v is None:
            return (0.0, 0.0, 0.0)
        return (
            o[0] + u[0] * uv[0] + v[0] * uv[1],
            o[1] + u[1] * uv[0] + v[1] * uv[1],
            o[2] + u[2] * uv[0] + v[2] * uv[1],
        )

    def _world_to_plane_uv(self, p_w: Vec3) -> Optional[UV2]:
        if self._plane_origin_w is None or self._plane_u_w is None or self._plane_v_w is None:
            return None
        rel = (
            p_w[0] - self._plane_origin_w[0],
            p_w[1] - self._plane_origin_w[1],
            p_w[2] - self._plane_origin_w[2],
        )
        return (
            rel[0] * self._plane_u_w[0] + rel[1] * self._plane_u_w[1] + rel[2] * self._plane_u_w[2],
            rel[0] * self._plane_v_w[0] + rel[1] * self._plane_v_w[1] + rel[2] * self._plane_v_w[2],
        )

    def _display_to_plane_world(self, x: int, y: int) -> Optional[Vec3]:
        if self._plane_origin_w is None or self._plane_normal_w is None:
            return None
        self._renderer.SetDisplayPoint(float(x), float(y), 0.0)
        self._renderer.DisplayToWorld()
        p0 = self._renderer.GetWorldPoint()
        self._renderer.SetDisplayPoint(float(x), float(y), 1.0)
        self._renderer.DisplayToWorld()
        p1 = self._renderer.GetWorldPoint()

        if abs(p0[3]) < 1e-12 or abs(p1[3]) < 1e-12:
            return None
        a = (p0[0] / p0[3], p0[1] / p0[3], p0[2] / p0[3])
        b = (p1[0] / p1[3], p1[1] / p1[3], p1[2] / p1[3])
        d = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        denom = d[0] * self._plane_normal_w[0] + d[1] * self._plane_normal_w[1] + d[2] * self._plane_normal_w[2]
        if abs(denom) < 1e-12:
            return None
        rel = (
            self._plane_origin_w[0] - a[0],
            self._plane_origin_w[1] - a[1],
            self._plane_origin_w[2] - a[2],
        )
        t = (rel[0] * self._plane_normal_w[0] + rel[1] * self._plane_normal_w[1] + rel[2] * self._plane_normal_w[2]) / denom
        return (a[0] + d[0] * t, a[1] + d[1] * t, a[2] + d[2] * t)

    def _world_to_display(self, p_w: Vec3) -> Optional[Tuple[float, float]]:
        self._renderer.SetWorldPoint(p_w[0], p_w[1], p_w[2], 1.0)
        self._renderer.WorldToDisplay()
        dp = self._renderer.GetDisplayPoint()
        return (float(dp[0]), float(dp[1]))

    def _clear_drag_state(self) -> None:
        self._drag_mode = None
        self._active_handle_index = None
        self._drag_start_uv = None
        self._drag_start_bounds = None

    # ---------------------------
    # Plane / marker utilities
    # ---------------------------
    def _add_marker(self, p_w: Vec3) -> None:
        src = vtk.vtkSphereSource()
        src.SetRadius(0.12)
        src.SetThetaResolution(16)
        src.SetPhiResolution(16)
        src.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetPosition(*p_w)
        actor.GetProperty().SetColor(1.0, 0.2, 0.2)
        actor.GetProperty().SetOpacity(1.0)
        actor.PickableOff()
        self._renderer.AddActor(actor)
        self._marker_actors.append(actor)
        self._render()

    def _compute_plane_from_3_points(self) -> None:
        p1, p2, p3 = self._picked_points_w[:3]
        v1 = (p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])
        v2 = (p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2])
        normal = self._cross(v1, v2)
        normal = self._normalize(normal, fallback=(0.0, 0.0, 1.0))

        norm_len = (normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]) ** 0.5
        if norm_len < 1e-8:
            print("[PlaneCutMode] plane normal too small -> reset", flush=True)
            self.clear_markers()
            return

        origin = (
            (p1[0] + p2[0] + p3[0]) / 3.0,
            (p1[1] + p2[1] + p3[1]) / 3.0,
            (p1[2] + p2[2] + p3[2]) / 3.0,
        )
        self._plane_origin_w = origin
        self._plane_normal_w = normal
        print(f"[PlaneCutMode] plane ready origin={origin}, normal={normal}", flush=True)

    def _world_plane_to_object_local(
        self,
        obj_transform: vtk.vtkTransform,
        origin_world: Vec3,
        normal_world: Vec3,
    ) -> Tuple[Vec3, Vec3]:
        inv = vtk.vtkTransform()
        inv.DeepCopy(obj_transform)
        inv.Inverse()

        o_local = inv.TransformPoint(origin_world)
        n_local = inv.TransformVector(normal_world)
        n_local = self._normalize((float(n_local[0]), float(n_local[1]), float(n_local[2])), fallback=(0.0, 0.0, 1.0))
        return (
            (float(o_local[0]), float(o_local[1]), float(o_local[2])),
            n_local,
        )

    # ---------------------------
    # Local ROI clipping
    # ---------------------------
    def _clip_polydata_local_roi(
        self,
        polydata: vtk.vtkPolyData,
        origin_local: Vec3,
        normal_local: Vec3,
        roi_points_local: List[Vec3],
        fill_cut_surface: bool = True,
    ) -> Tuple[Optional[vtk.vtkPolyData], Optional[vtk.vtkPolyData]]:
        if len(roi_points_local) < 3:
            return self._clip_polydata_two_sides(polydata, origin_local, normal_local, fill_cut_surface=fill_cut_surface)

        tri = vtk.vtkTriangleFilter()
        tri.SetInputData(polydata)
        tri.Update()
        base_poly = tri.GetOutput()

        loop_func = self._build_roi_loop_implicit(origin_local, normal_local, roi_points_local)
        if loop_func is None:
            print("[PlaneCutMode] failed to build ROI loop, fallback to full-object cut", flush=True)
            return self._clip_polydata_two_sides(polydata, origin_local, normal_local, fill_cut_surface=fill_cut_surface)

        roi_poly = self._extract_polydata_by_implicit(base_poly, loop_func, inside=True)
        non_roi_poly = self._extract_polydata_by_implicit(base_poly, loop_func, inside=False)
        if roi_poly is None or roi_poly.GetNumberOfPoints() == 0:
            print("[PlaneCutMode] ROI extraction empty, fallback to full-object cut", flush=True)
            return self._clip_polydata_two_sides(polydata, origin_local, normal_local, fill_cut_surface=fill_cut_surface)

        roi_pos, roi_neg = self._clip_polydata_two_sides(roi_poly, origin_local, normal_local, fill_cut_surface=fill_cut_surface)

        pts_pos = roi_pos.GetNumberOfPoints() if roi_pos is not None else 0
        pts_neg = roi_neg.GetNumberOfPoints() if roi_neg is not None else 0
        merge_to_pos = pts_pos >= pts_neg
        if non_roi_poly is not None and non_roi_poly.GetNumberOfPoints() > 0:
            if merge_to_pos:
                roi_pos = self._append_polydata([non_roi_poly, roi_pos])
            else:
                roi_neg = self._append_polydata([non_roi_poly, roi_neg])

        return roi_pos, roi_neg

    def _build_roi_loop_implicit(
        self,
        origin_local: Vec3,
        normal_local: Vec3,
        roi_points_local: List[Vec3],
    ) -> Optional[vtk.vtkImplicitSelectionLoop]:
        basis_u, basis_v = self._make_plane_basis(normal_local)
        projected = []
        for p in roi_points_local:
            proj = self._project_point_to_plane(p, origin_local, normal_local)
            rel = (proj[0] - origin_local[0], proj[1] - origin_local[1], proj[2] - origin_local[2])
            u = rel[0] * basis_u[0] + rel[1] * basis_u[1] + rel[2] * basis_u[2]
            v = rel[0] * basis_v[0] + rel[1] * basis_v[1] + rel[2] * basis_v[2]
            projected.append((proj, u, v))
        if len(projected) < 3:
            return None

        cu = sum(item[1] for item in projected) / len(projected)
        cv = sum(item[2] for item in projected) / len(projected)
        ordered = sorted(projected, key=lambda item: vtk.vtkMath.DegreesFromRadians(vtk.vtkMath.AngleBetweenVectors((1, 0, 0), (item[1] - cu, item[2] - cv, 0))) if abs(item[1]-cu)+abs(item[2]-cv) > 1e-12 else 0)
        # 用 atan2 重新算一次，避免上面 angleBetweenVectors 只給 0~180
        import math
        ordered = sorted(projected, key=lambda item: math.atan2(item[2] - cv, item[1] - cu))

        points = vtk.vtkPoints()
        for proj, _, _ in ordered:
            points.InsertNextPoint(*proj)
        loop = vtk.vtkImplicitSelectionLoop()
        loop.SetLoop(points)
        loop.SetAutomaticNormalGeneration(True)
        return loop

    def _extract_polydata_by_implicit(self, polydata: vtk.vtkPolyData, implicit_func, inside: bool) -> Optional[vtk.vtkPolyData]:
        extract = vtk.vtkExtractPolyDataGeometry()
        extract.SetInputData(polydata)
        extract.SetImplicitFunction(implicit_func)
        if inside:
            extract.ExtractInsideOn()
        else:
            extract.ExtractInsideOff()
        extract.ExtractBoundaryCellsOn()
        extract.Update()

        geo = vtk.vtkGeometryFilter()
        geo.SetInputConnection(extract.GetOutputPort())
        geo.Update()
        out = vtk.vtkPolyData()
        out.DeepCopy(geo.GetOutput())
        return self._post_process_polydata(out)

    def _append_polydata(self, polys: List[Optional[vtk.vtkPolyData]]) -> Optional[vtk.vtkPolyData]:
        valid = [p for p in polys if p is not None and p.GetNumberOfPoints() > 0]
        if not valid:
            return None
        if len(valid) == 1:
            out = vtk.vtkPolyData()
            out.DeepCopy(valid[0])
            return self._post_process_polydata(out)
        app = vtk.vtkAppendPolyData()
        for p in valid:
            app.AddInputData(p)
        app.Update()
        out = vtk.vtkPolyData()
        out.DeepCopy(app.GetOutput())
        return self._post_process_polydata(out)

    def _clip_polydata_two_sides(
        self,
        polydata: vtk.vtkPolyData,
        origin_local: Vec3,
        normal_local: Vec3,
        fill_cut_surface: bool = True,
    ) -> Tuple[Optional[vtk.vtkPolyData], Optional[vtk.vtkPolyData]]:
        tri = vtk.vtkTriangleFilter()
        tri.SetInputData(polydata)
        tri.Update()

        if fill_cut_surface:
            def clip_closed_with_normal(n):
                plane = vtk.vtkPlane()
                plane.SetOrigin(origin_local)
                plane.SetNormal(n)
                planes = vtk.vtkPlaneCollection()
                planes.AddItem(plane)
                clip = vtk.vtkClipClosedSurface()
                clip.SetInputData(tri.GetOutput())
                clip.SetClippingPlanes(planes)
                clip.GenerateFacesOn()
                clip.Update()
                out = vtk.vtkPolyData()
                out.DeepCopy(clip.GetOutput())
                return self._post_process_polydata(out)

            out_pos = clip_closed_with_normal(normal_local)
            out_neg = clip_closed_with_normal((-normal_local[0], -normal_local[1], -normal_local[2]))
            return out_pos, out_neg

        plane = vtk.vtkPlane()
        plane.SetOrigin(origin_local)
        plane.SetNormal(normal_local)

        clip_pos = vtk.vtkClipPolyData()
        clip_pos.SetInputData(tri.GetOutput())
        clip_pos.SetClipFunction(plane)
        clip_pos.InsideOutOn()
        clip_pos.GenerateClippedOutputOff()
        clip_pos.Update()
        out_pos = vtk.vtkPolyData()
        out_pos.DeepCopy(clip_pos.GetOutput())
        out_pos = self._post_process_polydata(out_pos)

        clip_neg = vtk.vtkClipPolyData()
        clip_neg.SetInputData(tri.GetOutput())
        clip_neg.SetClipFunction(plane)
        clip_neg.InsideOutOff()
        clip_neg.GenerateClippedOutputOff()
        clip_neg.Update()
        out_neg = vtk.vtkPolyData()
        out_neg.DeepCopy(clip_neg.GetOutput())
        out_neg = self._post_process_polydata(out_neg)
        return out_pos, out_neg

    # ---------------------------
    # Math / conversion helpers
    # ---------------------------
    def _make_plane_basis(self, normal: Vec3) -> Tuple[Vec3, Vec3]:
        n = self._normalize(normal, fallback=(0.0, 0.0, 1.0))
        ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
        u = self._normalize(self._cross(n, ref), fallback=(0.0, 1.0, 0.0))
        v = self._normalize(self._cross(n, u), fallback=(0.0, 0.0, 1.0))
        return u, v

    def _project_point_to_plane(self, point: Vec3, origin: Vec3, normal: Vec3) -> Vec3:
        rel = (point[0] - origin[0], point[1] - origin[1], point[2] - origin[2])
        d = rel[0] * normal[0] + rel[1] * normal[1] + rel[2] * normal[2]
        return (point[0] - d * normal[0], point[1] - d * normal[1], point[2] - d * normal[2])

    def _cross(self, a: Vec3, b: Vec3) -> Vec3:
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def _normalize(self, v: Vec3, fallback: Vec3 = (0.0, 0.0, 1.0)) -> Vec3:
        n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
        if n < 1e-12:
            return fallback
        return (v[0] / n, v[1] / n, v[2] / n)

    def _world_point_to_object_local(self, obj_transform: vtk.vtkTransform, point_world: Vec3) -> Vec3:
        inv = vtk.vtkTransform()
        inv.DeepCopy(obj_transform)
        inv.Inverse()
        p = inv.TransformPoint(point_world)
        return (float(p[0]), float(p[1]), float(p[2]))

    def _world_vector_to_object_local(self, obj_transform: vtk.vtkTransform, vec_world: Vec3) -> Vec3:
        inv = vtk.vtkTransform()
        inv.DeepCopy(obj_transform)
        inv.Inverse()
        v = inv.TransformVector(vec_world)
        return (float(v[0]), float(v[1]), float(v[2]))

    def _get_camera_view_up_world(self) -> Vec3:
        cam = self._renderer.GetActiveCamera()
        if cam is None:
            return (0.0, 0.0, 1.0)
        v = cam.GetViewUp()
        return self._normalize((float(v[0]), float(v[1]), float(v[2])), fallback=(0.0, 0.0, 1.0))

    # ---------------------------
    # Polydata builders / cleanup
    # ---------------------------
    def _make_quad_polydata(self, pts_w: List[Vec3]) -> vtk.vtkPolyData:
        points = vtk.vtkPoints()
        for p in pts_w:
            points.InsertNextPoint(*p)
        quad = vtk.vtkQuad()
        for i in range(4):
            quad.GetPointIds().SetId(i, i)
        cells = vtk.vtkCellArray()
        cells.InsertNextCell(quad)
        poly = vtk.vtkPolyData()
        poly.SetPoints(points)
        poly.SetPolys(cells)
        return poly

    def _make_polyline_polydata(self, pts_w: List[Vec3], closed: bool) -> vtk.vtkPolyData:
        points = vtk.vtkPoints()
        for p in pts_w:
            points.InsertNextPoint(*p)
        n = len(pts_w)
        line = vtk.vtkPolyLine()
        line.GetPointIds().SetNumberOfIds(n + 1 if closed else n)
        for i in range(n):
            line.GetPointIds().SetId(i, i)
        if closed:
            line.GetPointIds().SetId(n, 0)
        cells = vtk.vtkCellArray()
        cells.InsertNextCell(line)
        poly = vtk.vtkPolyData()
        poly.SetPoints(points)
        poly.SetLines(cells)
        return poly

    def _post_process_polydata(self, poly: Optional[vtk.vtkPolyData]) -> Optional[vtk.vtkPolyData]:
        if poly is None:
            return None
        clean = vtk.vtkCleanPolyData()
        clean.SetInputData(poly)
        clean.Update()
        tri = vtk.vtkTriangleFilter()
        tri.SetInputConnection(clean.GetOutputPort())
        tri.Update()
        out = vtk.vtkPolyData()
        out.DeepCopy(tri.GetOutput())
        return out

    # ---------------------------
    # Clear / render
    # ---------------------------
    def _clear_plane_preview(self) -> None:
        if self._plane_preview_actor is not None:
            try:
                self._renderer.RemoveActor(self._plane_preview_actor)
            except Exception:
                pass
        self._plane_preview_actor = None

    def _clear_roi_preview(self) -> None:
        for actor in self._roi_handle_actors:
            try:
                self._renderer.RemoveActor(actor)
            except Exception:
                pass
        self._roi_handle_actors.clear()

        for actor_name in ("_roi_outline_actor", "_roi_fill_actor"):
            actor = getattr(self, actor_name, None)
            if actor is not None:
                try:
                    self._renderer.RemoveActor(actor)
                except Exception:
                    pass
                setattr(self, actor_name, None)

    def _render(self) -> None:
        rw = self._renderer.GetRenderWindow()
        if rw is not None:
            rw.Render()

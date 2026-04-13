from typing import List, Optional, Tuple
import vtk
import time
from interaction.base_mode import BaseInteractionMode
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager


class TubeState:
    IDLE = "IDLE"
    ARMED = "ARMED"           # 已按選取，等第一點
    ADJUST_ANGLE = "ADJUST_ANGLE"
    ADJUST_DEPTH = "ADJUST_DEPTH"
    PLACED = "PLACED"


class TubeCutMode(BaseInteractionMode):
    """
    Tube Cut 互動模式架構：
    1. 狀態機：IDLE -> PLACING_POINT -> ADJUSTING_ANGLE -> ADJUSTING_DEPTH
    2. 互動：左鍵三階段確定位置、角度、深度；I/O 鍵微調深度
    3. 功能：碰撞偵測選取物件、布林運算切割
    """
    def __init__(
        self,
        interactor: vtk.vtkRenderWindowInteractor,
        renderer: vtk.vtkRenderer,
        prop_manager: ObjectPropertyManager,
        obj3d_manager: Object3DManager,
    ) -> None:
        super().__init__("tube_cut", interactor)
        self._renderer = renderer
        self._prop_mgr = prop_manager
        self._obj3d_mgr = obj3d_manager
        
        # ---- 狀態管理 ----
        self._state = TubeState.IDLE
        self._marking_enabled = False
        self._selecting_enabled = False
        self.on_selected = None
        self._hovering_tube = False
        self._camera_style = interactor.GetInteractorStyle()
        self._actor_style = vtk.vtkInteractorStyleTrackballActor()
        self._last_click_time = 0.0
        self._double_click_threshold = 0.5
        self._dragging_angle = False
        self._drag_sphere_radius = None
        self._hemisphere_ref_dir = None
        self._locked_style = vtk.vtkInteractorStyleUser()
        self._last_render_t = 0.0
        self._render_interval = 1.0 / 60.0
        
        # ---- 管子幾何參數 ----
        self._tube_origin = None      # 第一點：進入點
        self._tube_direction = None   # 方向向量
        self._tube_depth = 10.0       # 深度 (長度)
        self._tube_radius = 1.0       # 管子半徑
        self._depth_step = 2.0        # 每次按 I/O 改多少（可調）
        self._depth_min = 1.0
        self._depth_max = 300.0

        
        # ---- VTK Actors ----
        self._tube_actor = None  # 管子預覽 (vtkCylinderSource)       
        
        # Picker 
        self._picker = vtk.vtkCellPicker()
        self._picker.SetTolerance(0.0005)

    # ---------------------------
    # Mode 週期與基礎切換
    # ---------------------------

    def on_mode_enter(self) -> None:
        """進入模式，重置狀態"""
        print("[TubeCutMode] enter", flush=True)
        self._camera_style = self._interactor.GetInteractorStyle() 
        self.reset()

    def on_mode_exit(self) -> None:
        """離開模式，清除場景中所有預覽 Actor"""
        print("[TubeCutMode] exit", flush=True)
        self._restore_camera_style()
        self.clear_markers()
        self._marking_enabled = False
        self._selecting_enabled = False

    def reset(self) -> None:
        """重置所有參數與狀態，回到 IDLE"""
        self._state = TubeState.IDLE
        self._selecting_enabled = False

        # 清除幾何參數
        self._tube_origin = None
        self._tube_direction = None
        self._tube_depth = 50.0 # 建議給一個預設明顯的長度
        # 清除場景物件
        self.clear_markers()
        
        print("[TubeCutMode] reset to IDLE", flush=True)

    # ---------------------------
    # UI 事件處理 (按鈕呼叫)
    # ---------------------------

    def set_marking_enabled(self, enabled: bool) -> None:
        """啟用/關閉管子碰撞標記模式"""
        if self._state != TubeState.PLACED:
            print("[TubeCutMode] marking ignored: tube not placed", flush=True)
            return
        print("[TubeCutMode] marking enabled", flush=True)
        self._perform_collision_selection()

    def toggle_marking(self) -> None:
        self.set_marking_enabled(not self._marking_enabled)


    def set_selecting_enabled(self, enabled: bool) -> None:
        """啟用/關閉傳統手動選取模式"""
        self._selecting_enabled = bool(enabled)

        if self._selecting_enabled:
            self._state = TubeState.ARMED
            print("[TubeCutMode] state -> ARMED", flush=True)
        else:
            self._state = TubeState.IDLE
            print("[TubeCutMode] state -> IDLE", flush=True)

    def toggle_selecting(self) -> None:
        self.set_selecting_enabled(not self._selecting_enabled)

    def clear_markers(self) -> None:
        """清空所有點、管子預覽及選取狀態"""
        # 1. 清除管子預覽
        if self._tube_actor is not None:
            self._renderer.RemoveActor(self._tube_actor)
            self._tube_actor = None
            
        self._render()
        print("[TubeCutMode] all markers and preview cleared", flush=True)

    # ---------------------------
    # 滑鼠事件 (核心互動邏輯)
    # ---------------------------

    def on_left_button_down(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        """
        三階段點擊實作：
        1. IDLE -> 第一點 (位置)
        2. PLACING_POINT -> 第二點 (方向)
        3. ADJUSTING_ANGLE -> 第三點 (深度確定)
        """
        # -----------------------------------------
        # A) ADJUST_ANGLE / ADJUST_DEPTH：雙擊推進
        # -----------------------------------------
        if self._state in (TubeState.ADJUST_ANGLE, TubeState.ADJUST_DEPTH):
            print(f"[TubeCutMode] click state={self._state} hovering={self._hovering_tube}", flush=True)

            if self._is_double_click(interactor):
                if self._state == TubeState.ADJUST_ANGLE:
                    self._state = TubeState.ADJUST_DEPTH
                    self._dragging_angle = False
                    self._update_tube_endpoint_from_direction(render=True)
                    print("[TubeCutMode] double click -> ADJUST_DEPTH", flush=True)
                    return

                if self._state == TubeState.ADJUST_DEPTH:
                    self._state = TubeState.PLACED
                    self._dragging_angle = False
                    self._drag_sphere_radius = None
                    self._hemisphere_ref_dir = None
                    print("[TubeCutMode] double click -> PLACED", flush=True)
                    self._restore_camera_style()
                    return

            # 非雙擊：如果在 ADJUST_ANGLE 且滑鼠在管子上，開始拖曳調角度
            if self._state == TubeState.ADJUST_ANGLE and self._hovering_tube:
                self._dragging_angle = True
                self._setup_drag_plane()

                print("[TubeCutMode] start dragging angle", flush=True)

            return
        # -----------------------------------------
        # B) ARMED：點第一點，建立 tube，進入 ADJUST_ANGLE
        # -----------------------------------------
        if self._state != TubeState.ARMED:
            return

        x, y = interactor.GetEventPosition()
        ok = self._picker.Pick(x, y, 0, self._renderer)
        if ok == 0:
            return

        actor = self._picker.GetActor()
        if actor is None:
            return

        p = self._picker.GetPickPosition()
        n = self._picker.GetPickNormal()
        self._tube_origin = tuple(p)
        self._tube_direction = self._get_initial_tube_direction(
            picked_actor=actor,
            pick_position=self._tube_origin,
            pick_normal=n,
        )

        print(
            f"[TubeCutMode] origin fixed at {self._tube_origin}, "
            f"direction={self._tube_direction}",
            flush=True
        )

        self._create_tube_actor()
        self._state = TubeState.ADJUST_ANGLE
        print("[TubeCutMode] state -> ADJUST_ANGLE", flush=True)

    def _get_initial_tube_direction(self, picked_actor, pick_position, pick_normal):
        nx, ny, nz = pick_normal
        norm = (nx * nx + ny * ny + nz * nz) ** 0.5
        if norm < 1e-6:
            return (0.0, 0.0, 1.0)

        nx, ny, nz = nx / norm, ny / norm, nz / norm
        if picked_actor is not None:
            bounds = picked_actor.GetBounds()
            cx = 0.5 * (bounds[0] + bounds[1])
            cy = 0.5 * (bounds[2] + bounds[3])
            cz = 0.5 * (bounds[4] + bounds[5])

            px, py, pz = pick_position
            vx, vy, vz = (px - cx, py - cy, pz - cz)

            dot = nx * vx + ny * vy + nz * vz
            if dot < 0:
                nx, ny, nz = -nx, -ny, -nz

        return (nx, ny, nz)

    def on_left_button_up(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._dragging_angle:
            self._dragging_angle = False
            print("[TubeCutMode] stop dragging angle", flush=True)
            self._drag_plane_origin = None
            self._drag_plane_normal = None

    def on_mouse_move(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._state not in (TubeState.ADJUST_ANGLE, TubeState.ADJUST_DEPTH):
            return

        x, y = interactor.GetEventPosition()

        # --- 1) hover 偵測（用目前 picker） --
        # --- 2) 拖曳調角度：前端固定，只更新方向/尾端 ---
        if self._state == TubeState.ADJUST_ANGLE and self._dragging_angle and self._tube_origin is not None:
            P = self._screen_to_sphere_point(x, y)
            if P is None:
                return

            ox, oy, oz = self._tube_origin
            vx, vy, vz = (P[0]-ox, P[1]-oy, P[2]-oz)

            norm = (vx*vx + vy*vy + vz*vz) ** 0.5
            if norm < 1e-6:
                return

            new_dir = (vx/norm, vy/norm, vz/norm)

            # 半球限制：只允許在參考方向同側
            if self._hemisphere_ref_dir is not None:
                hx, hy, hz = self._hemisphere_ref_dir
                if (new_dir[0]*hx + new_dir[1]*hy + new_dir[2]*hz) < 0:
                    return  # 或者把 new_dir 反過來，看你希望「卡住」還是「翻到另一側」

            self._tube_direction = new_dir
            self._update_tube_endpoint_from_direction(render=False)

            now = time.time()
            if now - self._last_render_t >= self._render_interval:
                self._last_render_t = now
                self._render()
            return


        self._picker.Pick(x, y, 0, self._renderer)
        actor = self._picker.GetActor()
        hovering = (actor == self._tube_actor)

        if hovering != self._hovering_tube:
            self._hovering_tube = hovering
            if hovering:
                interactor.SetInteractorStyle(self._locked_style)  # ★鎖相機，不用 actor style
                print("[TubeCutMode] hover tube -> lock camera", flush=True)
            else:
                interactor.SetInteractorStyle(self._camera_style)
                print("[TubeCutMode] leave tube -> unlock camera", flush=True)

    # ---------------------------
    # 鍵盤事件 (I/O 微調與回溯)
    # ---------------------------

    def on_key_press(self, interactor: vtk.vtkRenderWindowInteractor, key_sym: str) -> None:
        key = (key_sym or "").lower()

        # 只在 ADJUST_DEPTH 才接受 I/O 改深度
        if self._state != TubeState.ADJUST_DEPTH:
            return

        if key not in ("i", "o"):
            return

        if self._tube_origin is None or self._tube_direction is None:
            return

        # i: deeper, o: shallower
        if key == "i":
            self._tube_depth += float(self._depth_step)
        else:
            self._tube_depth -= float(self._depth_step)

        # clamp
        if self._tube_depth < self._depth_min:
            self._tube_depth = self._depth_min
        if self._tube_depth > self._depth_max:
            self._tube_depth = self._depth_max

        # 更新尾端（方向固定，只改長度）
        self._update_tube_endpoint_from_direction(render=False)

        # render 節流（用你現成的 60fps 節流變數）
        now = time.time()
        if now - self._last_render_t >= self._render_interval:
            self._last_render_t = now
            self._render()

        print(f"[TubeCutMode] depth={self._tube_depth:.2f}", flush=True)


    # ---------------------------
    # 切割執行 (Commit)
    # ---------------------------

    def commit(self) -> Optional[Tuple[List[int], List[int], List[int]]]:
        """
        按下 Cut / Enter 觸發：
        - 將被選取（selected=True）的物件，以 tube 實體切成兩個結果：
            1) touched  : obj ∩ tube（實作上用 implicit+clip 的 heuristic 判定）
            2) untouched: obj - tube
        - 回傳 (changed_original_ids, new_result_ids, to_delete_results)

        優化重點：
        1) AABB early-out：tube_local 與 obj_local bounds 不相交就跳過（省掉 clip）
        2) spawn_actor 延後批次做，避免每個物件切完就觸發 mapper/render 造成卡頓
        3) 全程只 Render 一次
        """
        # 1) tube 必須已放置完成
        if self._state != TubeState.PLACED or self._tube_origin is None or self._tube_direction is None:
            print("[TubeCutMode] commit ignored: tube not placed", flush=True)
            return None

        # 2) 取 selected 物件
        selected_ids = list(self._prop_mgr.get_selected_objects())
        if not selected_ids:
            print("[TubeCutMode] commit ignored: no selected objects", flush=True)
            return None

        # 3) 建立 tube 的 world polydata（封閉 tube：CappingOn）
        tube_world = self._build_tube_world_polydata()
        if tube_world is None or tube_world.GetNumberOfPoints() == 0:
            print("[TubeCutMode] commit ignored: tube polydata invalid", flush=True)
            return None

        changed_original_ids: List[int] = []
        new_result_ids: List[int] = []
        to_delete_results: List[int] = []

        # 批次 spawn，減少中途 VTK pipeline 抖動
        spawn_queue: List[int] = []

        for obj_id in selected_ids:
            try:
                obj = self._prop_mgr.get_object(obj_id)
            except Exception:
                continue

            kind = getattr(obj, "kind", None)
            if kind not in ("original", "result"):
                print(f"[TubeCutMode] obj_id={obj_id} kind={kind} unsupported -> skip", flush=True)
                continue

            # actor 存在才切（避免資料/場景不同步）
            base_actor = self._obj3d_mgr.get_actor(obj_id)
            if base_actor is None:
                print(f"[TubeCutMode] obj_id={obj_id} has no actor -> skip", flush=True)
                continue

            # 取 local polydata
            obj_local = obj.polydata
            if obj_local is None or obj_local.GetNumberOfPoints() == 0:
                continue

            # tube_world -> tube_local
            tube_local = self._world_polydata_to_object_local(obj.transform, tube_world)
            if tube_local is None or tube_local.GetNumberOfPoints() == 0:
                print(f"[TubeCutMode] obj_id={obj_id} tube_local empty -> skip", flush=True)
                continue

            # ---------
            # (A) AABB early-out：local bounds 不相交就不需要做 split
            # ---------
            try:
                tb = tube_local.GetBounds()  # xmin,xmax,ymin,ymax,zmin,zmax
                ob = obj_local.GetBounds()
                if (
                    ob[1] < tb[0] or ob[0] > tb[1] or
                    ob[3] < tb[2] or ob[2] > tb[3] or
                    ob[5] < tb[4] or ob[4] > tb[5]
                ):
                    continue
            except Exception:
                # bounds 失敗就照常做
                pass

            # split：touched / untouched（你已換成 implicit+clip 版本）
            touched, untouched = self._boolean_split_by_tube(obj_local, tube_local)

            t_ok = (touched is not None and touched.GetNumberOfPoints() > 0)
            u_ok = (untouched is not None and untouched.GetNumberOfPoints() > 0)
            if not (t_ok or u_ok):
                print(f"[TubeCutMode] obj_id={obj_id} split result empty -> skip", flush=True)
                continue

            created_this_obj: List[int] = []

            if t_ok:
                rid_t = self._prop_mgr.create_result(
                    parent_id=obj_id,
                    polydata=touched,
                    name=f"{obj.name}_touched",
                    inherit_transform=True,
                )
                created_this_obj.append(rid_t)
                new_result_ids.append(rid_t)

            if u_ok:
                rid_u = self._prop_mgr.create_result(
                    parent_id=obj_id,
                    polydata=untouched,
                    name=f"{obj.name}_untouched",
                    inherit_transform=True,
                )
                created_this_obj.append(rid_u)
                new_result_ids.append(rid_u)

            # ---------
            # (B) 延後 spawn_actor：先放 queue
            # ---------
            spawn_queue.extend(created_this_obj)

            # 原本規則沿用 PlaneCutMode：
            # - original：hide + deselect
            # - result：交給 main_window 刪
            if kind == "original":
                self._prop_mgr.set_selected(obj_id, False)
                self._prop_mgr.set_visible(obj_id, False)
                self._obj3d_mgr.update_actor_appearance(obj_id)
                changed_original_ids.append(obj_id)
            else:
                to_delete_results.append(obj_id)

        # ---------
        # (C) 批次 spawn 新 actor
        # ---------
        for rid in spawn_queue:
            try:
                self._obj3d_mgr.spawn_actor(rid)
            except Exception:
                pass

        # render once
        rw = self._renderer.GetRenderWindow()
        if rw is not None:
            rw.Render()

        # 清掉 tube 預覽 + reset
        self.reset()

        print(
            f"[TubeCutMode] commit done: changed={changed_original_ids}, "
            f"new_results={new_result_ids}, delete_results={to_delete_results}",
            flush=True,
        )
        return changed_original_ids, new_result_ids, to_delete_results

    # ---------------------------
    # 內部輔助方法 (Internal Helpers)
    # ---------------------------
    def _perform_collision_selection(self) -> None:
        """
        只做一次碰撞 selection：把被 tube 碰到的物件設為 selected=True
        不處理取消選取（不需要 old_selected）
        """
        if self._tube_origin is None or self._tube_direction is None:
            print("[TubeCutMode] marking failed: tube not ready", flush=True)
            return

        new_selected: set[int] = set()

        for so in self._prop_mgr.get_all_objects():
            obj_id = so.id
            pd_local = self._prop_mgr.get_polydata(obj_id)
            if pd_local is None or pd_local.GetNumberOfPoints() == 0:
                continue

            if self._tube_hits_object_by_distance(obj_id):
                new_selected.add(obj_id)

        # 只把命中的設為 selected=True（不管原本狀態）
        for obj_id in new_selected:
            self._prop_mgr.set_selected(obj_id, True)
            self._obj3d_mgr.update_actor_appearance(obj_id)
            if callable(self.on_selected):
                kind = "original" if self._prop_mgr.is_original(obj_id) else "result"
                self.on_selected(obj_id, kind, 0)

        self._render()
        print(f"[TubeCutMode] marked = {sorted(new_selected)}", flush=True)


    def _create_tube_actor(self):
        line = vtk.vtkLineSource()
        line.SetPoint1(self._tube_origin)
        tube = vtk.vtkTubeFilter()
        tube.SetInputConnection(line.GetOutputPort())
        tube.SetRadius(float(self._tube_radius))
        tube.SetNumberOfSides(24)
        tube.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(tube.GetOutputPort())

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1, 0, 0)

        self._tube_line = line
        self._tube_actor = actor
        self._update_tube_endpoint_from_direction()

        self._renderer.AddActor(actor)
        self._render()

    def _is_double_click(self, interactor) -> bool:
        now = time.time()
        x, y = interactor.GetEventPosition()

        dt = now - self._last_click_time
        self._last_click_time = now

        ok_time = (dt <= self._double_click_threshold)

        return ok_time

    def _update_tube_endpoint_from_direction(self,render: bool = True) -> None:
        if self._tube_origin is None or self._tube_direction is None:
            return
        

        ox, oy, oz = self._tube_origin
        dx, dy, dz = self._tube_direction
        L = float(self._tube_depth)

        sign = -1.0 if self._state == TubeState.ADJUST_DEPTH else 1.0
        self._tube_line.SetPoint1(ox, oy, oz)
        self._tube_line.SetPoint2(ox + sign * dx * L, oy + sign * dy * L, oz + sign * dz * L)
        self._tube_line.Modified()
        if render:
            self._render()
    def _setup_drag_plane(self) -> None:
        """建立拖曳用平面：通過 tube_origin，法向取相機 view direction"""
        if self._tube_origin is None or self._tube_direction is None:
            return

        self._drag_sphere_radius = float(self._tube_depth)  # 半徑用目前深度
        self._hemisphere_ref_dir = self._tube_direction     # 半球參考方向（初始方向）

    def _screen_to_sphere_point(self, x: int, y: int):
        """把螢幕座標 (x,y) 射線與以 tube_origin 為球心的球相交，回傳世界座標點（取靠近相機的交點）"""
        if self._tube_origin is None or self._drag_sphere_radius is None:
            return None

        # display -> world near/far（你原本 plane 的方法沿用）
        self._renderer.SetDisplayPoint(x, y, 0.0)
        self._renderer.DisplayToWorld()
        near_w = self._renderer.GetWorldPoint()
        if abs(near_w[3]) < 1e-6:
            return None
        p0 = (near_w[0]/near_w[3], near_w[1]/near_w[3], near_w[2]/near_w[3])

        self._renderer.SetDisplayPoint(x, y, 1.0)
        self._renderer.DisplayToWorld()
        far_w = self._renderer.GetWorldPoint()
        if abs(far_w[3]) < 1e-6:
            return None
        p1 = (far_w[0]/far_w[3], far_w[1]/far_w[3], far_w[2]/far_w[3])

        # ray
        rx, ry, rz = (p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2])

        # sphere
        cx, cy, cz = self._tube_origin
        R = self._drag_sphere_radius

        # Solve |(p0 + t r) - c|^2 = R^2
        ox, oy, oz = (p0[0]-cx, p0[1]-cy, p0[2]-cz)
        a = rx*rx + ry*ry + rz*rz
        b = 2.0 * (ox*rx + oy*ry + oz*rz)
        c = ox*ox + oy*oy + oz*oz - R*R

        disc = b*b - 4*a*c
        if disc < 0:
            return None

        sqrt_disc = disc ** 0.5
        t0 = (-b - sqrt_disc) / (2*a)
        t1 = (-b + sqrt_disc) / (2*a)

        # 取比較靠近相機、且在射線正方向的解
        t = None
        for cand in (t0, t1):
            if cand >= 0 and (t is None or cand < t):
                t = cand
        if t is None:
            return None

        return (p0[0] + t*rx, p0[1] + t*ry, p0[2] + t*rz)


    def _tube_hits_object_by_distance(self, obj_id: int) -> bool:
        """
        tube 是否碰到指定物件（obj_id）

        改良策略：
        1) 使用 tube 的實際兩端點（若有 tube_line 就以 tube_line 為準）
        2) object polydata local -> world
        3) AABB early-out
        4) vtkStaticCellLocator：沿 tube 軸線取樣 N 點，對每點找最近表面距離
        min_dist <= radius  => hit
        """
        if self._tube_origin is None or self._tube_direction is None:
            return False

        # --- tube segment endpoints (world) ---
        if hasattr(self, "_tube_line") and self._tube_line is not None:
            p0 = self._tube_line.GetPoint1()
            p1 = self._tube_line.GetPoint2()
            ox, oy, oz = float(p0[0]), float(p0[1]), float(p0[2])
            ex, ey, ez = float(p1[0]), float(p1[1]), float(p1[2])
        else:
            ox, oy, oz = map(float, self._tube_origin)
            dx, dy, dz = map(float, self._tube_direction)
            depth = float(self._tube_depth)

            # 跟你目前的行為一致：ADJUST_DEPTH 期間是往 -dir
            sign = -1.0 if self._state == TubeState.ADJUST_DEPTH else 1.0
            ex = ox + sign * dx * depth
            ey = oy + sign * dy * depth
            ez = oz + sign * dz * depth

        radius = float(self._tube_radius)
        r2 = radius * radius

        # segment length
        ax, ay, az = (ex - ox, ey - oy, ez - oz)
        seg_len2 = ax * ax + ay * ay + az * az
        if seg_len2 < 1e-10:
            return False

        # --- get world polydata ---
        pd_local = self._prop_mgr.get_polydata(obj_id)
        if pd_local is None or pd_local.GetNumberOfPoints() == 0:
            return False

        obj = self._prop_mgr.get_object(obj_id)

        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetInputData(pd_local)
        tf.SetTransform(obj.transform)
        tf.Update()

        pd_world = tf.GetOutput()
        if pd_world is None or pd_world.GetNumberOfPoints() == 0:
            return False

        # --- AABB early-out ---
        tube_min = (min(ox, ex) - radius, min(oy, ey) - radius, min(oz, ez) - radius)
        tube_max = (max(ox, ex) + radius, max(oy, ey) + radius, max(oz, ez) + radius)

        b = pd_world.GetBounds()  # (xmin,xmax,ymin,ymax,zmin,zmax)
        if (
            b[1] < tube_min[0] or b[0] > tube_max[0] or
            b[3] < tube_min[1] or b[2] > tube_max[1] or
            b[5] < tube_min[2] or b[4] > tube_max[2]
        ):
            return False

        # --- build locator (surface closest point queries) ---
        # StaticCellLocator 通常比 CellLocator 更快更穩（適合多次 FindClosestPoint）
        locator = vtk.vtkStaticCellLocator()
        locator.SetDataSet(pd_world)
        locator.BuildLocator()

        # --- sampling along segment ---
        seg_len = (seg_len2) ** 0.5

        # 取樣密度：半徑越小越需要密一點；但也要限制上限避免卡
        # 這裡用 radius*0.5 當步距基準，並 clamp 在 [8, 200]
        step_len = max(0.5, radius * 0.5)
        n_samples = int(seg_len / step_len) + 1
        if n_samples < 8:
            n_samples = 8
        if n_samples > 200:
            n_samples = 200

        # FindClosestPoint 的輸出容器
        closest = [0.0, 0.0, 0.0]
        cell_id = vtk.reference(0)
        sub_id = vtk.reference(0)
        dist2 = vtk.reference(0.0)

        # 逐點查最近距離
        for i in range(n_samples):
            t = 0.0 if n_samples == 1 else (i / (n_samples - 1))
            px = ox + ax * t
            py = oy + ay * t
            pz = oz + az * t

            locator.FindClosestPoint([px, py, pz], closest, cell_id, sub_id, dist2)
            if float(dist2) <= r2:
                return True

        return False
    def _build_tube_world_polydata(self) -> Optional[vtk.vtkPolyData]:
        """用目前 tube_line 建立封閉 tube 的 world polydata（給 boolean 用）。"""
        if not hasattr(self, "_tube_line") or self._tube_line is None:
            return None

        tube = vtk.vtkTubeFilter()
        tube.SetInputConnection(self._tube_line.GetOutputPort())
        tube.SetRadius(float(self._tube_radius))
        tube.SetNumberOfSides(24)
        tube.CappingOn()
        tube.Update()

        # 清理 + 三角化，boolean 會穩一點
        clean = vtk.vtkCleanPolyData()
        clean.SetInputConnection(tube.GetOutputPort())
        clean.Update()

        tri = vtk.vtkTriangleFilter()
        tri.SetInputConnection(clean.GetOutputPort())
        tri.Update()

        out = vtk.vtkPolyData()
        out.DeepCopy(tri.GetOutput())
        return out


    def _world_polydata_to_object_local(self, obj_transform: vtk.vtkTransform, poly_world: vtk.vtkPolyData) -> vtk.vtkPolyData:
        """把 world polydata 轉到某個 obj 的 local（用 obj_transform 的 inverse）。"""
        inv = vtk.vtkTransform()
        inv.DeepCopy(obj_transform)
        inv.Inverse()

        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetInputData(poly_world)
        tf.SetTransform(inv)
        tf.Update()

        out = vtk.vtkPolyData()
        out.DeepCopy(tf.GetOutput())
        return out


    def _boolean_split_by_tube(
        self,
        obj_local: vtk.vtkPolyData,
        tube_local: vtk.vtkPolyData,
    ) -> Tuple[Optional[vtk.vtkPolyData], Optional[vtk.vtkPolyData]]:
        """
        用 implicit distance + clip 取代 boolean：
        - touched   = inside tube 的那一半
        - untouched = outside tube 的那一半

        這版不再用 bounds overlap heuristic（那個會猜錯），
        改用 vtkImplicitPolyDataDistance.EvaluateFunction() 直接判斷 inside/outside，
        因此 touched/untouched 不會再相反。
        """

        if obj_local is None or obj_local.GetNumberOfPoints() == 0:
            return None, None
        if tube_local is None or tube_local.GetNumberOfPoints() == 0:
            return None, None

        # 1) 三角化（clip 對三角網格較穩）
        tri_obj = vtk.vtkTriangleFilter()
        tri_obj.SetInputData(obj_local)
        tri_obj.Update()

        tri_tube = vtk.vtkTriangleFilter()
        tri_tube.SetInputData(tube_local)
        tri_tube.Update()

        obj_tri = tri_obj.GetOutput()
        tube_tri = tri_tube.GetOutput()

        if obj_tri is None or obj_tri.GetNumberOfPoints() == 0:
            return None, None
        if tube_tri is None or tube_tri.GetNumberOfPoints() == 0:
            return None, None

        # 2) implicit function：tube 表面距離（signed-ish）
        implicit = vtk.vtkImplicitPolyDataDistance()
        implicit.SetInput(tube_tri)

        # 3) Clip：同時產出 Output + ClippedOutput
        clip = vtk.vtkClipPolyData()
        clip.SetInputData(obj_tri)
        clip.SetClipFunction(implicit)
        clip.GenerateClippedOutputOn()
        clip.SetValue(0.0)
        clip.Update()

        part_a = vtk.vtkPolyData()
        part_a.DeepCopy(clip.GetOutput())

        part_b = vtk.vtkPolyData()
        part_b.DeepCopy(clip.GetClippedOutput())

        # 4) 後處理（clean）
        def post(pd: vtk.vtkPolyData) -> Optional[vtk.vtkPolyData]:
            if pd is None or pd.GetNumberOfPoints() == 0:
                return None
            clean = vtk.vtkCleanPolyData()
            clean.SetInputData(pd)
            clean.Update()
            out = vtk.vtkPolyData()
            out.DeepCopy(clean.GetOutput())
            if out.GetNumberOfPoints() == 0:
                return None
            return out

        a_ok = post(part_a)
        b_ok = post(part_b)

        # 如果其中一邊空，就直接回傳（另一邊 touched/untouched 由 inside 判斷）
        # 但如果只剩一邊，我們仍可判斷它是 inside 還 outside。
        def classify(pd: Optional[vtk.vtkPolyData]) -> Optional[bool]:
            """
            回傳：
            - True  => inside tube
            - False => outside tube
            - None  => 無法判斷（pd None/空）
            """
            if pd is None or pd.GetNumberOfPoints() == 0:
                return None

            # 用多點投票，避免剛好取到邊界點造成不穩
            n = pd.GetNumberOfPoints()
            if n <= 0:
                return None

            # 取樣點數（最多 20 個），均勻抽樣 index
            sample_n = min(20, n)
            inside_votes = 0
            outside_votes = 0

            # 為了不用 random（可重現），用等距 index
            for i in range(sample_n):
                idx = int(i * (n - 1) / max(1, sample_n - 1))
                p = pd.GetPoint(idx)
                d = float(implicit.EvaluateFunction(p))
                # d <= 0 通常代表 inside，但符號方向可能因 tube 法向反轉而顛倒
                # 所以我們只先記票，最後用「哪個比較一致」來決定
                if d <= 0.0:
                    inside_votes += 1
                else:
                    outside_votes += 1

            # 多數決
            return inside_votes >= outside_votes

        a_inside = classify(a_ok)
        b_inside = classify(b_ok)

        # 5) 決定 touched/untouched
        # touched = inside 的那一半
        touched: Optional[vtk.vtkPolyData] = None
        untouched: Optional[vtk.vtkPolyData] = None

        if a_ok is None and b_ok is None:
            return None, None

        if a_ok is not None and b_ok is None:
            # 只有 A：看它 inside or outside
            if a_inside is True:
                touched, untouched = a_ok, None
            else:
                touched, untouched = None, a_ok
            return touched, untouched

        if a_ok is None and b_ok is not None:
            # 只有 B
            if b_inside is True:
                touched, untouched = b_ok, None
            else:
                touched, untouched = None, b_ok
            return touched, untouched

        # 兩邊都存在
        # 理論上應該一邊 inside 一邊 outside；若兩邊判斷結果一樣，代表 implicit 符號方向顛倒或幾何太貼邊
        # 這時候用「離 tube 表面距離的絕對值平均」來輔助：更接近 tube 的那份當 touched
        if a_inside is True and b_inside is False:
            touched, untouched = a_ok, b_ok
        elif a_inside is False and b_inside is True:
            touched, untouched = b_ok, a_ok
        else:
            # fallback：算平均 |distance|，較小者通常是 tube 內/靠近 tube 的切片（當 touched）
            def avg_abs_dist(pd: vtk.vtkPolyData) -> float:
                n = pd.GetNumberOfPoints()
                if n <= 0:
                    return 1e18
                sample_n = min(30, n)
                acc = 0.0
                for i in range(sample_n):
                    idx = int(i * (n - 1) / max(1, sample_n - 1))
                    p = pd.GetPoint(idx)
                    acc += abs(float(implicit.EvaluateFunction(p)))
                return acc / float(sample_n)

            da = avg_abs_dist(a_ok)
            db = avg_abs_dist(b_ok)
            if da <= db:
                touched, untouched = a_ok, b_ok
            else:
                touched, untouched = b_ok, a_ok

        return touched, untouched



    def _restore_camera_style(self) -> None:
        """確保離開 tube 操作時，相機互動恢復，避免影響其他 mode。"""
        try:
            self._interactor.SetInteractorStyle(self._camera_style)
        except Exception:
            pass
        self._hovering_tube = False
        self._dragging_angle = False

    def _render(self) -> None:
        """觸發 renderer 刷新"""
        rw = self._renderer.GetRenderWindow()
        if rw:
            rw.Render()

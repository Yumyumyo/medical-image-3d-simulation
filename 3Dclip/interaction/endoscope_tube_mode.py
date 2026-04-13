import time
from typing import Optional
import vtk

from interaction.tube_cut_mode import TubeCutMode, TubeState


class EndoscopeTubeMode(TubeCutMode):
    """
    內視鏡用的「管子放置」模式：
    - 沿用 TubeCutMode 的互動
    - 支援中心模式（半內半外）
    - 提供 on_tube_updated callback
    """
    def __init__(
        self,
        interactor: vtk.vtkRenderWindowInteractor,
        renderer: vtk.vtkRenderer,
        prop_manager,
        obj3d_manager,
    ) -> None:
        super().__init__(interactor, renderer, prop_manager, obj3d_manager)
        self.name = "endoscope_tube"
        self.on_tube_updated = None
        self._initial_centered = True
        self._depth_step = 5.0
        self._drag_last_xy = None
        self._drag_rotate_sensitivity = 0.35

    def clear_markers(self) -> None:
        super().clear_markers()
        if callable(self.on_tube_updated):
            self.on_tube_updated(None, None)

    def set_depth(self, depth: float) -> None:
        self._tube_depth = float(depth)
        if self._tube_depth < self._depth_min:
            self._tube_depth = self._depth_min
        if self._tube_depth > self._depth_max:
            self._tube_depth = self._depth_max
        self._update_tube_endpoint_from_direction(render=True)

    def adjust_depth(self, delta: float) -> None:
        self.set_depth(self._tube_depth + float(delta))

    def get_depth_step(self) -> float:
        return float(self._depth_step)

    def has_tube(self) -> bool:
        return self._tube_line is not None

    def set_initial_centered(self, enabled: bool) -> None:
        self._initial_centered = bool(enabled)
        self._update_tube_endpoint_from_direction(render=True)

    def clear_markers(self) -> None:
        super().clear_markers()
        if callable(self.on_tube_updated):
            self.on_tube_updated(None, None)

    def _update_tube_endpoint_from_direction(self, render: bool = True) -> None:
        if self._tube_origin is None or self._tube_direction is None:
            return

        ox, oy, oz = self._tube_origin
        dx, dy, dz = self._tube_direction
        L = float(self._tube_depth)

        half = L * 0.5
        self._tube_line.SetPoint1(ox + dx * half, oy + dy * half, oz + dz * half)
        self._tube_line.SetPoint2(ox - dx * half, oy - dy * half, oz - dz * half)

        self._tube_line.Modified()
        if render:
            self._render()

        if callable(self.on_tube_updated) and self._tube_line is not None:
            self.on_tube_updated(self._tube_line.GetPoint1(), self._tube_line.GetPoint2())

    def _setup_drag_plane(self) -> None:
        if self._tube_origin is None or self._tube_direction is None:
            return
        self._drag_sphere_radius = float(self._tube_depth)
        self._hemisphere_ref_dir = self._tube_direction

    def _get_initial_tube_direction(self, picked_actor, pick_position, pick_normal):
        camera = self._renderer.GetActiveCamera()
        if camera is None:
            return super()._get_initial_tube_direction(picked_actor, pick_position, pick_normal)

        dx, dy, dz = camera.GetDirectionOfProjection()
        norm = (dx * dx + dy * dy + dz * dz) ** 0.5
        if norm < 1e-6:
            return super()._get_initial_tube_direction(picked_actor, pick_position, pick_normal)

        # Follow the current viewing ray so the scope extends straight inward
        # from the clicked point instead of tilting toward the surface normal.
        return (dx / norm, dy / norm, dz / norm)

    def get_tube_actor(self) -> Optional[vtk.vtkActor]:
        return self._tube_actor

    def on_left_button_down(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._can_drag_existing_tube():
            if self._is_double_click(interactor):
                return
            if not self._is_mouse_over_tube(interactor):
                return
            self._hovering_tube = True
            self._dragging_angle = True
            self._drag_last_xy = interactor.GetEventPosition()
            interactor.SetInteractorStyle(self._locked_style)
            return
        super().on_left_button_down(interactor)

    def on_left_button_up(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        self._drag_last_xy = None
        super().on_left_button_up(interactor)

    def on_mouse_move(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._can_drag_existing_tube():
            if self._dragging_angle and self._tube_origin is not None:
                self._rotate_tube_from_mouse_drag(interactor)
                return

            hovering = self._is_mouse_over_tube(interactor)
            if hovering != self._hovering_tube:
                self._hovering_tube = hovering
                if hovering:
                    interactor.SetInteractorStyle(self._locked_style)
                else:
                    interactor.SetInteractorStyle(self._camera_style)
            return

        super().on_mouse_move(interactor)

    def _can_drag_existing_tube(self) -> bool:
        return (
            self._tube_actor is not None
            and self._tube_origin is not None
            and self._tube_direction is not None
            and self._state in (TubeState.ADJUST_DEPTH, TubeState.PLACED)
        )

    def _is_mouse_over_tube(self, interactor: vtk.vtkRenderWindowInteractor) -> bool:
        if self._tube_actor is None:
            return False

        x, y = interactor.GetEventPosition()
        self._picker.Pick(x, y, 0, self._renderer)
        if self._picker.GetActor() == self._tube_actor:
            return True

        if not hasattr(self, "_tube_line") or self._tube_line is None:
            return False

        p0 = self._tube_line.GetPoint1()
        p1 = self._tube_line.GetPoint2()
        d0 = self._world_to_display(p0)
        d1 = self._world_to_display(p1)
        if d0 is None or d1 is None:
            return False

        distance_px = self._distance_point_to_segment_2d(
            float(x), float(y),
            float(d0[0]), float(d0[1]),
            float(d1[0]), float(d1[1]),
        )
        return distance_px <= 12.0

    def _rotate_tube_from_mouse_drag(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        if self._tube_direction is None:
            return

        current_xy = interactor.GetEventPosition()
        if self._drag_last_xy is None:
            self._drag_last_xy = current_xy
            return

        last_x, last_y = self._drag_last_xy
        x, y = current_xy
        dx = x - last_x
        dy = y - last_y
        self._drag_last_xy = current_xy

        if dx == 0 and dy == 0:
            return

        camera = self._renderer.GetActiveCamera()
        if camera is None:
            return

        dop = self._normalize_vec(camera.GetDirectionOfProjection())
        view_up = self._normalize_vec(camera.GetViewUp())
        if dop is None:
            dop = self._normalize_vec(self._tube_direction)
        if dop is None:
            return

        right_axis = self._normalize_vec(self._cross(dop, view_up)) if view_up is not None else None
        if right_axis is None:
            # Initial endoscope setup can align view-up with the viewing ray,
            # which makes the screen-right axis collapse to zero until the user
            # rotates the outer camera once. Fall back to a stable world axis.
            fallback_up = (0.0, 1.0, 0.0)
            if abs(dop[0] * fallback_up[0] + dop[1] * fallback_up[1] + dop[2] * fallback_up[2]) > 0.95:
                fallback_up = (1.0, 0.0, 0.0)
            right_axis = self._normalize_vec(self._cross(dop, fallback_up))
            if right_axis is None:
                return
            view_up = self._normalize_vec(self._cross(right_axis, dop))
        elif view_up is None:
            view_up = self._normalize_vec(self._cross(right_axis, dop))

        if view_up is None:
            return

        yaw = dx * self._drag_rotate_sensitivity
        pitch = -dy * self._drag_rotate_sensitivity

        transform = vtk.vtkTransform()
        transform.PostMultiply()
        transform.RotateWXYZ(yaw, view_up[0], view_up[1], view_up[2])
        transform.RotateWXYZ(pitch, right_axis[0], right_axis[1], right_axis[2])
        new_dir = transform.TransformVector(*self._tube_direction)

        norm = (new_dir[0] ** 2 + new_dir[1] ** 2 + new_dir[2] ** 2) ** 0.5
        if norm < 1e-6:
            return

        self._tube_direction = (new_dir[0] / norm, new_dir[1] / norm, new_dir[2] / norm)
        self._update_tube_endpoint_from_direction(render=False)

        now = time.time()
        if now - self._last_render_t >= self._render_interval:
            self._last_render_t = now
            self._render()

    def _normalize_vec(self, vec) -> Optional[tuple]:
        if vec is None:
            return None
        x, y, z = float(vec[0]), float(vec[1]), float(vec[2])
        norm = (x * x + y * y + z * z) ** 0.5
        if norm < 1e-6:
            return None
        return (x / norm, y / norm, z / norm)

    def _cross(self, a, b) -> tuple:
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def _world_to_display(self, world_point) -> Optional[tuple]:
        self._renderer.SetWorldPoint(float(world_point[0]), float(world_point[1]), float(world_point[2]), 1.0)
        self._renderer.WorldToDisplay()
        display_point = self._renderer.GetDisplayPoint()
        if display_point is None:
            return None
        return (display_point[0], display_point[1], display_point[2])

    def _distance_point_to_segment_2d(
        self,
        px: float,
        py: float,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> float:
        vx = x1 - x0
        vy = y1 - y0
        wx = px - x0
        wy = py - y0

        seg_len2 = vx * vx + vy * vy
        if seg_len2 <= 1e-6:
            dx = px - x0
            dy = py - y0
            return (dx * dx + dy * dy) ** 0.5

        t = (wx * vx + wy * vy) / seg_len2
        t = max(0.0, min(1.0, t))
        cx = x0 + t * vx
        cy = y0 + t * vy
        dx = px - cx
        dy = py - cy
        return (dx * dx + dy * dy) ** 0.5

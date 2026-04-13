# preview_slice_manager.py
import vtk

class PreviewSliceManager:
    """
    用於 3D 預覽切片平面：
    - 顯示可拖曳/旋轉的平面
    - 對場景中的物件做即時 clipping
    """

    def __init__(self, scene3D_manager, obj_manager, vtk_widget=None, main_window=None):
        self.scene = scene3D_manager
        self.obj_manager = obj_manager
        self.vtkWidget = vtk_widget
        self.main_window = main_window

        self.ren = self.scene.renderer
        self.iren = self.scene.iren

        self.current_normal = [0, 0, -1]

        # preview plane
        self.plane_source = None
        self.plane_actor = None

        # clipping
        self.clip_plane = vtk.vtkPlane()

        # interaction
        self.drag_mode = None
        self.last_mouse_pos = None
        self.plane_locked = False
        self.left_button_down = False
        self.right_button_down = False
        self.camera_left_dragging = False
        self.camera_right_dragging = False
        self.style_before_plane_drag = None

        # picker (只用於點擊判斷)
        self.plane_picker = vtk.vtkCellPicker()
        self.plane_picker.SetTolerance(0.005)
        self.plane_picker.PickFromListOn()

        # observers
        self._obs_mouse_move = None
        self._obs_left_press = None
        self._obs_left_release = None
        self._obs_right_press = None
        self._obs_right_release = None
        self._obs_wheel_forward = None
        self._obs_wheel_backward = None
        self._slice_visibility_snapshot = None

    # ==========================================================
    # Public API
    # ==========================================================
    def enter(self):
        self._hide_original_slices()

        if self.plane_actor:
            self.ren.AddActor(self.plane_actor)
            self._setup_plane_picker()
            self._bind_events()
            self._apply_clipping()
            self.scene.renderer.GetRenderWindow().Render()
            return

        self._create_preview_plane()
        self._setup_plane_picker()
        self._bind_events()
        self._apply_clipping()
        self.scene.renderer.GetRenderWindow().Render()

    def exit(self):
        self._unbind_events()
        self.drag_mode = None
        self.last_mouse_pos = None
        self.left_button_down = False
        self.right_button_down = False
        self.camera_left_dragging = False
        self.camera_right_dragging = False
        if self.style_before_plane_drag:
            self.iren.SetInteractorStyle(self.style_before_plane_drag)
            self.style_before_plane_drag = None

        if self.plane_actor:
            self.ren.RemoveActor(self.plane_actor)

        self._clear_clipping()
        self._show_original_slices()

        self.scene.renderer.GetRenderWindow().Render()

    # ==========================================================
    # Plane Creation
    # ==========================================================
    def _create_preview_plane(self):
        bounds = [0]*6
        self.ren.ComputeVisiblePropBounds(bounds)
        xmin,xmax,ymin,ymax,zmin,zmax = bounds

        center = [(xmin+xmax)/2, (ymin+ymax)/2, (zmin+zmax)/2]
        size = max(xmax-xmin, ymax-ymin, zmax-zmin) * 1.2

        self.plane_source = vtk.vtkPlaneSource()
        self.plane_source.SetOrigin(center[0]-size/2, center[1]-size/2, center[2])
        self.plane_source.SetPoint1(center[0]+size/2, center[1]-size/2, center[2])
        self.plane_source.SetPoint2(center[0]-size/2, center[1]+size/2, center[2])
        self.plane_source.SetCenter(center)
        self.plane_source.SetNormal(self.current_normal)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self.plane_source.GetOutputPort())

        self.plane_actor = vtk.vtkActor()
        self.plane_actor.SetMapper(mapper)
        self.plane_actor.PickableOn()
        self.plane_actor.GetProperty().SetColor(0.9, 0.9, 0.9)
        self.plane_actor.GetProperty().SetOpacity(0.35)
        self.plane_actor.GetProperty().SetLighting(False)

        self.ren.AddActor(self.plane_actor)

        self.clip_plane.SetOrigin(center)
        self.clip_plane.SetNormal(self.current_normal)

    def _setup_plane_picker(self):
        self.plane_picker.InitializePickList()
        if self.plane_actor:
            self.plane_picker.AddPickList(self.plane_actor)

    # ==========================================================
    # Events
    # ==========================================================
    def _bind_events(self):
        self._obs_mouse_move = self.iren.AddObserver("MouseMoveEvent", self._on_mouse_move)
        self._obs_left_press = self.iren.AddObserver("LeftButtonPressEvent", self._on_left_button_press)
        self._obs_left_release = self.iren.AddObserver("LeftButtonReleaseEvent", self._on_left_button_release)
        self._obs_right_press = self.iren.AddObserver("RightButtonPressEvent", self._on_right_button_press)
        self._obs_right_release = self.iren.AddObserver("RightButtonReleaseEvent", self._on_right_button_release)
        self._obs_wheel_forward = self.iren.AddObserver("MouseWheelForwardEvent", self._on_mouse_wheel_forward)
        self._obs_wheel_backward = self.iren.AddObserver("MouseWheelBackwardEvent", self._on_mouse_wheel_backward)

    def _unbind_events(self):
        for obs in [self._obs_mouse_move, self._obs_left_press, self._obs_left_release,
                    self._obs_right_press, self._obs_right_release,
                    self._obs_wheel_forward, self._obs_wheel_backward]:
            if obs:
                self.iren.RemoveObserver(obs)

    # ==========================================================
    # Interaction: mouse events
    # ==========================================================
    def _on_left_button_press(self,obj,evt):
        self.left_button_down = True
        x,y = self.iren.GetEventPosition()
        if self._is_click_on_plane(x,y) and not self.plane_locked:
            if self.iren.GetAltKey() or self.iren.GetControlKey():
                self.drag_mode = "rotate"
            else:
                self.drag_mode = "move"
            self.last_mouse_pos = (x,y)
            if not self.style_before_plane_drag:
                self.style_before_plane_drag = self.iren.GetInteractorStyle()
                self.iren.SetInteractorStyle(vtk.vtkInteractorStyleUser())
            return
        self.drag_mode = None
        self.camera_left_dragging = True
        self.iren.GetInteractorStyle().OnLeftButtonDown()

    def _on_left_button_release(self,obj,evt):
        self.left_button_down = False
        if self.drag_mode in ("move","rotate"):
            self.drag_mode = None
            self.last_mouse_pos = None
            if self.style_before_plane_drag:
                self.iren.SetInteractorStyle(self.style_before_plane_drag)
                self.style_before_plane_drag = None
        if self.camera_left_dragging:
            self.camera_left_dragging = False
            self.iren.GetInteractorStyle().OnLeftButtonUp()

    def _on_right_button_press(self,obj,evt):
        self.right_button_down = True
        self.camera_right_dragging = True
        self.iren.GetInteractorStyle().OnRightButtonDown()

    def _on_right_button_release(self,obj,evt):
        self.right_button_down = False
        if self.camera_right_dragging:
            self.camera_right_dragging = False
            self.iren.GetInteractorStyle().OnRightButtonUp()

    def _on_mouse_move(self,obj,evt):
        if self.drag_mode in ("move","rotate"):
            if not self.left_button_down:
                self.drag_mode = None
                self.last_mouse_pos = None
                if self.style_before_plane_drag:
                    self.iren.SetInteractorStyle(self.style_before_plane_drag)
                    self.style_before_plane_drag = None
                return
            x,y = self.iren.GetEventPosition()
            if self.drag_mode=="move":
                self._move_plane()
            else:
                if not self.last_mouse_pos:
                    return
                dx = x - self.last_mouse_pos[0]
                dy = y - self.last_mouse_pos[1]
                self._rotate_plane_normal(dx,dy)
            self.last_mouse_pos = self.iren.GetEventPosition()
            self._apply_clipping()
            self.scene.renderer.GetRenderWindow().Render()
            return
        if self.camera_left_dragging or self.camera_right_dragging:
            self.iren.GetInteractorStyle().OnMouseMove()
            return

    # ==========================================================
    # Plane movement / rotation / wheel
    # ==========================================================
    def _move_plane(self):
        x,y = self.iren.GetEventPosition()
        if not self.last_mouse_pos:
            return
        last_x,last_y = self.last_mouse_pos
        dx = x - last_x
        dy = y - last_y

        camera = self.ren.GetActiveCamera()
        view_up = list(camera.GetViewUp())
        direction = list(camera.GetDirectionOfProjection())

        right = [0,0,0]
        vtk.vtkMath.Cross(direction,view_up,right)
        vtk.vtkMath.Normalize(view_up)
        vtk.vtkMath.Normalize(right)

        lateral_sensitivity = 0.5
        vertical_sensitivity = 0.5
        depth_sensitivity = 0.5

        center = list(self.plane_source.GetCenter())
        normal = list(self.current_normal)

        if self.iren.GetShiftKey():
            new_center = [center[i]-dy*depth_sensitivity*normal[i] for i in range(3)]
        else:
            new_center = [center[i]+dx*lateral_sensitivity*right[i]+dy*vertical_sensitivity*view_up[i] for i in range(3)]

        self.plane_source.SetCenter(new_center)
        self.clip_plane.SetOrigin(new_center)

    def _rotate_plane_normal(self,dx,dy):
        camera=self.ren.GetActiveCamera()
        normal=list(self.clip_plane.GetNormal())
        view_up=list(camera.GetViewUp())
        direction=list(camera.GetDirectionOfProjection())

        right=[view_up[1]*direction[2]-view_up[2]*direction[1],
               view_up[2]*direction[0]-view_up[0]*direction[2],
               view_up[0]*direction[1]-view_up[1]*direction[0]]

        vtk.vtkMath.Normalize(view_up)
        vtk.vtkMath.Normalize(right)

        sensitivity=0.3
        transform=vtk.vtkTransform()
        transform.PostMultiply()
        transform.RotateWXYZ(dx*sensitivity,*view_up)
        transform.RotateWXYZ(dy*sensitivity,*right)

        new_normal=list(transform.TransformNormal(normal))
        vtk.vtkMath.Normalize(new_normal)

        self.current_normal=new_normal
        self.clip_plane.SetNormal(new_normal)
        self.plane_source.SetNormal(new_normal)

    def _on_mouse_wheel_forward(self,obj,evt):
        self._slice_move(2)
    def _on_mouse_wheel_backward(self,obj,evt):
        self._slice_move(-2)
    def _slice_move(self,step):
        normal=self.current_normal
        center=list(self.plane_source.GetCenter())
        new_center=[center[i]+normal[i]*step for i in range(3)]
        self.plane_source.SetCenter(new_center)
        self.clip_plane.SetOrigin(new_center)
        self._apply_clipping()
        self.scene.renderer.GetRenderWindow().Render()

    # ==========================================================
    # Clipping
    # ==========================================================
    def _apply_clipping(self):
        if self._is_plane_outside_all_original_actors():
            self.obj_manager.clear_preview_clipping()
            return

        plane=vtk.vtkPlane()
        plane.SetOrigin(self.clip_plane.GetOrigin())
        plane.SetNormal(self.current_normal)
        self.obj_manager.apply_preview_clipping(plane)

    def _clear_clipping(self):
        self.obj_manager.clear_preview_clipping()

    def _is_plane_outside_all_original_actors(self):
        if not self.plane_source:
            return False

        origin = self.plane_source.GetOrigin()
        p1 = self.plane_source.GetPoint1()
        p2 = self.plane_source.GetPoint2()
        p3 = (p1[0]+p2[0]-origin[0], p1[1]+p2[1]-origin[1], p1[2]+p2[2]-origin[2])
        plane_pts = (origin,p1,p2,p3)
        plane_min_x = min(p[0] for p in plane_pts)
        plane_max_x = max(p[0] for p in plane_pts)
        plane_min_y = min(p[1] for p in plane_pts)
        plane_max_y = max(p[1] for p in plane_pts)
        plane_min_z = min(p[2] for p in plane_pts)
        plane_max_z = max(p[2] for p in plane_pts)
        eps=1e-3

        for actor in self.obj_manager.all_actors():
            b = actor.GetBounds()
            if not b:
                continue
            if b[0]>b[1] or b[2]>b[3] or b[4]>b[5]:
                continue
            overlap_x = not (plane_max_x<b[0]-eps or plane_min_x>b[1]+eps)
            overlap_y = not (plane_max_y<b[2]-eps or plane_min_y>b[3]+eps)
            overlap_z = not (plane_max_z<b[4]-eps or plane_min_z>b[5]+eps)
            if overlap_x and overlap_y and overlap_z:
                return False
        return True

    # ==========================================================
    # Utils
    # ==========================================================
    def _is_click_on_plane(self,x,y):
        if not self.plane_actor:
            return False
        hit = self.plane_picker.Pick(x,y,0,self.ren)
        if hit <= 0:
            return False
        return self.plane_picker.GetActor() == self.plane_actor

    def _hide_original_slices(self):
        self._slice_visibility_snapshot = self.scene.get_slice_visibility()
        for s in self.scene.get_slice_actors().values():
            s.SetVisibility(0)
        self._sync_slice_checkboxes({
            "sagittal": False,
            "coronal": False,
            "axial": False,
        })

    def _show_original_slices(self):
        visibility = self._slice_visibility_snapshot or {
            "sagittal": True,
            "coronal": True,
            "axial": True,
        }
        for name, visible in visibility.items():
            self.scene.set_slice_visibility(name, visible)
        self._sync_slice_checkboxes(visibility)
        self._slice_visibility_snapshot = None

    def _sync_slice_checkboxes(self, visibility):
        if not self.main_window or not hasattr(self.main_window, "_set_slice_checkbox_states"):
            return
        self.main_window._set_slice_checkbox_states(visibility)

import vtk
import numpy as np

class EndoscopeCamera:
    def __init__(self, renderer):
        self.renderer = renderer
        self.original_camera = None
        self.fov = 60
        self.min_distance = 0.1  # 非常小的最小距離，允許進入物體內部
        self.max_distance = 10000.0  # 非常大的最大距離
        
    def save_original_camera(self):
        """保存原始相機狀態"""
        cam = self.renderer.GetActiveCamera()
        self.original_camera = {
            'position': cam.GetPosition(),
            'focal_point': cam.GetFocalPoint(),
            'view_up': cam.GetViewUp(),
            'view_angle': cam.GetViewAngle()
        }
        print(f"[EndoscopeCamera] 保存原始相機位置: {self.original_camera['position']}")
        
    def restore_original_camera(self):
        """恢復原始相機狀態"""
        if self.original_camera:
            cam = self.renderer.GetActiveCamera()
            cam.SetPosition(self.original_camera['position'])
            cam.SetFocalPoint(self.original_camera['focal_point'])
            cam.SetViewUp(self.original_camera['view_up'])
            cam.SetViewAngle(self.original_camera['view_angle'])
            print(f"[EndoscopeCamera] 恢復原始相機位置")
            
    def enter_from_surface(self, surface_point):
        """從表面點進入內視鏡 - 改進版本，允許深入物體內部"""
        print(f"[EndoscopeCamera] 從表面點進入: {surface_point}")
        
        cam = self.renderer.GetActiveCamera()
        
        # 1. 獲取場景邊界
        actors = self.renderer.GetActors()
        actors.InitTraversal()
        
        bounds = None
        actor = actors.GetNextItem()
        while actor:
            actor_bounds = actor.GetBounds()
            if actor_bounds:
                if bounds is None:
                    bounds = list(actor_bounds)
                else:
                    bounds[0] = min(bounds[0], actor_bounds[0])
                    bounds[1] = max(bounds[1], actor_bounds[1])
                    bounds[2] = min(bounds[2], actor_bounds[2])
                    bounds[3] = max(bounds[3], actor_bounds[3])
                    bounds[4] = min(bounds[4], actor_bounds[4])
                    bounds[5] = max(bounds[5], actor_bounds[5])
            actor = actors.GetNextItem()
        
        # 2. 計算從表面點進入的方向
        surface_np = np.array(surface_point)
        
        # 計算從當前相機位置到表面點的方向
        current_pos = np.array(cam.GetPosition())
        direction = surface_np - current_pos
        direction_distance = np.linalg.norm(direction)
        
        if direction_distance > 0:
            direction = direction / direction_distance
        else:
            # 如果當前位置就是表面點，使用從表面指向內部的方向
            if bounds:
                # 計算場景中心
                center = np.array([
                    (bounds[0] + bounds[1]) / 2,
                    (bounds[2] + bounds[3]) / 2,
                    (bounds[4] + bounds[5]) / 2
                ])
                direction = center - surface_np
                direction_distance = np.linalg.norm(direction)
                if direction_distance > 0:
                    direction = direction / direction_distance
                else:
                    direction = np.array([0, 0, -1])  # 預設方向
            else:
                direction = np.array([0, 0, -1])  # 預設方向
        
        # 3. 設置相機位置（從表面點稍微後退，以便能看到表面）
        initial_distance = 50.0  # 初始後退距離
        camera_position = surface_np - direction * initial_distance
        
        print(f"[EndoscopeCamera] 相機位置: {camera_position}")
        print(f"[EndoscopeCamera] 焦點位置: {surface_point}")
        
        # 4. 設置相機
        cam.SetPosition(camera_position)
        cam.SetFocalPoint(surface_point)
        
        # 5. 計算上方向向量
        up_vector = np.array([0, 1, 0])
        
        # 如果上方向與視線方向平行，使用其他方向
        if np.abs(np.dot(direction, up_vector)) > 0.99:
            up_vector = np.array([0, 0, 1])
        
        cam.SetViewUp(up_vector)
        cam.SetViewAngle(self.fov)
        
        # 6. 重置裁剪範圍
        self.renderer.ResetCameraClippingRange()
        cam.SetClippingRange(0.01, 10000.0)  # 設置更寬的裁剪範圍
        
    def move_forward(self, distance=5.0):
        """向前移動 - 移除距離限制，允許進入物體內部"""
        cam = self.renderer.GetActiveCamera()
        pos = np.array(cam.GetPosition())
        foc = np.array(cam.GetFocalPoint())
        
        # 計算視線方向
        view_direction = foc - pos
        view_distance = np.linalg.norm(view_direction)
        
        if view_distance > 0:
            # 正規化方向向量
            view_direction = view_direction / view_distance
            
            # 移除最小距離限制，允許進入物體內部
            # 但保持一個非常小的安全距離
            if view_distance < 0.1 and distance > 0:
                # 如果已經非常接近焦點，稍微後退一點再前進
                new_foc = foc + view_direction * 10.0  # 將焦點向前移動
                cam.SetFocalPoint(new_foc)
                foc = new_foc
                view_direction = foc - pos
                view_distance = np.linalg.norm(view_direction)
                if view_distance > 0:
                    view_direction = view_direction / view_distance
            
            # 移動相機和焦點
            new_pos = pos + view_direction * distance
            new_foc = foc + view_direction * distance
            
            cam.SetPosition(new_pos)
            cam.SetFocalPoint(new_foc)
            
            # 更新裁剪範圍以適應新位置
            new_distance = np.linalg.norm(new_foc - new_pos)
            cam.SetClippingRange(max(0.001, new_distance * 0.01), new_distance * 10.0)
            
            print(f"[EndoscopeCamera] 向前移動 {distance:.1f} 單位")
            print(f"[EndoscopeCamera] 新位置: {new_pos}, 新焦點: {new_foc}")
    
    def move_backward(self, distance=5.0):
        """向後移動 - 移除距離限制"""
        cam = self.renderer.GetActiveCamera()
        pos = np.array(cam.GetPosition())
        foc = np.array(cam.GetFocalPoint())
        
        # 計算視線方向
        view_direction = foc - pos
        view_distance = np.linalg.norm(view_direction)
        
        if view_distance > 0:
            # 正規化方向向量
            view_direction = view_direction / view_distance
            
            # 移動相機和焦點
            new_pos = pos - view_direction * distance
            new_foc = foc - view_direction * distance
            
            cam.SetPosition(new_pos)
            cam.SetFocalPoint(new_foc)
            
            # 更新裁剪範圍以適應新位置
            new_distance = np.linalg.norm(new_foc - new_pos)
            cam.SetClippingRange(max(0.001, new_distance * 0.01), new_distance * 10.0)
            
            print(f"[EndoscopeCamera] 向後移動 {distance:.1f} 單位")
            print(f"[EndoscopeCamera] 新位置: {new_pos}, 新焦點: {new_foc}")
    
    def move_left(self, distance=5.0):
        """向左移動"""
        self._move_lateral(-distance)
        
    def move_right(self, distance=5.0):
        """向右移動"""
        self._move_lateral(distance)
        
    def _move_lateral(self, distance):
        """橫向移動"""
        cam = self.renderer.GetActiveCamera()
        pos = np.array(cam.GetPosition())
        foc = np.array(cam.GetFocalPoint())
        
        # 計算前方向量
        forward = foc - pos
        forward_distance = np.linalg.norm(forward)
        
        if forward_distance > 0:
            forward = forward / forward_distance
            up = np.array(cam.GetViewUp())
            
            # 計算右方向量（叉積）
            right = np.cross(forward, up)
            right_norm = np.linalg.norm(right)
            
            if right_norm > 0:
                right = right / right_norm
                
                # 移動相機和焦點
                new_pos = pos + right * distance
                new_foc = foc + right * distance
                
                cam.SetPosition(new_pos)
                cam.SetFocalPoint(new_foc)
                
                direction = "右" if distance > 0 else "左"
                print(f"[EndoscopeCamera] 向{direction}移動 {abs(distance):.1f} 單位")
    
    def move_up(self, distance=5.0):
        """向上移動"""
        self._move_vertical(distance)
        
    def move_down(self, distance=5.0):
        """向下移動"""
        self._move_vertical(-distance)
        
    def _move_vertical(self, distance):
        """垂直移動"""
        cam = self.renderer.GetActiveCamera()
        pos = np.array(cam.GetPosition())
        foc = np.array(cam.GetFocalPoint())
        up = np.array(cam.GetViewUp())
        
        # 正規化上向量
        up_norm = np.linalg.norm(up)
        if up_norm > 0:
            up = up / up_norm
            
            # 移動相機和焦點
            new_pos = pos + up * distance
            new_foc = foc + up * distance
            
            cam.SetPosition(new_pos)
            cam.SetFocalPoint(new_foc)
            
            direction = "上" if distance > 0 else "下"
            print(f"[EndoscopeCamera] 向{direction}移動 {abs(distance):.1f} 單位")
    
    def rotate(self, yaw=0, pitch=0):
        """旋轉相機 - 簡化版本，無需 scipy"""
        cam = self.renderer.GetActiveCamera()
        
        if abs(yaw) > 0 or abs(pitch) > 0:
            # 使用 VTK 的內建旋轉方法
            cam.Yaw(yaw)
            cam.Pitch(pitch)
            cam.OrthogonalizeViewUp()
            
            print(f"[EndoscopeCamera] 旋轉: yaw={yaw}, pitch={pitch}")
        
    def set_fov(self, fov):
        """設置視野"""
        cam = self.renderer.GetActiveCamera()
        cam.SetViewAngle(fov)
        self.fov = fov
        print(f"[EndoscopeCamera] 視野設置為 {fov} 度")
    
    def _update_clipping_range(self, camera):
        """動態更新裁剪範圍"""
        pos = np.array(camera.GetPosition())
        foc = np.array(camera.GetFocalPoint())
        
        distance = np.linalg.norm(foc - pos)
        near = max(0.001, distance * 0.01)
        far = distance * 100.0
        
        camera.SetClippingRange(near, far)
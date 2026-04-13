from typing import Dict, List, Optional, Tuple
import vtk
from interaction.base_mode import BaseInteractionMode
from manager.obj_property_manager import ObjectPropertyManager
from manager.obj3D_manager import Object3DManager


class SimpleCutMode(BaseInteractionMode):
    """
    簡單切割模式：
    - 支援上下、左右、前後切割
    - 點擊按鍵切換顯示上半/下半
    - 重製回到初始狀態
    """

    def __init__(
        self,
        interactor: vtk.vtkRenderWindowInteractor,
        renderer: vtk.vtkRenderer,
        prop_manager: ObjectPropertyManager,
        obj3d_manager: Object3DManager,
    ) -> None:
        super().__init__("simple_cut", interactor)
        self._renderer = renderer
        self._prop_mgr = prop_manager
        self._obj3d_mgr = obj3d_manager

        # 切割狀態
        self._current_direction: Optional[str] = None  # "up", "down", "left", "right", "front", "back"
        self._show_upper: bool = True  # True: 顯示上/右/前半, False: 顯示下/左/後半

        # 原始資料備份
        self._original_polydata: Dict[int, vtk.vtkPolyData] = {}
        self._clipped_actors: Dict[int, vtk.vtkActor] = {}

        # 方向到normal的映射
        self._direction_normals = {
            "up": (0, 0, 1),
            "down": (0, 0, -1),
            "right": (1, 0, 0),
            "left": (-1, 0, 0),
            "front": (0, 1, 0),
            "back": (0, -1, 0),
        }

    def on_mode_enter(self) -> None:
        print("[SimpleCutMode] enter", flush=True)
        # 重新進入模式時先還原原始 actor，避免殘留上一輪切割結果
        self.reset()
        self._backup_original_data()

    def on_mode_exit(self) -> None:
        print("[SimpleCutMode] exit", flush=True)
        self.reset()

    def set_cut_direction(self, direction: str) -> None:
        """設置切割方向"""
        if direction not in self._direction_normals:
            print(f"[SimpleCutMode] Invalid direction: {direction}", flush=True)
            return

        if not self._original_polydata:
            self._backup_original_data()

        if self._current_direction == direction:
            # 如果是同一個方向，直接切換狀態
            self.toggle_cut_state()
        else:
            # 如果是新方向，重置狀態為顯示上半
            self._current_direction = direction
            self._show_upper = True
            self._apply_cut()

    def toggle_cut_state(self) -> None:
        """切換顯示上半/下半"""
        if self._current_direction is None:
            print("[SimpleCutMode] No direction set", flush=True)
            return
        self._show_upper = not self._show_upper
        self._apply_cut()

    def reset(self) -> None:
        """重製回到初始狀態"""
        self._current_direction = None
        self._show_upper = True
        self._remove_clipped_actors()
        self._restore_original_actors()
        self._original_polydata.clear()

    def _backup_original_data(self) -> None:
        """備份所有原始物件的polydata"""
        self._original_polydata.clear()
        for obj in self._prop_mgr.get_original_objects():
            obj_id = obj.id
            if obj.polydata:
                # 深拷貝polydata
                polydata_copy = vtk.vtkPolyData()
                polydata_copy.DeepCopy(obj.polydata)
                self._original_polydata[obj_id] = polydata_copy

    def _remove_clipped_actors(self) -> None:
        """移除所有切割後的 actors，保留原始 actor 可見性處理給呼叫端決定。"""
        for actor in self._clipped_actors.values():
            self._renderer.RemoveActor(actor)
        self._clipped_actors.clear()

    def _clear_clipped_actors(self) -> None:
        """清除所有切割後的 actors，並將原始 actor 設為可見。"""
        self._remove_clipped_actors()
        for obj_id in self._original_polydata.keys():
            original_actor = self._obj3d_mgr.get_actor(obj_id)
            if original_actor:
                original_actor.SetVisibility(True)

    def _restore_original_actors(self) -> None:
        """恢復原始actors 並確保他們可見。"""
        for obj_id in self._original_polydata.keys():
            actor = self._obj3d_mgr.get_actor(obj_id)
            if actor:
                mapper = actor.GetMapper()
                if mapper:
                    original_polydata = self._original_polydata.get(obj_id)
                    if original_polydata:
                        mapper.RemoveAllClippingPlanes()  # 先移除裁切平面
                        mapper.SetInputData(original_polydata)
                        mapper.Update()
                actor.SetVisibility(True)

    def _apply_cut(self) -> None:
        """應用切割"""
        if self._current_direction is None:
            return

        normal = self._direction_normals[self._current_direction]
        self._remove_clipped_actors()

        # 全方向統一使用所有物件的整體邊界中心
        global_bounds = self._get_global_bounds()
        if global_bounds is None:
            return
        global_center = [
            (global_bounds[0] + global_bounds[1]) / 2,
            (global_bounds[2] + global_bounds[3]) / 2,
            (global_bounds[4] + global_bounds[5]) / 2,
        ]

        for obj_id, original_polydata in self._original_polydata.items():
            center = global_center

            # 創建clip plane
            plane = vtk.vtkPlane()
            plane.SetOrigin(center)
            plane.SetNormal(normal)

            # 創建clipper
            clipper = vtk.vtkClipPolyData()
            clipper.SetInputData(original_polydata)
            clipper.SetClipFunction(plane)
            clipper.SetInsideOut(not self._show_upper)  # InsideOut控制保留哪側
            clipper.Update()

            # 創建新actor
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(clipper.GetOutputPort())
            mapper.RemoveAllClippingPlanes()  # 確保沒有裁切平面

            actor = vtk.vtkActor()
            actor.SetMapper(mapper)

            # 複製原始actor的屬性
            original_actor = self._obj3d_mgr.get_actor(obj_id)
            if original_actor:
                actor.SetProperty(original_actor.GetProperty())
                actor.SetUserTransform(original_actor.GetUserTransform())

            # 添加到renderer
            self._renderer.AddActor(actor)
            self._clipped_actors[obj_id] = actor

            # 隱藏原始actor
            if original_actor:
                original_actor.SetVisibility(False)

        self._renderer.GetRenderWindow().Render()

    def _get_global_bounds(self) -> Optional[List[float]]:
        """計算所有原始物件的整體邊界"""
        if not self._original_polydata:
            return None
        
        # 初始化邊界
        bounds = [float('inf'), float('-inf')] * 3  # [xmin, xmax, ymin, ymax, zmin, zmax]
        
        for original_polydata in self._original_polydata.values():
            obj_bounds = original_polydata.GetBounds()
            for i in range(3):
                bounds[i*2] = min(bounds[i*2], obj_bounds[i*2])      # min
                bounds[i*2+1] = max(bounds[i*2+1], obj_bounds[i*2+1])  # max
        
        # 檢查是否有有效的邊界
        if bounds[0] == float('inf') or bounds[1] == float('-inf'):
            return None
            
        return bounds

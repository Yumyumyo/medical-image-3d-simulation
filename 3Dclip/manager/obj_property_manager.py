from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Iterable, Tuple

import vtk


# -------------- #
#  Data Model    #
# -------------- #

@dataclass
class SceneObject:
    """
    純資料層：不碰 VTK actor、不碰 UI。

    kind:
        "original"  - 匯入的原始物件
        "result"    - 由切割產生的結果物件
    parent_id:
        - 對於 original: None
        - 對於 result:   指向「原始物件」的 id（不是上一代 result）
    """
    id: int
    name: str
    kind: str  # "original" | "result"
    parent_id: Optional[int]

    polydata: vtk.vtkPolyData

    visible: bool = True
    color: Tuple[float, float, float] = (0.8, 0.8, 0.8)
    opacity: float = 1.0

    selected: bool = False
    locked: bool = False
    group: str = "default"

    # 使用 vtkTransform 表示位置 / 旋轉（目前不處理 scale）
    transform: vtk.vtkTransform = field(default_factory=vtk.vtkTransform)

    def clone_transform(self) -> vtk.vtkTransform:
        """回傳 transform 的深拷貝（給別的 object 用）。"""
        t = vtk.vtkTransform()
        t.DeepCopy(self.transform)
        return t


# -------------- #
#   Manager      #
# -------------- #

class ObjectPropertyManager:
    """
    管理所有 SceneObject 狀態的資料層。

    - 不處理 VTK actor（那是 Object3DManager 的工作）
    - 不處理 UI（list widget / checkbox）
    - PlaneCut / 其它互動邏輯，只能透過這個 manager 操作物件狀態
    """

    def __init__(self) -> None:
        self._objects: Dict[int, SceneObject] = {}
        self._next_id: int = 1

    # --------- 基本工具 --------- #

    def _generate_id(self) -> int:
        obj_id = self._next_id
        self._next_id += 1
        return obj_id

    def _require_exists(self, obj_id: int) -> SceneObject:
        if obj_id not in self._objects:
            raise KeyError(f"SceneObject id={obj_id} does not exist.")
        return self._objects[obj_id]

    # --------- 建立 / 刪除 --------- #

    def create_original(
        self,
        name: str,
        polydata: vtk.vtkPolyData,
        *,
        color: Tuple[float, float, float] = (0.8, 0.8, 0.8),
        opacity: float = 1.0,
        group: str = "default",
    ) -> int:
        """
        新增一個原始物件，回傳 id。
        """
        if not isinstance(polydata, vtk.vtkPolyData):
            raise TypeError("polydata must be a vtkPolyData.")

        obj_id = self._generate_id()
        obj = SceneObject(
            id=obj_id,
            name=name,
            kind="original",
            parent_id=None,
            polydata=polydata,
            visible=True,
            color=color,
            opacity=opacity,
            group=group,
        )
        self._objects[obj_id] = obj
        return obj_id

    def create_result(
        self,
        parent_id: int,
        polydata: vtk.vtkPolyData,
        *,
        name: Optional[str] = None,
        color: Tuple[float, float, float] = (0.8, 0.8, 0.8),
        opacity: float = 1.0,
        group: str = "default",
        inherit_transform: bool = True,
    ) -> int:
        """
        新增一個切割結果物件（result）。

        parent_id:
            - 可以是 original 或 result 的 id
            - 但本物件的 parent_id 一律指向「最原始的 original 物件」

        inherit_transform:
            - True: 繼承 parent 的 transform
            - False: 使用 identity transform
        """
        parent = self._require_exists(parent_id)

        # parent_original_id: 一律指向原始物件
        if parent.kind == "original":
            original_id = parent.id
        else:
            # parent 是 result，parent.parent_id 應該是 original
            original_id = parent.parent_id if parent.parent_id is not None else parent.id

        if not isinstance(polydata, vtk.vtkPolyData):
            raise TypeError("polydata must be a vtkPolyData.")

        obj_id = self._generate_id()
        obj_name = name if name is not None else f"{parent.name}_cut_{obj_id}"

        new_obj = SceneObject(
            id=obj_id,
            name=obj_name,
            kind="result",
            parent_id=original_id,
            polydata=polydata,
            visible=True,
            color=color,
            opacity=opacity,
            group=group,
        )

        if inherit_transform:
            new_obj.transform.DeepCopy(parent.transform)

        self._objects[obj_id] = new_obj
        return obj_id

    def delete_object(self, obj_id: int) -> None:
        """
        從資料層移除物件。

        一般建議只刪 result；
        original 通常用 hide_object() 即可。
        這裡不強制限制，由呼叫端自己決定策略。
        """
        self._require_exists(obj_id)
        del self._objects[obj_id]

    # --------- 取得 / 查詢 --------- #

    def get_object(self, obj_id: int) -> SceneObject:
        """取得指定 id 的 SceneObject（直接回傳物件本身）。"""
        return self._require_exists(obj_id)

    def get_all_objects(self) -> List[SceneObject]:
        return list(self._objects.values())

    def get_original_objects(self) -> List[SceneObject]:
        return [obj for obj in self._objects.values() if obj.kind == "original"]

    def get_result_objects(self) -> List[SceneObject]:
        return [obj for obj in self._objects.values() if obj.kind == "result"]

    def get_selected_objects(self) -> List[int]:
        """回傳所有 selected=True 的物件 id（給 multi-cut 用）。"""
        return [obj.id for obj in self._objects.values() if obj.selected]

    # --------- 屬性更新 --------- #

    def set_visible(self, obj_id: int, visible: bool) -> None:
        obj = self._require_exists(obj_id)
        obj.visible = bool(visible)

    def set_color(self, obj_id: int, color: Iterable[float]) -> None:
        obj = self._require_exists(obj_id)
        r, g, b = color
        obj.color = (float(r), float(g), float(b))

    def set_opacity(self, obj_id: int, opacity: float) -> None:
        obj = self._require_exists(obj_id)
        obj.opacity = float(opacity)

    def set_selected(self, obj_id: int, selected: bool) -> None:
        obj = self._require_exists(obj_id)
        obj.selected = bool(selected)

    def clear_selection(self) -> None:
        for obj in self._objects.values():
            obj.selected = False

    def set_locked(self, obj_id: int, locked: bool) -> None:
        obj = self._require_exists(obj_id)
        obj.locked = bool(locked)

    def set_group(self, obj_id: int, group: str) -> None:
        obj = self._require_exists(obj_id)
        obj.group = str(group)

    def set_transform(self, obj_id: int, transform: vtk.vtkTransform) -> None:
        """
        將外部給的 transform 深拷貝到物件自己的 transform。
        """
        if not isinstance(transform, vtk.vtkTransform):
            raise TypeError("transform must be a vtkTransform.")
        obj = self._require_exists(obj_id)
        obj.transform.DeepCopy(transform)

    # --------- 輔助判斷 / 取欄位 --------- #

    def is_original(self, obj_id: int) -> bool:
        return self._require_exists(obj_id).kind == "original"

    def is_result(self, obj_id: int) -> bool:
        return self._require_exists(obj_id).kind == "result"

    def get_parent_id(self, obj_id: int) -> Optional[int]:
        return self._require_exists(obj_id).parent_id

    def get_polydata(self, obj_id: int) -> vtk.vtkPolyData:
        return self._require_exists(obj_id).polydata

    # --------- 其他小工具 --------- #

    def rename(self, obj_id: int, new_name: str) -> None:
        obj = self._require_exists(obj_id)
        obj.name = str(new_name)

    # ------------------------------------------------------------------ #
    # Undo/Redo / Restore helpers
    # ------------------------------------------------------------------ #
    def clear_all(self) -> None:
        """清空所有物件，並重置 id 計數器。"""
        self._objects.clear()
        self._next_id = 1

    def register_object(self, obj: SceneObject) -> None:
        """註冊一個已存在的 SceneObject（保留其 id）。

        主要用於 Undo/Redo 或從快照還原場景。
        """
        self._objects[obj.id] = obj
        if obj.id >= self._next_id:
            self._next_id = obj.id + 1

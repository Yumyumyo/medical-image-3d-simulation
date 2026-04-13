from __future__ import annotations

from typing import Dict, Optional

import vtk

from manager.obj_property_manager import ObjectPropertyManager, SceneObject


class Object3DManager:
    """
    Mesh 3D 顯示管理層：
    - 負責將 SceneObject 轉成 vtkActor，放進 3D renderer
    - 維護 obj_id <-> actor 的雙向對應
    - 提供新增 / 刪除 / 顯示 / 隱藏 / 更新外觀與 transform 的介面

    不負責：
    - 切割邏輯（PlaneCut / LineCut）
    - SceneObject 資料內容（交給 ObjectPropertyManager）
    - UI 控制（list widget / 按鈕）
    """

    def __init__(self, renderer: vtk.vtkRenderer, prop_manager: ObjectPropertyManager) -> None:
        """
        Parameters
        ----------
        renderer : vtkRenderer
            專門用來顯示 mesh 物件的 renderer（不要拿來放 3D slice plane）。
        prop_manager : ObjectPropertyManager
            物件資料管理器，從這裡取得 SceneObject。
        """
        self._renderer = renderer
        self._prop_manager = prop_manager

        # 雙向 mapping
        self._obj_id_to_actor: Dict[int, vtk.vtkActor] = {}
        self._actor_to_obj_id: Dict[vtk.vtkProp3D, int] = {}
        self._obj_id_to_silhouette: Dict[int, vtk.vtkActor] = {}
        self._preview_cap_actors: Dict[int, vtk.vtkActor] = {}
        # 預設關閉裁切「封口」預覽：這會很吃效能，也會改變裁切外觀
        self._preview_caps_enabled: bool = False

    # ------------------------------------------------------------------ #
    # 內部小工具
    # ------------------------------------------------------------------ #

    def _create_actor_from_object(self, obj: SceneObject) -> vtk.vtkActor:
        """根據 SceneObject 建立對應的 vtkActor（尚未加入 renderer）。"""
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(obj.polydata)
        mapper.Update()

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)

        # 讓 actor name 保留，方便 debug / 其他模組查詢
        try:
            actor.SetName(obj.name)
        except Exception:
            pass

        # 外觀
        r, g, b = obj.color
        actor.GetProperty().SetColor(r, g, b)
        actor.GetProperty().SetOpacity(obj.opacity)
        actor.SetVisibility(1 if obj.visible else 0)

        # transform：直接使用 SceneObject 裡的 vtkTransform
        actor.SetUserTransform(obj.transform)

        return actor

    def _register_actor(self, obj_id: int, actor: vtk.vtkActor) -> None:
        """把 actor 納入雙向 mapping。"""
        self._obj_id_to_actor[obj_id] = actor
        self._actor_to_obj_id[actor] = obj_id

    def _unregister_actor(self, actor: vtk.vtkActor) -> None:
        """從 mapping 中移除 actor（呼叫前請先 RemoveActor）。"""
        obj_id = self._actor_to_obj_id.pop(actor, None)
        if obj_id is not None:
            self._obj_id_to_actor.pop(obj_id, None)
    
    def _ensure_silhouette_actor(self, obj_id: int) -> vtk.vtkActor | None:
        """如果還沒有為這個 obj_id 建立 silhouette，就建一個並加到 renderer。"""
        # 已經有就直接拿
        sil = self._obj_id_to_silhouette.get(obj_id)
        if sil is not None:
            return sil

        obj = self._prop_manager.get_object(obj_id)
        base_actor = self._obj_id_to_actor.get(obj_id)
        if base_actor is None:
            return None

        poly = obj.polydata

        # 如果 polydata 沒幾個點，乾脆不要做 silhouette，避免怪異崩潰
        if not isinstance(poly, vtk.vtkPolyData) or poly.GetNumberOfPoints() == 0:
            return None

        # === 這一塊是「給 silhouette 專用的降解析度 polydata」 ===
        dec = vtk.vtkDecimatePro()
        dec.SetInputData(obj.polydata)
        dec.SetTargetReduction(0.85)   # 0.7 = 刪掉 70% 面數，保留 30%，你可以自己調
        dec.PreserveTopologyOn()
        dec.BoundaryVertexDeletionOff()   # 通常這樣輪廓比較穩定（可視情況加）
        dec.Update()

        sil_filter = vtk.vtkPolyDataSilhouette()
        sil_filter.SetInputConnection(dec.GetOutputPort())
        sil_filter.SetCamera(self._renderer.GetActiveCamera())

        sil_mapper = vtk.vtkPolyDataMapper()
        sil_mapper.SetInputConnection(sil_filter.GetOutputPort())

        sil_actor = vtk.vtkActor()
        sil_actor.SetMapper(sil_mapper)
        sil_actor.GetProperty().SetColor(1.0, 1.0, 0.0)
        sil_actor.GetProperty().SetLineWidth(2.5)
        sil_actor.GetProperty().SetOpacity(1.0)
        sil_actor.GetProperty().SetLighting(False)
        sil_actor.GetProperty().SetRepresentationToWireframe()
        sil_actor.PickableOff()
        sil_actor.SetVisibility(0)

        # 跟著同一個 transform
        sil_actor.SetUserTransform(base_actor.GetUserTransform())

        self._renderer.AddActor(sil_actor)
        self._obj_id_to_silhouette[obj_id] = sil_actor
        return sil_actor

    # ------------------------------------------------------------------ #
    # 對外 API
    # ------------------------------------------------------------------ #

    def spawn_actor(self, obj_id: int) -> vtk.vtkActor:
        """
        為指定 SceneObject 建立對應的 actor，加入 renderer，並記錄在 mapping 中。

        若該 obj_id 已經有 actor：
        - 先移除舊 actor，再建立新的（避免重複）。
        """
        # 若已存在 actor，先刪掉
        if obj_id in self._obj_id_to_actor:
            self.remove_actor(obj_id)

        obj = self._prop_manager.get_object(obj_id)
        actor = self._create_actor_from_object(obj)

        self._renderer.AddActor(actor)
        self._register_actor(obj_id, actor)

        # 如果是場景中第一個 actor，reset camera 一次，避免看不到模型
        if len(self._obj_id_to_actor) == 1:
            self._renderer.ResetCamera()

        return actor

    def remove_actor(self, obj_id: int) -> None:
        actor = self._obj_id_to_actor.get(obj_id)
        if actor is not None:
            self._renderer.RemoveActor(actor)
            self._unregister_actor(actor)

        # ⭐ 順便處理 silhouette actor
        sil = self._obj_id_to_silhouette.pop(obj_id, None)
        if sil is not None:
            self._renderer.RemoveActor(sil)


    def hide_actor(self, obj_id: int) -> None:
        """將 actor 設為不可見（不影響 SceneObject.visible）。"""
        actor = self._obj_id_to_actor.get(obj_id)
        if actor is None:
            return
        actor.SetVisibility(0)

    def show_actor(self, obj_id: int) -> None:
        """將 actor 設為可見（不改 SceneObject.visible，只負責視覺）。"""
        actor = self._obj_id_to_actor.get(obj_id)
        if actor is None:
            return
        actor.SetVisibility(1)

    def update_actor_appearance(self, obj_id: int) -> None:
        """
        當 SceneObject 的 color / opacity / visible 改變時，
        呼叫此函式同步到 actor。
        """
        obj = self._prop_manager.get_object(obj_id)
        actor = self._obj_id_to_actor.get(obj_id)
        if actor is None:
            return

        # 顏色與透明度
        r, g, b = obj.color
        prop = actor.GetProperty()
        prop.SetColor(r, g, b)
        prop.SetOpacity(obj.opacity)

        # 可見度
        actor.SetVisibility(1 if obj.visible else 0)

        # 控制 silhouette
        sil = self._obj_id_to_silhouette.get(obj_id)

        if getattr(obj, "selected", False) and obj.visible:
            # 沒有就建立
            if sil is None:
                sil = self._ensure_silhouette_actor(obj_id)
            if sil is not None:
                sil.SetVisibility(1)
        else:
            # 取消選取/隱藏時，把 silhouette actor 移除並刪掉
            if sil is not None:
                sil.SetVisibility(0)
                self._renderer.RemoveActor(sil)
                del self._obj_id_to_silhouette[obj_id]
                sil = None

        rw = self._renderer.GetRenderWindow()
        if rw is not None:
            rw.Render()


    def update_actor_transform(self, obj_id: int) -> None:
        """
        當 SceneObject.transform 更新後，呼叫此函式同步到 actor。
        """
        obj = self._prop_manager.get_object(obj_id)
        actor = self._obj_id_to_actor.get(obj_id)
        if actor is None:
            return

        actor.SetUserTransform(obj.transform)

        sil = self._obj_id_to_silhouette.get(obj_id)
        if sil is not None:
            sil.SetUserTransform(obj.transform)

    def update_actor_position(self, obj_id: int, position: Tuple[float, float, float]) -> None:
        """直接更新 actor 位置，並同步 silhouette 位置。"""
        actor = self._obj_id_to_actor.get(obj_id)
        if actor is None:
            return
        actor.SetPosition(position)

        sil = self._obj_id_to_silhouette.get(obj_id)
        if sil is not None:
            sil.SetPosition(position)

    def refresh_all_from_properties(self) -> None:
        """
        重新依照目前的 SceneObject 狀態，更新全部 actor 的
        appearance 與 transform。
        """
        for obj_id in list(self._obj_id_to_actor.keys()):
            try:
                self.update_actor_appearance(obj_id)
                self.update_actor_transform(obj_id)
            except KeyError:
                # 資料已不存在 → 把 actor 也移除
                self.remove_actor(obj_id)

    # ------------------------------------------------------------------ #
    # 查詢 / 反查
    # ------------------------------------------------------------------ #

    def get_actor(self, obj_id: int) -> Optional[vtk.vtkActor]:
        """由物件 id 取得對應的 actor，若不存在則回傳 None。"""
        return self._obj_id_to_actor.get(obj_id)

    def get_obj_id_from_actor(self, actor: vtk.vtkProp3D) -> Optional[int]:
        """
        由 actor 反查物件 id（通常用在 picking 之後）。
        若 actor 不在 mapping 中，回傳 None。
        """
        return self._actor_to_obj_id.get(actor)
    
    def all_actors(self):
        """
        回傳目前 renderer 中由 Object3DManager 管理的所有 mesh actor。
        """
        return list(self._obj_id_to_actor.values())

    def set_preview_caps_enabled(self, enabled: bool) -> None:
        """是否在裁切預覽時產生封口面（較耗時、外觀也不同）。"""
        self._preview_caps_enabled = bool(enabled)
        if not self._preview_caps_enabled:
            self._clear_preview_cap_actors()


    # ------------------------------------------------------------------ #
    # 初始化輔助
    # ------------------------------------------------------------------ #

    def spawn_actors_for_all_objects(self) -> None:
        """
        為目前 ObjectPropertyManager 中的所有物件建立 actor。
        通常在載入完所有模型後呼叫一次。
        """
        for obj in self._prop_manager.get_all_objects():
            self.spawn_actor(obj.id)

    def apply_preview_clipping(self, planes: vtk.vtkPlane | list[vtk.vtkPlane]):
        if not isinstance(planes, list):
            planes = [planes]
        self._clear_preview_cap_actors()

        for actor in self.all_actors():
            obj_id = self.get_obj_id_from_actor(actor)
            if obj_id is None:
                continue

            obj = self._prop_manager.get_object(obj_id)
            mapper = actor.GetMapper()
            if not mapper:
                continue

            mapper.RemoveAllClippingPlanes()
            if not obj.visible:
                continue

            # 預設：只用 clipping plane，速度快、外觀也維持「原始物件被裁切」
            if not self._preview_caps_enabled:
                for plane in planes:
                    mapper.AddClippingPlane(plane)
                actor.SetVisibility(1 if obj.visible else 0)
                continue

            if self._is_skin_object(obj):
                for plane in planes:
                    mapper.AddClippingPlane(plane)
                actor.SetVisibility(1 if obj.visible else 0)
                continue

            preview_actor = self._build_preview_cap_actor(obj, actor, planes)
            if preview_actor is not None:
                actor.SetVisibility(0)
                self._renderer.AddActor(preview_actor)
                self._preview_cap_actors[obj_id] = preview_actor
            else:
                for plane in planes:
                    mapper.AddClippingPlane(plane)

    def clear_preview_clipping(self):
        self._clear_preview_cap_actors()
        for actor in self.all_actors():
            mapper = actor.GetMapper()
            if mapper:
                mapper.RemoveAllClippingPlanes()
            obj_id = self.get_obj_id_from_actor(actor)
            if obj_id is None:
                continue
            obj = self._prop_manager.get_object(obj_id)
            actor.SetVisibility(1 if obj.visible else 0)

    def _clear_preview_cap_actors(self) -> None:
        for actor in self._preview_cap_actors.values():
            self._renderer.RemoveActor(actor)
        self._preview_cap_actors.clear()

    def _is_skin_object(self, obj: SceneObject) -> bool:
        text = f"{obj.name} {obj.group}".lower()
        keywords = ("skin", "scalp", "皮膚", "表皮")
        return any(keyword in text for keyword in keywords)

    def _build_preview_cap_actor(
        self,
        obj: SceneObject,
        base_actor: vtk.vtkActor,
        planes: list[vtk.vtkPlane],
    ) -> vtk.vtkActor | None:
        poly = obj.polydata
        if poly is None or poly.GetNumberOfPoints() == 0:
            return None

        transform = vtk.vtkTransform()
        transform.SetMatrix(base_actor.GetMatrix())

        world_tf = vtk.vtkTransformPolyDataFilter()
        world_tf.SetTransform(transform)
        world_tf.SetInputData(poly)
        world_tf.Update()

        world_poly = vtk.vtkPolyData()
        world_poly.DeepCopy(world_tf.GetOutput())
        if world_poly.GetNumberOfPoints() == 0:
            return None

        tri = vtk.vtkTriangleFilter()
        tri.SetInputData(world_poly)
        tri.Update()

        plane_collection = vtk.vtkPlaneCollection()
        for plane in planes:
            plane_copy = vtk.vtkPlane()
            plane_copy.SetOrigin(plane.GetOrigin())
            plane_copy.SetNormal(plane.GetNormal())
            plane_collection.AddItem(plane_copy)

        clipper = vtk.vtkClipClosedSurface()
        clipper.SetInputConnection(tri.GetOutputPort())
        clipper.SetClippingPlanes(plane_collection)
        clipper.GenerateFacesOn()
        clipper.Update()

        clipped = clipper.GetOutput()
        if clipped is None or clipped.GetNumberOfPoints() == 0:
            return None

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(clipped)
        mapper.Update()

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*obj.color)
        actor.GetProperty().SetOpacity(obj.opacity)
        actor.GetProperty().SetLighting(False)
        actor.PickableOn()
        actor.SetVisibility(1 if obj.visible else 0)
        return actor


    # ------------------------------------------------------------------ #
    # Undo/Redo / Snapshot helpers
    # ------------------------------------------------------------------ #
    def all_actors(self) -> list[vtk.vtkActor]:
        """回傳目前場景中的所有 actor（不包含 silhouette）。"""
        return list(self._actor_to_obj_id.keys())

    def cut_actors(self) -> list[vtk.vtkActor]:
        """回傳目前被視為切割結果的 actor 列表。"""
        result = []
        for obj in self._prop_manager.get_result_objects():
            actor = self._obj_id_to_actor.get(obj.id)
            if actor is not None:
                result.append(actor)
        return result

    def get_info(self, actor: vtk.vtkProp3D) -> dict | None:
        """提供給 HistoryManager 的 Query API。"""
        obj_id = self.get_obj_id_from_actor(actor)
        if obj_id is None:
            return None
        try:
            so = self._prop_manager.get_object(obj_id)
        except KeyError:
            return None

        return {
            "obj_id": obj_id,
            "so": so,
            "poly": so.polydata,
            "is_cut_result": so.kind == "result",
        }

    def _add_actor_from_poly_with_full_transform(
        self,
        obj_id: int,
        name: str,
        poly: vtk.vtkPolyData,
        color: tuple,
        position: tuple,
        orientation: tuple,
        scale: tuple,
        user_transform: Optional[vtk.vtkTransform],
        selected: bool,
        visible: bool,
        opacity: float,
        locked: bool,
        group: str,
        kind: str,
        parent_id: int | None = None,
    ) -> vtk.vtkActor:
        """建立 SceneObject + actor，並保留指定的 obj_id。

        這個方法主要供 HistoryManager 還原用，能在還原時保留原有 ID。
        """
        # 1) 建立 SceneObject
        so = SceneObject(
            id=obj_id,
            name=name,
            kind=kind,
            parent_id=parent_id,
            polydata=poly,
            visible=visible,
            color=color,
            opacity=opacity,
            selected=selected,
            locked=locked,
            group=group,
            transform=vtk.vtkTransform(),
        )

        if user_transform is not None:
            so.transform.DeepCopy(user_transform)
        else:
            # 若沒有 user_transform，則根據 position/orientation/scale 建立
            so.transform.Identity()
            so.transform.Translate(*position)
            so.transform.RotateX(orientation[0])
            so.transform.RotateY(orientation[1])
            so.transform.RotateZ(orientation[2])
            so.transform.Scale(*scale)

        # 2) 註冊到 PropertyManager
        # 直接存入，不走 create_original/create_result（避免重新失去 id）
        self._prop_manager.register_object(so)

        # 3) 建立 actor
        actor = self.spawn_actor(obj_id)
        return actor

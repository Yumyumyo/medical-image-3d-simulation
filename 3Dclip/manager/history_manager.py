# manager/history_manager.py

from __future__ import annotations

from typing import List, Dict, Any, Optional, Callable
import vtk
import os
import json
import base64
import tempfile

from manager.obj3D_manager import Object3DManager
from manager.obj_property_manager import ObjectPropertyManager, SceneObject


class HistoryManager:
    """管理 3D 場景的 Undo / Redo。

    目前的 Undo/Redo 以「整個場景快照」為單位。

    設計概念：
    - 每次「操作完成」之後，呼叫 push_state() 存一份場景快照
    - undo():
        - 先把現在狀態存到 redo_stack
        - 還原到 undo_stack 最後一筆
    - redo():
        - 先把現在狀態存到 undo_stack
        - 還原到 redo_stack 最後一筆
    """

    def __init__(
        self,
        obj3d_mgr: Object3DManager,
        prop_mgr: ObjectPropertyManager,
        max_history: int = 50,
        on_restored: Optional[Callable[[], None]] = None,
    ):
        self.obj3d_mgr = obj3d_mgr
        self.prop_mgr = prop_mgr
        self.max_history = max_history
        self.on_restored = on_restored

        # 每個元素都是「場景快照」
        # 每筆紀錄是一個 dict list，代表當下所有 SceneObject（包含 poly/transform）
        self.undo_stack: List[List[Dict[str, Any]]] = []
        self.redo_stack: List[List[Dict[str, Any]]] = []

    # ----------------------------------------------------------------------
    # 對外 API
    # ----------------------------------------------------------------------
    def push_state(self):
        """在「完成一個操作」後呼叫，記錄目前場景。"""
        snapshot = self._capture_scene_state()
        self.undo_stack.append(snapshot)

        # 限制長度，避免記憶體爆炸
        if len(self.undo_stack) > self.max_history:
            self.undo_stack.pop(0)

        # 任何新的操作出現時，Redo 歷史要清空
        self.redo_stack.clear()

    def can_undo(self) -> bool:
        return len(self.undo_stack) > 1

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def undo(self):
        """回到上一個快照（previous）。"""
        # 至少要有 2 筆：前一筆 + 目前這筆，才有得退
        if len(self.undo_stack) < 2:
            print("[History] 沒有可以 Undo 的紀錄")
            return

        # 1) 把「目前這筆」丟到 redo
        current = self.undo_stack.pop()
        self.redo_stack.append(current)

        # 2) 還原到「上一筆」
        prev = self.undo_stack[-1]
        self._restore_scene_state(prev)
        print("[History] Undo 完成")

    def redo(self):
        """重做上一個被 Undo 的快照。"""
        if len(self.redo_stack) == 0:
            print("[History] 沒有可以 Redo 的紀錄")
            return

        nxt = self.redo_stack.pop()
        self.undo_stack.append(nxt)
        self._restore_scene_state(nxt)
        print("[History] Redo 完成")

    # ----------------------------------------------------------------------
    # 保存 / 載入
    # ----------------------------------------------------------------------
    def save_scene_as_obj(self, file_path: str, include_hidden: bool = False) -> bool:
        """儲存目前場景到單一 OBJ 檔案（舊 API）。"""
        if not file_path:
            return False

        if not file_path.lower().endswith('.obj'):
            file_path += '.obj'

        append_filter = vtk.vtkAppendPolyData()
        has_data = False

        for so in self.prop_mgr.get_all_objects():
            if not include_hidden and not so.visible:
                continue

            poly = vtk.vtkPolyData()
            poly.DeepCopy(so.polydata)

            transform = so.transform if so.transform is not None else vtk.vtkTransform()
            tfilter = vtk.vtkTransformPolyDataFilter()
            tfilter.SetInputData(poly)
            tfilter.SetTransform(transform)
            tfilter.Update()

            append_filter.AddInputData(tfilter.GetOutput())
            has_data = True

        if not has_data:
            return False

        append_filter.Update()

        clean = vtk.vtkCleanPolyData()
        clean.SetInputConnection(append_filter.GetOutputPort())
        clean.Update()

        writer = vtk.vtkOBJWriter()
        writer.SetFileName(file_path)
        writer.SetInputData(clean.GetOutput())

        try:
            writer.Write()
            print(f"[History] 成功儲存 OBJ: {file_path}")
            return True
        except Exception as e:
            print(f"[History] 儲存 OBJ 失敗: {e}")
            return False

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        import re
        cleaned = re.sub(r'[<>:"/\\|?*]', '_', filename)
        cleaned = re.sub(r'\s+', '_', cleaned.strip())
        if not cleaned:
            cleaned = 'object'
        return cleaned

    def save_scene_per_object_obj(self, folder: str, include_hidden: bool = False) -> bool:
        """每個物件輸出一個 OBJ，並保存 metadata（名稱/顏色/id）。"""
        if not folder:
            return False

        os.makedirs(folder, exist_ok=True)

        meta = []
        saved_count = 0

        for so in self.prop_mgr.get_all_objects():
            if not include_hidden and not so.visible:
                continue

            obj_name = self._sanitize_filename(so.name or f"object_{so.id}")
            obj_filename = f"{obj_name}_{so.id}.obj"
            obj_path = os.path.join(folder, obj_filename)

            poly = vtk.vtkPolyData()
            poly.DeepCopy(so.polydata)

            transform = so.transform if so.transform is not None else vtk.vtkTransform()
            tfilter = vtk.vtkTransformPolyDataFilter()
            tfilter.SetInputData(poly)
            tfilter.SetTransform(transform)
            tfilter.Update()

            # use clean to remove duplicates / unused points
            clean = vtk.vtkCleanPolyData()
            clean.SetInputConnection(tfilter.GetOutputPort())
            clean.Update()

            writer = vtk.vtkOBJWriter()
            writer.SetFileName(obj_path)
            writer.SetInputData(clean.GetOutput())

            try:
                writer.Write()
                saved_count += 1

                meta.append({
                    'id': so.id,
                    'name': so.name,
                    'obj_file': obj_filename,
                    'kind': so.kind,
                    'color': list(so.color) if so.color is not None else None,
                    'opacity': float(so.opacity),
                    'visible': bool(so.visible),
                    'selected': bool(so.selected),
                    'locked': bool(so.locked),
                    'group': so.group,
                })

            except Exception as e:
                print(f"[History] 儲存物件 OBJ 失敗: {so.id} {so.name}: {e}")

        meta_path = os.path.join(folder, 'scene_metadata.json')
        try:
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump({'objects': meta}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[History] 儲存 metadata 失敗: {e}")
            return False

        print(f"[History] 物件 OBJ 儲存完成：{saved_count} 個，metadata：{meta_path}")
        return saved_count > 0

    def save_scene_to_file(self, file_path: str, include_hidden: bool = False) -> bool:
        """儲存完整場景到單一 .scene 檔案（包含所有物件資料）。"""
        if not file_path.lower().endswith('.scene'):
            file_path += '.scene'

        objects_data = []
        for so in self.prop_mgr.get_all_objects():
            if not include_hidden and not so.visible:
                continue

            # 寫到臨時檔案然後讀取內容
            with tempfile.NamedTemporaryFile(suffix='.vtp', delete=False) as tmp_file:
                tmp_path = tmp_file.name

            writer = vtk.vtkXMLPolyDataWriter()
            writer.SetFileName(tmp_path)
            writer.SetInputData(so.polydata)
            writer.Write()

            # 讀取檔案內容並 base64 編碼
            with open(tmp_path, 'rb') as f:
                poly_bytes = f.read()
            poly_b64 = base64.b64encode(poly_bytes).decode('utf-8')

            # 刪除臨時檔案
            os.unlink(tmp_path)

            # transform to matrix
            mat_flat = None
            if so.transform:
                mat = so.transform.GetMatrix()
                mat_flat = [mat.GetElement(r, c) for r in range(4) for c in range(4)]

            objects_data.append({
                'id': so.id,
                'name': so.name,
                'kind': so.kind,
                'parent_id': so.parent_id,
                'color': list(so.color) if so.color else None,
                'opacity': float(so.opacity),
                'visible': bool(so.visible),
                'selected': bool(so.selected),
                'locked': bool(so.locked),
                'group': so.group,
                'polydata_b64': poly_b64,
                'transform': mat_flat,
            })

        data = {'version': 1, 'objects': objects_data}
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[History] 成功儲存工作檔: {file_path}")
            return True
        except Exception as e:
            print(f"[History] 儲存工作檔失敗: {e}")
            return False

    def load_scene_from_file(self, file_path: str) -> bool:
        """從單一 .scene 檔案載入完整場景。"""
        if not os.path.exists(file_path):
            print(f"[History] 工作檔不存在: {file_path}")
            return False

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            objects = data.get('objects', [])
            snapshot = []
            for obj in objects:
                poly_b64 = obj.get('polydata_b64')
                poly_bytes = base64.b64decode(poly_b64)

                # 寫到臨時檔案然後讀取
                with tempfile.NamedTemporaryFile(suffix='.vtp', delete=False) as tmp_file:
                    tmp_path = tmp_file.name

                with open(tmp_path, 'wb') as f:
                    f.write(poly_bytes)

                reader = vtk.vtkXMLPolyDataReader()
                reader.SetFileName(tmp_path)
                reader.Update()
                poly = reader.GetOutput()

                # 刪除臨時檔案
                os.unlink(tmp_path)

                mat_flat = obj.get('transform')
                transform = None
                if mat_flat:
                    transform = vtk.vtkTransform()
                    mat = vtk.vtkMatrix4x4()
                    k = 0
                    for r in range(4):
                        for c in range(4):
                            mat.SetElement(r, c, float(mat_flat[k]))
                            k += 1
                    transform.SetMatrix(mat)

                snapshot.append({
                    'id': int(obj['id']),
                    'name': obj['name'],
                    'kind': obj.get('kind', 'original'),
                    'parent_id': obj.get('parent_id'),
                    'color': tuple(obj['color']) if obj['color'] else (0.8, 0.8, 0.8),
                    'opacity': float(obj['opacity']),
                    'visible': bool(obj['visible']),
                    'selected': bool(obj['selected']),
                    'locked': bool(obj['locked']),
                    'group': obj.get('group', 'default'),
                    'poly': poly,
                    'transform': transform,
                })

            self._restore_scene_state(snapshot)

            # 清空 undo/redo，並以載入狀態為 baseline
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.undo_stack.append(self._capture_scene_state())

            print(f"[History] 成功載入工作檔: {file_path}")
            return True
        except Exception as e:
            print(f"[History] 載入工作檔失敗: {e}")
            return False

    def save_to_folder(self, folder: str):
        """將當前場景儲存為 scene.json + mesh_XXXX.vtp。"""
        os.makedirs(folder, exist_ok=True)

        snapshot = self._capture_scene_state()

        objects_json = []
        for i, st in enumerate(snapshot):
            # 1) polydata -> vtp
            mesh_name = f"mesh_{i:04d}.vtp"
            mesh_path = os.path.join(folder, mesh_name)

            w = vtk.vtkXMLPolyDataWriter()
            w.SetFileName(mesh_path)
            w.SetInputData(st["poly"])
            w.Write()

            # 2) transform -> 16 numbers
            mat = st.get("transform")
            mat_flat = None
            if mat is not None:
                mat_flat = [mat.GetMatrix().GetElement(r, c) for r in range(4) for c in range(4)]

            objects_json.append({
                "id": st["id"],
                "name": st["name"],
                "kind": st["kind"],
                "parent_id": st.get("parent_id"),
                "color": list(st["color"]),
                "opacity": float(st["opacity"]),
                "visible": bool(st["visible"]),
                "selected": bool(st["selected"]),
                "locked": bool(st["locked"]),
                "group": st.get("group"),
                "mesh_file": mesh_name,
                "transform": mat_flat,
            })

        meta = {"version": 1, "objects": objects_json}
        with open(os.path.join(folder, "scene.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def load_from_folder(self, folder: str):
        """從資料夾載入工作狀態（JSON + 多個 VTP），並直接還原場景。"""
        scene_path = os.path.join(folder, "scene.json")
        if not os.path.exists(scene_path):
            raise FileNotFoundError(f"scene.json not found: {scene_path}")

        with open(scene_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        snapshot = []
        for obj in meta.get("objects", []):
            mesh_path = os.path.join(folder, obj["mesh_file"])
            if not os.path.exists(mesh_path):
                raise FileNotFoundError(f"mesh file not found: {mesh_path}")

            r = vtk.vtkXMLPolyDataReader()
            r.SetFileName(mesh_path)
            r.Update()
            poly = vtk.vtkPolyData()
            poly.DeepCopy(r.GetOutput())

            mat_flat = obj.get("transform")
            transform = None
            if mat_flat is not None:
                transform = vtk.vtkTransform()
                mat = vtk.vtkMatrix4x4()
                k = 0
                for rr in range(4):
                    for cc in range(4):
                        mat.SetElement(rr, cc, float(mat_flat[k]))
                        k += 1
                transform.SetMatrix(mat)

            snapshot.append({
                "id": int(obj["id"]),
                "name": obj["name"],
                "kind": obj.get("kind", "original"),
                "parent_id": obj.get("parent_id"),
                "color": tuple(obj["color"]),
                "opacity": float(obj["opacity"]),
                "visible": bool(obj["visible"]),
                "selected": bool(obj["selected"]),
                "locked": bool(obj["locked"]),
                "group": obj.get("group"),
                "poly": poly,
                "transform": transform,
            })

        self._restore_scene_state(snapshot)

        # 載入後把 undo/redo 清空，並以「載入狀態」當新的 baseline
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.undo_stack.append(self._capture_scene_state())

    # ----------------------------------------------------------------------
    # 內部：場景快照擷取 / 還原
    # ----------------------------------------------------------------------
    def _capture_scene_state(self) -> List[Dict[str, Any]]:
        """從 ObjectPropertyManager 取出全部 SceneObject，並逐一複製成快照。"""
        snapshot: List[Dict[str, Any]] = []

        for so in self.prop_mgr.get_all_objects():
            # polydata
            poly_copy = vtk.vtkPolyData()
            poly_copy.DeepCopy(so.polydata)

            # transform
            transform_copy = vtk.vtkTransform()
            transform_copy.DeepCopy(so.transform)

            snapshot.append({
                "id": so.id,
                "name": so.name,
                "kind": so.kind,
                "parent_id": so.parent_id,
                "color": tuple(so.color),
                "opacity": float(so.opacity),
                "visible": bool(so.visible),
                "selected": bool(so.selected),
                "locked": bool(so.locked),
                "group": so.group,
                "poly": poly_copy,
                "transform": transform_copy,
            })

        return snapshot

    def _restore_scene_state(self, snapshot: List[Dict[str, Any]]):
        """用快照還原 ObjectPropertyManager + Object3DManager 的狀態。"""
        # 1) 清空現有場景
        for actor in list(self.obj3d_mgr.all_actors()):
            obj_id = self.obj3d_mgr.get_obj_id_from_actor(actor)
            if obj_id is not None:
                self.obj3d_mgr.remove_actor(obj_id)

        self.prop_mgr.clear_all()

        # 2) 依照快照重新建立物件和 actor
        for obj_state in snapshot:
            # 建立 SceneObject（保留原 id）
            so = SceneObject(
                id=obj_state["id"],
                name=obj_state.get("name", ""),
                kind=obj_state.get("kind", "original"),
                parent_id=obj_state.get("parent_id"),
                polydata=obj_state["poly"],
                visible=obj_state.get("visible", True),
                color=tuple(obj_state.get("color", (0.8, 0.8, 0.8))),
                opacity=float(obj_state.get("opacity", 1.0)),
                selected=bool(obj_state.get("selected", False)),
                locked=bool(obj_state.get("locked", False)),
                group=obj_state.get("group", "default"),
                transform=vtk.vtkTransform(),
            )
            # 確保 name 恢復（防止資料不完整）
            so.name = str(obj_state.get("name", so.name))

            # restore transform
            tr = obj_state.get("transform")
            if isinstance(tr, vtk.vtkTransform):
                so.transform.DeepCopy(tr)

            self.prop_mgr.register_object(so)

        # 3) 依照 property manager 建立 actor
        for so in self.prop_mgr.get_all_objects():
            self.obj3d_mgr.spawn_actor(so.id)

        # 4) 讓 actor 跟 SceneObject 完全同步
        self.obj3d_mgr.refresh_all_from_properties()

        if callable(self.on_restored):
            self.on_restored()

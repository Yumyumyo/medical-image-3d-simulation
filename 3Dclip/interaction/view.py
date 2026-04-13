#from __future__ import annotations

import vtk

from interaction.base_mode import BaseInteractionMode


class NoLeftDragCameraStyle(vtk.vtkInteractorStyleTrackballCamera):
    """Trackball camera style with left-button drag disabled."""

    def OnLeftButtonDown(self):
        return

    def OnLeftButtonUp(self):
        return


class CameraMoveMode(BaseInteractionMode):
    """
    使用 VTK 內建的 TrackballCamera 來控制相機。

    設計重點：
    - on_mode_enter() 時，把 interactor style 換成 TrackballCamera
    - 其他事件函式可以全部空著，讓 VTK 自己處理滑鼠/鍵盤
    """

    def __init__(self, interactor: vtk.vtkRenderWindowInteractor, renderer: vtk.vtkRenderer) -> None:
        super().__init__(name="camera", interactor=interactor)

        self._renderer = renderer
        self._style = NoLeftDragCameraStyle()
        # 讓 style 知道要操作哪個 renderer / interactor
        self._style.SetDefaultRenderer(self._renderer)
        self._style.SetInteractor(self._interactor)

    # ---- 模式切換 ----

    def on_mode_enter(self) -> None:
        """
        切換到 Camera 模式時，讓 interactor 使用 TrackballCamera style。
        """
        self._interactor.SetInteractorStyle(self._style)

    def on_mode_exit(self) -> None:
        """
        離開 Camera 模式時，不一定要清掉 style，
        通常由下一個 mode 決定要不要換自己的 style。
        可以在之後 PlaneCutMode 內部改成 vtkInteractorStyleUser() 等。
        """
        # 目前先不強制改回 None，避免閃爍或意外行為。
        pass

    # ---- 事件處理 ----
    # CameraMoveMode 不需要自己實作滑鼠/鍵盤，
    # 因為 vtkInteractorStyleTrackballCamera 已經幫你做好，
    # InteractionModeManager 的事件 callback 對這個 mode 可視為 no-op。

    # 你也可以視需要 override on_key_press 以攔截特定按鍵。
    def on_key_press(self, interactor: vtk.vtkRenderWindowInteractor, key_sym: str) -> None:
        # 例如按下 'r' reset camera （可以視需求加入）
        if key_sym.lower() == "r":
            self._renderer.ResetCamera()
            self._interactor.Render()

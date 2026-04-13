from __future__ import annotations

from typing import Optional

import vtk


class BaseInteractionMode:
    """
    所有互動模式的基底類別。

    - 不直接綁定 VTK event（由 InteractionModeManager 統一處理）
    - 子類別只需要實作自己需要的事件即可
    """

    def __init__(self, name: str, interactor: vtk.vtkRenderWindowInteractor) -> None:
        self.name = name
        self._interactor = interactor

    # ---- 模式切換週期 ----

    def on_mode_enter(self) -> None:
        """被設為 current_mode 時呼叫。"""
        pass

    def on_mode_exit(self) -> None:
        """被其他模式取代前呼叫。"""
        pass
    
    # ---- 滑鼠事件 ----

    def on_left_button_down(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        pass

    def on_left_button_up(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        pass

    def on_right_button_down(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        pass

    def on_right_button_up(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        pass

    def on_mouse_move(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        pass

    def on_mouse_wheel_forward(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        pass

    def on_mouse_wheel_backward(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        pass
    
    # ---- 初始化模式按鈕事件 ----
    def toggle_selecting(self) -> None:
        pass
    
    def toggle_marking(self) -> None:
        pass

    def commit(self) -> None:
        pass
      
    def clear_markers(self) -> None:
        pass
    
    def reset(self) -> None:
        pass
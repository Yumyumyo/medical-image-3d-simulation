#from __future__ import annotations

from typing import Dict, Optional, Callable, List, Tuple

import vtk

from interaction.base_mode import BaseInteractionMode


class InteractionModeManager:
    """
    負責：
    - 接收 VTK 的互動事件
    - 將事件轉送給目前啟用的 BaseInteractionMode
    - 提供 set_mode("camera" / "plane_cut" / ...) 給 UI 使用

    不負責：
    - 實際互動邏輯（交給各個 Mode）
    - 相機 / 切割 / 移動的具體行為
    """

    def __init__(self, interactor: vtk.vtkRenderWindowInteractor) -> None:
        self._interactor = interactor

        self._modes: Dict[str, BaseInteractionMode] = {}
        self._current_mode: Optional[BaseInteractionMode] = None
        self._current_mode_name: Optional[str] = None

        # 紀錄 VTK observer 的 tag（以便必要時移除）
        self._observer_ids = []
        self._bind_vtk_events()
        self._key_callbacks = {}
        self._single_click_callbacks: List[Callable[[], None]] = []
        self._left_button_down_filter_callbacks: List[Callable[[], bool]] = []
        self._left_button_release_callbacks: List[Callable[[], None]] = []
        self._suppress_style = vtk.vtkInteractorStyleUser()
        self._style_before_suppress = None

    # ------------------------------------------------------------------
    # mode 註冊 / 切換
    # ------------------------------------------------------------------

    def register_mode(self, mode: BaseInteractionMode) -> None:
        """
        註冊一個互動模式。
        mode.name 會當成 key，例如 "camera", "plane_cut"。
        """
        self._modes[mode.name] = mode

    def register_left_button_down_filter(self, callback: Callable[[], bool]) -> None:
        if callback not in self._left_button_down_filter_callbacks:
            self._left_button_down_filter_callbacks.append(callback)

    def register_left_button_release_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._left_button_release_callbacks:
            self._left_button_release_callbacks.append(callback)

    def set_mode(self, name: str) -> None:
        """
        切換目前互動模式。

        UI 範例：
            mode_manager.set_mode("camera")
            mode_manager.set_mode("plane_cut")
        """
        
        "for debug check set mode success"
        print("set_mode called with:", name, flush=True)
        print("available:", list(self._modes.keys()), flush=True)

        if name == self._current_mode_name:
            return

        # 離開舊模式
        if self._current_mode is not None:
            self._current_mode.on_mode_exit()

        # 啟用新模式
        mode = self._modes.get(name)
        if mode is None:
            raise KeyError(f"Interaction mode '{name}' not registered.")

        self._current_mode = mode
        self._current_mode_name = name
        self._current_mode.on_mode_enter()

    def get_current_mode_name(self) -> Optional[str]:
        return self._current_mode_name

    # ------------------------------------------------------------------
    # VTK 事件綁定（統一入口）
    # ------------------------------------------------------------------

    def _bind_vtk_events(self) -> None:
        """
        將 VTK 的互動事件綁到本 manager。
        manager 再把事件轉送給 current_mode。
        """
        # 這裡 obj 就是 interactor 本人，不太需要用到
        self._observer_ids.append(
            self._interactor.AddObserver("LeftButtonPressEvent", self._on_left_button_down)
        )
        self._observer_ids.append(
            self._interactor.AddObserver("LeftButtonReleaseEvent", self._on_left_button_up)
        )
        self._observer_ids.append(
            self._interactor.AddObserver("RightButtonPressEvent", self._on_right_button_down)
        )
        self._observer_ids.append(
            self._interactor.AddObserver("RightButtonReleaseEvent", self._on_right_button_up)
        )
        self._observer_ids.append(
            self._interactor.AddObserver("MouseMoveEvent", self._on_mouse_move)
        )
        self._observer_ids.append(
            self._interactor.AddObserver("MouseWheelForwardEvent", self._on_mouse_wheel_forward)
        )
        self._observer_ids.append(
            self._interactor.AddObserver("MouseWheelBackwardEvent", self._on_mouse_wheel_backward)
        )
        self._observer_ids.append(
            self._interactor.AddObserver("KeyPressEvent", self._on_key_press)
        )

    # ------------------------------------------------------------------
    # VTK event handlers → 轉送到 current_mode
    # ------------------------------------------------------------------

    def _get_mode(self) -> Optional[BaseInteractionMode]:
        return self._current_mode
    
    def current_mode(self) -> Optional[BaseInteractionMode]:
        """給 UI 取得目前模式用（Phase 0）。"""
        return self._current_mode

    def _reset_style_interaction_state(self, style) -> None:
        if style is None:
            return
        if hasattr(style, "reset_interaction_state"):
            try:
                style.reset_interaction_state()
            except Exception:
                pass
        for fn_name in ("EndRotate", "EndPan", "EndSpin", "EndDolly"):
            fn = getattr(style, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    def _on_left_button_down(self, obj, event_name) -> None:
        suppress = False
        for cb in list(self._left_button_down_filter_callbacks):
            try:
                if cb():
                    suppress = True
            except Exception:
                pass
        for cb in list(self._single_click_callbacks):
            try:
                cb()
            except Exception:
                pass

        if suppress:
            # 鎖住本次 left-drag 的相機旋轉；left up 後再恢復原 style
            self._style_before_suppress = self._interactor.GetInteractorStyle()
            self._reset_style_interaction_state(self._style_before_suppress)
            self._interactor.SetInteractorStyle(self._suppress_style)
        if suppress and hasattr(obj, "SetAbortFlag"):
            obj.SetAbortFlag(1)
        mode = self._get_mode()
        if mode is not None:
            mode.on_left_button_down(self._interactor)

    def _on_left_button_up(self, obj, event_name) -> None:
        for cb in list(self._left_button_release_callbacks):
            try:
                cb()
            except Exception:
                pass
        mode = self._get_mode()
        if mode is not None:
            mode.on_left_button_up(self._interactor)
        if self._style_before_suppress is not None:
            self._reset_style_interaction_state(self._style_before_suppress)
            self._interactor.SetInteractorStyle(self._style_before_suppress)
            self._style_before_suppress = None

    def _on_right_button_down(self, obj, event_name) -> None:
        mode = self._get_mode()
        if mode is not None:
            mode.on_right_button_down(self._interactor)

    def _on_right_button_up(self, obj, event_name) -> None:
        mode = self._get_mode()
        if mode is not None:
            mode.on_right_button_up(self._interactor)

    def _on_mouse_move(self, obj, event_name) -> None:
        mode = self._get_mode()
        if mode is not None:
            mode.on_mouse_move(self._interactor)

    def _on_mouse_wheel_forward(self, obj, event_name) -> None:
        mode = self._get_mode()
        if mode is not None:
            mode.on_mouse_wheel_forward(self._interactor)

    def _on_mouse_wheel_backward(self, obj, event_name) -> None:
        mode = self._get_mode()
        if mode is not None:
            mode.on_mouse_wheel_backward(self._interactor)

    def _on_key_press(self, obj, event_name) -> None:
        key_sym = self._interactor.GetKeySym()
        print("VTK KeyPressEvent fired", key_sym, flush=True)

        # --- UI-level key binding (包含 Enter) ---
        cb = self._key_callbacks.get(key_sym)
        if cb is not None:
            cb()
            return
        # --- fallback: mode-specific ---
        mode = self._get_mode()
        if mode is not None:
            try:
                mode.on_key_press(self._interactor, key_sym)
            except TypeError:
                mode.on_key_press(key_sym)

    def register_key_callback(self, key: str, cb) -> None:
        """
        key: VTK key_sym，例如 's', 'p', 'c'
        cb: callable
        """
        self._key_callbacks[key] = cb

    def register_single_click_callback(self, cb: Callable[[], None]) -> None:
        self._single_click_callbacks.append(cb)

    def handle_key_press(self, event) -> bool:
        """
        給 Qt keyPressEvent 使用。回傳 True 表示已處理。
        """
        key_sym = None
        text = ""
        try:
            text = event.text()
        except Exception:
            text = ""

        if text:
            if len(text) == 1 and text.isalpha():
                key_sym = text.lower()
            else:
                key_sym = text
        else:
            try:
                from PyQt5 import QtCore
                key = event.key()
                key_map = {
                    QtCore.Qt.Key_Escape: "Escape",
                    QtCore.Qt.Key_Up: "Up",
                    QtCore.Qt.Key_Down: "Down",
                    QtCore.Qt.Key_Left: "Left",
                    QtCore.Qt.Key_Right: "Right",
                    QtCore.Qt.Key_Return: "Return",
                    QtCore.Qt.Key_Enter: "KP_Enter",
                    QtCore.Qt.Key_BracketLeft: "[",
                    QtCore.Qt.Key_BracketRight: "]",
                }
                key_sym = key_map.get(key)
            except Exception:
                key_sym = None

        if not key_sym:
            return False

        cb = self._key_callbacks.get(key_sym)
        if cb is not None:
            cb()
            return True

        mode = self._get_mode()
        if mode is not None and hasattr(mode, "on_key_press"):
            try:
                return bool(mode.on_key_press(self._interactor, key_sym))
            except TypeError:
                return bool(mode.on_key_press(key_sym))

        return False

# layout.py
from PyQt5 import QtWidgets, QtCore

class LayoutManager:
    def __init__(self, main_window):
        self.main_window = main_window
        self.ui = main_window.ui
        self.adjusting = False  # 防止遞歸調用
        
    def setup_layout_sizing(self):
        """設置左側和右側面板的大小策略，使其可以伸縮"""
        # 設置左側面板 (widget_node_6) 可伸縮
        if hasattr(self.ui, 'widget_node_6'):
            self.ui.widget_node_6.setMinimumWidth(200)  # 最小寬度
            # 允許左側面板隨視窗寬度擴展
            self.ui.widget_node_6.setMaximumWidth(16777215)
            self.ui.widget_node_6.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding
            )
            
            # 使內容widget也可以伸縮
            if hasattr(self.ui, 'frame_node_9'):
                self.ui.frame_node_9.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Expanding
                )
        
        # 設置右側面板 (widget_node_5) 可伸縮
        if hasattr(self.ui, 'widget_node_5'):
            self.ui.widget_node_5.setMinimumWidth(300)  # 最小寬度
            self.ui.widget_node_5.setMaximumWidth(500)  # 最大寬度（可選）
            
            # 使內容widget也可以伸縮
            if hasattr(self.ui, 'frame_node_7'):
                self.ui.frame_node_7.setSizePolicy(
                    QtWidgets.QSizePolicy.Preferred,
                    QtWidgets.QSizePolicy.Preferred
                )
        
        # 設置中間視圖區域 (viewer_container_5) 可優先伸縮
        if hasattr(self.ui, 'viewer_container_5'):
            self.ui.viewer_container_5.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding
            )
        
        # 設置水平布局 (horizontalLayout_6) 的 stretch 和 spacing
        if hasattr(self.ui, 'horizontalLayout_6'):
            # 設置伸縮比例
            self.ui.horizontalLayout_6.setStretch(0, 2)   # 左側控制面板
            self.ui.horizontalLayout_6.setStretch(1, 8)   # 中間視圖區域（減少這個值）
            self.ui.horizontalLayout_6.setStretch(2, 3)   # 右側列表區域
            
            # 設置間距
            self.ui.horizontalLayout_6.setSpacing(10)  # 控制面板之間的間距
        
        print("Initialization complete")
    
    def setup_ui_enhancements(self):
        """設置UI增強功能"""
        # 設置整個窗體的布局策略 - 改為 Preferred 避免無限擴張
        self.main_window.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        
        # 設置主網格布局
        self.ui.gridLayout.setColumnStretch(0, 1)
        self.ui.gridLayout.setRowStretch(0, 1)
        
        # 設置所有子widget的尺寸策略 - 改為 Preferred
        for widget in self.main_window.findChildren(QtWidgets.QWidget):
            if widget.objectName().startswith(('widget_', 'frame_')):
                if not widget.objectName() in ['widget_node_5', 'widget_node_6', 'viewer_container_5']:
                    widget.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
    
    def adjust_dynamic_layout(self):
        """動態調整布局 - 加入防護機制"""
        if self.adjusting: return
        self.adjusting = True
        
        try:
            window_width = self.main_window.width()
            
            # 1. 調整比例 (完全照抄 cut2/main.py)
            if hasattr(self.ui, 'horizontalLayout_6'):
                if window_width < 1200:
                    self.ui.horizontalLayout_6.setStretch(0, 1)
                    self.ui.horizontalLayout_6.setStretch(1, 8)
                    self.ui.horizontalLayout_6.setStretch(2, 4)
                elif window_width < 1600:
                    self.ui.horizontalLayout_6.setStretch(0, 2)
                    self.ui.horizontalLayout_6.setStretch(1, 10)
                    self.ui.horizontalLayout_6.setStretch(2, 5)
                else:
                    self.ui.horizontalLayout_6.setStretch(0, 2)
                    self.ui.horizontalLayout_6.setStretch(1, 12)
                    self.ui.horizontalLayout_6.setStretch(2, 6)
            
            # 2. 更新列表寬度
            self.update_list_widths()
            
        except Exception as e:
            print(f"Dynamic layout adjustment error: {e}")
        finally:
            self.adjusting = False
    
    def safe_update_list_widths(self):
        """安全更新列表寬度 - 不觸發布局變更"""
        try:
            # 為右側面板的樹狀列表設置適當寬度
            if hasattr(self.ui, 'widget_node_5'):
                # 獲取右側面板的可用寬度
                available_width = self.ui.widget_node_5.width()
                
                # 設置樹狀列表的寬度（留出一些邊距）
                list_width = max(250, available_width - 20)  # 減去邊距
                
                for tree in [self.ui.importedFileList, self.ui.cutObjectsList]:
                    if tree:
                        # 設置固定寬度或最小寬度
                        tree.setMinimumWidth(list_width)
                        tree.setMaximumWidth(500)  # 限制最大寬度
            
            # 為左側面板的控件設置適當寬度
            if hasattr(self.ui, 'widget_node_6'):
                left_width = self.ui.widget_node_6.width()
                button_width = max(120, left_width - 40)  # 按鈕寬度
                
                # 遍歷左側面板的所有按鈕和下拉框
                from PyQt5 import QtWidgets
                for widget in self.ui.frame_node_9.findChildren(QtWidgets.QPushButton):
                    # icon-only 工具按鈕維持固定大小，不參與文字按鈕寬度拉伸
                    if widget.objectName() in {"btn_undo", "btn_redo", "btn_save"}:
                        continue
                    widget.setMinimumWidth(button_width)
                    widget.setMaximumWidth(300)
                
                for widget in self.ui.frame_node_9.findChildren(QtWidgets.QComboBox):
                    widget.setMinimumWidth(button_width)
                    widget.setMaximumWidth(300)
                        
        except Exception as e:
            print(f"更新列表寬度時出錯: {e}")
    
    def update_list_widths(self):
        """更新列表寬度的公開方法"""
        self.safe_update_list_widths()
    

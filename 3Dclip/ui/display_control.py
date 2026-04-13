# display_control.py
from PyQt5 import QtWidgets, QtCore, QtGui

class DisplayControl:
    """獨立頂部工具欄，集成到布局中"""
    
    def __init__(self, main_window):
        self.main_window = main_window
        self.ui = main_window.ui

        # 隱藏控制台時保留其尺寸，避免左側面板寬度跟著縮小
        if hasattr(self.ui, 'textBrowser'):
            policy = self.ui.textBrowser.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            self.ui.textBrowser.setSizePolicy(policy)

        self.create_top_toolbar_in_layout()
        self.is_fullscreen = False
        self.original_geometry = {}  # 儲存原始幾何資訊
        # 預設不顯示控制台，需由上方按鈕手動打開
        self.toggle_console()
    
    def create_top_toolbar_in_layout(self):
        """創建集成到布局中的頂部工具欄"""
        # 創建工具欄
        self.toolbar = QtWidgets.QFrame()
        self.toolbar.setObjectName("display_toolbar")
        self.toolbar.setFixedHeight(35)
        self.toolbar.setStyleSheet("""
            QFrame#display_toolbar {
                background-color: #1F2A35;
                border-bottom: 1px solid #2E3A45;
            }
        """)
        
        # 創建水平布局
        layout = QtWidgets.QHBoxLayout(self.toolbar)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)
        
        # 添加標籤
        title_label = QtWidgets.QLabel("顯示控制:")
        title_label.setStyleSheet("""
            QLabel {
                color: #0078D7;
                font-weight: bold;
                font-size: 11px;
            }
        """)
        layout.addWidget(title_label)
        
        # 添加控制項目
        self.create_toolbar_controls(layout)
        
        # 將工具欄添加到現有布局的頂部
        self.insert_toolbar_into_layout()
        
        # 儲存原始幾何資訊
        self.save_original_geometry()
    
    def save_original_geometry(self):
        """儲存原始元件幾何資訊"""
        self.original_geometry = {}
        
        # 儲存3D視窗的原始大小和位置
        if hasattr(self.ui, 'viewer_container_5'):
            viewer = self.ui.viewer_container_5
            self.original_geometry['viewer_container'] = {
                'x': viewer.x(),
                'y': viewer.y(),
                'width': viewer.width(),
                'height': viewer.height()
            }
        
        # 儲存2D視窗的原始大小和位置
        if hasattr(self.ui, 'frame_2D_5'):
            frame_2d = self.ui.frame_2D_5
            self.original_geometry['frame_2D'] = {
                'x': frame_2d.x(),
                'y': frame_2d.y(),
                'width': frame_2d.width(),
                'height': frame_2d.height(),
                'visible': frame_2d.isVisible()
            }
    
    def insert_toolbar_into_layout(self):
        """將工具欄插入到現有布局的頂部"""
        # 方法：創建一個新的垂直布局容器
        # 獲取主窗口的中心widget（如果有的話）
        central_widget = self.main_window
        
        # 創建一個垂直布局的容器
        main_container = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 添加工具欄
        main_layout.addWidget(self.toolbar)
        
        # 創建內容容器來裝原有的UI
        content_container = QtWidgets.QWidget()
        
        # 獲取原有的UI布局
        if hasattr(self.ui, 'gridLayout'):
            # 從原父窗口中移除gridLayout
            old_parent = self.ui.gridLayout.parent()
            if old_parent:
                # 創建一個新的widget來裝原有的gridLayout
                content_container.setLayout(self.ui.gridLayout)
        
        # 添加內容容器
        main_layout.addWidget(content_container)
        
        # 設置新的布局到主窗口
        main_container_layout = QtWidgets.QVBoxLayout(central_widget)
        main_container_layout.setContentsMargins(0, 0, 0, 0)
        main_container_layout.addWidget(main_container)
        
        # 保存內容容器引用
        self.content_container = content_container
    
    def create_toolbar_controls(self, layout):
        """創建工具欄控制項目"""
        # 2D視圖按鈕
        self.btn_2d = self.create_toolbar_button("2D視圖", True)
        self.btn_2d.clicked.connect(self.toggle_2d)
        layout.addWidget(self.btn_2d)
        
        # 左側面板按鈕
        self.btn_left = self.create_toolbar_button("左側面板", True)
        self.btn_left.clicked.connect(self.toggle_left)
        layout.addWidget(self.btn_left)
        
        # 右側列表按鈕
        self.btn_right = self.create_toolbar_button("右側列表", True)
        self.btn_right.clicked.connect(self.toggle_right)
        layout.addWidget(self.btn_right)
        
        # 控制台按鈕
        self.btn_console = self.create_toolbar_button("控制台", True)
        self.btn_console.setChecked(False)
        self.btn_console.clicked.connect(self.toggle_console)
        layout.addWidget(self.btn_console)
        
        # 分隔線
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.VLine)
        line.setStyleSheet("background-color: #2E3A45;")
        layout.addWidget(line)
        
        # 功能按鈕
        self.btn_all = self.create_toolbar_button("顯示全部", False)
        self.btn_all.clicked.connect(self.show_all)
        layout.addWidget(self.btn_all)
        
        self.btn_full = self.create_toolbar_button("全屏3D", False)
        self.btn_full.clicked.connect(self.toggle_fullscreen_with_center)
        layout.addWidget(self.btn_full)
        
        # 彈性空間
        layout.addStretch()
        
        # 狀態標籤
        self.status_label = QtWidgets.QLabel("正常模式")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #A0A8B0;
                font-size: 10px;
                padding-right: 5px;
            }
        """)
        layout.addWidget(self.status_label)
    
    def create_toolbar_button(self, text, checkable):
        """創建工具欄按鈕"""
        btn = QtWidgets.QPushButton(text)
        btn.setCheckable(checkable)
        if checkable:
            btn.setChecked(True)
        btn.setFixedHeight(25)
        btn.setMinimumWidth(70)
        btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        btn.setStyleSheet("""
            QPushButton {
                background-color: #2D3A46;
                border: 1px solid #3A4B5B;
                border-radius: 3px;
                color: #E6EBF0;
                font-size: 10px;
                padding: 2px 8px;
            }
            QPushButton:checked {
                background-color: #0078D7;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3B4A5A;
                border: 1px solid #4A5B6A;
            }
            QPushButton:pressed {
                background-color: #1A5C8F;
            }
        """)
        return btn
    
    # ===== 控制功能 =====
    
    def toggle_2d(self):
        """切換2D視圖顯示，並動態調整3D視窗大小"""
        show = self.btn_2d.isChecked()
        
        if hasattr(self.ui, 'frame_2D_5'):
            self.ui.frame_2D_5.setVisible(show)
        
        # 動態調整3D視窗大小
        self.adjust_3d_view_on_2d_toggle(show)
        
        self.update_status()
    
    def adjust_3d_view_on_2d_toggle(self, show_2d):
        """根據2D視圖顯示狀態調整3D視窗大小"""
        if not hasattr(self.ui, 'viewer_container_5'):
            return
        
        viewer = self.ui.viewer_container_5
        parent = viewer.parentWidget()
        
        if not parent:
            return
        
        # 獲取父容器大小
        parent_width = parent.width()
        parent_height = parent.height()
        toolbar_height = self.toolbar.height() if hasattr(self, 'toolbar') else 0
        
        if show_2d:
            # 顯示2D時，恢復原始布局
            # 查找viewer_container在horizontalLayout_6中的位置
            if hasattr(self.ui, 'horizontalLayout_6'):
                # 讓布局管理器處理
                pass
            else:
                # 手動調整：如果2D顯示，3D佔右側3/4
                viewer_width = int(parent_width * 0.75)
                viewer_height = parent_height - toolbar_height
                viewer_x = parent_width - viewer_width
                viewer_y = toolbar_height
                viewer.setGeometry(viewer_x, viewer_y, viewer_width, viewer_height)
        else:
            # 隱藏2D時，3D視窗填滿整個區域
            viewer_width = parent_width
            viewer_height = parent_height - toolbar_height
            viewer_x = 0
            viewer_y = toolbar_height
            viewer.setGeometry(viewer_x, viewer_y, viewer_width, viewer_height)
        
        # 強制重繪
        viewer.update()
        
        # 如果有VTK widget，也重繪
        if hasattr(self.main_window, 'vtkWidget'):
            self.main_window.vtkWidget.GetRenderWindow().Render()
    
    def toggle_left(self):
        show = self.btn_left.isChecked()
        if hasattr(self.ui, 'widget_node_6'):
            self.ui.widget_node_6.setVisible(show)
        self.update_status()
    
    def toggle_right(self):
        show = self.btn_right.isChecked()
        if hasattr(self.ui, 'widget_node_5'):
            self.ui.widget_node_5.setVisible(show)
        self.update_status()
    
    def toggle_console(self):
        show = self.btn_console.isChecked()
        if hasattr(self.ui, 'textBrowser'):
            self.ui.textBrowser.setVisible(show)
        self.update_status()
    
    def update_status(self):
        """更新狀態標籤"""
        status_parts = []
        
        if hasattr(self, 'btn_2d'):
            status_parts.append(f"2D:{'✓' if self.btn_2d.isChecked() else '✗'}")
        if hasattr(self, 'btn_left'):
            status_parts.append(f"左:{'✓' if self.btn_left.isChecked() else '✗'}")
        if hasattr(self, 'btn_right'):
            status_parts.append(f"右:{'✓' if self.btn_right.isChecked() else '✗'}")
        if hasattr(self, 'btn_console'):
            status_parts.append(f"控制台:{'✓' if self.btn_console.isChecked() else '✗'}")
        
        if self.is_fullscreen:
            status_parts.append("全屏")
        
        self.status_label.setText(" | ".join(status_parts))
    
    def show_all(self):
        """顯示全部元件"""
        self.btn_2d.setChecked(True)
        self.btn_left.setChecked(True)
        self.btn_right.setChecked(True)
        self.btn_console.setChecked(True)
        
        self.toggle_2d()
        self.toggle_left()
        self.toggle_right()
        self.toggle_console()
        
        # 如果是在全屏模式，退出全屏
        if self.is_fullscreen:
            self.toggle_fullscreen_with_center()
    
    def toggle_fullscreen_with_center(self):
        """全屏切換，並將3D視窗置中（x和y都置中）"""
        if not self.is_fullscreen:
            # 進入全屏模式
            self.is_fullscreen = True
            
            # 隱藏其他元件（保留工具列和3D）
            self.btn_2d.setChecked(False)
            self.btn_left.setChecked(False)
            self.btn_right.setChecked(False)
            self.btn_console.setChecked(False)
            
            self.toggle_2d()
            self.toggle_left()
            self.toggle_right()
            self.toggle_console()
            
            # 調整3D視窗位置和大小（完全置中）
            self.adjust_3d_view_for_fullscreen()
            
            # 更新全屏按鈕文字
            self.btn_full.setText("退出全屏")
            
        else:
            # 退出全屏模式
            self.is_fullscreen = False
            
            # 恢復顯示
            self.btn_2d.setChecked(True)
            self.btn_left.setChecked(True)
            self.btn_right.setChecked(True)
            self.btn_console.setChecked(True)
            
            self.toggle_2d()
            self.toggle_left()
            self.toggle_right()
            self.toggle_console()
            
            # 恢復3D視窗
            self.restore_3d_view()
            
            # 更新全屏按鈕文字
            self.btn_full.setText("全屏3D")
        
        self.update_status()
    
    def adjust_3d_view_for_fullscreen(self):
        """全屏模式調整3D視窗位置和大小（x和y都置中）"""
        if not hasattr(self.ui, 'viewer_container_5'):
            return
        
        viewer = self.ui.viewer_container_5
        
        # 獲取父窗口
        parent = viewer.parentWidget()
        if not parent:
            parent = self.main_window
        
        # 獲取可用空間（考慮工具列高度）
        toolbar_height = self.toolbar.height() if hasattr(self, 'toolbar') else 0
        parent_width = parent.width()
        parent_height = parent.height()
        
        # 計算可用高度（減去工具列）
        available_height = parent_height - toolbar_height
        
        # 設定3D視窗大小（保留一些邊距，讓使用者可以看到工具列）
        # 使用90%的寬度和高度，這樣周圍有留白
        margin_ratio = 0.05  # 5%的邊距
        
        viewer_width = int(parent_width * (1 - 2 * margin_ratio))
        viewer_height = int(available_height * (1 - 2 * margin_ratio))
        
        # 計算置中位置（x和y都置中）
        x = (parent_width - viewer_width) // 2
        y = toolbar_height + (available_height - viewer_height) // 2
        
        # 設置3D視窗位置和大小
        viewer.setGeometry(x, y, viewer_width, viewer_height)
        
        # 強制重繪
        viewer.update()
        
        # 如果有VTK widget，也重繪
        if hasattr(self.main_window, 'vtkWidget'):
            self.main_window.vtkWidget.GetRenderWindow().Render()
    
    def restore_3d_view(self):
        """恢復3D視窗到正常模式"""
        # 正常模式下，讓布局管理器處理
        if hasattr(self, 'content_container'):
            self.content_container.show()
        
        # 如果有VTK widget，重繪
        if hasattr(self.main_window, 'vtkWidget'):
            self.main_window.vtkWidget.GetRenderWindow().Render()
    
    def on_window_resize(self):
        """窗口大小變化時的處理"""
        if self.is_fullscreen:
            # 如果在全屏模式，重新調整3D視窗（保持置中）
            self.adjust_3d_view_for_fullscreen()
        else:
            # 如果在正常模式，根據2D顯示狀態調整3D視窗
            if hasattr(self, 'btn_2d'):
                self.adjust_3d_view_on_2d_toggle(self.btn_2d.isChecked())

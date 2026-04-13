def theme(app):
    app.setStyleSheet("""
        QWidget {
            background-color: #14191F;
            color: #E5EAF0;
            font-family: "Microsoft JhengHei";
            font-size: 14px;
        }

        /* 特別處理 widget_node_5，使其背景透明或與底色相同 */
        QWidget#widget_node_5 {
            background-color: #14191F; /* 與底色相同 */
            border: none; /* 移除邊框 */
        }
        
        /* 特別處理 widget_node_6 */
        QWidget#widget_node_6 {
            background-color: transparent;
        }

        QLabel {
            color: #E8EDF3;
        }

        QFrame {
            background-color: #1A2028;
            border: 1px solid #28313C;
            border-radius: 6px;
        }

        QPushButton {
            background-color: #1F2A35;
            color: #E6EBF0;
            border: 1px solid #2E3A45;
            border-radius: 6px;
            padding: 6px 12px;
        }
        QPushButton:hover {
            background-color: #2D3A46;
            color: white;
        }
        QPushButton:pressed {
            background-color: #3A4B5B;
        }

        QComboBox {
            background-color: #1C232C;
            color: #E6EBF0;
            border: 1px solid #2E3A45;
            border-radius: 4px;
            padding: 4px;
        }
        QComboBox:hover {
            border: 1px solid #0078D7;
        }
        QComboBox QAbstractItemView {
            background-color: #1F2731;
            color: #E6EBF0;
            selection-background-color: #0078D7;
            selection-color: #FFFFFF;
            border-radius: 4px;
        }

        QPlainTextEdit {
            background-color: #1A2028;
            color: #E6EBF0;
            border: 1px solid #2E3A45;
            border-radius: 6px;
            padding: 6px;
            selection-background-color: #0078D7;
        }

        QSlider::groove:horizontal {
            background: #1E252E;
            height: 6px;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #0078D7;
            width: 14px;
            height: 14px;
            margin: -4px 0;
            border-radius: 7px;
        }
        QSlider::sub-page:horizontal {
            background: #2196F3;
            border-radius: 3px;
        }

        QTreeWidget {
            background-color: #1C232C;
            alternate-background-color: #202833;
            color: #E6EBF0;
            border: 1px solid #2E3A45;
            border-radius: 6px;
        }
        QTreeWidget::item:selected {
            background-color: #0078D7;
            color: #FFFFFF;
        }
        QTreeWidget::item:hover {
            background-color: #2A3542;
        }

        QHeaderView::section {
            background-color: #1F2731;
            color: #E6EBF0;
            border: 1px solid #2E3A45;
            padding: 4px;
        }

        QScrollBar:vertical {
            background: #1E252E;
            width: 10px;
            margin: 0;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #2E3A45;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: #3B4A5A;
        }
        
        /* 額外處理 viewport 相關的 widget */
        QWidget#viewer_container_5 {
            background-color: transparent;
        }
        
        QWidget#widget_7,
        QWidget#widget_3,
        QWidget#widget,
        QWidget#widget_6,
        QWidget#widget_5,
        QWidget#widget_2 {
            background-color: transparent;
        }
        
        /* 處理文本瀏覽器 */
        QTextBrowser {
            background-color: #1A2028;
            color: #E6EBF0;
            border: 1px solid #2E3A45;
            border-radius: 6px;
            padding: 6px;
        }
    """)

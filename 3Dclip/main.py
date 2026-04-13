import sys
from PyQt5 import QtWidgets

from ui.main_window import MainWindow
from ui import background_design
from ui import untitled_rc

##test which branch would it be

def main():
    app = QtWidgets.QApplication(sys.argv)
    background_design.theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

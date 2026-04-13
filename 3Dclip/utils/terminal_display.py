from PyQt5 import QtCore

class TerminalDisplay(QtCore.QObject):
    text_written = QtCore.pyqtSignal(str)
    def write(self, text):
        if text.strip():
            self.text_written.emit(str(text))
    def flush(self):
        pass
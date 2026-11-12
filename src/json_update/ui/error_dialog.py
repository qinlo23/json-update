from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)


class ErrorDialog(QDialog):
    def __init__(self, title, summary, errors, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self._previous_state = Qt.WindowState.WindowNoState
        layout = QVBoxLayout(self)
        label = QLabel(summary)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlainText('\n'.join(errors) if errors else '没有错误。')
        layout.addWidget(self.details, 1)
        buttons = QHBoxLayout()
        copy = QPushButton('复制全部')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.details.toPlainText()))
        buttons.addWidget(copy)
        buttons.addStretch()
        self.fullscreen_button = QPushButton('全屏')
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        buttons.addWidget(self.fullscreen_button)
        close = QPushButton('关闭')
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.fullscreen_shortcut = QShortcut(QKeySequence('F11'), self)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        available = self.screen().availableGeometry()
        self.resize(min(1000, int(available.width() * .9)),
                    min(700, int(available.height() * .9)))

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.setWindowState(self._previous_state)
            self.fullscreen_button.setText('全屏')
        else:
            self._previous_state = self.windowState()
            self.showFullScreen()
            self.fullscreen_button.setText('退出全屏')

    def reject(self):
        # QDialog routes Escape here even when a child text editor has focus.
        if self.isFullScreen():
            self.toggle_fullscreen()
        else:
            super().reject()

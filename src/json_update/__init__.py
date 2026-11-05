"""JSON batch field editor."""


def main() -> None:
    import sys
    from PySide6.QtWidgets import QApplication
    from json_update.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName('JSON 批量字段编辑工具')
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

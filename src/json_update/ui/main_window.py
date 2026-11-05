from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from json_update.core.session import Document, scan


class ScanWorker(QThread):
    completed = Signal(int, object, object, object)

    def __init__(self, generation, root, file_pattern, known, parent):
        super().__init__(parent)
        self.generation, self.root = generation, root
        self.file_pattern, self.known = file_pattern, known

    def run(self):
        documents, errors, candidates = {}, [], []
        try:
            paths, errors = scan(self.root, self.isInterruptionRequested)
            for path in paths:
                if self.isInterruptionRequested():
                    return
                if not self.file_pattern.search(path.relative_to(self.root).as_posix()):
                    continue
                candidates.append(path)
                if path in self.known:
                    continue
                try:
                    documents[path] = Document.load(path)
                except (OSError, ValueError, RecursionError) as exc:
                    errors.append(f'{path}: {exc}')
        except Exception as exc:
            errors.append(f'扫描失败：{exc}')
        self.completed.emit(self.generation, documents, candidates, errors)


class SaveWorker(QThread):
    completed = Signal(object, object)

    def __init__(self, documents, parent):
        super().__init__(parent)
        self.documents = documents

    def run(self):
        successes, errors = [], []
        for doc in self.documents:
            try:
                count = doc.save()
                successes.append((doc.path, count))
            except Exception as exc:
                errors.append(f'{doc.path}: {exc}')
        self.completed.emit(successes, errors)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('JSON 批量字段筛选与修改工具')
        self.resize(1180, 780)
        self.root = None
        self.documents = {}
        self.current = None
        self.generation = 0
        self.workers = set()
        self.saving = False
        self.after_save = None
        self.errors = []
        self.field_pattern = re.compile('^label$')
        self.file_pattern = re.compile('')

        shell = QWidget()
        outer = QVBoxLayout(shell)
        splitter = QSplitter()
        outer.addWidget(splitter)
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.addWidget(QLabel('目录（递归扫描 JSON 文件）'))
        row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setReadOnly(True)
        self.choose = QPushButton('选择目录')
        self.choose.clicked.connect(self.choose_directory)
        row.addWidget(self.directory, 1)
        row.addWidget(self.choose)
        layout.addLayout(row)
        layout.addWidget(QLabel('JSON 文件筛选正则（匹配相对路径）'))
        self.file_regex = QPlainTextEdit()
        self.file_regex.setPlaceholderText(r'.*config.*\.json$；留空匹配所有文件')
        self.file_regex.setMaximumHeight(90)
        layout.addWidget(self.file_regex)
        self.file_error = QLabel()
        self.file_error.setWordWrap(True)
        self.file_error.setStyleSheet('color: #b42318')
        layout.addWidget(self.file_error)
        layout.addWidget(QLabel('JSON 字段筛选正则（匹配名称或完整路径）'))
        self.field_regex = QPlainTextEdit('^label$')
        self.field_regex.setMaximumHeight(90)
        layout.addWidget(self.field_regex)
        self.field_error = QLabel()
        self.field_error.setWordWrap(True)
        self.field_error.setStyleSheet('color: #b42318')
        layout.addWidget(self.field_error)
        layout.addWidget(QLabel('匹配的 JSON 文件（* 表示未保存）'))
        self.files = QListWidget()
        self.files.currentItemChanged.connect(self.select_file)
        layout.addWidget(self.files, 1)
        splitter.addWidget(left)

        right = QWidget()
        editor_layout = QVBoxLayout(right)
        self.heading = QLabel('选择目录后，点击左侧文件开始编辑')
        self.heading.setWordWrap(True)
        editor_layout.addWidget(self.heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        editor_layout.addWidget(self.scroll, 1)
        self.clear_editor()
        self.save_button = QPushButton('完成并保存')
        self.save_button.clicked.connect(lambda: self.save_all())
        editor_layout.addWidget(self.save_button, alignment=Qt.AlignmentFlag.AlignRight)
        splitter.addWidget(right)
        splitter.setSizes([410, 770])
        splitter.setStretchFactor(0, 35)
        splitter.setStretchFactor(1, 65)
        footer = QHBoxLayout()
        self.status = QLabel('请选择目录；字段正则默认筛选 label')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.error_button = QPushButton('查看错误 (0)')
        self.error_button.clicked.connect(self.show_errors)
        footer.addWidget(self.error_button)
        outer.addLayout(footer)
        self.setCentralWidget(shell)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.refresh)
        self.file_regex.textChanged.connect(self.schedule_refresh)
        self.field_regex.textChanged.connect(self.schedule_refresh)

    def clear_editor(self):
        content = QWidget()
        self.fields_layout = QVBoxLayout(content)
        self.fields_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(content)

    def dirty(self):
        return [doc for doc in self.documents.values() if doc.edits]

    def protect_changes(self, continuation):
        if not self.dirty():
            continuation()
            return
        box = QMessageBox(self)
        box.setWindowTitle('存在未保存的修改')
        box.setText('保存所有修改后继续，还是放弃修改？')
        save = box.addButton('保存', QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton('放弃', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == save:
            self.save_all(continuation)
        elif box.clickedButton() == discard:
            continuation()

    def choose_directory(self):
        selected = QFileDialog.getExistingDirectory(self, '选择 JSON 主目录', str(self.root or Path.home()))
        if not selected:
            return
        root = Path(selected).resolve()
        if root == self.root:
            self.refresh()
            return
        self.protect_changes(lambda: self.set_root(root))

    def set_root(self, root):
        self.root = root
        self.documents.clear()
        self.current = None
        self.directory.setText(str(root))
        self.files.clear()
        self.clear_editor()
        self.heading.setText('正在查找匹配字段…')
        self.refresh()

    def schedule_refresh(self):
        # Invalidate pending results immediately, including while debounce is pending.
        self.generation += 1
        for worker in self.workers:
            worker.requestInterruption()
        self.timer.start()

    def refresh(self):
        valid = True
        patterns = {}
        for widget, error, attribute in (
            (self.file_regex, self.file_error, 'file_pattern'),
            (self.field_regex, self.field_error, 'field_pattern'),
        ):
            try:
                pattern = re.compile(widget.toPlainText())
                error.clear()
                patterns[attribute] = pattern
            except re.error as exc:
                error.setText(f'正则错误：{exc}')
                valid = False
        if not valid or self.root is None or self.saving:
            return
        for attribute, pattern in patterns.items():
            setattr(self, attribute, pattern)
        self.generation += 1
        for worker in self.workers:
            worker.requestInterruption()
        worker = ScanWorker(self.generation, self.root, self.file_pattern,
                            set(self.documents), self)
        self.workers.add(worker)
        worker.completed.connect(self.scan_finished)
        worker.finished.connect(lambda w=worker: self.release_worker(w))
        self.status.setText('正在扫描与解析…')
        worker.start()

    def release_worker(self, worker):
        self.workers.discard(worker)
        worker.deleteLater()

    def scan_finished(self, generation, documents, candidates, errors):
        if generation != self.generation or self.saving:
            return
        self.documents.update(documents)
        self.errors = list(errors)
        previous = self.current
        self.files.blockSignals(True)
        self.files.clear()
        selected = None
        for path in candidates:
            doc = self.documents.get(path)
            if doc is None:
                continue
            self.errors.extend(f'{path}: {error}' for error in doc.errors)
            matched = doc.matching(self.field_pattern)
            if matched:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, path)
                self.files.addItem(item)
                self.update_item(item)
                if path == previous:
                    selected = item
        self.files.blockSignals(False)
        if selected is None and self.files.count():
            selected = self.files.item(0)
        self.files.setCurrentItem(selected)
        if selected is None:
            self.current = None
            self.clear_editor()
            self.heading.setText('没有匹配的字符串字段')
        else:
            self.select_file(selected)
        self.error_button.setText(f'查看错误 ({len(self.errors)})')
        self.update_status()

    def update_item(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        doc = self.documents[path]
        item.setText(('* ' if doc.edits else '') + path.relative_to(self.root).as_posix())
        item.setToolTip(str(path))

    def update_status(self):
        dirty = self.dirty()
        self.status.setText(f'匹配 {self.files.count()} 个文件；待保存 {len(dirty)} 个文件 / '
                            f'{sum(len(d.edits) for d in dirty)} 个字段；错误 {len(self.errors)} 项')

    def select_file(self, item, previous=None):
        if item is None:
            return
        self.current = item.data(Qt.ItemDataRole.UserRole)
        doc = self.documents[self.current]
        groups = doc.editor_groups(self.field_pattern)
        field_count = sum(len(group) for group in groups)
        self.heading.setText(f'{self.current.relative_to(self.root).as_posix()} — '
                             f'{len(groups)} 个编辑项 / {field_count} 个字段')
        self.clear_editor()
        for targets in groups:
            target = targets[0]
            value = doc.edits.get(target, target.original)
            title = f'{value or "（空标签）"}（{len(targets)} 处）' if target.name == 'label' else target.name
            label = QLabel(title)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet('font-weight: bold')
            self.fields_layout.addWidget(label)
            path_label = QLabel(target.path if len(targets) == 1 else
                                f'同名标签共 {len(targets)} 处，修改此输入框将全部同步（悬停查看路径）')
            path_label.setToolTip('\n'.join(f.path for f in targets))
            path_label.setTextFormat(Qt.TextFormat.PlainText)
            path_label.setWordWrap(True)
            path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.fields_layout.addWidget(path_label)
            editor = QPlainTextEdit()
            editor.setPlainText(value)
            editor.setMinimumHeight(75)
            editor.setMaximumHeight(150)
            editor.textChanged.connect(lambda d=doc, group=targets, e=editor: self.record_edit(d, group, e))
            self.fields_layout.addWidget(editor)
        self.fields_layout.addStretch()

    def record_edit(self, doc, targets, editor):
        doc.edit_group(targets, editor.toPlainText())
        for index in range(self.files.count()):
            item = self.files.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == doc.path:
                self.update_item(item)
                break
        self.update_status()

    def save_all(self, continuation=None):
        if self.saving:
            return
        documents = self.dirty()
        if not documents:
            if continuation:
                continuation()
            else:
                QMessageBox.information(self, '保存', '没有需要保存的修改。')
            return
        self.saving = True
        self.after_save = continuation
        self.timer.stop()
        self.generation += 1
        for worker in self.workers:
            worker.requestInterruption()
        self.centralWidget().setEnabled(False)
        self.status.setText('正在安全保存…')
        self.save_worker = SaveWorker(documents, self)
        self.save_worker.completed.connect(self.save_finished)
        self.save_worker.finished.connect(self.save_worker.deleteLater)
        self.save_worker.start()

    def save_finished(self, successes, errors):
        self.saving = False
        self.centralWidget().setEnabled(True)
        self.errors.extend(errors)
        self.error_button.setText(f'查看错误 ({len(self.errors)})')
        for index in range(self.files.count()):
            self.update_item(self.files.item(index))
        if self.files.currentItem():
            self.select_file(self.files.currentItem())
        self.update_status()
        message = f'成功保存 {len(successes)} 个文件，修改 {sum(n for _, n in successes)} 个字段。'
        if errors:
            box = QMessageBox(QMessageBox.Icon.Warning, '部分文件保存失败',
                              message + f'\n失败 {len(errors)} 个文件，修改已保留。', parent=self)
            box.setDetailedText('\n'.join(errors))
            box.exec()
        else:
            QMessageBox.information(self, '保存完成', message)
        continuation, self.after_save = self.after_save, None
        if continuation and not errors:
            continuation()

    def show_errors(self):
        box = QMessageBox(self)
        box.setWindowTitle('扫描与保存错误')
        box.setText(f'共 {len(self.errors)} 项错误。' if self.errors else '没有错误。')
        if self.errors:
            box.setDetailedText('\n'.join(self.errors))
        box.exec()

    def closeEvent(self, event: QCloseEvent):
        if self.saving:
            event.ignore()
            return
        event.ignore()
        self.protect_changes(self.finish_close)

    def finish_close(self):
        for worker in tuple(self.workers):
            worker.requestInterruption()
            worker.wait()
        # Save completion may be queued just before QThread exits.
        if hasattr(self, 'save_worker'):
            try:
                self.save_worker.wait()
            except RuntimeError:
                pass
        self.hide()
        QApplication.instance().quit()

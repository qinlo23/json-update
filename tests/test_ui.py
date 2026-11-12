import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit

from json_update.ui.main_window import MainWindow
from json_update.ui.error_dialog import ErrorDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('后台任务未在规定时间完成')


def stop(window, app):
    window.timer.stop()
    for worker in tuple(window.workers):
        worker.requestInterruption()
        worker.wait()
    app.processEvents()
    window.deleteLater()
    app.processEvents()


def test_edit_switch_filter_save(app, tmp_path, monkeypatch):
    for name in ('one', 'two'):
        (tmp_path / f'{name}.json').write_text(json.dumps({'label': name}), encoding='utf-8')
    window = MainWindow()
    window.file_regex.clear()  # These scenarios exercise all fixture files.
    window.timer.stop()
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    window.set_root(tmp_path)
    wait_for(app, lambda: window.files.count() == 2 and not window.workers)
    editor = window.scroll.widget().findChild(QPlainTextEdit)
    editor.setPlainText('新标签')
    first = window.current
    window.files.setCurrentRow(1)
    assert window.current != first
    window.scroll.widget().findChild(QPlainTextEdit).setPlainText('另一个标签')
    window.files.setCurrentRow(0)
    assert window.scroll.widget().findChild(QPlainTextEdit).toPlainText() == '新标签'
    assert len(window.dirty()) == 2
    window.file_regex.setPlainText('no-files')
    window.timer.stop()
    window.refresh()
    wait_for(app, lambda: not window.workers)
    assert window.files.count() == 0
    assert len(window.dirty()) == 2
    window.save_all()
    wait_for(app, lambda: not window.saving)
    assert not window.dirty()
    assert json.loads(first.read_text(encoding='utf-8-sig'))['label'] == '新标签'
    assert json.loads((tmp_path / 'two.json').read_text(encoding='utf-8-sig'))['label'] == '另一个标签'
    stop(window, app)


def test_invalid_regex_stale_scan_and_bad_file(app, tmp_path):
    (tmp_path / 'good.json').write_text('{"label":"ok"}', encoding='utf-8')
    (tmp_path / 'bad.json').write_text('{', encoding='utf-8')
    window = MainWindow()
    window.file_regex.clear()  # These scenarios exercise all fixture files.
    window.timer.stop()
    window.set_root(tmp_path)
    wait_for(app, lambda: not window.workers)
    assert window.files.count() == 1 and window.errors
    before = window.field_pattern
    window.field_regex.setPlainText('[')
    window.timer.stop()
    window.refresh()
    assert window.field_error.text()
    assert window.field_pattern is before
    window.scan_finished(window.generation - 1, {}, [], [])
    assert window.files.count() == 1
    stop(window, app)


@pytest.mark.parametrize('choice', ['保存', '放弃', '取消'])
def test_unsaved_prompt_choices(app, tmp_path, monkeypatch, choice):
    (tmp_path / 'data.json').write_text('{"label":"old"}', encoding='utf-8')
    window = MainWindow()
    window.file_regex.clear()  # These scenarios exercise all fixture files.
    window.timer.stop()
    window.set_root(tmp_path)
    wait_for(app, lambda: not window.workers)
    window.scroll.widget().findChild(QPlainTextEdit).setPlainText('new')
    selected = []
    def exec_box(box):
        selected[:] = [next(button for button in box.buttons() if button.text() == choice)]
    monkeypatch.setattr(QMessageBox, 'exec', exec_box)
    monkeypatch.setattr(QMessageBox, 'clickedButton', lambda box: selected[0])
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    continued = []
    window.protect_changes(lambda: continued.append(True))
    if choice == '保存':
        wait_for(app, lambda: not window.saving)
        assert not window.dirty()
        assert json.loads((tmp_path / 'data.json').read_text(encoding='utf-8-sig'))['label'] == 'new'
    assert bool(continued) == (choice != '取消')
    stop(window, app)


def test_partial_failure_keeps_edits_and_blocks_continuation(app, tmp_path, monkeypatch):
    for name in ('one', 'two'):
        (tmp_path / f'{name}.json').write_text('{"label":"old"}', encoding='utf-8')
    window = MainWindow()
    window.file_regex.clear()  # These scenarios exercise all fixture files.
    window.timer.stop()
    window.set_root(tmp_path)
    wait_for(app, lambda: not window.workers)
    for doc in window.documents.values():
        doc.edit(doc.fields[0], 'new')
    (tmp_path / 'two.json').write_text('{"label":"external"}', encoding='utf-8')
    monkeypatch.setattr(ErrorDialog, 'exec', lambda *args: None)
    continued = []
    window.save_all(lambda: continued.append(True))
    wait_for(app, lambda: not window.saving)
    assert len(window.dirty()) == 1
    assert window.dirty()[0].path.name == 'two.json'
    assert not continued
    assert window.errors
    for index in range(window.files.count()):
        item = window.files.item(index)
        expected = '#b58100' if item.data(Qt.ItemDataRole.UserRole).name == 'two.json' else '#16803c'
        assert item.foreground().color().name() == expected
    stop(window, app)


def test_grouped_label_input_edits_current_file_without_dialog(app, tmp_path):
    source = {'items': [{'label': '摆放物品'}, {'label': '摆放物品'}, {'label': '椅子推归位'}]}
    for name in ('one', 'two'):
        (tmp_path / f'{name}.json').write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    window = MainWindow()
    window.file_regex.clear()  # These scenarios exercise all fixture files.
    window.timer.stop()
    window.set_root(tmp_path)
    wait_for(app, lambda: not window.workers)
    current = window.documents[window.current]
    original = current.path.read_bytes()
    editors = window.scroll.widget().findChildren(QPlainTextEdit)
    assert len(editors) == 2
    assert editors[0].toPlainText() == '摆放物品'
    editors[0].setPlainText('整理物品')
    editors[0].setPlainText('整理完成')
    assert len(current.edits) == 2
    assert len(current.label_groups()['整理完成']) == 2
    assert current.path.read_bytes() == original
    assert all(not doc.edits for doc in window.documents.values() if doc is not current)
    assert window.files.currentItem().text().startswith('* ')
    window.files.setCurrentRow(1)
    window.files.setCurrentRow(0)
    editors = window.scroll.widget().findChildren(QPlainTextEdit)
    assert len(editors) == 2 and editors[0].toPlainText() == '整理完成'
    editors[0].setPlainText('摆放物品')
    assert not current.edits
    stop(window, app)


def test_file_colors_and_session_reset(app, tmp_path, monkeypatch):
    (tmp_path / 'data.json').write_text('{"label":"old"}', encoding='utf-8')
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    window = MainWindow()
    window.file_regex.clear()
    window.timer.stop()
    window.set_root(tmp_path)
    wait_for(app, lambda: not window.workers)
    assert window.files.item(0).foreground().style() == Qt.BrushStyle.NoBrush
    editor = window.scroll.widget().findChild(QPlainTextEdit)
    editor.setPlainText('new')
    assert window.files.item(0).foreground().color().name() == '#b58100'
    editor.setPlainText('old')
    assert window.files.item(0).foreground().style() == Qt.BrushStyle.NoBrush
    editor.setPlainText('new')
    window.save_all()
    wait_for(app, lambda: not window.saving)
    assert window.files.item(0).foreground().color().name() == '#16803c'
    editor = window.scroll.widget().findChild(QPlainTextEdit)
    editor.setPlainText('again')
    assert window.files.item(0).foreground().color().name() == '#b58100'
    editor.setPlainText('new')
    assert window.files.item(0).foreground().color().name() == '#16803c'
    window.refresh()
    wait_for(app, lambda: not window.workers)
    assert window.files.item(0).foreground().color().name() == '#16803c'
    window.file_regex.setPlainText('no-files')
    window.timer.stop()
    window.refresh()
    wait_for(app, lambda: not window.workers)
    assert window.files.count() == 0
    window.file_regex.clear()
    window.timer.stop()
    window.refresh()
    wait_for(app, lambda: not window.workers)
    assert window.files.item(0).foreground().color().name() == '#16803c'
    window.set_root(tmp_path)
    wait_for(app, lambda: not window.workers)
    assert window.files.item(0).foreground().style() == Qt.BrushStyle.NoBrush
    stop(window, app)


def test_error_dialog_details_copy_and_fullscreen(app):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton

    errors = ['<b>纯文本</b> ' + '很长的错误' * 100] * 100
    dialog = ErrorDialog('错误', '共 100 项错误', errors)
    dialog.show()
    app.processEvents()
    assert dialog.details.isReadOnly()
    assert dialog.details.toPlainText() == '\n'.join(errors)
    assert dialog.details.verticalScrollBar().maximum() > 0
    assert dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    available = dialog.screen().availableGeometry()
    assert dialog.width() <= available.width() * .9
    assert dialog.height() <= available.height() * .9
    next(b for b in dialog.findChildren(QPushButton) if b.text() == '复制全部').click()
    assert app.clipboard().text() == '\n'.join(errors)
    dialog.resize(500, 400)
    app.processEvents()
    assert dialog.size().width() == 500
    dialog.showMaximized()
    app.processEvents()
    dialog.details.setFocus()
    QTest.keyClick(dialog.details, Qt.Key.Key_F11)
    app.processEvents()
    assert dialog.isFullScreen()
    QTest.keyClick(dialog.details, Qt.Key.Key_Escape)
    app.processEvents()
    assert not dialog.isFullScreen() and dialog.isMaximized()
    assert dialog.isVisible()
    dialog.showNormal()
    app.processEvents()
    dialog.fullscreen_button.click()
    assert dialog.isFullScreen()
    dialog.fullscreen_button.click()
    assert not dialog.isFullScreen() and not dialog.isMaximized()
    dialog.close()
    empty = ErrorDialog('错误', '共 0 项错误', [])
    assert empty.details.toPlainText() == '没有错误。'
    empty.deleteLater()
    dialog.deleteLater()

import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit

from json_update.ui.main_window import MainWindow


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
    window.set_root(tmp_path)
    wait_for(app, lambda: not window.workers)
    for doc in window.documents.values():
        doc.edit(doc.fields[0], 'new')
    (tmp_path / 'two.json').write_text('{"label":"external"}', encoding='utf-8')
    monkeypatch.setattr(QMessageBox, 'exec', lambda *args: None)
    continued = []
    window.save_all(lambda: continued.append(True))
    wait_for(app, lambda: not window.saving)
    assert len(window.dirty()) == 1
    assert window.dirty()[0].path.name == 'two.json'
    assert not continued
    assert window.errors
    stop(window, app)

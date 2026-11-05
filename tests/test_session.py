import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from json_update.core.session import Document, display_path, scan, strict_loads


def create(tmp_path, data, name='data.json'):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return Document.load(path)


def test_embedded_labels_and_unchanged_fields(tmp_path):
    inner = {'width': 1600, 'videoSegmentTool': {'result': [
        {'label': '摆放物品', 'start': 1.25, 'attributes': {'手': ['是']}},
        {'label': '椅子推归位', 'start': 2}]}}
    outer = {'task_id': 365, 'annotation': {'result': json.dumps(inner)}, 'video': {'filename': '视频.mp4'}}
    doc = create(tmp_path, outer)
    fields = doc.matching(re.compile('^label$'))
    assert len(fields) == 2
    assert fields[0].path == '$.annotation.result::<json>.videoSegmentTool.result[0].label'
    assert not any(f.path == '$.annotation.result' for f in doc.fields)
    doc.edit(fields[0], '新标签"\\\n中文')
    doc.edit(fields[1], '已归位')
    assert doc.save() == 2
    result = json.loads(doc.path.read_text(encoding='utf-8-sig'))
    assert isinstance(result['annotation']['result'], str)
    new_inner = json.loads(result['annotation']['result'])
    inner['videoSegmentTool']['result'][0]['label'] = '新标签"\\\n中文'
    inner['videoSegmentTool']['result'][1]['label'] = '已归位'
    assert new_inner == inner
    result['annotation']['result'] = outer['annotation']['result']
    assert result == outer
    assert not doc.edits
    assert doc.save() == 0


def test_paths_arrays_and_revert(tmp_path):
    doc = create(tmp_path, {'a.b': [{'中文': '值'}], 'plain': 'x', 'number': 5, 'null': None})
    assert len(doc.fields) == 2
    target = doc.fields[0]
    assert target.path == '$["a.b"][0]["中文"]'
    assert display_path(('a"b',)) == '$["a\\"b"]'
    doc.edit(target, 'new')
    assert doc.edits
    doc.edit(target, '值')
    assert not doc.edits
    assert doc.matching(re.compile(r'a\.b')) == [target]


def test_invalid_internal_isolated(tmp_path):
    doc = create(tmp_path, {'annotation': {'result': '{broken'}, 'name': 'valid'})
    assert doc.errors
    assert [f.name for f in doc.fields] == ['name']
    doc.edit(doc.fields[0], 'changed')
    doc.save()
    assert json.loads(doc.path.read_text(encoding='utf-8-sig'))['annotation']['result'] == '{broken'


@pytest.mark.parametrize('text', ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":0.123456789012345678901}', '{"x":1e-999}', '{broken'])
def test_reject_unsafe_json(text):
    with pytest.raises(ValueError):
        strict_loads(text)


def test_format_preserved(tmp_path):
    path = tmp_path / '格式.json'
    raw = b'\xef\xbb\xbf' + '{\r\n\t"name": "中文",\r\n\t"other": 42\r\n}\r\n'.encode()
    path.write_bytes(raw)
    doc = Document.load(path)
    doc.edit(doc.fields[0], '修改')
    doc.save()
    result = path.read_bytes()
    assert result.startswith(b'\xef\xbb\xbf')
    assert result.endswith(b'\r\n')
    assert b'\r\n\t"' in result
    assert b'\n' not in result.replace(b'\r\n', b'')
    assert '修改'.encode() in result


def test_external_conflict_preserves_edits(tmp_path):
    doc = create(tmp_path, {'name': 'before'})
    doc.edit(doc.fields[0], 'after')
    doc.path.write_text('{"external": true}', encoding='utf-8')
    with pytest.raises(ValueError, match='外部修改'):
        doc.save()
    assert doc.edits
    assert doc.path.read_text(encoding='utf-8-sig') == '{"external": true}'


@pytest.mark.parametrize('failure', ['replace', 'fsync', 'NamedTemporaryFile'])
def test_write_failure_preserves_original_and_retry(tmp_path, failure):
    doc = create(tmp_path, {'name': 'before'})
    doc.edit(doc.fields[0], 'after')
    module = 'tempfile' if failure == 'NamedTemporaryFile' else 'os'
    with patch(f'json_update.core.session.{module}.{failure}', side_effect=OSError('模拟失败')):
        with pytest.raises(OSError):
            doc.save()
    assert doc.path.read_bytes() == doc.raw
    assert doc.edits
    assert not list(tmp_path.glob('.json-update-*'))
    assert doc.save() == 1


def test_scan_nested_symlinks_errors(tmp_path):
    child = tmp_path / '中文'
    child.mkdir()
    (child / 'a.JSON').write_text('{}', encoding='utf-8')
    (tmp_path / 'ignore.txt').write_text('{}', encoding='utf-8')
    try:
        (tmp_path / 'cycle').symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / 'alias.json').symlink_to(child / 'a.JSON')
    except (OSError, NotImplementedError):
        # Windows may require developer mode or administrator privileges.
        pass
    paths, errors = scan(tmp_path)
    assert paths == [child / 'a.JSON']
    assert not errors
    with patch('json_update.core.session.os.scandir', side_effect=PermissionError('拒绝访问')):
        paths, errors = scan(tmp_path)
    assert not paths and errors


def test_sessions_independent_and_partial_save(tmp_path):
    first = create(tmp_path, {'label': 'one'}, 'one.json')
    second = create(tmp_path, {'label': 'two'}, 'two.json')
    first.edit(first.fields[0], 'new one')
    second.edit(second.fields[0], 'new two')
    assert not first.matching(re.compile('no-match'))
    second.path.write_text('external', encoding='utf-8')
    assert first.save() == 1
    with pytest.raises(ValueError):
        second.save()
    assert not first.edits and second.edits


def test_root_string_and_embedded_root_string(tmp_path):
    doc = create(tmp_path, 'original')
    doc.edit(doc.fields[0], 'changed')
    doc.save()
    assert json.loads(doc.path.read_text(encoding='utf-8-sig')) == 'changed'
    doc = create(tmp_path, {'annotation': {'result': '"original"'}})
    doc.edit(doc.fields[0], 'changed')
    doc.save()
    assert json.loads(json.loads(doc.path.read_text(encoding='utf-8-sig'))['annotation']['result']) == 'changed'


def test_invalid_encoding(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_bytes(b'\xff\xfe{}')
    with pytest.raises(UnicodeError):
        Document.load(path)


def test_change_during_write_is_rejected(tmp_path):
    doc = create(tmp_path, {'label': 'old'})
    doc.edit(doc.fields[0], 'new')
    original_raw = doc.raw
    with patch.object(Path, 'read_bytes', side_effect=[original_raw, b'changed externally']):
        with pytest.raises(ValueError, match='保存期间'):
            doc.save()
    assert doc.path.read_bytes() == original_raw
    assert doc.edits
    assert not list(tmp_path.glob('.json-update-*'))


def test_internal_compact_and_outer_no_trailing_newline(tmp_path):
    doc = create(tmp_path, {'annotation': {'result': '{"label":"old","number":1}'}})
    doc.edit(doc.fields[0], '新')
    doc.save()
    assert not doc.path.read_bytes().endswith(b'\n')
    inner = json.loads(doc.path.read_text(encoding='utf-8-sig'))['annotation']['result']
    assert inner == '{"label":"新","number":1}'


def test_grouped_labels_edit_all_occurrences_and_save(tmp_path):
    inner = {'result': [{'label': '摆放物品'}, {'label': '摆放物品'}, {'label': '椅子推归位'}]}
    doc = create(tmp_path, {'annotation': {'result': json.dumps(inner)}, 'description': '摆放物品'})
    other = create(tmp_path, {'label': '摆放物品'}, 'other.json')
    groups = doc.editor_groups(re.compile('^label$'))
    assert [len(g) for g in groups] == [2, 1]
    raw = doc.path.read_bytes()
    doc.edit_group(groups[0], '整理物品')
    doc.edit_group(groups[0], '整理物品完成')  # Further keystrokes keep the same targets.
    assert len(doc.edits) == 2
    assert doc.path.read_bytes() == raw and not other.edits
    assert doc.save() == 2
    saved = json.loads(doc.path.read_text(encoding='utf-8'))
    assert saved['description'] == '摆放物品'
    assert [x['label'] for x in json.loads(saved['annotation']['result'])['result']] == [
        '整理物品完成', '整理物品完成', '椅子推归位']


def test_grouped_labels_revert_merge_and_hidden_occurrences(tmp_path):
    doc = create(tmp_path, [{'label': '旧'}, {'label': '旧'}, {'label': '另一个'}, {'description': '旧'}])
    groups = doc.editor_groups(re.compile('^label$'))
    doc.edit_group(groups[0], '')
    assert len(doc.edits) == 2
    doc.edit_group(groups[0], '旧')
    assert not doc.edits
    doc.edit_group(groups[0], '另一个')
    merged = doc.editor_groups(re.compile('^label$'))
    assert len(merged) == 1 and len(merged[0]) == 3
    doc.edit_group(merged[0], '统一')
    assert len(doc.edits) == 3
    # A matching label brings all identical occurrences into its editor.
    assert len(doc.editor_groups(re.compile(r'^\$\[0\]\.label$'))[0]) == 3
    assert len(doc.editor_groups(re.compile('description'))) == 1
    assert not doc.editor_groups(re.compile('no-match'))

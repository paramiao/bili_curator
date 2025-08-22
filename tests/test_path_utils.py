import sys
from pathlib import Path
sys.path.append('/Users/paramiao/development/bili_curator')
from bili_curator_v6.app.utils.path_utils import strip_info_suffix, base_name_from_json_path


def test_strip_info_suffix():
    assert strip_info_suffix('title.info') == 'title'
    assert strip_info_suffix('title') == 'title'
    assert strip_info_suffix('') == ''


def test_base_name_from_json_path_info_json(tmp_path: Path):
    p = tmp_path / '视频标题.info.json'
    p.write_text('{}', encoding='utf-8')
    assert base_name_from_json_path(p) == '视频标题'


def test_base_name_from_json_path_plain_json(tmp_path: Path):
    p = tmp_path / '复杂 标题.json'
    p.write_text('{}', encoding='utf-8')
    assert base_name_from_json_path(p) == '复杂 标题'

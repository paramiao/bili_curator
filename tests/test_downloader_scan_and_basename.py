from pathlib import Path
from typing import List

from bili_curator_v6.app.utils.path_utils import (
    strip_info_suffix,
    base_name_from_json_path,
)


def _names(paths: List[Path]) -> List[str]:
    return sorted(p.name for p in paths)


def test_rglob_json_matches_info_json(tmp_path: Path):
    # Arrange: create mixed json files
    # a.info.json and a.json should both be discovered by rglob("*.json")
    files = [
        tmp_path / "a.info.json",
        tmp_path / "a.json",
        tmp_path / "中文 标题.info.json",
        tmp_path / "子目录" / "b.info.json",
        tmp_path / "子目录" / "c.json",
    ]
    for p in files:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")

    # Act
    found_json_only = list(tmp_path.rglob("*.json"))
    found_info_json = list(tmp_path.rglob("*.info.json"))

    # Assert
    # 1) *.json should include all *.info.json
    info_set = {p for p in found_info_json}
    json_set = {p for p in found_json_only}
    assert info_set.issubset(json_set)

    # 2) Scanning both patterns then de-duplicating should equal scanning only *.json
    both = {p for p in found_json_only} | {p for p in found_info_json}
    assert both == json_set


def test_basename_extraction_with_info_and_plain_json(tmp_path: Path):
    # Arrange
    f1 = tmp_path / "视频标题.info.json"
    f2 = tmp_path / "复杂 标题.json"
    f1.write_text("{}", encoding="utf-8")
    f2.write_text("{}", encoding="utf-8")

    # Act + Assert
    assert base_name_from_json_path(f1) == "视频标题"
    assert base_name_from_json_path(f2) == "复杂 标题"


def test_strip_info_suffix_idempotent():
    assert strip_info_suffix("title.info") == "title"
    assert strip_info_suffix("title") == "title"
    assert strip_info_suffix("") == ""

from pathlib import Path


def strip_info_suffix(name: str) -> str:
    """去除基名末尾的 '.info' 后缀（若存在）。
    示例：
    - 'title.info' -> 'title'
    - 'title' -> 'title'
    """
    if not name:
        return name
    return name[:-5] if name.endswith('.info') else name


def base_name_from_json_path(json_file: Path) -> str:
    """从 json 路径推导出视频基名，兼容 '*.info.json' 与 '*.json'。
    例如：
    - /path/title.info.json -> 'title'
    - /path/title.json -> 'title'
    """
    stem = json_file.stem  # '*.info.json' -> 'title.info'
    return strip_info_suffix(stem)

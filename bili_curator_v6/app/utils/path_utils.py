from pathlib import Path

# v6 compatibility bridge: delegate to current implementation
from bili_curator.app.utils.path_utils import (
    strip_info_suffix as _strip_info_suffix,
    base_name_from_json_path as _base_name_from_json_path,
)

__all__ = ["strip_info_suffix", "base_name_from_json_path"]

def strip_info_suffix(name: str) -> str:
    return _strip_info_suffix(name)

def base_name_from_json_path(json_file: Path) -> str:
    return _base_name_from_json_path(json_file)

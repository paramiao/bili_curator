"""
集中管理项目中容易混淆的常量（字段名、Settings键等）。
新增开发必须从此处引用，禁止散落硬编码。
"""

# —— API 字段名 ——
API_FIELD_EXPECTED_TOTAL = "expected_total"                  # 推荐使用
API_FIELD_EXPECTED_TOTAL_COMPAT = "expected_total_videos"    # 向后兼容字段

# —— Settings 键格式 ——
SETTINGS_REMOTE_TOTAL_FMT = "remote_total:{id}"              # 统一键
SETTINGS_REMOTE_TOTAL_LEGACY_FMT = "expected_total:{id}"     # 旧键（兼容读取/过渡期双写）


def settings_key_remote_total(sub_id: int) -> str:
    return SETTINGS_REMOTE_TOTAL_FMT.format(id=sub_id)


def settings_key_remote_total_legacy(sub_id: int) -> str:
    return SETTINGS_REMOTE_TOTAL_LEGACY_FMT.format(id=sub_id)

import os
import json
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def setup_app_with_tmp_db(tmp_path: Path):
    # 指向临时 SQLite 路径（在导入 app 之前设置）
    db_file = tmp_path / "test.db"
    os.environ["DB_PATH"] = str(db_file)

    # 延迟导入，确保读取新的 DB_PATH
    from app import models as models_module
    importlib.reload(models_module)

    from app import api as api_module
    importlib.reload(api_module)

    # 组件
    app = api_module.app
    models = models_module

    return app, models


@pytest.fixture()
def client_and_models(tmp_path):
    app, models = setup_app_with_tmp_db(tmp_path)
    client = TestClient(app)
    return client, models


def write_remote_snapshot(session, sub_id: int, total: int):
    # 直接写入 settings，遵循 remote_total_store 的数据结构
    from app.constants import settings_key_remote_total, settings_key_remote_total_legacy
    from app.models import Settings

    payload = {
        'total': int(total),
        'timestamp': '2030-01-01T00:00:00',  # 未来时间，避免过期
        'url': 'https://example.com/mock',
    }
    for key in (settings_key_remote_total(sub_id), settings_key_remote_total_legacy(sub_id)):
        row = session.query(Settings).filter(Settings.key == key).first()
        if not row:
            row = Settings(key=key, value=json.dumps(payload))
            session.add(row)
        else:
            row.value = json.dumps(payload)
    session.commit()


def test_pending_and_remote_total_consistency(client_and_models, tmp_path):
    client, models = client_and_models
    Session = models.db.SessionLocal
    session = Session()

    try:
        # 1) 种入订阅
        sub = models.Subscription(name="测试订阅7", type="collection", url="https://example.com/coll", is_active=True)
        session.add(sub)
        session.commit()
        sid = sub.id

        # 2) 创建两个已下载视频（一个带尺寸字段，一个尺寸字段为空但有磁盘文件以触发回退）
        # 2.1 有 total_size 的视频
        v1 = models.Video(
            bilibili_id="BV1xxxxxx111",
            title="已下载1",
            subscription_id=sid,
            video_path=str(tmp_path / "v1.mp4"),
            total_size=1234,
            downloaded=True,
        )
        # 2.2 尺寸字段为空，但磁盘真实存在
        v2_path = tmp_path / "v2.mp4"
        v2_path.write_bytes(b"0" * 2048)  # 2KB 文件
        v2 = models.Video(
            bilibili_id="BV1xxxxxx222",
            title="已下载2",
            subscription_id=sid,
            video_path=str(v2_path),
            downloaded=True,
            total_size=None,
            file_size=None,
        )

        # 3) 创建一个永久失败视频（不计入 on_disk_total，但要扣除 pending）
        v3 = models.Video(
            bilibili_id="BV1xxxxxx333",
            title="失败视频",
            subscription_id=sid,
            video_path=None,
            downloaded=False,
            download_failed=True,
        )

        session.add_all([v1, v2, v3])
        session.commit()

        # 4) 写入远端快照：期望总数为 10
        write_remote_snapshot(session, sid, total=10)

        # 5) 计算期望口径
        on_disk_total = session.query(models.Video).filter(models.Video.subscription_id == sid, models.Video.video_path.isnot(None)).count()
        failed_perm = session.query(models.Video).filter(models.Video.subscription_id == sid, models.Video.download_failed == True).count()
        expected_total = 10
        pending = max(0, expected_total - on_disk_total - failed_perm)

        # 6) /api/subscriptions 应包含该订阅，且 expected_total/pending 一致
        r1 = client.get("/api/subscriptions")
        assert r1.status_code == 200
        items = r1.json()
        row = next((x for x in items if int(x.get("id")) == sid), None)
        assert row is not None, "订阅未返回在 /api/subscriptions"
        assert row.get("expected_total") == expected_total
        assert row.get("pending") == pending
        assert row.get("on_disk_total") == on_disk_total
        assert row.get("failed_perm") == failed_perm

        # 7) /api/download/aggregate 中该订阅的 pending_estimated/remote_total 与上面一致
        r2 = client.get("/api/download/aggregate")
        assert r2.status_code == 200
        agg = r2.json()
        # 找到该订阅条目
        agg_row = next((x for x in agg if int(x.get("subscription", {}).get("id", -1)) == sid), None)
        assert agg_row is not None, "订阅未返回在 /api/download/aggregate"
        assert int(agg_row.get("pending_estimated") or 0) == pending
        assert int(agg_row.get("downloaded") or 0) == on_disk_total
        # remote_total 可能为 None 或 int，这里统一断言等于 expected_total
        assert int(agg_row.get("remote_total") or 0) == expected_total

        # 8) /api/overview 聚合：remote_total/pending_total/local_total/failed_perm_total 一致
        r3 = client.get("/api/overview")
        assert r3.status_code == 200
        ov = r3.json()
        assert int(ov.get("remote_total") or 0) >= expected_total  # 仅一个订阅时应等于
        assert int(ov.get("pending_total") or 0) >= pending
        assert int(ov.get("local_total") or 0) >= on_disk_total
        assert int(ov.get("failed_perm_total") or 0) >= failed_perm

        # 9) 验证容量回退：downloaded_size_bytes 至少包含 v1.total_size 与 v2 的真实磁盘大小
        sz = int(ov.get("downloaded_size_bytes") or 0)
        assert sz >= (1234 + 2048)

    finally:
        session.close()

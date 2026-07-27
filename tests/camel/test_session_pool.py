import pytest
from app.camel.session_pool import SessionPool


class FakeClient:
    def __init__(self):
        self.created = []
        self.titles = []

    async def create_session(self, http, title, model):
        sid = f"sess-{len(self.created)}"
        self.created.append((title, model))
        return sid

    async def update_session_title(self, http, session_id, title):
        self.titles.append((session_id, title))
        return True


async def test_session_reuse_and_rotation():
    pool = SessionPool(rotate_turns=3)
    client = FakeClient()

    s1 = await pool.get_session(None, client, "claude-opus-4-7", "acc@x.com")
    assert s1 == "sess-0"
    # 3 句内复用
    for _ in range(2):
        pool.record_turn("acc@x.com")
        assert await pool.get_session(None, client, "claude-opus-4-7", "acc@x.com") == "sess-0"
    # 第 3 句到达上限 → 轮换
    pool.record_turn("acc@x.com")
    s2 = await pool.get_session(None, client, "claude-opus-4-7", "acc@x.com")
    assert s2 == "sess-1"
    # 旧会话遗弃不删除（FakeClient 无 delete 调用即证明）


async def test_title_sync_only_once():
    pool = SessionPool(rotate_turns=10)
    client = FakeClient()
    await pool.get_session(None, client, "m", "acc@x.com")
    await pool.sync_title(None, client, "acc@x.com", "你好世界" * 30)
    await pool.sync_title(None, client, "acc@x.com", "第二次不应生效")
    assert len(client.titles) == 1
    assert len(client.titles[0][1]) == 20  # 标题截断 20 字


async def test_independent_accounts():
    pool = SessionPool(rotate_turns=10)
    client = FakeClient()
    sa = await pool.get_session(None, client, "m", "a@x.com")
    sb = await pool.get_session(None, client, "m", "b@x.com")
    assert sa != sb

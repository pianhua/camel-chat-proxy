from app.camel.session_pool import SessionPool


def test_rotate_turns_adjustable_at_runtime():
    pool = SessionPool(rotate_turns=10)
    pool.rotate_turns = 1
    assert pool.rotate_turns == 1

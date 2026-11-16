"""穿测发现的可用性缺陷回归：单用户工作台的并发轮询不得耗尽连接池。

浏览器并发轮询多个读投影（status/journey/列表），慢投影即使在浏览器超时后
仍持有连接到查询完成；默认池（5+10）会被占满，写请求随之 pool timeout 500。
SQLite WAL 支持多并发读，文件库的池必须给轮询留出余量；内存库用
SingletonThreadPool，不接受 QueuePool 参数，必须保持默认。
"""

from shine.db import make_engine


def test_sqlite_file_pool_has_room_for_concurrent_polls(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/pool.db")
    try:
        assert engine.pool.size() >= 20
        assert engine.pool._max_overflow >= 40
        assert engine.pool._timeout >= 60
    finally:
        engine.dispose()


def test_in_memory_sqlite_keeps_default_singleton_pool():
    engine = make_engine("sqlite://")
    try:
        assert type(engine.pool).__name__ == "SingletonThreadPool"
    finally:
        engine.dispose()
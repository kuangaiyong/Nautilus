"""测试数据库统一配置。

私有化部署已全面改用 MySQL，测试同样走 MySQL，但用独立的 `nautilus_test` 库，
与运行库 `nautilus_private` 物理隔离，绝不污染真实数据。可用环境变量
`TEST_DATABASE_URL` 覆盖（如 CI 指向别的实例）。

各测试文件模块级 `create_engine(TEST_DATABASE_URL)`，并在 fixture 里
`drop_all` + `create_all` 保证每个测试从干净表开始（MySQL 物理库不像
SQLite :memory: 自动销毁，必须显式清理）。
"""
import os

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "mysql+pymysql://root:VideoEval2026@127.0.0.1:3306/nautilus_test?charset=utf8mb4",
)

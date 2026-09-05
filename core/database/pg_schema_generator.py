"""PostgreSQL 静态结构文件生成器（开发期工具，不参与运行时）。

用法（在插件仓库根目录）：
    python -m core.database.pg_schema_generator

流程：在临时 SQLite 库上按序执行全部迁移，内省表/索引后复用
postgres_translator 做类型与约束翻译，生成仅含字面量语句的静态结构
文件（固定写入本目录下的 postgres_schema.py）。运行时
（postgres_backend）只消费该静态文件，不做任何运行时 DDL 拼接；
插件结构变更时重新执行本工具刷新即可。
"""

import os
import sqlite3
import tempfile

from .migration import run_migrations
from .postgres_translator import build_schema_registry, foreign_key_statement, quote_ident

# 固定输出位置：与本模块同目录的 postgres_schema.py（不接收外部路径输入）
DEFAULT_OUTPUT = os.path.join(os.path.dirname(__file__), "postgres_schema.py")


def _render_literal(text: str, indent: int = 8) -> str:
    """把多行 DDL 渲染为可嵌入 Python 源码的字符串字面量。"""
    escaped = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    lines = escaped.splitlines()
    if len(lines) <= 1:
        return '"""' + escaped + '"""'
    padding = " " * indent
    body = ("\n" + padding).join(line.rstrip() for line in lines)
    return '"""' + body + "\n" + " " * (indent - 4) + '"""'


def generate_schema_module(migrations_dir: str) -> str:
    """生成 postgres_schema.py 的完整源码文本。"""
    import gc
    import shutil

    temp_dir = tempfile.mkdtemp(prefix="pg_schema_")
    try:
        temp_db = os.path.join(temp_dir, "schema_probe.db")
        sqlite3.connect(temp_db).close()
        run_migrations(temp_db, migrations_dir)
        gc.collect()  # 回收 run_migrations 内部未显式关闭的连接（Windows 文件锁）
        conn = sqlite3.connect(temp_db)
        try:
            registry = build_schema_registry(conn)
        finally:
            conn.close()
    finally:
        gc.collect()
        shutil.rmtree(temp_dir, ignore_errors=True)

    out = []
    out.append('"""PostgreSQL 静态结构清单（由 pg_schema_generator 自动生成，勿手改）。')
    out.append("")
    out.append("内容来自 SQLite 迁移链（001-049）的内省与翻译：")
    out.append("- PG_TABLE_DDL: 每张表的 CREATE TABLE IF NOT EXISTS 语句")
    out.append("- PG_INDEX_DDL: 全部二级索引语句")
    out.append("- PG_FK_DDL: 数据复制完成后补建的外键约束（表名 → 语句列表）")
    out.append("- TABLE_COLUMNS: 表到 [(列名, PG 类型)] 的清单，驱动数据迁移与取值适配")
    out.append("- TABLE_IDENTITY: 表到自增主键列的映射（无自增主键的表缺省）")
    out.append("- TABLE_INSERT_SQL: 表到全列 INSERT 模板（? 占位）的映射，供数据迁移使用")
    out.append('"""')
    out.append("")
    out.append("PG_TABLE_DDL = {")
    for name in sorted(registry["tables"]):
        info = registry["tables"][name]
        out.append("    %s: %s," % (quote_ident(name), _render_literal(info["create_sql"])))
    out.append("}")
    out.append("")
    out.append("PG_INDEX_DDL = [")
    for ddl in registry["indexes"]:
        out.append("    %s," % _render_literal(ddl))
    out.append("]")
    out.append("")
    out.append("PG_FK_DDL = {")
    for name in sorted(registry["tables"]):
        info = registry["tables"][name]
        statements = [
            foreign_key_statement(info, idx, clause)
            for idx, clause in enumerate(info.get("fk_clauses", []))
        ]
        if statements:
            rendered = ", ".join(_render_literal(stmt) for stmt in statements)
            out.append("    %s: [%s]," % (quote_ident(name), rendered))
    out.append("}")
    out.append("")
    out.append("TABLE_COLUMNS = {")
    for name in sorted(registry["tables"]):
        info = registry["tables"][name]
        pairs = ", ".join(
            "(%s, %s)" % (quote_ident(col), repr(pg_type))
            for col, pg_type in info["columns"]
        )
        out.append("    %s: [%s]," % (quote_ident(name), pairs))
    out.append("}")
    out.append("")
    out.append("TABLE_IDENTITY = {")
    for name in sorted(registry["tables"]):
        identity = registry["tables"][name].get("identity")
        if identity:
            out.append("    %s: %s," % (quote_ident(name), quote_ident(identity)))
    out.append("}")
    out.append("")
    out.append("TABLE_INSERT_SQL = {")
    for name in sorted(registry["tables"]):
        info = registry["tables"][name]
        columns = info["columns"]
        col_list = ", ".join(quote_ident(col) for col, _pg_type in columns)
        placeholders = ", ".join(["?"] * len(columns))
        insert_sql = (
            "INSERT INTO " + quote_ident(name)
            + " (" + col_list + ") VALUES (" + placeholders + ")"
        )
        out.append("    %s: %s," % (quote_ident(name), _render_literal(insert_sql, indent=4)))
    out.append("}")
    out.append("")
    out.append("TABLE_NAMES = [")
    for name in sorted(registry["tables"]):
        out.append("    %s," % quote_ident(name))
    out.append("]")
    out.append("")
    return "\n".join(out)


def main():
    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    source = generate_schema_module(migrations_dir)
    # mkstemp 在目标目录内生成安全临时文件，写完后原子替换为固定名
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(DEFAULT_OUTPUT), prefix="postgres_schema_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)
        os.replace(tmp_path, DEFAULT_OUTPUT)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    print("已生成:", DEFAULT_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

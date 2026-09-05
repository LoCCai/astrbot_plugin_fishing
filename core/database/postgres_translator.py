"""SQLite → PostgreSQL 方言翻译层（pg8000 native 接口，:pN 命名参数）。

插件的全部仓储层按 SQLite 方言手写静态 SQL。为了让同一套仓储既能跑在
SQLite 上、又能跑在 PostgreSQL 上，本模块提供两类纯函数翻译：

1. 运行时语句翻译（translate_statement）：在每次执行前把 SQLite 风格语句
   改写为 PostgreSQL 等价语句——`?` 占位符改写为 `:p1/:p2/...` 命名参数、
   INSERT OR IGNORE 改写为 ON CONFLICT DO NOTHING、json_each 改写为
   jsonb_array_elements_text、双参 MAX 改写为 GREATEST、LIKE 改写为
   ILIKE，并剥离 PRAGMA / 显式 BEGIN 等由驱动事务语义替代的语句。
   翻译不引入任何外部数据，所有值仍以命名参数绑定传入。
2. 结构翻译（build_schema_registry / translate_table_ddl）：从 SQLite 数据库
   的内省结果生成 PostgreSQL 建表/索引 DDL 与表结构注册表，供建库引导和
   数据迁移使用。
"""

import re

from astrbot.api import logger

# 表/列名一律先校验再加引号，杜绝标识符注入
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SQLITE_TYPE_MAP = (
    (re.compile(r"^\b(BIG\s*INT|INT8)\b", re.I), "BIGINT"),
    (re.compile(r"^\b(SMALL\s*INT|TINY\s*INT|BOOLEAN|BOOL)\b", re.I), "SMALLINT"),
    (re.compile(r"^\b(REAL|FLOAT|DOUBLE(\s+PRECISION)?)\b", re.I), "DOUBLE PRECISION"),
    (re.compile(r"^\b(NUMERIC|DECIMAL)\b", re.I), "NUMERIC"),
    (re.compile(r"^\bBLOB\b", re.I), "BYTEA"),
    (re.compile(r"^\b(DATETIME|TIMESTAMP)\b", re.I), "TIMESTAMPTZ"),
    (re.compile(r"^\bDATE\b", re.I), "DATE"),
    (re.compile(r"^\bTIME\b", re.I), "TIME"),
    (re.compile(r"^\b(VARCHAR|CHARACTER\s+VARYING|CHAR|CHARACTER|CLOB|TEXT)\b(\s*\(\s*\d+\s*\))?", re.I), "TEXT"),
    (re.compile(r"^\bINT(EGER)?\b", re.I), "BIGINT"),
)

# 列约束关键字的起点，类型 token 扫描遇到即停
_CONSTRAINT_KEYWORDS = ("PRIMARY", "NOT", "NULL", "UNIQUE", "DEFAULT", "CHECK", "REFERENCES", "COLLATE", "CONSTRAINT", "GENERATED", "AUTOINCREMENT")

META_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS fishing_plugin_meta ("
    '"key" TEXT PRIMARY KEY, "value" TEXT)'
)


def quote_ident(name: str) -> str:
    """校验并给标识符加双引号。非法标识符直接拒绝，而不是转义后放行。"""
    if not _IDENTIFIER_RE.match(name or ""):
        raise ValueError("非法数据库标识符: %r" % (name,))
    return '"' + name + '"'


def _split_outside_single_quotes(sql: str):
    """按单引号字符串字面量切段，返回 [(segment, is_literal), ...]。"""
    parts = re.split(r"('(?:[^']|'')*')", sql)
    return [(part, idx % 2 == 1) for idx, part in enumerate(parts) if part]


def _map_outside_single_quotes(sql: str, transform):
    """只对字面量以外的部分应用 transform。"""
    return "".join(part if is_literal else transform(part) for part, is_literal in _split_outside_single_quotes(sql))


def _two_arg_max_to_greatest(sql: str) -> str:
    """SQLite 的 MAX(a, b) 标量函数改为 PostgreSQL 的 GREATEST(a, b)。

    只改写括号内顶层带逗号的 MAX（即标量用法），单参聚合 MAX 保持不变。
    """
    result = []
    pos = 0
    pattern = re.compile(r"\bMAX\s*\(", re.I)
    while True:
        match = pattern.search(sql, pos)
        if match is None:
            result.append(sql[pos:])
            break
        open_index = match.end() - 1
        depth = 0
        close_index = -1
        for i in range(open_index, len(sql)):
            ch = sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_index = i
                    break
        if close_index < 0:
            result.append(sql[pos:])
            break
        inner = sql[open_index + 1:close_index]
        depth = 0
        has_top_level_comma = False
        for ch in inner:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                has_top_level_comma = True
                break
        result.append(sql[pos:match.start()])
        result.append("GREATEST(" if has_top_level_comma else match.group(0))
        pos = open_index + 1
    return "".join(result)


def _identity_column_of(registry, table: str):
    if not registry:
        return None
    info = registry.get("tables", {}).get(table)
    if not info:
        return None
    return info.get("identity")


def _append_conflict_clause(sql: str, clause: str) -> str:
    """把 ON CONFLICT 子句插入语句尾部（若有 RETURNING 则放在它之前）。"""
    returning = re.search(r"\bRETURNING\b", sql, re.I)
    if returning is None:
        return sql.rstrip().rstrip(";") + " " + clause
    head = sql[: returning.start()].rstrip().rstrip(";")
    tail = sql[returning.start():]
    return head + " " + clause + " " + tail


def _rewrite_sqlite_sequence(sql: str, registry) -> str:
    """DELETE FROM sqlite_sequence 重置自增 → identity 列 RESTART。"""
    match = re.search(
        r"DELETE\s+FROM\s+sqlite_sequence\s+WHERE\s+name\s*=\s*'([A-Za-z_]\w*)'",
        sql,
        re.I,
    )
    if match is None:
        return sql
    table = match.group(1)
    identity = _identity_column_of(registry, table)
    if identity is None:
        logger.warning("PG 模式：sqlite_sequence 重置指向未知表 %s，已跳过", table)
        return "SELECT 1"
    return (
        "ALTER TABLE " + quote_ident(table)
        + " ALTER COLUMN " + quote_ident(identity) + " RESTART WITH 1"
    )


def translate_statement(sql: str, registry=None):
    """把 SQLite 方言语句翻译为 pg8000 命名参数风格的 PostgreSQL 语句。

    返回 (translated_sql, kind)；kind 为 "skip" 表示该语句在 PG 下不应执行
    （PRAGMA、显式 BEGIN 等，由驱动自身事务语义替代）。
    """
    statement = sql.strip() if sql else ""
    if not statement:
        return "", "skip", False
    if re.match(r"^PRAGMA\b", statement, re.I):
        return "", "skip", False
    if re.match(r"^BEGIN\b", statement, re.I):
        # pg8000 在首条语句时自动开启事务，无需（也不能）显式 BEGIN
        return "", "skip", False

    # json_each(?) → jsonb_array_elements_text(CAST(? AS jsonb))。
    # 先回填一个 ? 占位，随后统一编号；CAST 形式避免 :name 与 ::type 的解析歧义。
    statement = re.sub(
        r"json_each\(\s*\?\s*\)",
        "jsonb_array_elements_text(CAST(? AS jsonb))",
        statement,
        flags=re.I,
    )

    insert_ignore = re.search(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", statement, re.I)
    insert_replace = re.search(
        r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+([A-Za-z_]\w*)", statement, re.I
    )
    if insert_ignore:
        statement = re.sub(
            r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, count=1, flags=re.I
        )
        statement = _append_conflict_clause(statement, "ON CONFLICT DO NOTHING")
    elif insert_replace:
        table = insert_replace.group(1)
        info = (registry or {}).get("tables", {}).get(table)
        if not info:
            raise RuntimeError(
                "INSERT OR REPLACE 需要表结构注册表，且表 %s 不在其中" % table
            )
        pk = info.get("pk") or []
        cols = [name for name, _pg_type in info.get("columns", []) if name not in pk]
        if not pk or not cols:
            raise RuntimeError("表 %s 缺少可用的主键/列信息，无法翻译 REPLACE" % table)
        statement = re.sub(
            r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\b", "INSERT INTO", statement, count=1, flags=re.I
        )
        assignments = ", ".join(quote_ident(c) + " = EXCLUDED." + quote_ident(c) for c in cols)
        targets = ", ".join(quote_ident(c) for c in pk)
        statement = _append_conflict_clause(
            statement,
            "ON CONFLICT (" + targets + ") DO UPDATE SET " + assignments,
        )

    statement = _rewrite_sqlite_sequence(statement, registry)
    statement = _two_arg_max_to_greatest(statement)
    statement = _map_outside_single_quotes(
        statement, lambda seg: re.sub(r"\bLIKE\b", "ILIKE", seg, flags=re.I)
    )

    # 写入自增主键表时补 RETURNING，让仓储层的 cursor.lastrowid 继续可用
    is_insert = re.match(r"^\s*INSERT\b", statement, re.I)
    appended_returning = False
    if is_insert and registry:
        table_match = re.search(
            r"^\s*INSERT\s+INTO\s+[\"']?([A-Za-z_]\w*)[\"']?", statement, re.I
        )
        if table_match and not re.search(r"\bRETURNING\b", statement, re.I):
            identity = _identity_column_of(registry, table_match.group(1))
            if identity:
                statement = (
                    statement.rstrip().rstrip(";") + " RETURNING " + quote_ident(identity)
                )
                appended_returning = True

    # `?` 依序编号为命名参数 :p1、:p2……（单引号字面量内不处理）
    counter = {"n": 0}

    def _sub(_m):
        counter["n"] += 1
        return ":p" + str(counter["n"])

    statement = _map_outside_single_quotes(statement, lambda seg: re.sub(r"\?", _sub, seg))
    return statement, "sql", appended_returning


# ---------------------------------------------------------------------------
# 结构（DDL）翻译
# ---------------------------------------------------------------------------

def _strip_ident(token: str) -> str:
    return token.strip().strip('"').strip("[").strip("]").strip("`")


def _split_top_level(body: str, separator: str = ","):
    """按括号深度切分顶层分隔符。"""
    parts = []
    current = []
    depth = 0
    in_literal = False
    i = 0
    while i < len(body):
        ch = body[i]
        if in_literal:
            current.append(ch)
            if ch == "'":
                if i + 1 < len(body) and body[i + 1] == "'":
                    current.append("'")
                    i += 1
                else:
                    in_literal = False
        elif ch == "'":
            in_literal = True
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _map_type(type_tokens: str) -> str:
    text = type_tokens.strip()
    for pattern, pg_type in _SQLITE_TYPE_MAP:
        if pattern.match(text):
            return pg_type
    raise ValueError("无法映射的 SQLite 列类型: %r" % type_tokens)


def _translate_default(default_expr: str):
    expr = default_expr.strip()
    if re.match(r"^CURRENT_TIMESTAMP$", expr, re.I):
        return "CURRENT_TIMESTAMP"
    if re.match(r"^TRUE$", expr, re.I):
        return "1"  # 布尔语义列统一映射为 SMALLINT
    if re.match(r"^FALSE$", expr, re.I):
        return "0"
    if re.match(r"^-?\d+(\.\d+)?$", expr):
        return expr
    if re.match(r"^'.*'$", expr, re.S):
        return expr
    if re.match(r"^NULL$", expr, re.I):
        return "NULL"
    raise ValueError("暂不支持的 DEFAULT 表达式: %r（可手工调整 PG 库结构）" % default_expr)


def _parse_column_def(segment: str):
    """解析 SQLite 列定义。

    返回 (name, pg_type, flags, default_expr 或 None, fk_clause 或 None)。
    """
    tokens = segment.replace("\n", " ").strip()
    match = re.match(r"^([\"'`\[]?)([A-Za-z_]\w*)\1?\s+(.*)$", tokens, re.S)
    if match is None:
        raise ValueError("无法解析列定义: %r" % segment)
    name = match.group(2)
    rest = match.group(3).strip()

    pieces = rest.split()
    type_tokens = []
    consumed = 0
    for piece in pieces:
        head = piece.upper().split("(")[0]
        if head in _CONSTRAINT_KEYWORDS or head in ("ASC", "DESC"):
            break
        type_tokens.append(piece)
        consumed += 1
    if not type_tokens:
        raise ValueError("列 %s 缺少类型声明: %r" % (name, segment))
    pg_type = _map_type(" ".join(type_tokens))
    constraints = " ".join(pieces[consumed:]).strip()

    flags = {"not_null": False, "primary_key": False, "unique": False}
    if re.search(r"\bNOT\s+NULL\b", constraints, re.I):
        flags["not_null"] = True
    if re.search(r"\bPRIMARY\s+KEY\b", constraints, re.I):
        flags["primary_key"] = True
    if re.search(r"\bUNIQUE\b", constraints, re.I):
        flags["unique"] = True

    default_expr = None
    default_match = re.search(
        r"\bDEFAULT\s+(.+?)(?=$|\bCHECK\b|\bREFERENCES\b|\bCOLLATE\b|\bUNIQUE\b|\bNOT\s+NULL\b|\bPRIMARY\b)",
        constraints,
        re.I | re.S,
    )
    if default_match:
        default_expr = _translate_default(default_match.group(1))

    fk_clause = None
    references_match = re.search(r"\b(REFERENCES\s+.+)$", constraints, re.I | re.S)
    if references_match:
        fk_clause = re.sub(r"\s+", " ", references_match.group(1)).strip()

    return name, pg_type, flags, default_expr, fk_clause


def _parse_table_constraints(segment: str):
    """识别表级约束，返回 (kind, text)。kind ∈ pk/unique/check/fk/other。"""
    upper = segment.upper()
    if upper.startswith("PRIMARY KEY") or (upper.startswith("CONSTRAINT") and "PRIMARY KEY" in upper):
        return "pk", segment
    if upper.startswith("FOREIGN KEY") or (upper.startswith("CONSTRAINT") and "FOREIGN KEY" in upper):
        return "fk", segment
    if upper.startswith("UNIQUE") or (upper.startswith("CONSTRAINT") and "UNIQUE" in upper):
        return "unique", segment
    if upper.startswith("CHECK") or (upper.startswith("CONSTRAINT") and "CHECK" in upper):
        return "check", segment
    return "other", segment


def _extract_parenthesised(text: str, keyword: str):
    match = re.search(keyword + r"\s*\(([^)]*)\)", text, re.I)
    if match is None:
        return None
    return [_strip_ident(col) for col in match.group(1).split(",") if col.strip()]


def _strip_line_comments(sql: str) -> str:
    """去掉字面量以外的 -- 行注释（保留字符串字面量内容）。"""
    def _sub(segment):
        return re.sub(r"--[^\n]*", "", segment)

    return _map_outside_single_quotes(sql, _sub)


def translate_table_ddl(table_name: str, create_sql: str) -> dict:
    """把一条 SQLite CREATE TABLE 翻译为 PostgreSQL 结构信息。"""
    body_match = re.search(r"\((.*)\)\s*(WITHOUT\s+ROWID|STRICT)?\s*$", create_sql, re.S)
    if body_match is None:
        raise ValueError("无法解析 CREATE TABLE 语句: %s" % table_name)
    body = _strip_line_comments(body_match.group(1))

    columns = []            # [(name, pg_type)]
    column_lines = []       # 已翻译的列定义 SQL
    table_pk = None
    table_constraints = []  # 保留在建表语句内的表级约束（PK/UNIQUE/CHECK）
    fk_clauses = []         # 延后添加的外键约束片段

    for segment in _split_top_level(body):
        first_word = re.match(r"^[\"'`\[]?([A-Za-z_]\w*)", segment)
        keyword = first_word.group(1).upper() if first_word else ""
        if keyword in ("CONSTRAINT", "FOREIGN", "PRIMARY", "UNIQUE", "CHECK"):
            kind, text = _parse_table_constraints(segment)
            if kind == "pk":
                table_pk = _extract_parenthesised(text, "PRIMARY KEY")
            elif kind == "fk":
                fk_clauses.append(re.sub(r"\s+", " ", text).strip())
            elif kind in ("unique", "check"):
                table_constraints.append(re.sub(r"\s+", " ", text).strip())
            continue

        name, pg_type, flags, default_expr, fk_clause = _parse_column_def(segment)
        columns.append((name, pg_type))

        is_integer_family = pg_type in ("BIGINT", "SMALLINT")
        column_identity = flags["primary_key"] and is_integer_family

        parts = [quote_ident(name), pg_type]
        if column_identity:
            # 对齐 SQLite rowid 别名语义：允许显式插入，未提供时自动生成
            parts.append("GENERATED BY DEFAULT AS IDENTITY")
        if flags["not_null"]:
            parts.append("NOT NULL")
        if default_expr is not None:
            parts.append("DEFAULT " + default_expr)
        if flags["unique"]:
            parts.append("UNIQUE")
        if flags["primary_key"]:
            parts.append("PRIMARY KEY")
        column_lines.append(" ".join(parts))

        if fk_clause:
            fk_clauses.append("FOREIGN KEY (" + quote_ident(name) + ") " + fk_clause)

    # 表级单列整型主键同样是 rowid 别名，补 IDENTITY
    if table_pk and len(table_pk) == 1:
        for idx, (name, pg_type) in enumerate(columns):
            if name == table_pk[0] and pg_type in ("BIGINT", "SMALLINT"):
                if "IDENTITY" not in column_lines[idx] and "PRIMARY KEY" not in column_lines[idx]:
                    column_lines[idx] += " GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"

    identity = None
    for (name, _pg_type), line in zip(columns, column_lines):
        if "GENERATED BY DEFAULT AS IDENTITY" in line:
            identity = name
            break

    pk = list(table_pk) if table_pk else []
    if not pk:
        for (name, _pg_type), line in zip(columns, column_lines):
            if re.search(r"\bPRIMARY KEY\b", line):
                pk.append(name)

    quoted_columns = ",\n    ".join(column_lines)
    constraint_lines = ",\n    ".join(table_constraints)
    body_sql = "    " + quoted_columns
    if constraint_lines:
        body_sql += ",\n    " + constraint_lines
    create_pg = "CREATE TABLE IF NOT EXISTS " + quote_ident(table_name) + " (\n" + body_sql + "\n)"

    return {
        "name": table_name,
        "columns": columns,
        "pk": pk,
        "identity": identity,
        "create_sql": create_pg,
        "fk_clauses": fk_clauses,
    }


def build_schema_registry(sqlite_conn) -> dict:
    """从 SQLite 连接内省全部表与索引，生成 PostgreSQL 结构注册表。"""
    rows = sqlite_conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"
    ).fetchall()

    registry = {"tables": {}, "indexes": [], "skipped": []}
    for kind, name, sql in rows:
        if name.startswith("sqlite_"):
            continue
        if kind == "table":
            if name == "schema_version":
                # schema_version 只服务 SQLite 迁移器，PG 侧用 fishing_plugin_meta
                registry["skipped"].append(name)
                continue
            registry["tables"][name] = translate_table_ddl(name, sql)
        elif kind == "index":
            if sql:
                registry["indexes"].append(re.sub(r"\s+", " ", sql.strip()))
    return registry


def foreign_key_statement(table_info, index: int, fk_clause: str) -> str:
    """为延后添加的外键生成带固定约束名的 ALTER 语句。"""
    constraint_name = "fk_" + table_info["name"] + "_" + str(index)
    return (
        "ALTER TABLE " + quote_ident(table_info["name"])
        + " ADD CONSTRAINT " + constraint_name + " " + fk_clause
    )

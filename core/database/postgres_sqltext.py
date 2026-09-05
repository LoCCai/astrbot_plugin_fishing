"""SQLite 方言语句到 PostgreSQL 命名参数语句的准备函数（纯函数，无连接）。

prepare() 的全部处理都是「? 占位符 → :p1/:p2 编号」的等价改写与两类
语句替换（BEGIN/COMMIT/PRAGMA 由 pg8000 事务语义替代、sqlite_sequence
重置由 fishing_reset_identities() 函数承接）；语句文本不携带用户数据，
取值全部经命名参数绑定。
"""

import re

from .postgres_schema import (
    DUMP_INSERT_PATTERN,
    RESET_IDENTITY_CALL,
    SEQUENCE_RESET_PATTERN,
    SKIP_PREFIXES,
)

_SEQUENCE_RESET_RE = re.compile(SEQUENCE_RESET_PATTERN)

# iterdump 数据行形如：INSERT INTO "users" VALUES(...)
_DUMP_INSERT_RE = re.compile(DUMP_INSERT_PATTERN)


def _to_named_placeholders(sql: str, params) -> tuple:
    """把字面量之外的 ? 依序编号为 :p1、:p2……（单引号字面量内不处理）。

    返回 (改写语句, 命名参数字典)。语句文本不含任何用户数据。
    """
    named = {}
    if not params:
        return sql, named
    out = []
    counter = 0
    in_literal = False
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        if in_literal:
            out.append(ch)
            if ch == "'":
                if i + 1 < length and sql[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_literal = False
        elif ch == "'":
            in_literal = True
            out.append(ch)
        elif ch == "?":
            counter += 1
            name = "p" + str(counter)
            named[name] = params[counter - 1]
            out.append(":" + name)
        else:
            out.append(ch)
        i += 1
    return "".join(out), named


def prepare(sql: str, params=None):
    """把 SQLite 方言语句准备为 pg8000 可执行的命名参数语句。

    返回 (statement, named, flags)：
    - statement 为 None 表示该语句在 PG 下应跳过（BEGIN/COMMIT/PRAGMA）；
    - flags 为 dict，含 has_returning（语句是否携带 RETURNING 子句）。
    """
    statement = sql.strip() if sql else ""
    upper = statement.upper()
    if not statement or upper.startswith(tuple(SKIP_PREFIXES)):
        return None, None, {}

    if _SEQUENCE_RESET_RE.match(statement):
        return RESET_IDENTITY_CALL, {"t": _first_param(params)}, {}

    statement, named = _to_named_placeholders(statement, params)
    flags = {"has_returning": " RETURNING " in statement}
    return statement, named, flags


def _first_param(params):
    if not params:
        return None
    return params[0]


def parse_dump_insert(line: str):
    """解析 iterdump 数据行，返回 (表名, 参数值列表) 或 None。

    iterdump 的字面量只有三种：'字符串'（'' 转义）、数字、NULL。
    解析只提取值，值将作为绑定参数写入，不进入任何语句文本。
    """
    match = _DUMP_INSERT_RE.match(line.strip())
    if not match:
        return None
    table = match.group(1)
    body = match.group(2)
    values = []
    current = []
    in_literal = False
    i = 0
    length = len(body)
    while i < length:
        ch = body[i]
        if in_literal:
            if ch == "'":
                if i + 1 < length and body[i + 1] == "'":
                    current.append("'")
                    i += 1
                else:
                    in_literal = False
                    values.append("".join(current))
                    current = []
            else:
                current.append(ch)
        elif ch == "'":
            in_literal = True
            current = []
        elif ch == ",":
            values.append(_dump_token(current))
            current = []
        else:
            current.append(ch)
        i += 1
    values.append(_dump_token(current))
    return table, values


def _dump_token(chars):
    token = "".join(chars).strip()
    if token.upper() == "NULL":
        return None
    return token

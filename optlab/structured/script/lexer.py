"""Tokens of the payoff language, with line and column for error messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

KEYWORDS = {"underlying", "param", "event", "pay", "as", "end", "if", "then", "else", "terminate", "and", "or",
            "not"}


class ScriptError(ValueError):
    """Syntax or validation error, located in the script text."""

    def __init__(self, message: str, line: int = 0, col: int = 0):
        self.message, self.line, self.col = message, line, col
        where = f"line {line}, col {col}: " if line else ""
        super().__init__(where + message)


@dataclass(frozen=True)
class Token:
    kind: str  # NUMBER, DATE, NAME, KEYWORD, OP, NEWLINE, EOF
    text: str
    line: int
    col: int


_TOKEN = re.compile(r"""
    (?P<SPACE>[ \t\r]+)
  | (?P<COMMENT>\#[^\n]*)
  | (?P<NEWLINE>\n)
  | (?P<DATE>\d{4}-\d{2}-\d{2}(?![\w.]))
  | (?P<NUMBER>(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?)
  | (?P<NAME>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<OP><=|>=|==|[-+*/()<>=,])
""", re.VERBOSE)


def tokenize(text: str) -> list[Token]:
    tokens, line, start, pos = [], 1, 0, 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        col = pos - start + 1
        if m is None:
            raise ScriptError(f"unexpected character {text[pos]!r}", line, col)
        kind, value = m.lastgroup, m.group()
        if kind == "NEWLINE":
            tokens.append(Token("NEWLINE", "\n", line, col))
            line, start = line + 1, m.end()
        elif kind == "NAME" and value in KEYWORDS:
            tokens.append(Token("KEYWORD", value, line, col))
        elif kind not in ("SPACE", "COMMENT"):
            tokens.append(Token(kind, value, line, col))
        pos = m.end()
    tokens.append(Token("NEWLINE", "\n", line, pos - start + 1))
    tokens.append(Token("EOF", "", line, pos - start + 1))
    return tokens

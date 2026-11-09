"""PSD language lexer.

Design goals (AI-first):
  - tiny, unambiguous token set
  - newline-terminated statements (semicolons optional)
  - newlines inside ( ) and [ ] are ignored (line continuation)
  - { } are always blocks, so newlines inside them are real statement ends
  - string literals support {expr} interpolation
"""

from __future__ import annotations

from dataclasses import dataclass, field

KEYWORDS = {
    "fn", "let", "if", "elif", "else", "while", "for", "in",
    "return", "break", "continue", "true", "false", "and", "or", "not",
}

# Multi-character operators, longest first.
OPERATORS = [
    "->", "+=", "-=", "*=", "/=", "%=",
    "==", "!=", "<=", ">=", "&&", "||",
    "+", "-", "*", "/", "%", "=", "<", ">", "!",
    "(", ")", "[", "]", "{", "}", ",", ":", ";", ".",
]


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self) -> str:
        return f"error: {self.message} (line {self.line}, col {self.col})"


@dataclass
class Token:
    type: str          # KEYWORD, IDENT, INT, FLOAT, STRING, OP, NEWLINE, EOF
    value: object      # str for OP/KEYWORD/IDENT, int, float, list-of-parts for STRING
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value!r}, {self.line}:{self.col})"


@dataclass
class StrPart:
    """One segment of a string literal: either literal text or an expression."""
    kind: str          # "lit" | "expr"
    text: str
    line: int
    col: int


@dataclass
class Lexer:
    src: str
    pos: int = 0
    line: int = 1
    col: int = 1
    _paren_depth: int = 0      # ( and [ suppress NEWLINE tokens
    tokens: list = field(default_factory=list)

    # ---- low-level helpers -------------------------------------------------
    def _peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.src[i] if i < len(self.src) else ""

    def _advance(self, n: int = 1) -> str:
        out = ""
        for _ in range(n):
            if self.pos >= len(self.src):
                break
            ch = self.src[self.pos]
            out += ch
            self.pos += 1
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        return out

    def _emit_newline(self) -> None:
        if self._paren_depth == 0:
            # collapse runs of newlines into one token
            if self.tokens and self.tokens[-1].type == "NEWLINE":
                return
            self.tokens.append(Token("NEWLINE", "\n", self.line, self.col))

    # ---- string scanning --------------------------------------------------
    def _scan_string(self, interp: bool = False) -> Token:
        start_line, start_col = self.line, self.col
        self._advance()  # opening quote
        parts: list[StrPart] = []
        buf: list[str] = []
        buf_line, buf_col = self.line, self.col

        def flush() -> None:
            nonlocal buf, buf_line, buf_col
            if buf:
                parts.append(StrPart("lit", "".join(buf), buf_line, buf_col))
                buf = []
            buf_line, buf_col = self.line, self.col

        while True:
            ch = self._peek()
            if ch == "":
                raise LexError("unterminated string literal", start_line, start_col)
            if ch == "\n":
                raise LexError("newline inside string literal (use \\n)", self.line, self.col)
            if ch == '"':
                self._advance()
                flush()
                break
            if ch == "\\":
                self._advance()
                esc = self._advance()
                mapping = {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
                           "\\": "\\", '"': '"', "'": "'"}
                if esc in mapping:
                    buf.append(mapping[esc])
                    continue
                raise LexError(f"unknown escape sequence \\{esc}", self.line, self.col)
            if ch == "{":
                if not interp:
                    buf.append(self._advance())
                    continue
                if self._peek(1) == "{":
                    self._advance(2)
                    buf.append("{")
                    continue
                # interpolation expression: scan to matching }
                flush()
                expr_line, expr_col = self.line, self.col
                self._advance()  # {
                depth = 1
                expr_chars: list[str] = []
                while True:
                    c = self._peek()
                    if c == "":
                        raise LexError("unterminated { } inside string", expr_line, expr_col)
                    if c == '"':
                        # keep nested string literal intact
                        sub = self._scan_string_raw()
                        expr_chars.append(sub)
                        continue
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            self._advance()
                            break
                    expr_chars.append(self._advance())
                parts.append(StrPart("expr", "".join(expr_chars), expr_line, expr_col))
                buf_line, buf_col = self.line, self.col
                continue
            if ch == "}":
                if not interp:
                    buf.append(self._advance())
                    continue
                if self._peek(1) == "}":
                    self._advance(2)
                    buf.append("}")
                    continue
                raise LexError("unmatched '}' in f-string (use }} for a literal brace)", self.line, self.col)
            buf.append(self._advance())

        return Token("STRING", parts, start_line, start_col)

    def _scan_string_raw(self) -> str:
        """Scan a nested string literal inside an interpolation, returning raw source."""
        chars = [self._advance()]  # opening quote
        while True:
            c = self._peek()
            if c == "":
                raise LexError("unterminated string literal", self.line, self.col)
            chars.append(self._advance())
            if c == "\\" and self._peek():
                chars.append(self._advance())
                continue
            if c == '"':
                return "".join(chars)

    # ---- number scanning --------------------------------------------------
    def _scan_number(self) -> Token:
        start_line, start_col = self.line, self.col
        chars: list[str] = []
        while self._peek().isdigit() or self._peek() == "_":
            chars.append(self._advance())
        is_float = False
        if self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            chars.append(self._advance())
            while self._peek().isdigit() or self._peek() == "_":
                chars.append(self._advance())
        if self._peek() in ("e", "E") and (self._peek(1).isdigit() or
                                           (self._peek(1) in "+-" and self._peek(2).isdigit())):
            is_float = True
            chars.append(self._advance())
            if self._peek() in "+-":
                chars.append(self._advance())
            while self._peek().isdigit() or self._peek() == "_":
                chars.append(self._advance())
        text = "".join(chars).replace("_", "")
        if is_float:
            return Token("FLOAT", float(text), start_line, start_col)
        return Token("INT", int(text), start_line, start_col)

    # ---- main loop --------------------------------------------------------
    def tokenize(self) -> list[Token]:
        while True:
            ch = self._peek()
            if ch == "":
                self._emit_newline()
                self.tokens.append(Token("EOF", None, self.line, self.col))
                return self.tokens

            # whitespace (not newline)
            if ch in " \t\r":
                self._advance()
                continue

            if ch == "\n":
                line, col = self.line, self.col
                self._advance()
                if self._paren_depth == 0:
                    if self.tokens and self.tokens[-1].type not in ("NEWLINE",):
                        self.tokens.append(Token("NEWLINE", "\n", line, col))
                continue

            # comments: # ... and // ...
            if ch == "#" or (ch == "/" and self._peek(1) == "/"):
                while self._peek() not in ("", "\n"):
                    self._advance()
                continue

            if ch == '"':
                self.tokens.append(self._scan_string(interp=False))
                continue

            if ch == "'":
                raise LexError(
                    "strings use double quotes in PSD — write \"...\" "
                    "(single quotes are not string delimiters; "
                    "use f\"...\" to interpolate)",
                    self.line, self.col)

            if ch.isdigit():
                self.tokens.append(self._scan_number())
                continue

            if ch.isalpha() or ch == "_":
                start_line, start_col = self.line, self.col
                chars = []
                while self._peek().isalnum() or self._peek() == "_":
                    chars.append(self._advance())
                word = "".join(chars)
                if word == "f" and self._peek() == '"':
                    # f-string: "..." with {expr} interpolation
                    self.tokens.append(self._scan_string(interp=True))
                    continue
                if word in KEYWORDS:
                    self.tokens.append(Token("KEYWORD", word, start_line, start_col))
                else:
                    self.tokens.append(Token("IDENT", word, start_line, start_col))
                continue

            # operators
            for op in OPERATORS:
                if self.src.startswith(op, self.pos):
                    start_line, start_col = self.line, self.col
                    self._advance(len(op))
                    if op in ("(", "["):
                        self._paren_depth += 1
                    elif op in (")", "]"):
                        self._paren_depth = max(0, self._paren_depth - 1)
                    # && and || are aliases for and / or
                    if op == "&&":
                        self.tokens.append(Token("KEYWORD", "and", start_line, start_col))
                    elif op == "||":
                        self.tokens.append(Token("KEYWORD", "or", start_line, start_col))
                    else:
                        self.tokens.append(Token("OP", op, start_line, start_col))
                    break
            else:
                raise LexError(f"unexpected character {ch!r}", self.line, self.col)


def tokenize(src: str) -> list[Token]:
    return Lexer(src).tokenize()

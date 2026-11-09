"""Recursive-descent parser for PSD -> AST."""

from __future__ import annotations

from .ast_nodes import (
    Assign, BinOp, Block, BoolLit, Break, Call, Continue, ExprStmt, FloatLit,
    For, FromImport, FuncDef, If, Import, Index, IntLit, LetDecl, ListLit,
    MapLit, MethodCall, Module, Name, Param, Return, StrInterp, UnaryOp, While,
    BOOL, FLOAT, INT, STR, VOID, JSON, ListTy, MapTy, Type,
)
from .lexer import Lexer, StrPart, Token, tokenize


class ParseError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self) -> str:
        return f"error: {self.message} (line {self.line}, col {self.col})"


COMPOUND_ASSIGN = {"+=", "-=", "*=", "/=", "%="}

TYPE_NAMES = {"int": INT, "float": FLOAT, "str": STR, "bool": BOOL}


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<input>"):
        self.tokens = tokens
        self.pos = 0
        self.filename = filename

    # ---- token helpers ----------------------------------------------------
    def peek(self, offset: int = 0) -> Token:
        i = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[i]

    def next(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.type != "EOF":
            self.pos += 1
        return tok

    def at(self, type_: str, value=None) -> bool:
        tok = self.peek()
        if tok.type != type_:
            return False
        return value is None or tok.value == value

    def at_op(self, *ops: str) -> bool:
        tok = self.peek()
        return tok.type == "OP" and tok.value in ops

    def at_kw(self, *words: str) -> bool:
        tok = self.peek()
        return tok.type == "KEYWORD" and tok.value in words

    def error(self, message: str, tok: Token | None = None) -> "ParseError":
        tok = tok or self.peek()
        return ParseError(message, tok.line, tok.col)

    def expect(self, type_: str, value=None, what: str | None = None) -> Token:
        tok = self.peek()
        if tok.type != type_ or (value is not None and tok.value != value):
            want = what or (value if value is not None else type_)
            got = repr(tok.value) if tok.type != "EOF" else "end of file"
            raise self.error(f"expected {want}, got {got}", tok)
        return self.next()

    def skip_newlines(self) -> None:
        while self.at("NEWLINE"):
            self.next()

    # ---- entry -----------------------------------------------------------
    def parse_module(self) -> Module:
        funcs = []
        imports = []
        self.skip_newlines()
        while not self.at("EOF"):
            if self.at_kw("fn"):
                funcs.append(self.parse_func())
            elif self.at("IDENT", "import") or self.at("IDENT", "from"):
                imports.append(self.parse_import())
            else:
                tok = self.peek()
                if tok.type == "IDENT" and tok.value in ("var", "const", "def", "func"):
                    hint = ("PSD declares variables with 'let'"
                            if tok.value in ("var", "const")
                            else "PSD defines functions with 'fn'")
                    raise self.error(
                        f"{hint} (top level: only 'fn' definitions and imports)", tok)
                raise self.error(
                    "expected a function definition ('fn ...') or an import")
            self.skip_newlines()
        return Module(funcs, imports)

    # ---- imports ---------------------------------------------------------
    def parse_import(self):
        """import "path.psd" [as alias] | from "path.psd" import a, b"""
        tok = self.next()          # 'import' or 'from'
        path_tok = self.expect("STRING", what="import path string")
        parts = path_tok.value
        if not (len(parts) == 1 and parts[0].kind == "lit"):
            raise self.error("import path must be a plain string", path_tok)
        path = parts[0].text
        if tok.value == "import":
            alias = None
            if self.at("IDENT", "as"):
                self.next()
                alias = self.expect("IDENT", what="module alias").value
            else:
                alias = path.rsplit("/", 1)[-1]
                if alias.endswith(".psd"):
                    alias = alias[:-4]
                if not alias.isidentifier():
                    raise self.error(
                        f"cannot derive a module name from {path!r} — use 'as <name>'",
                        tok)
            return Import(path, alias, tok.line, tok.col)
        # from "path" import a, b
        self.expect("IDENT", "import", what="'import'")
        names = []
        while True:
            names.append(self.expect("IDENT", what="function name").value)
            if self.at_op(","):
                self.next()
                continue
            break
        return FromImport(path, names, tok.line, tok.col)

    # ---- functions -------------------------------------------------------
    def parse_func(self) -> FuncDef:
        kw = self.expect("KEYWORD", "fn")
        name = self.expect("IDENT", what="function name")
        self.expect("OP", "(", what="'('")
        params = []
        self.skip_newlines()
        if not self.at_op(")"):
            while True:
                self.skip_newlines()
                pname = self.expect("IDENT", what="parameter name")
                self.expect("OP", ":", what="':' (parameter types are required)")
                pty = self.parse_type()
                params.append(Param(pname.value, pty, pname.line, pname.col))
                self.skip_newlines()
                if self.at_op(","):
                    self.next()
                    continue
                break
        self.skip_newlines()
        self.expect("OP", ")", what="')'")
        ret_ty = None
        if self.at_op("->"):
            self.next()
            ret_ty = self.parse_type()
        body = self.parse_block()
        return FuncDef(name.value, params, ret_ty, body, kw.line, kw.col)

    def parse_type(self) -> Type:
        tok = self.peek()
        if tok.type == "OP" and tok.value == "[":
            self.next()
            self.skip_newlines()
            elem = self.parse_type()
            self.skip_newlines()
            self.expect("OP", "]", what="']'")
            return ListTy(elem)
        if tok.type == "OP" and tok.value == "{":
            # {str: T} map type
            self.next()
            self.skip_newlines()
            key = self.expect("IDENT", what="'str' (map keys must be str)")
            if key.value != "str":
                raise self.error("map keys must be str", key)
            self.expect("OP", ":", what="':'")
            self.skip_newlines()
            elem = self.parse_type()
            self.skip_newlines()
            self.expect("OP", "}", what="'}'")
            return MapTy(elem)
        if tok.type == "IDENT" and tok.value == "json":
            self.next()
            return JSON
        if tok.type == "IDENT" and tok.value in TYPE_NAMES:
            self.next()
            return TYPE_NAMES[tok.value]
        py_types = {"list": "[T] (e.g. [int])", "dict": "{str: T} (e.g. {str: int})",
                    "set": "[T] with push/contains (PSD has no set type)",
                    "tuple": "[T] (PSD lists are the sequence type)",
                    "string": "str", "integer": "int", "boolean": "bool",
                    "double": "float", "void": "omit the return annotation"}
        if tok.type == "IDENT" and tok.value in py_types:
            raise self.error(
                f"PSD has no type '{tok.value}' — use {py_types[tok.value]}", tok)
        raise self.error(f"expected a type (int, float, str, bool, json, [T], {{str: T}}), got {tok.value!r}", tok)

    # ---- statements ------------------------------------------------------
    def parse_block(self) -> Block:
        self.skip_newlines()
        start = self.expect("OP", "{", what="'{'")
        stmts = []
        self.skip_newlines()
        while not self.at_op("}"):
            if self.at("EOF"):
                raise self.error("unterminated block (missing '}')", start)
            stmts.append(self.parse_stmt())
            self.end_stmt()
            self.skip_newlines()
        self.next()  # }
        return Block(start.line, start.col, stmts)

    def end_stmt(self) -> None:
        """A statement must end with a newline, ';', or '}'."""
        if self.at("NEWLINE") or self.at_op(";"):
            self.next()
        elif self.at_op("}") or self.at("EOF"):
            pass
        else:
            raise self.error(f"expected end of statement, got {self.peek().value!r}")

    def parse_stmt(self):
        tok = self.peek()
        if tok.type == "IDENT":
            decl_hints = {
                "def": "PSD defines functions with 'fn'",
                "func": "PSD defines functions with 'fn'",
                "var": "PSD declares variables with 'let'",
                "const": "PSD declares variables with 'let'",
            }
            if tok.value in decl_hints:
                raise self.error(decl_hints[tok.value], tok)
        if self.at_kw("let"):
            return self.parse_let()
        if self.at_op("{"):
            return self.parse_block()      # bare block (introduces a scope)
        if self.at_kw("if"):
            return self.parse_if()
        if self.at_kw("while"):
            return self.parse_while()
        if self.at_kw("for"):
            return self.parse_for()
        if self.at_kw("return"):
            tok = self.next()
            expr = None
            if not (self.at("NEWLINE") or self.at_op(";", "}") or self.at("EOF")):
                expr = self.parse_expr()
            return Return(tok.line, tok.col, expr)
        if self.at_kw("break"):
            tok = self.next()
            return Break(tok.line, tok.col)
        if self.at_kw("continue"):
            tok = self.next()
            return Continue(tok.line, tok.col)
        return self.parse_expr_or_assign()

    def parse_let(self) -> LetDecl:
        tok = self.expect("KEYWORD", "let")
        name = self.expect("IDENT", what="variable name")
        ann = None
        if self.at_op(":"):
            self.next()
            ann = self.parse_type()
        self.expect("OP", "=", what="'=' (variables must be initialized)")
        self.skip_newlines()
        expr = self.parse_expr()
        return LetDecl(tok.line, tok.col, name.value, ann, expr)

    def parse_if(self) -> If:
        tok = self.expect("KEYWORD", "if")
        cond = self.parse_expr()
        body = self.parse_block()
        orelse = None
        saved = self.pos
        self.skip_newlines()
        if self.at_kw("elif"):
            self.next()
            orelse = self.parse_if_from_elif()
        elif self.at_kw("else"):
            self.next()
            if self.at_kw("if"):        # else if
                self.next()
                orelse = self.parse_if_from_elif()
            else:
                orelse = self.parse_block()
        else:
            self.pos = saved
        return If(tok.line, tok.col, cond, body, orelse)

    def parse_if_from_elif(self) -> If:
        """Called after 'elif' (or 'else if') has been consumed."""
        tok = self.tokens[self.pos - 1]
        cond = self.parse_expr()
        body = self.parse_block()
        orelse = None
        saved = self.pos
        self.skip_newlines()
        if self.at_kw("elif"):
            self.next()
            orelse = self.parse_if_from_elif()
        elif self.at_kw("else"):
            self.next()
            if self.at_kw("if"):
                self.next()
                orelse = self.parse_if_from_elif()
            else:
                orelse = self.parse_block()
        else:
            self.pos = saved
        return If(tok.line, tok.col, cond, body, orelse)

    def parse_while(self) -> While:
        tok = self.expect("KEYWORD", "while")
        cond = self.parse_expr()
        body = self.parse_block()
        return While(tok.line, tok.col, cond, body)

    def parse_for(self) -> For:
        tok = self.expect("KEYWORD", "for")
        var = self.expect("IDENT", what="loop variable")
        self.expect("KEYWORD", "in", what="'in'")
        it = self.parse_expr()
        body = self.parse_block()
        return For(tok.line, tok.col, var.value, it, body)

    def parse_expr_or_assign(self):
        expr = self.parse_expr()
        tok = self.peek()
        if tok.type == "OP" and (tok.value == "=" or tok.value in COMPOUND_ASSIGN):
            if not isinstance(expr, (Name, Index)):
                raise self.error("left side of assignment must be a variable or index", tok)
            self.next()
            self.skip_newlines()
            rhs = self.parse_expr()
            return Assign(expr.line, expr.col, expr, tok.value, rhs)
        return ExprStmt(expr.line, expr.col, expr)

    # ---- expressions -----------------------------------------------------
    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.at_kw("or"):
            self.next()
            self.skip_newlines()
            right = self.parse_and()
            left = BinOp(line=left.line, col=left.col, op="or", left=left, right=right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.at_kw("and"):
            self.next()
            self.skip_newlines()
            right = self.parse_not()
            left = BinOp(line=left.line, col=left.col, op="and", left=left, right=right)
        return left

    def parse_not(self):
        if self.at_kw("not") or self.at_op("!"):
            tok = self.next()
            operand = self.parse_not()
            return UnaryOp(line=tok.line, col=tok.col, op="!", operand=operand)
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_additive()
        while self.at_op("==", "!=", "<", "<=", ">", ">="):
            op = self.next()
            self.skip_newlines()
            right = self.parse_additive()
            left = BinOp(line=op.line, col=op.col, op=op.value, left=left, right=right)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.at_op("+", "-"):
            op = self.next()
            self.skip_newlines()
            right = self.parse_multiplicative()
            left = BinOp(line=op.line, col=op.col, op=op.value, left=left, right=right)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.at_op("*", "/", "%"):
            op = self.next()
            self.skip_newlines()
            right = self.parse_unary()
            left = BinOp(line=op.line, col=op.col, op=op.value, left=left, right=right)
        return left

    def parse_unary(self):
        if self.at_op("-"):
            tok = self.next()
            operand = self.parse_unary()
            return UnaryOp(line=tok.line, col=tok.col, op="-", operand=operand)
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while True:
            if self.at_op("("):
                if not isinstance(expr, Name):
                    raise self.error("only named functions can be called")
                self.next()
                args = []
                self.skip_newlines()
                if not self.at_op(")"):
                    while True:
                        self.skip_newlines()
                        args.append(self.parse_expr())
                        self.skip_newlines()
                        if self.at_op("="):
                            raise self.error(
                                "PSD calls take arguments positionally — "
                                "'name=' keyword syntax is not supported", self.peek())
                        if self.at_op(","):
                            self.next()
                            continue
                        break
                self.skip_newlines()
                self.expect("OP", ")", what="')'")
                expr = Call(line=expr.line, col=expr.col, func=expr.name, args=args)
            elif self.at_op("["):
                self.next()
                self.skip_newlines()
                idx = self.parse_expr()
                self.skip_newlines()
                self.expect("OP", "]", what="']'")
                expr = Index(line=expr.line, col=expr.col, obj=expr, idx=idx)
            elif self.at_op("."):
                self.next()
                mname = self.expect("IDENT", what="method name")
                self.expect("OP", "(", what="'(' after method name")
                args = []
                self.skip_newlines()
                if not self.at_op(")"):
                    while True:
                        self.skip_newlines()
                        args.append(self.parse_expr())
                        self.skip_newlines()
                        if self.at_op("="):
                            raise self.error(
                                "PSD calls take arguments positionally — "
                                "'name=' keyword syntax is not supported", self.peek())
                        if self.at_op(","):
                            self.next()
                            continue
                        break
                self.skip_newlines()
                self.expect("OP", ")", what="')'")
                expr = MethodCall(line=expr.line, col=expr.col, recv=expr,
                                  name=mname.value, args=args)
            else:
                return expr

    def parse_primary(self):
        tok = self.peek()
        if tok.type == "INT":
            self.next()
            return IntLit(line=tok.line, col=tok.col, value=tok.value)
        if tok.type == "FLOAT":
            self.next()
            return FloatLit(line=tok.line, col=tok.col, value=tok.value)
        if tok.type == "STRING":
            self.next()
            parts = []
            for part in tok.value:  # list[StrPart]
                if part.kind == "lit":
                    parts.append(("lit", part.text))
                else:
                    sub = Parser(Lexer(part.text).tokenize(), self.filename)
                    sub.skip_newlines()
                    e = sub.parse_expr()
                    sub.skip_newlines()
                    if not sub.at("EOF"):
                        raise self.error(f"unexpected token in string interpolation: {sub.peek().value!r}",
                                         sub.peek())
                    e.line, e.col = part.line, part.col
                    parts.append(("expr", e))
            return StrInterp(line=tok.line, col=tok.col, parts=parts)
        if tok.type == "KEYWORD" and tok.value in ("true", "false"):
            self.next()
            return BoolLit(line=tok.line, col=tok.col, value=(tok.value == "true"))
        if tok.type == "IDENT":
            py_hints = {
                "True": "PSD booleans are lowercase — write 'true'",
                "False": "PSD booleans are lowercase — write 'false'",
                "None": "PSD has no None — use a typed empty value like \"\" or 0",
            }
            if tok.value in py_hints:
                raise self.error(py_hints[tok.value], tok)
            self.next()
            return Name(line=tok.line, col=tok.col, name=tok.value)
        if tok.type == "OP" and tok.value == "(":
            self.next()
            self.skip_newlines()
            expr = self.parse_expr()
            self.skip_newlines()
            self.expect("OP", ")", what="')'")
            return expr
        if tok.type == "OP" and tok.value == "[":
            self.next()
            elems = []
            self.skip_newlines()
            if not self.at_op("]"):
                while True:
                    self.skip_newlines()
                    elems.append(self.parse_expr())
                    self.skip_newlines()
                    if self.at_op(","):
                        self.next()
                        if self.at_op("]"):
                            break
                        continue
                    break
            self.skip_newlines()
            self.expect("OP", "]", what="']'")
            return ListLit(line=tok.line, col=tok.col, elems=elems)
        if tok.type == "OP" and tok.value == "{":
            # map literal: {"key": value, ident: value, ...}
            self.next()
            pairs = []
            self.skip_newlines()
            if not self.at_op("}"):
                while True:
                    self.skip_newlines()
                    ktok = self.peek()
                    if ktok.type == "STRING":
                        self.next()
                        parts = ktok.value
                        if len(parts) != 1 or parts[0].kind != "lit":
                            raise self.error("map keys must be plain strings", ktok)
                        key = parts[0].text
                    elif ktok.type == "IDENT":
                        self.next()
                        key = ktok.value
                    else:
                        raise self.error("map keys must be strings or identifiers", ktok)
                    self.expect("OP", ":", what="':' after map key")
                    self.skip_newlines()
                    val = self.parse_expr()
                    pairs.append((key, val))
                    self.skip_newlines()
                    if self.at_op(","):
                        self.next()
                        if self.at_op("}"):
                            break
                        continue
                    break
            self.skip_newlines()
            self.expect("OP", "}", what="'}'")
            return MapLit(line=tok.line, col=tok.col, pairs=pairs)
        raise self.error(f"unexpected token {tok.value!r}", tok)


def parse(src: str, filename: str = "<input>") -> Module:
    return Parser(tokenize(src), filename).parse_module()

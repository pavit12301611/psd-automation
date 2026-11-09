"""Semantic analysis / type checking for PSD.

- static typing with local inference (let x = 5  =>  x: int)
- int -> float promotion; any value coerces to `json`
- homogeneous literals keep static types; mixed literals become `json`
- clear, line-numbered errors with "did you mean ...?" hints
"""

from __future__ import annotations

from .ast_nodes import (
    Assign, BinOp, Block, BoolLit, Break, Call, Continue, ExprStmt, FloatLit,
    For, FuncDef, If, Index, IntLit, LetDecl, ListLit, MapLit, MethodCall,
    Module, Name, Param, Return, StrInterp, UnaryOp, While,
    BOOL, FLOAT, INT, JSON, RANGE, STR, VOID,
    BoolTy, FloatTy, IntTy, JsonTy, ListTy, MapTy, RangeTy, StrTy, Type, VoidTy,
)


class CheckError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


BUILTIN_NAMES = {
    "print", "len", "push", "pop", "str", "int", "float", "range",
    "read_file", "write_file", "append_file", "exists", "delete", "list_dir",
    "run", "capture", "shell", "capture_shell",
    "http_get", "http_post", "http_request",
    "json_parse", "json_encode",
    "args", "env", "exit",
    "try_read_file", "try_write_file", "try_int", "try_json_parse",
    "try_http_get", "try_capture", "try_capture_shell",
    "html_escape", "url_encode", "url_decode", "sqrt",
    "http_listen", "http_accept", "http_reply",
    # v0.4: ML math, time/random, console/dirs, recoverable run, redirects
    "exp", "log", "pow",
    "sleep_ms", "time_ms", "random", "rand_int",
    "read_line", "cwd", "make_dir", "try_make_dir", "try_float", "try_run",
    "http_redirect",
    # capability expansion: terminal/games, desktop, concurrent serving, images
    "term_clear", "term_home", "read_key", "term_size", "open_url",
    "http_listen_pool", "http_reply_file",
    "image_info", "image_process",
}


def _close(a: str, b: str) -> bool:
    """Cheap fuzzy match for 'did you mean' hints."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 2:
        return False
    diffs = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
    return diffs <= 2 or a.startswith(b) or b.startswith(a)


def suggest(name: str, options) -> str:
    hits = [o for o in sorted(options) if _close(name, o)]
    if hits:
        return f" — did you mean '{hits[0]}'?"
    return ""


def promote(a: Type, b: Type):
    """Return a common numeric type or None."""
    if isinstance(a, IntTy) and isinstance(b, IntTy):
        return INT
    if isinstance(a, (IntTy, FloatTy)) and isinstance(b, (IntTy, FloatTy)):
        return FLOAT
    return None


def assignable(src: Type, dst: Type) -> bool:
    """Can a value of type src flow into a slot of type dst?"""
    if src == dst:
        return True
    if isinstance(dst, FloatTy) and isinstance(src, IntTy):
        return True
    if isinstance(dst, JsonTy) and not isinstance(src, (VoidTy, RangeTy)):
        return True
    return False


def same_shape(a: Type, b: Type) -> bool:
    return a == b or (isinstance(a, (IntTy, FloatTy)) and isinstance(b, (IntTy, FloatTy)))


class Checker:
    def __init__(self, module: Module, prog=None):
        self.module = module
        self.prog = prog
        if prog is None:
            # single-module fallback (direct API use)
            from .loader import Program
            self.prog = Program()
            self.prog.module = module
            self.prog.entry_mod = ""
            self.prog.order = list(module.funcs)
            self.prog.tables = {"": {f.name: f for f in module.funcs}}
            self.prog.aliases = {"": {}}
        self.errors: list[str] = []
        self.funcs: dict[str, FuncDef] = {}
        self.aliases: dict[str, str] = {}
        self.scopes: list[dict[str, Type]] = []
        self.cur_func: FuncDef | None = None
        self.loop_depth = 0

    # ---- helpers ---------------------------------------------------------
    def err(self, msg: str, line: int, col: int) -> None:
        self.errors.append(f"error: {msg} (line {line}, col {col})")

    def lookup(self, name: str) -> Type | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    # ---- entry -----------------------------------------------------------
    def check(self) -> Module:
        for f in self.prog.order:
            if f.name in self.funcs:
                self.err(f"duplicate function '{f.name}'", f.line, f.col)
            elif f.name in BUILTIN_NAMES:
                self.err(f"'{f.name}' is a builtin and cannot be redefined", f.line, f.col)
            else:
                self.funcs[f.name] = f

        # Pre-pass: functions that never return a value are void. This lets
        # forward calls to procedures work without explicit annotations.
        for f in self.prog.order:
            if f.ret_ty is None and not any(True for _ in self.walk_returns(f.body)):
                f.inferred_ret_ty = VOID

        entry = self.prog.tables[self.prog.entry_mod]
        if "main" not in entry:
            self.errors.append("error: program must define fn main()")
        else:
            main = entry["main"]
            if main.params:
                self.err("fn main() takes no parameters", main.line, main.col)
            if main.ret_ty is not None and not isinstance(main.ret_ty, (IntTy, VoidTy)):
                self.err("fn main() must return void or int (exit code)", main.line, main.col)

        # import aliases must not collide with callable names in their module
        for mod, amap in self.prog.aliases.items():
            table = self.prog.tables[mod]
            for alias in amap:
                if alias in table:
                    self.errors.append(
                        f"error: import alias '{alias}' conflicts with a "
                        f"function of the same name (file {self.prog.files.get(mod, '?')})")

        for f in self.prog.order:
            self.check_func(f)

        if self.errors:
            raise CheckError(self.errors)
        return self.module

    # ---- functions -------------------------------------------------------
    def check_func(self, f: FuncDef) -> None:
        self.cur_func = f
        mod = getattr(f, "mod", "")
        self.funcs = self.prog.tables[mod]
        self.aliases = self.prog.aliases.get(mod, {})
        self.scopes = [{p.name: p.ty for p in f.params}]
        self.check_block(f.body, new_scope=True)

        ret_ty = f.ret_ty
        if ret_ty is None:
            f.inferred_ret_ty = self.infer_return_type(f.body)
        else:
            f.inferred_ret_ty = ret_ty
            if not isinstance(ret_ty, VoidTy) and not self.block_returns(f.body):
                self.err(f"function '{f.name}' must return a value of type {ret_ty} "
                         f"on all paths", f.line, f.col)
        self.cur_func = None

    def infer_return_type(self, block: Block) -> Type:
        found: Type | None = None
        for st in self.walk_returns(block):
            if found is None:
                found = st.expr.ty
            elif found != st.expr.ty:
                if assignable(st.expr.ty, found):
                    pass
                elif assignable(found, st.expr.ty):
                    found = st.expr.ty
                else:
                    self.err(f"inconsistent return types: {found} and {st.expr.ty}",
                             st.line, st.col)
        return found if found is not None else VOID

    def walk_returns(self, block: Block):
        for st in block.stmts:
            if isinstance(st, Return) and st.expr is not None:
                yield st
            elif isinstance(st, If):
                yield from self.walk_returns(st.body)
                if isinstance(st.orelse, Block):
                    yield from self.walk_returns(st.orelse)
                elif isinstance(st.orelse, If):
                    yield from self.walk_returns(st.orelse.body)
                    if isinstance(st.orelse.orelse, Block):
                        yield from self.walk_returns(st.orelse.orelse)

    def block_returns(self, block: Block) -> bool:
        for st in block.stmts:
            if self.stmt_returns(st):
                return True
        return False

    def stmt_returns(self, st) -> bool:
        if isinstance(st, Return):
            return True
        if isinstance(st, If):
            if st.orelse is None:
                return False
            if isinstance(st.orelse, Block):
                return self.block_returns(st.body) and self.block_returns(st.orelse)
            return self.block_returns(st.body) and self.stmt_returns(st.orelse)
        return False

    # ---- statements ------------------------------------------------------
    def check_block(self, block: Block, new_scope: bool = True) -> None:
        if new_scope:
            self.scopes.append({})
        for st in block.stmts:
            self.check_stmt(st)
        if new_scope:
            self.scopes.pop()

    def check_stmt(self, st) -> None:
        if isinstance(st, Block):
            self.check_block(st)

        elif isinstance(st, LetDecl):
            ty = self.check_expr(st.expr, want=st.ann)
            if st.ann is not None:
                if not assignable(ty, st.ann):
                    self.err(f"cannot assign {ty} to '{st.name}' of type {st.ann}",
                             st.line, st.col)
                ty = st.ann
                self.coerce_note(st.expr, ty)
            if isinstance(ty, VoidTy):
                self.err(f"cannot bind '{st.name}' to a void value", st.line, st.col)
                ty = INT
            if st.name in self.scopes[-1]:
                self.err(f"duplicate variable '{st.name}' in this scope", st.line, st.col)
            self.scopes[-1][st.name] = ty
            st.resolved_ty = ty

        elif isinstance(st, Assign):
            self.check_expr(st.expr)
            if isinstance(st.target, Name):
                ty = self.lookup(st.target.name)
                if ty is None:
                    self.err(f"undefined variable '{st.target.name}'"
                             f"{suggest(st.target.name, self.all_names())}", st.line, st.col)
                    return
                st.target.ty = ty
                self.check_assign_value(st, ty)
            elif isinstance(st.target, Index):
                self.check_expr(st.target.obj)
                self.check_expr(st.target.idx)
                obj_ty = st.target.obj.ty
                idx_ty = st.target.idx.ty
                if isinstance(obj_ty, ListTy):
                    if not isinstance(idx_ty, IntTy):
                        self.err(f"list index must be int, got {idx_ty}", st.line, st.col)
                        return
                    st.target.ty = obj_ty.elem
                    self.check_assign_value(st, obj_ty.elem)
                elif isinstance(obj_ty, MapTy):
                    if not isinstance(idx_ty, StrTy):
                        self.err(f"map key must be str, got {idx_ty}", st.line, st.col)
                        return
                    st.target.ty = obj_ty.elem
                    self.check_assign_value(st, obj_ty.elem)
                elif isinstance(obj_ty, JsonTy):
                    if not isinstance(idx_ty, (StrTy, IntTy)):
                        self.err(f"json index must be str or int, got {idx_ty}", st.line, st.col)
                        return
                    st.target.ty = JSON
                    self.check_assign_value(st, JSON)
                else:
                    self.err(f"cannot index into {obj_ty}", st.line, st.col)

        elif isinstance(st, ExprStmt):
            self.check_expr(st.expr)

        elif isinstance(st, Return):
            if st.expr is None:
                want = self.cur_func.ret_ty
                if want is not None and not isinstance(want, VoidTy):
                    self.err(f"function must return {want}", st.line, st.col)
            else:
                got = self.check_expr(st.expr, want=self.cur_func.ret_ty)
                if isinstance(got, VoidTy):
                    self.err("cannot return a void value", st.line, st.col)
                elif self.cur_func.ret_ty is not None:
                    if not assignable(got, self.cur_func.ret_ty):
                        self.err(f"return type mismatch: expected {self.cur_func.ret_ty}, got {got}",
                                 st.line, st.col)
                    else:
                        self.coerce_note(st.expr, self.cur_func.ret_ty)

        elif isinstance(st, If):
            self.check_cond(st.cond)
            self.check_block(st.body)
            if isinstance(st.orelse, Block):
                self.check_block(st.orelse)
            elif isinstance(st.orelse, If):
                self.check_stmt(st.orelse)

        elif isinstance(st, While):
            self.check_cond(st.cond)
            self.loop_depth += 1
            self.check_block(st.body)
            self.loop_depth -= 1

        elif isinstance(st, For):
            self.check_expr(st.iter)
            it_ty = st.iter.ty
            if isinstance(it_ty, RangeTy):
                var_ty = INT
            elif isinstance(it_ty, ListTy):
                var_ty = it_ty.elem
            elif isinstance(it_ty, JsonTy):
                var_ty = JSON
            elif isinstance(it_ty, VoidTy):
                self.err("for-loop iterator is void", st.line, st.col)
                var_ty = INT
            else:
                self.err(f"cannot iterate over {it_ty} (use range(..), a list, or keys(..))",
                         st.line, st.col)
                var_ty = INT
            self.scopes.append({st.var: var_ty})
            st.var_ty = var_ty
            self.loop_depth += 1
            self.check_block(st.body, new_scope=False)
            self.loop_depth -= 1
            self.scopes.pop()

        elif isinstance(st, Break):
            if self.loop_depth == 0:
                self.err("'break' outside of a loop", st.line, st.col)

        elif isinstance(st, Continue):
            if self.loop_depth == 0:
                self.err("'continue' outside of a loop", st.line, st.col)

        else:
            self.err(f"internal: unknown statement {type(st).__name__}", st.line, st.col)

    def all_names(self):
        names = set(self.funcs) | BUILTIN_NAMES
        for scope in self.scopes:
            names |= set(scope)
        return names

    def check_assign_value(self, st: Assign, target_ty: Type) -> None:
        rhs_ty = st.expr.ty
        if st.op == "=":
            if not assignable(rhs_ty, target_ty):
                self.err(f"cannot assign {rhs_ty} to {target_ty}", st.line, st.col)
            else:
                self.coerce_note(st.expr, target_ty)
        elif st.op in ("+=", "-=", "*=", "/=", "%="):
            if isinstance(target_ty, JsonTy):
                self.err(f"compound assignment '{st.op}' is not supported on json "
                         f"(read, convert, then assign)", st.line, st.col)
                return
            if st.op == "+=" and isinstance(target_ty, StrTy):
                if not isinstance(rhs_ty, StrTy):
                    self.err(f"'+=' on str needs str, got {rhs_ty}", st.line, st.col)
                return
            if isinstance(target_ty, (IntTy, FloatTy)) and isinstance(rhs_ty, (IntTy, FloatTy)):
                if isinstance(target_ty, FloatTy) and isinstance(rhs_ty, IntTy):
                    self.coerce_note(st.expr, FLOAT)
                elif isinstance(target_ty, IntTy) and isinstance(rhs_ty, FloatTy):
                    self.err(f"cannot apply {st.op} to int and float", st.line, st.col)
                return
            self.err(f"cannot apply {st.op} to {target_ty} and {rhs_ty}", st.line, st.col)

    def check_cond(self, expr) -> None:
        ty = self.check_expr(expr)
        if isinstance(expr, MapLit):
            self.err("condition must be bool (this looks like a map literal)", expr.line, expr.col)
        elif not isinstance(ty, (BoolTy, JsonTy)):
            self.err(f"condition must be bool (or json), got {ty}", expr.line, expr.col)

    def coerce_note(self, expr, target: Type):
        """Mark expr as flowing into target; returns the effective type used."""
        if isinstance(target, FloatTy) and isinstance(expr.ty, IntTy):
            expr.coerce_to = FLOAT
            return FLOAT
        if isinstance(target, JsonTy) and not isinstance(expr.ty, JsonTy):
            expr.coerce_to = JSON
            return JSON
        return expr.ty

    # ---- expressions -----------------------------------------------------
    def check_expr(self, expr, want: Type | None = None) -> Type:
        ty = self._check_expr(expr, want)
        expr.ty = ty
        return ty

    def _check_expr(self, expr, want: Type | None) -> Type:
        if isinstance(expr, IntLit):
            return INT
        if isinstance(expr, FloatLit):
            return FLOAT
        if isinstance(expr, BoolLit):
            return BOOL

        if isinstance(expr, StrInterp):
            for kind, part in expr.parts:
                if kind == "expr":
                    pty = self.check_expr(part)
                    if isinstance(pty, (VoidTy, RangeTy)):
                        self.err(f"cannot interpolate a {pty} value", part.line, part.col)
            return STR

        if isinstance(expr, ListLit):
            return self.check_list_lit(expr, want)

        if isinstance(expr, MapLit):
            return self.check_map_lit(expr, want)

        if isinstance(expr, Name):
            ty = self.lookup(expr.name)
            if ty is None:
                py_names = {
                    "None": "PSD has no None — for a JSON null use json_parse(\"null\"); "
                            "for 'no value' just omit it",
                    "null": "PSD has no null literal — use json_parse(\"null\") for a JSON null",
                    "True": "PSD booleans are lowercase: true",
                    "False": "PSD booleans are lowercase: false",
                }
                if expr.name in py_names:
                    self.err(f"undefined variable '{expr.name}' — "
                             f"{py_names[expr.name]}", expr.line, expr.col)
                else:
                    self.err(f"undefined variable '{expr.name}'"
                             f"{suggest(expr.name, self.all_names())}", expr.line, expr.col)
                return INT
            return ty

        if isinstance(expr, UnaryOp):
            ty = self.check_expr(expr.operand)
            if expr.op == "-":
                if not isinstance(ty, (IntTy, FloatTy)):
                    self.err(f"unary '-' needs a number, got {ty}", expr.line, expr.col)
                    return INT
                return ty
            if not isinstance(ty, BoolTy):
                self.err(f"'!'/'not' needs bool, got {ty}", expr.line, expr.col)
            return BOOL

        if isinstance(expr, BinOp):
            return self.check_binop(expr)

        if isinstance(expr, Call):
            return self.check_call(expr, want)

        if isinstance(expr, MethodCall):
            return self.check_method(expr)

        if isinstance(expr, Index):
            return self.check_index(expr)

        self.err(f"internal: unknown expression {type(expr).__name__}", expr.line, expr.col)
        return INT

    # ---- literals --------------------------------------------------------
    def check_list_lit(self, expr: ListLit, want: Type | None) -> Type:
        # explicit target wins
        if isinstance(want, JsonTy):
            for e in expr.elems:
                t = self.check_expr(e)
                if isinstance(t, (VoidTy, RangeTy)):
                    self.err(f"cannot put a {t} value in a list", e.line, e.col)
                else:
                    self.coerce_note(e, JSON)
            return JSON
        if not expr.elems:
            if isinstance(want, ListTy):
                return want
            self.err("empty list literal needs a type annotation "
                     "(e.g. let x: [int] = [])", expr.line, expr.col)
            return ListTy(INT)

        elem_want = want.elem if isinstance(want, ListTy) else None
        probe = next((e for e in expr.elems
                      if not (isinstance(e, ListLit) and not e.elems)), None)
        if probe is None:
            if isinstance(elem_want, ListTy):
                return want
            self.err("empty list literal needs a type annotation", expr.line, expr.col)
            return ListTy(INT)
        elem_ty = self.check_expr(probe, want=elem_want)
        mixed = False
        for e in expr.elems:
            if e is probe:
                continue
            t = self.check_expr(e, want=elem_ty)
            if same_shape(elem_ty, t):
                if isinstance(elem_ty, (IntTy, FloatTy)) and isinstance(t, (IntTy, FloatTy)) \
                        and not isinstance(elem_ty, type(t)):
                    elem_ty = FLOAT
            else:
                mixed = True
        if mixed:
            # heterogeneous list -> json
            for e in expr.elems:
                self.coerce_note(e, JSON)
            return JSON
        for e in expr.elems:
            if isinstance(elem_ty, FloatTy) and isinstance(e.ty, IntTy):
                e.coerce_to = FLOAT
        return ListTy(elem_ty)

    def check_map_lit(self, expr: MapLit, want: Type | None) -> Type:
        if isinstance(want, JsonTy):
            for _k, v in expr.pairs:
                t = self.check_expr(v)
                if isinstance(t, (VoidTy, RangeTy)):
                    self.err(f"cannot put a {t} value in a map", v.line, v.col)
                else:
                    self.coerce_note(v, JSON)
            return JSON
        if not expr.pairs:
            if isinstance(want, MapTy):
                return want
            if isinstance(want, JsonTy):
                return JSON
            self.err("empty map literal needs a type annotation "
                     "(e.g. let m: {str: int} = {} or let m: json = {})",
                     expr.line, expr.col)
            return MapTy(INT)

        elem_want = want.elem if isinstance(want, MapTy) else None
        probe = expr.pairs[0][1]
        elem_ty = self.check_expr(probe, want=elem_want)
        mixed = False
        for _k, v in expr.pairs[1:]:
            t = self.check_expr(v)
            if same_shape(elem_ty, t):
                if isinstance(elem_ty, (IntTy, FloatTy)) and isinstance(t, (IntTy, FloatTy)) \
                        and not isinstance(elem_ty, type(t)):
                    elem_ty = FLOAT
            else:
                mixed = True
        if mixed:
            for _k, v in expr.pairs:
                self.coerce_note(v, JSON)
            return JSON
        for _k, v in expr.pairs:
            if isinstance(elem_ty, FloatTy) and isinstance(v.ty, IntTy):
                v.coerce_to = FLOAT
        return MapTy(elem_ty)

    # ---- indexing --------------------------------------------------------
    def check_index(self, expr: Index) -> Type:
        obj_ty = self.check_expr(expr.obj)
        idx_ty = self.check_expr(expr.idx)
        if isinstance(obj_ty, ListTy):
            if not isinstance(idx_ty, IntTy):
                self.err(f"list index must be int, got {idx_ty}", expr.idx.line, expr.idx.col)
            return obj_ty.elem
        if isinstance(obj_ty, MapTy):
            if not isinstance(idx_ty, StrTy):
                self.err(f"map key must be str, got {idx_ty}", expr.idx.line, expr.idx.col)
            return obj_ty.elem
        if isinstance(obj_ty, JsonTy):
            if not isinstance(idx_ty, (StrTy, IntTy)):
                self.err(f"json index must be str or int, got {idx_ty}",
                         expr.idx.line, expr.idx.col)
            return JSON
        if isinstance(obj_ty, StrTy):
            self.err("str indexing is not supported (use .split() / .contains())",
                     expr.line, expr.col)
            return STR
        self.err(f"cannot index into {obj_ty}", expr.line, expr.col)
        return INT

    # ---- binops ---------------------------------------------------------
    def check_binop(self, expr: BinOp) -> Type:
        op = expr.op
        lt = self.check_expr(expr.left)
        rt = self.check_expr(expr.right)

        if op in ("and", "or"):
            if not isinstance(lt, BoolTy) or not isinstance(rt, BoolTy):
                self.err(f"'{op}' needs bool operands, got {lt} and {rt}", expr.line, expr.col)
            return BOOL

        if isinstance(lt, JsonTy) or isinstance(rt, JsonTy):
            if op not in ("==", "!="):
                self.err(f"cannot apply '{op}' to json directly — convert first "
                         f"(int(..), float(..), str(..))", expr.line, expr.col)
                return INT

        if op == "+" and isinstance(lt, StrTy):
            if not isinstance(rt, StrTy):
                self.err(f"'+' on str needs str on both sides, got {lt} and {rt}",
                         expr.line, expr.col)
            return STR

        if op in ("+", "-", "*", "/", "%"):
            common = promote(lt, rt)
            if common is None:
                self.err(f"cannot apply '{op}' to {lt} and {rt}", expr.line, expr.col)
                return INT
            if isinstance(common, FloatTy):
                if isinstance(lt, IntTy):
                    expr.left.coerce_to = FLOAT
                if isinstance(rt, IntTy):
                    expr.right.coerce_to = FLOAT
            return common

        if op in ("==", "!="):
            # json can be compared with any scalar or another json
            if isinstance(lt, JsonTy) or isinstance(rt, JsonTy):
                if isinstance(lt, (VoidTy, RangeTy)) or isinstance(rt, (VoidTy, RangeTy)):
                    self.err(f"cannot compare {lt} and {rt}", expr.line, expr.col)
                else:
                    if not isinstance(lt, JsonTy):
                        self.coerce_note(expr.left, JSON)
                    if not isinstance(rt, JsonTy):
                        self.coerce_note(expr.right, JSON)
                return BOOL
            if isinstance(lt, StrTy) and isinstance(rt, StrTy):
                return BOOL
            if isinstance(lt, BoolTy) and isinstance(rt, BoolTy):
                return BOOL
            if isinstance(lt, MapTy) or isinstance(rt, MapTy) or \
               isinstance(lt, ListTy) or isinstance(rt, ListTy):
                self.err(f"cannot compare {lt} and {rt}", expr.line, expr.col)
                return BOOL
            common = promote(lt, rt)
            if common is None:
                self.err(f"cannot compare {lt} and {rt}", expr.line, expr.col)
                return BOOL
            if isinstance(common, FloatTy):
                if isinstance(lt, IntTy):
                    expr.left.coerce_to = FLOAT
                if isinstance(rt, IntTy):
                    expr.right.coerce_to = FLOAT
            return BOOL

        if op in ("<", "<=", ">", ">="):
            common = promote(lt, rt)
            if common is None:
                self.err(f"cannot compare {lt} and {rt} with '{op}'", expr.line, expr.col)
                return BOOL
            if isinstance(common, FloatTy):
                if isinstance(lt, IntTy):
                    expr.left.coerce_to = FLOAT
                if isinstance(rt, IntTy):
                    expr.right.coerce_to = FLOAT
            return BOOL

        self.err(f"internal: unknown operator '{op}'", expr.line, expr.col)
        return INT

    # ---- methods ---------------------------------------------------------
    STR_METHODS = {
        "upper": ([], STR), "lower": ([], STR), "trim": ([], STR),
        "len": ([], INT),
        "contains": ([STR], BOOL), "starts_with": ([STR], BOOL),
        "ends_with": ([STR], BOOL),
        "replace": ([STR, STR], STR),
        "split": ([STR], ListTy(STR)),
        "repeat": ([INT], STR),
        "to_int": ([], INT), "to_float": ([], FLOAT),
    }
    LIST_METHODS = {
        "push": ("elem", VOID), "pop": ("self", None),   # special
        "contains": ("elem", BOOL),
        "reverse": ([], VOID), "len": ([], INT),
        "join": ([STR], STR),
    }
    MAP_METHODS = {
        "keys": ([], ListTy(STR)), "values": ("self", None),
        "has": ([STR], BOOL), "remove": ([STR], VOID),
        "get": ("get", None), "len": ([], INT),
    }
    JSON_METHODS = {
        "keys": ([], ListTy(STR)), "has": ([STR], BOOL),
        "len": ([], INT), "push": ("json", VOID),
        # str-style methods work on json strings (Python-like ergonomics)
        "upper": ([], STR), "lower": ([], STR), "trim": ([], STR),
        "contains": ([STR], BOOL), "starts_with": ([STR], BOOL),
        "ends_with": ([STR], BOOL),
        "replace": ([STR, STR], STR), "split": ([STR], ListTy(STR)),
        "repeat": ([INT], STR),
        "to_int": ([], INT), "to_float": ([], FLOAT),
    }

    def check_method(self, expr: MethodCall) -> Type:
        # module call: alias.fn(...) — resolved statically, receiver is not a value
        if isinstance(expr.recv, Name):
            alias = expr.recv.name
            mod = self.aliases.get(alias)
            if mod is not None and self.lookup(alias) is None:
                table = self.prog.tables[mod]
                f = table.get(expr.name)
                if f is None:
                    self.err(f"module '{alias}' has no function '{expr.name}'"
                             f"{suggest(expr.name, table)}", expr.line, expr.col)
                    for a in expr.args:
                        self.check_expr(a)
                    return INT
                return self.check_user_call(f, f"{alias}.{expr.name}", expr)
            if mod is not None and self.lookup(alias) is not None:
                f = self.prog.tables[mod].get(expr.name)
                if f is not None:
                    self.err(f"module alias '{alias}' is shadowed by a local "
                             f"variable — rename the variable to call "
                             f"'{alias}.{expr.name}()'", expr.line, expr.col)

        recv_ty = self.check_expr(expr.recv)
        name = expr.name
        arg_tys = [self.check_expr(a) for a in expr.args]

        def bad_arg(i, want_ty, got):
            self.err(f"method '{name}' argument {i + 1}: expected {want_ty}, got {got}",
                     expr.args[i].line, expr.args[i].col)

        if isinstance(recv_ty, StrTy):
            sig = self.STR_METHODS.get(name)
            if sig is None:
                self.err(f"str has no method '{name}'"
                         f"{suggest(name, self.STR_METHODS)}", expr.line, expr.col)
                return STR
            params, ret = sig
            if len(arg_tys) != len(params):
                self.err(f"str.{name}() takes {len(params)} argument(s), got {len(arg_tys)}",
                         expr.line, expr.col)
            else:
                for i, (p, g) in enumerate(zip(params, arg_tys)):
                    if not assignable(g, p):
                        bad_arg(i, p, g)
            return ret

        if isinstance(recv_ty, ListTy):
            if name == "push":
                if len(arg_tys) != 1:
                    self.err("list.push() takes exactly 1 argument", expr.line, expr.col)
                elif not assignable(arg_tys[0], recv_ty.elem):
                    self.err(f"push(): cannot push {arg_tys[0]} into {recv_ty}",
                             expr.line, expr.col)
                else:
                    self.coerce_note(expr.args[0], recv_ty.elem)
                return VOID
            if name == "pop":
                if arg_tys:
                    self.err("list.pop() takes no arguments", expr.line, expr.col)
                return recv_ty.elem
            if name == "contains":
                if len(arg_tys) != 1 or not assignable(arg_tys[0], recv_ty.elem):
                    self.err(f"list.contains() takes one {recv_ty.elem}", expr.line, expr.col)
                return BOOL
            sig = self.LIST_METHODS.get(name)
            if sig is None or sig[0] == "elem" or sig[0] == "self":
                self.err(f"list has no method '{name}'"
                         f"{suggest(name, ['push', 'pop', 'contains', 'reverse', 'len', 'join'])}",
                         expr.line, expr.col)
                return INT
            params, ret = sig
            if len(arg_tys) != len(params):
                self.err(f"list.{name}() takes {len(params)} argument(s), got {len(arg_tys)}",
                         expr.line, expr.col)
            elif name == "join" and not isinstance(recv_ty.elem, (IntTy, FloatTy, StrTy, BoolTy)):
                self.err(f"join() needs a list of scalars, got {recv_ty}", expr.line, expr.col)
            else:
                for i, (p, g) in enumerate(zip(params, arg_tys)):
                    if not assignable(g, p):
                        bad_arg(i, p, g)
            return ret

        if isinstance(recv_ty, MapTy):
            if name == "values":
                if arg_tys:
                    self.err("map.values() takes no arguments", expr.line, expr.col)
                return ListTy(recv_ty.elem)
            if name == "get":
                # m.get(k, default) -> T ; m.get(k) -> T (panics if missing)
                if len(arg_tys) not in (1, 2):
                    self.err("map.get() takes (key) or (key, default)", expr.line, expr.col)
                else:
                    if not isinstance(arg_tys[0], StrTy):
                        bad_arg(0, STR, arg_tys[0])
                    if len(arg_tys) == 2 and not assignable(arg_tys[1], recv_ty.elem):
                        bad_arg(1, recv_ty.elem, arg_tys[1])
                return recv_ty.elem
            sig = self.MAP_METHODS.get(name)
            if sig is None or sig[0] in ("self", "get"):
                self.err(f"map has no method '{name}'"
                         f"{suggest(name, ['keys', 'values', 'has', 'remove', 'get', 'len'])}",
                         expr.line, expr.col)
                return INT
            params, ret = sig
            if len(arg_tys) != len(params):
                self.err(f"map.{name}() takes {len(params)} argument(s), got {len(arg_tys)}",
                         expr.line, expr.col)
            else:
                for i, (p, g) in enumerate(zip(params, arg_tys)):
                    if not assignable(g, p):
                        bad_arg(i, p, g)
            return ret

        if isinstance(recv_ty, JsonTy):
            if name == "push":
                if len(arg_tys) != 1:
                    self.err("json.push() takes exactly 1 argument", expr.line, expr.col)
                else:
                    self.coerce_note(expr.args[0], JSON)
                return VOID
            sig = self.JSON_METHODS.get(name)
            if sig is None or sig[0] == "json":
                self.err(f"json has no method '{name}'"
                         f"{suggest(name, ['keys', 'has', 'len', 'push', 'upper', 'trim'])}",
                         expr.line, expr.col)
                return INT
            params, ret = sig
            if len(arg_tys) != len(params):
                self.err(f"json.{name}() takes {len(params)} argument(s), got {len(arg_tys)}",
                         expr.line, expr.col)
            else:
                for i, (p, g) in enumerate(zip(params, arg_tys)):
                    if not assignable(g, p):
                        bad_arg(i, p, g)
            return ret

        self.err(f"cannot call methods on {recv_ty}", expr.line, expr.col)
        for a in expr.args:
            self.check_expr(a)
        return INT

    # ---- calls -----------------------------------------------------------
    def check_user_call(self, f: FuncDef, name: str, expr) -> Type:
        """Validate a call to a user function (Call or module MethodCall)."""
        if len(expr.args) != len(f.params):
            self.err(f"'{name}' expects {len(f.params)} argument(s), got {len(expr.args)}",
                     expr.line, expr.col)
            for a in expr.args:
                self.check_expr(a)
            return f.ret_ty or f.inferred_ret_ty or VOID
        for a, p in zip(expr.args, f.params):
            at = self.check_expr(a, want=p.ty)
            if not assignable(at, p.ty):
                self.err(f"argument '{p.name}' of '{name}': expected {p.ty}, got {at}",
                         a.line, a.col)
            else:
                self.coerce_note(a, p.ty)
        expr.sig = f
        ret = f.ret_ty if f.ret_ty is not None else f.inferred_ret_ty
        if ret is None:
            self.err(f"cannot infer return type of '{name}' at this point — "
                     f"add an explicit return type (fn {name}(...) -> int)",
                     expr.line, expr.col)
            return INT
        return ret

    def check_call(self, expr: Call, want: Type | None) -> Type:
        name = expr.func
        if name in self.funcs:
            return self.check_user_call(self.funcs[name], name, expr)

        def nargs(n_options, label):
            if len(expr.args) not in n_options:
                self.err(f"{label}", expr.line, expr.col)
                for a in expr.args:
                    self.check_expr(a)
                return False
            return True

        def arg(i, *types):
            at = self.check_expr(expr.args[i])
            if not any(isinstance(at, t) for t in types):
                exp = " or ".join(getattr(t, "_name", None) or
                                  {IntTy: "int", FloatTy: "float", StrTy: "str",
                                   BoolTy: "bool", JsonTy: "json", ListTy: "list",
                                   MapTy: "map"}.get(t, str(t)) for t in types)
                self.err(f"argument {i + 1}: expected {exp}, got {at}",
                         expr.args[i].line, expr.args[i].col)
            return at

        if name == "print":
            for a in expr.args:
                at = self.check_expr(a)
                if isinstance(at, (VoidTy, RangeTy)):
                    self.err(f"cannot print a {at} value", a.line, a.col)
                elif isinstance(at, ListTy):
                    self.err("cannot print a list directly (print its elements)", a.line, a.col)
                elif isinstance(at, MapTy):
                    self.err("cannot print a map directly (use json_encode or keys)", a.line, a.col)
            return VOID

        if name == "len":
            if not nargs({1}, "len() takes exactly 1 argument"):
                return INT
            at = self.check_expr(expr.args[0])
            if not isinstance(at, (StrTy, ListTy, MapTy, JsonTy)):
                self.err(f"len() needs str, list, map or json, got {at}", expr.line, expr.col)
            return INT

        if name == "push":
            if not nargs({2}, "push(list, value) takes exactly 2 arguments"):
                return VOID
            lt = self.check_expr(expr.args[0])
            vt = self.check_expr(expr.args[1])
            if isinstance(lt, JsonTy):
                self.coerce_note(expr.args[1], JSON)
            elif isinstance(lt, ListTy):
                if not assignable(vt, lt.elem):
                    self.err(f"push(): cannot push {vt} into {lt}", expr.line, expr.col)
                else:
                    self.coerce_note(expr.args[1], lt.elem)
            else:
                self.err(f"push() first argument must be a list or json, got {lt}",
                         expr.line, expr.col)
            return VOID

        if name == "pop":
            if not nargs({1}, "pop(list) takes exactly 1 argument"):
                return INT
            lt = self.check_expr(expr.args[0])
            if isinstance(lt, ListTy):
                return lt.elem
            if isinstance(lt, JsonTy):
                return JSON
            self.err(f"pop() needs a list or json, got {lt}", expr.line, expr.col)
            return INT

        if name == "str":
            if not nargs({1}, "str(x) takes exactly 1 argument"):
                return STR
            at = self.check_expr(expr.args[0])
            if isinstance(at, (VoidTy, RangeTy)):
                self.err(f"cannot convert {at} to str", expr.line, expr.col)
            return STR

        if name in ("int", "float"):
            ret = INT if name == "int" else FLOAT
            if not nargs({1}, f"{name}(x) takes exactly 1 argument"):
                return ret
            at = self.check_expr(expr.args[0])
            if not isinstance(at, (IntTy, FloatTy, BoolTy, StrTy, JsonTy)):
                self.err(f"cannot convert {at} to {name}", expr.line, expr.col)
            return ret

        if name == "range":
            n = len(expr.args)
            if n not in (1, 2):
                self.err("range(n) or range(a, b)", expr.line, expr.col)
            for a in expr.args:
                at = self.check_expr(a)
                if not isinstance(at, IntTy):
                    self.err(f"range() arguments must be int, got {at}", a.line, a.col)
            expr.is_range = True
            return RANGE

        # ---- v0.2: files / shell / http / json / process -----------------
        if name in ("read_file", "http_get"):
            labels = {"read_file": "read_file(path)", "http_get": "http_get(url)"}
            if not nargs({1}, f"{labels[name]} takes exactly 1 argument"):
                return STR
            arg(0, StrTy)
            return STR

        if name in ("write_file", "append_file", "delete", "exit"):
            labels = {"write_file": "write_file(path, contents)",
                      "append_file": "append_file(path, contents)",
                      "delete": "delete(path)", "exit": "exit(code)"}
            need = {2} if name in ("write_file", "append_file") else {1}
            if not nargs(need, f"{labels[name]}"):
                return VOID
            if name == "exit":
                arg(0, IntTy)
            else:
                arg(0, StrTy)
                if name in ("write_file", "append_file"):
                    arg(1, StrTy)
            return VOID

        if name == "exists":
            if not nargs({1}, "exists(path) takes exactly 1 argument"):
                return BOOL
            arg(0, StrTy)
            return BOOL

        if name == "list_dir":
            if not nargs({1}, "list_dir(path) takes exactly 1 argument"):
                return ListTy(STR)
            arg(0, StrTy)
            return ListTy(STR)

        if name in ("run", "capture"):
            labels = {"run": "run([prog, arg, ...])", "capture": "capture([prog, arg, ...])"}
            if not nargs({1}, f"{labels[name]} takes exactly 1 argument (a [str] list)"):
                return INT if name == "run" else STR
            at = self.check_expr(expr.args[0])
            if not (isinstance(at, ListTy) and isinstance(at.elem, StrTy)):
                self.err(f"argument 1: expected [str], got {at}", expr.args[0].line, expr.args[0].col)
            return INT if name == "run" else STR

        if name in ("shell", "capture_shell"):
            labels = {"shell": "shell(cmd)", "capture_shell": "capture_shell(cmd)"}
            if not nargs({1}, f"{labels[name]} takes exactly 1 argument"):
                return INT if name == "shell" else STR
            arg(0, StrTy)
            return INT if name == "shell" else STR

        # ---- recoverable errors (try_*) + web serving --------------------
        if name in ("try_read_file", "try_int", "try_json_parse", "try_http_get"):
            labels = {"try_read_file": "try_read_file(path)", "try_int": "try_int(s)",
                      "try_json_parse": "try_json_parse(s)",
                      "try_http_get": "try_http_get(url)"}
            if not nargs({1}, f"{labels[name]} takes exactly 1 argument"):
                return JSON
            arg(0, StrTy)
            return JSON

        if name == "try_write_file":
            if not nargs({2}, "try_write_file(path, contents) takes exactly 2 arguments"):
                return JSON
            arg(0, StrTy); arg(1, StrTy)
            return JSON

        if name in ("try_capture", "try_capture_shell"):
            labels = {"try_capture": "try_capture([prog, ...][, timeout_secs])",
                      "try_capture_shell": "try_capture_shell(cmd[, timeout_secs])"}
            if not nargs({1, 2}, f"{labels[name]} takes 1 or 2 arguments"):
                return JSON
            if name == "try_capture":
                at = self.check_expr(expr.args[0])
                if not (isinstance(at, ListTy) and isinstance(at.elem, StrTy)):
                    self.err(f"argument 1: expected [str], got {at}",
                             expr.args[0].line, expr.args[0].col)
            else:
                arg(0, StrTy)
            if len(expr.args) == 2:
                arg(1, IntTy)
            return JSON

        if name in ("html_escape", "url_encode", "url_decode"):
            labels = {"html_escape": "html_escape(s)", "url_encode": "url_encode(s)",
                      "url_decode": "url_decode(s)"}
            if not nargs({1}, f"{labels[name]} takes exactly 1 argument"):
                return STR
            arg(0, StrTy)
            return STR

        if name == "sqrt":
            if not nargs({1}, "sqrt(x) takes exactly 1 argument"):
                return FLOAT
            at = self.check_expr(expr.args[0])
            if not isinstance(at, (IntTy, FloatTy)):
                self.err(f"argument 1: expected int or float, got {at}",
                         expr.args[0].line, expr.args[0].col)
            return FLOAT

        # ---- v0.4 capabilities -------------------------------------------
        # ---- v0.4: float math for ML (sigmoid/softmax/losses) ------------
        if name in ("exp", "log"):
            if not nargs({1}, f"{name}(x) takes exactly 1 argument"):
                return FLOAT
            at = self.check_expr(expr.args[0])
            if not isinstance(at, (IntTy, FloatTy)):
                self.err(f"argument 1: expected int or float, got {at}",
                         expr.args[0].line, expr.args[0].col)
            return FLOAT

        if name == "pow":
            if not nargs({2}, "pow(x, y) takes exactly 2 arguments"):
                return FLOAT
            for i in (0, 1):
                at = self.check_expr(expr.args[i])
                if not isinstance(at, (IntTy, FloatTy)):
                    self.err(f"argument {i + 1}: expected int or float, got {at}",
                             expr.args[i].line, expr.args[i].col)
            return FLOAT

        # ---- v0.4: time / randomness / console / dirs --------------------
        if name == "sleep_ms":
            if not nargs({1}, "sleep_ms(ms) takes exactly 1 argument"):
                return VOID
            arg(0, IntTy)
            return VOID

        if name == "time_ms":
            if not nargs({0}, "time_ms() takes no arguments"):
                return INT
            return INT

        if name == "random":
            if not nargs({0}, "random() takes no arguments"):
                return FLOAT
            return FLOAT

        if name == "rand_int":
            if not nargs({1}, "rand_int(n) takes exactly 1 argument"):
                return INT
            arg(0, IntTy)
            return INT

        if name in ("term_clear", "term_home"):
            if not nargs({0}, f"{name}() takes no arguments"):
                return VOID
            return VOID

        if name == "read_key":
            if not nargs({0}, "read_key() takes no arguments"):
                return STR
            return STR

        if name == "term_size":
            if not nargs({0}, "term_size() takes no arguments"):
                return JSON
            return JSON

        if name == "open_url":
            if not nargs({1}, "open_url(url) takes exactly 1 argument"):
                return INT
            arg(0, StrTy)
            return INT
        if name == "read_line":
            if not nargs({0, 1}, "read_line() or read_line(prompt)"):
                return STR
            if len(expr.args) == 1:
                arg(0, StrTy)
            return STR

        if name == "cwd":
            if not nargs({0}, "cwd() takes no arguments"):
                return STR
            return STR

        if name == "make_dir":
            if not nargs({1}, "make_dir(path) takes exactly 1 argument"):
                return VOID
            arg(0, StrTy)
            return VOID

        if name == "try_make_dir":
            if not nargs({1}, "try_make_dir(path) takes exactly 1 argument"):
                return JSON
            arg(0, StrTy)
            return JSON

        if name == "try_float":
            if not nargs({1}, "try_float(s) takes exactly 1 argument"):
                return JSON
            arg(0, StrTy)
            return JSON

        if name == "try_run":
            if not nargs({1, 2}, "try_run([prog, ...][, timeout_secs]) takes 1 or 2 arguments"):
                return JSON
            at = self.check_expr(expr.args[0])
            if not (isinstance(at, ListTy) and isinstance(at.elem, StrTy)):
                self.err(f"argument 1: expected [str], got {at}",
                         expr.args[0].line, expr.args[0].col)
            if len(expr.args) == 2:
                arg(1, IntTy)
            return JSON

        if name == "http_redirect":
            if not nargs({1}, "http_redirect(url) takes exactly 1 argument"):
                return VOID
            arg(0, StrTy)
            return VOID

        if name == "http_listen_pool":
            if not nargs({2, 3}, "http_listen_pool(port, workers) or "
                                 "http_listen_pool(port, host, workers)"):
                return INT
            arg(0, IntTy)
            if len(expr.args) == 2:
                arg(1, IntTy)
            else:
                arg(1, StrTy)
                arg(2, IntTy)
            return INT

        if name == "http_reply_file":
            if not nargs({2}, "http_reply_file(path, content_type) takes exactly 2 arguments"):
                return VOID
            arg(0, StrTy); arg(1, StrTy)
            return VOID

        if name == "image_info":
            if not nargs({1}, "image_info(path) takes exactly 1 argument"):
                return JSON
            arg(0, StrTy)
            return JSON

        if name == "image_process":
            if not nargs({4}, "image_process(in, out, op, arg) takes exactly 4 arguments"):
                return JSON
            arg(0, StrTy); arg(1, StrTy); arg(2, StrTy); arg(3, IntTy)
            return JSON

        if name == "http_listen":
            if not nargs({1, 2}, "http_listen(port[, host]) takes 1 or 2 arguments"):
                return INT
            arg(0, IntTy)
            if len(expr.args) == 2:
                arg(1, StrTy)
            return INT

        if name == "http_accept":
            if not nargs({0}, "http_accept() takes no arguments"):
                return JSON
            return JSON

        if name == "http_reply":
            if not nargs({3}, "http_reply(status, content_type, body) takes exactly 3 arguments"):
                return VOID
            arg(0, IntTy); arg(1, StrTy); arg(2, StrTy)
            return VOID

        if name == "http_post":
            if not nargs({3}, "http_post(url, body, content_type) takes exactly 3 arguments"):
                return STR
            arg(0, StrTy); arg(1, StrTy); arg(2, StrTy)
            return STR

        if name == "http_request":
            if not nargs({4}, "http_request(method, url, body, headers) takes 4 arguments "
                              "(body may be \"\")"):
                return JSON
            arg(0, StrTy); arg(1, StrTy); arg(2, StrTy)
            hat = self.check_expr(expr.args[3])
            if not (isinstance(hat, MapTy) and isinstance(hat.elem, StrTy)):
                self.err(f"argument 4: expected {{str: str}}, got {hat}",
                         expr.args[3].line, expr.args[3].col)
            return JSON

        if name == "json_parse":
            if not nargs({1}, "json_parse(s) takes exactly 1 argument"):
                return JSON
            arg(0, StrTy)
            return JSON

        if name == "json_encode":
            if not nargs({1}, "json_encode(v) takes exactly 1 argument"):
                return STR
            at = self.check_expr(expr.args[0])
            if isinstance(at, (VoidTy, RangeTy)):
                self.err(f"cannot encode {at}", expr.line, expr.col)
            elif not isinstance(at, JsonTy):
                self.coerce_note(expr.args[0], JSON)
            return STR

        if name == "args":
            if not nargs({0}, "args() takes no arguments"):
                return ListTy(STR)
            return ListTy(STR)

        if name == "env":
            if len(expr.args) not in (1, 2):
                self.err("env(name) or env(name, default)", expr.line, expr.col)
                for a in expr.args:
                    self.check_expr(a)
                return STR
            arg(0, StrTy)
            if len(expr.args) == 2:
                arg(1, StrTy)
            return STR

        py_calls = {
            "input": "PSD reads stdin with read_line([prompt])",
            "open": "PSD reads/writes whole files with read_file(path) / write_file(path, s)",
            "println": "PSD uses print(...)",
            "printf": "PSD uses print(f\"...{expr}...\") with f-strings",
            "len_": "PSD uses len(x)",
        }
        if name in py_calls:
            self.err(f"unknown function '{name}' — {py_calls[name]}",
                     expr.line, expr.col)
        else:
            self.err(f"unknown function '{name}'{suggest(name, BUILTIN_NAMES | set(self.funcs))}",
                     expr.line, expr.col)
        for a in expr.args:
            self.check_expr(a)
        return INT


def check(module: Module, prog=None) -> Module:
    return Checker(module, prog).check()

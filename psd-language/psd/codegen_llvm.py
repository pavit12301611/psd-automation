"""LLVM IR text emitter for PSD.

Strategy:
  - all locals are allocas in the entry block (LLVM's mem2reg cleans this up)
  - every str / [T] value is an owned, reference-counted pointer
  - borrowing builtins don't consume arguments; the caller decrefs temporaries
  - scopes emit decrefs on exit (incl. break/continue/return paths)
"""

from __future__ import annotations

from .ast_nodes import (
    Assign, BinOp, Block, BoolLit, Break, Call, Continue, ExprStmt, FloatLit,
    For, FuncDef, If, Index, IntLit, LetDecl, ListLit, MapLit, MethodCall,
    Module, Name, Param, Return, StrInterp, UnaryOp, While,
    BOOL, FLOAT, INT, JSON, STR, VOID,
    BoolTy, FloatTy, IntTy, JsonTy, ListTy, MapTy, StrTy, Type, VoidTy,
    is_refcounted,
)


class CodegenError(Exception):
    pass


RUNTIME_DECLS = """
declare ptr @psd_str_new(ptr, i64)
declare void @psd_str_incref(ptr)
declare void @psd_str_decref(ptr)
declare i64 @psd_str_len(ptr)
declare ptr @psd_str_concat(ptr, ptr)
declare i64 @psd_str_eq(ptr, ptr)
declare ptr @psd_str_from_i64(i64)
declare ptr @psd_str_from_f64(double)
declare ptr @psd_str_from_bool(i8)
declare i64 @psd_str_to_i64(ptr)
declare i64 @psd_float_to_i64(double)
declare ptr @psd_try_read_file(ptr)
declare ptr @psd_try_write_file(ptr, ptr)
declare ptr @psd_try_int(ptr)
declare ptr @psd_try_json_parse(ptr)
declare ptr @psd_try_http_get(ptr)
declare ptr @psd_try_capture(ptr, i64)
declare ptr @psd_try_capture_shell(ptr, i64)
declare ptr @psd_html_escape(ptr)
declare ptr @psd_url_encode(ptr)
declare ptr @psd_url_decode(ptr)
declare double @psd_sqrt(double)
declare double @psd_exp(double)
declare double @psd_log(double)
declare double @psd_pow_f64(double, double)
declare void @psd_sleep_ms(i64)
declare i64 @psd_time_ms()
declare double @psd_random()
declare i64 @psd_rand_int(i64)
declare void @psd_term_clear()
declare void @psd_term_home()
declare ptr @psd_read_key()
declare ptr @psd_term_size()
declare i64 @psd_open_url(ptr)
declare i64 @psd_http_listen_pool(i64, ptr, i64)
declare void @psd_http_reply_file(ptr, ptr)
declare ptr @psd_image_info(ptr)
declare ptr @psd_image_process(ptr, ptr, ptr, i64)
declare ptr @psd_read_line(ptr)
declare ptr @psd_cwd()
declare void @psd_make_dir(ptr)
declare ptr @psd_try_make_dir(ptr)
declare ptr @psd_try_float(ptr)
declare ptr @psd_try_run(ptr, i64)
declare void @psd_http_redirect(ptr)
declare i64 @psd_http_listen(i64, ptr)
declare ptr @psd_http_accept()
declare void @psd_http_reply(i64, ptr, ptr)
declare double @psd_str_to_f64(ptr)
declare ptr @psd_str_upper(ptr)
declare ptr @psd_str_lower(ptr)
declare ptr @psd_str_trim(ptr)
declare i64 @psd_str_contains(ptr, ptr)
declare i64 @psd_str_starts_with(ptr, ptr)
declare i64 @psd_str_ends_with(ptr, ptr)
declare ptr @psd_str_replace(ptr, ptr, ptr)
declare ptr @psd_str_repeat(ptr, i64)
declare ptr @psd_str_split(ptr, ptr)
declare ptr @psd_list_new(i64, i64)
declare void @psd_list_incref(ptr)
declare void @psd_list_decref(ptr)
declare i64 @psd_list_len(ptr)
declare void @psd_list_push(ptr, i64)
declare i64 @psd_list_get(ptr, i64)
declare void @psd_list_set(ptr, i64, i64)
declare i64 @psd_list_pop(ptr)
declare i64 @psd_list_contains(ptr, i64)
declare void @psd_list_reverse(ptr)
declare ptr @psd_list_join(ptr, ptr)
declare ptr @psd_map_new(i64, i64)
declare void @psd_map_incref(ptr)
declare void @psd_map_decref(ptr)
declare i64 @psd_map_len(ptr)
declare void @psd_map_set(ptr, ptr, i64)
declare i64 @psd_map_get(ptr, ptr)
declare i64 @psd_map_has(ptr, ptr)
declare void @psd_map_remove(ptr, ptr)
declare ptr @psd_map_keys(ptr)
declare ptr @psd_map_values(ptr)
declare ptr @psd_json_null()
declare ptr @psd_json_from_bool(i8)
declare ptr @psd_json_from_int(i64)
declare ptr @psd_json_from_float(double)
declare ptr @psd_json_from_str(ptr)
declare ptr @psd_json_from_list(ptr)
declare ptr @psd_json_from_map(ptr)
declare ptr @psd_json_wrap(i64, i64)
declare void @psd_json_incref(ptr)
declare void @psd_json_decref(ptr)
declare i64 @psd_json_len(ptr)
declare ptr @psd_json_get_str(ptr, ptr)
declare ptr @psd_json_get_idx(ptr, i64)
declare void @psd_json_set_str(ptr, ptr, ptr)
declare void @psd_json_set_idx(ptr, i64, ptr)
declare i64 @psd_json_has(ptr, ptr)
declare void @psd_json_push(ptr, ptr)
declare ptr @psd_json_keys(ptr)
declare i64 @psd_json_as_int(ptr)
declare double @psd_json_as_float(ptr)
declare i8 @psd_json_as_bool(ptr)
declare ptr @psd_json_as_str(ptr)
declare ptr @psd_json_encode(ptr)
declare ptr @psd_json_display(ptr)
declare i64 @psd_json_eq(ptr, ptr)
declare i8 @psd_json_truthy(ptr)
declare ptr @psd_json_parse(ptr)
declare void @psd_print_json(ptr)
declare ptr @psd_read_file(ptr)
declare void @psd_write_file(ptr, ptr)
declare void @psd_append_file(ptr, ptr)
declare i64 @psd_exists(ptr)
declare void @psd_delete(ptr)
declare ptr @psd_list_dir(ptr)
declare i64 @psd_run(ptr)
declare ptr @psd_capture(ptr)
declare i64 @psd_shell(ptr)
declare ptr @psd_capture_shell(ptr)
declare ptr @psd_http_get(ptr)
declare ptr @psd_http_post(ptr, ptr, ptr)
declare ptr @psd_http_request(ptr, ptr, ptr, ptr)
declare ptr @psd_args()
declare ptr @psd_env(ptr, ptr)
declare void @psd_exit(i64)
declare void @psd_set_args(i32, ptr)
declare i64 @psd_div_i64(i64, i64)
declare i64 @psd_mod_i64(i64, i64)
declare void @psd_print_i64(i64)
declare void @psd_print_f64(double)
declare void @psd_print_bool(i8)
declare void @psd_print_str(ptr)
declare void @psd_print_space()
declare void @psd_print_nl()
declare void @psd_panic(ptr)
"""

ELEM_SCALAR, ELEM_STR, ELEM_LIST, ELEM_MAP, ELEM_JSON, ELEM_FLOAT, ELEM_BOOL = 0, 1, 2, 3, 4, 5, 6


def ir_ty(ty: Type) -> str:
    if isinstance(ty, IntTy):
        return "i64"
    if isinstance(ty, FloatTy):
        return "double"
    if isinstance(ty, BoolTy):
        return "i8"
    if isinstance(ty, (StrTy, ListTy, MapTy, JsonTy)):
        return "ptr"
    if isinstance(ty, VoidTy):
        return "void"
    raise CodegenError(f"no IR type for {ty}")


def elem_kind(ty: Type) -> int:
    if isinstance(ty, StrTy):
        return ELEM_STR
    if isinstance(ty, ListTy):
        return ELEM_LIST
    if isinstance(ty, MapTy):
        return ELEM_MAP
    if isinstance(ty, JsonTy):
        return ELEM_JSON
    if isinstance(ty, FloatTy):
        return ELEM_FLOAT
    if isinstance(ty, BoolTy):
        return ELEM_BOOL
    return ELEM_SCALAR


def escape_bytes(s: str) -> str:
    out = []
    for b in s.encode("utf-8"):
        if b == 0x5C:
            out.append("\\5C")
        elif b == 0x22:
            out.append("\\22")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append(f"\\{b:02X}")
    return "".join(out)


class FuncEmitter:
    def __init__(self, cg: "Codegen", func: FuncDef):
        self.cg = cg
        self.func = func
        self.body: list[str] = []
        self.allocas: list[str] = []
        self.reg = 0
        self.label = 0
        self.scopes: list[dict[str, tuple[str, Type, bool]]] = []
        self.loops: list[tuple[str, str, int, list]] = []   # (break, continue, depth, owned_regs)
        self.terminated = False

    # ---- emission helpers ------------------------------------------------
    def emit(self, line: str) -> None:
        if not self.terminated:
            self.body.append("  " + line)

    def force_emit(self, line: str) -> None:
        self.body.append("  " + line)

    def new_reg(self) -> str:
        self.reg += 1
        return f"%t{self.reg}"

    def new_label(self, hint: str) -> str:
        self.label += 1
        return f"{hint}{self.label}"

    def alloca(self, ty: str, name_hint: str) -> str:
        self.reg += 1
        reg = f"%t{self.reg}"
        self.allocas.append(f"  {reg} = alloca {ty}    ; {name_hint}")
        return reg

    def raw(self, ty: str, op: str) -> str:
        """Emit `<reg> = op` and return the register."""
        r = self.new_reg()
        self.emit(f"{r} = {op}")
        return r

    def terminate(self) -> None:
        self.terminated = True

    def start_label(self, label: str) -> None:
        self.terminated = False
        self.force_emit(f"{label}:")

    # ---- scopes / rc -----------------------------------------------------
    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        scope = self.scopes.pop()
        for reg, ty, owned in scope.values():
            if owned:
                self.decref_var(reg, ty)

    def cleanup_scopes_until(self, depth: int) -> None:
        """Decref owned vars in scopes[depth:], without popping."""
        for scope in self.scopes[depth:]:
            for reg, ty, owned in scope.values():
                if owned:
                    self.decref_var(reg, ty)

    def cleanup_loop_temps(self) -> None:
        """Decref loop-owned temporaries (e.g. the list being iterated)."""
        for _b, _c, _d, owned_regs in self.loops:
            for reg, ty in owned_regs:
                self.decref(ty, reg)

    def declare(self, name: str, ty: Type, owned: bool = True) -> tuple[str, Type, bool]:
        if isinstance(ty, (IntTy, FloatTy, BoolTy)):
            ir = ir_ty(ty)
        else:
            ir = "ptr"
        addr = self.alloca(ir, name)
        entry = (addr, ty, owned)
        self.scopes[-1][name] = entry
        return entry

    def lookup(self, name: str) -> tuple[str, Type, bool]:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise CodegenError(f"internal: unresolved name '{name}'")

    def incref(self, ty: Type, reg: str) -> None:
        if isinstance(ty, StrTy):
            self.emit(f"call void @psd_str_incref(ptr {reg})")
        elif isinstance(ty, ListTy):
            self.emit(f"call void @psd_list_incref(ptr {reg})")
        elif isinstance(ty, MapTy):
            self.emit(f"call void @psd_map_incref(ptr {reg})")
        elif isinstance(ty, JsonTy):
            self.emit(f"call void @psd_json_incref(ptr {reg})")

    def decref(self, ty: Type, reg: str) -> None:
        if isinstance(ty, StrTy):
            self.emit(f"call void @psd_str_decref(ptr {reg})")
        elif isinstance(ty, ListTy):
            self.emit(f"call void @psd_list_decref(ptr {reg})")
        elif isinstance(ty, MapTy):
            self.emit(f"call void @psd_map_decref(ptr {reg})")
        elif isinstance(ty, JsonTy):
            self.emit(f"call void @psd_json_decref(ptr {reg})")

    def decref_var(self, addr: str, ty: Type) -> None:
        """Decref the value STORED in an alloca (load first!)."""
        val = self.raw(ir_ty(ty), f"load {ir_ty(ty)}, ptr {addr}")
        self.decref(ty, val)

    def discard(self, ty: Type, reg: str) -> None:
        self.decref(ty, reg)

    # ---- conversions -----------------------------------------------------
    def coerce(self, val: str, frm: Type, to: Type):
        if frm == to:
            return val
        if isinstance(frm, IntTy) and isinstance(to, FloatTy):
            return self.raw("double", f"sitofp i64 {val} to double")
        if isinstance(to, JsonTy) and not isinstance(frm, JsonTy):
            slot = self.to_slot(val, frm)
            kind = elem_kind(frm)
            return self.raw("ptr", f"call ptr @psd_json_wrap(i64 {slot}, i64 {kind})")
        raise CodegenError(f"internal: cannot coerce {frm} -> {to}")

    def to_slot(self, val: str, ty: Type) -> str:
        if isinstance(ty, IntTy):
            return val
        if isinstance(ty, BoolTy):
            return self.raw("i64", f"zext i8 {val} to i64")
        if isinstance(ty, FloatTy):
            return self.raw("i64", f"bitcast double {val} to i64")
        return self.raw("i64", f"ptrtoint ptr {val} to i64")

    def from_slot(self, slot: str, ty: Type, owned: bool) -> str:
        if isinstance(ty, IntTy):
            return slot
        if isinstance(ty, BoolTy):
            return self.raw("i8", f"trunc i64 {slot} to i8")
        if isinstance(ty, FloatTy):
            return self.raw("double", f"bitcast i64 {slot} to double")
        p = self.raw("ptr", f"inttoptr i64 {slot} to ptr")
        if owned:
            self.incref(ty, p)
        return p

    # ---- expressions -----------------------------------------------------
    def eval(self, e) -> tuple[str, Type]:
        """Evaluate an expression; str/list/map/json results are owned references."""
        val, ty = self._eval(e)
        want = getattr(e, "coerce_to", None)
        if want is not None:
            if isinstance(want, FloatTy) and isinstance(ty, IntTy):
                val = self.coerce(val, INT, FLOAT)
                ty = FLOAT
            elif isinstance(want, JsonTy) and not isinstance(ty, JsonTy):
                val = self.coerce(val, ty, JSON)
                ty = JSON
        return val, ty

    def _eval(self, e) -> tuple[str, Type]:
        if isinstance(e, IntLit):
            # integer literals wrap to the signed 64-bit range (same rule as
            # arithmetic overflow), so -9223372036854775808 spells INT_MIN
            v = ((e.value + 2 ** 63) % 2 ** 64) - 2 ** 63
            return str(v), INT
        if isinstance(e, FloatLit):
            v = e.value
            if v != v:      # nan
                return "0x7FF8000000000000", FLOAT
            if v == float("inf"):
                return "0x7FF0000000000000", FLOAT
            if v == float("-inf"):
                return "0xFFF0000000000000", FLOAT
            import struct
            bits = struct.unpack("<Q", struct.pack("<d", v))[0]
            return f"0x{bits:016X}", FLOAT
        if isinstance(e, BoolLit):
            return ("1" if e.value else "0"), BOOL

        if isinstance(e, StrInterp):
            return self.eval_str_interp(e)

        if isinstance(e, ListLit):
            return self.eval_list_lit(e)

        if isinstance(e, MapLit):
            return self.eval_map_lit(e)

        if isinstance(e, Name):
            addr, ty, _owned = self.lookup(e.name)
            loaded = self.raw(ir_ty(ty), f"load {ir_ty(ty)}, ptr {addr}")
            if is_refcounted(ty):
                self.incref(ty, loaded)
            return loaded, ty

        if isinstance(e, UnaryOp):
            v, ty = self.eval(e.operand)
            if e.op == "-":
                if isinstance(ty, IntTy):
                    return self.raw("i64", f"sub i64 0, {v}"), INT
                return self.raw("double", f"fneg double {v}"), FLOAT
            c = self.raw("i1", f"icmp eq i8 {v}, 0")
            return self.raw("i8", f"zext i1 {c} to i8"), BOOL

        if isinstance(e, BinOp):
            return self.eval_binop(e)

        if isinstance(e, Call):
            return self.eval_call(e)

        if isinstance(e, MethodCall):
            return self.eval_method(e)

        if isinstance(e, Index):
            return self.eval_index(e)

        raise CodegenError(f"internal: unknown expr {type(e).__name__}")

    def eval_str_interp(self, e: StrInterp) -> tuple[str, Type]:
        pieces: list[tuple[str, Type]] = []
        for kind, part in e.parts:
            if kind == "lit":
                gname, glen = self.cg.string_const_meta(part)
                pieces.append((self.raw("ptr", f"call ptr @psd_str_new(ptr {gname}, i64 {glen})"), STR))
            else:
                v, ty = self.eval(part)
                if isinstance(ty, StrTy):
                    pieces.append((v, STR))
                elif isinstance(ty, IntTy):
                    pieces.append((self.raw("ptr", f"call ptr @psd_str_from_i64(i64 {v})"), STR))
                elif isinstance(ty, FloatTy):
                    pieces.append((self.raw("ptr", f"call ptr @psd_str_from_f64(double {v})"), STR))
                elif isinstance(ty, BoolTy):
                    pieces.append((self.raw("ptr", f"call ptr @psd_str_from_bool(i8 {v})"), STR))
                else:  # json
                    pieces.append((self.raw("ptr", f"call ptr @psd_json_display(ptr {v})"), STR))
                    self.decref(JSON, v)
        if not pieces:
            z = self.raw("ptr", f"call ptr @psd_str_new(ptr null, i64 0)")
            return z, STR
        acc = pieces[0][0]
        for p, _ in pieces[1:]:
            t = self.raw("ptr", f"call ptr @psd_str_concat(ptr {acc}, ptr {p})")
            self.decref(STR, acc)
            self.decref(STR, p)
            acc = t
        return acc, STR

    def eval_list_lit(self, e: ListLit) -> tuple[str, Type]:
        if isinstance(e.ty, JsonTy):
            # heterogeneous list literal -> json array
            lst = self.raw("ptr", f"call ptr @psd_list_new(i64 {ELEM_JSON}, i64 {max(len(e.elems), 4)})")
            for el in e.elems:
                v, vty = self.eval(el)
                v = self.coerce(v, vty, JSON)
                slot = self.to_slot(v, JSON)
                self.emit(f"call void @psd_list_push(ptr {lst}, i64 {slot})")
                self.decref(JSON, v)
            return self.raw("ptr", f"call ptr @psd_json_from_list(ptr {lst})"), JSON
        ty = e.ty if isinstance(e.ty, ListTy) else ListTy(INT)
        kind = elem_kind(ty.elem)
        n = len(e.elems)
        lst = self.raw("ptr", f"call ptr @psd_list_new(i64 {kind}, i64 {max(n, 4)})")
        for el in e.elems:
            v, vty = self.eval(el)
            v = self.coerce(v, vty, ty.elem)
            slot = self.to_slot(v, ty.elem)
            self.emit(f"call void @psd_list_push(ptr {lst}, i64 {slot})")
            self.decref(ty.elem, v)
        return lst, ty

    def eval_map_lit(self, e: MapLit) -> tuple[str, Type]:
        if isinstance(e.ty, JsonTy):
            m = self.raw("ptr", f"call ptr @psd_map_new(i64 {ELEM_JSON}, i64 {max(len(e.pairs), 4)})")
            for key, val in e.pairs:
                gname, glen = self.cg.string_const_meta(key)
                kreg = self.raw("ptr", f"call ptr @psd_str_new(ptr {gname}, i64 {glen})")
                v, vty = self.eval(val)
                v = self.coerce(v, vty, JSON)
                slot = self.to_slot(v, JSON)
                self.emit(f"call void @psd_map_set(ptr {m}, ptr {kreg}, i64 {slot})")
                self.decref(STR, kreg)
                self.decref(JSON, v)
            return self.raw("ptr", f"call ptr @psd_json_from_map(ptr {m})"), JSON
        ty = e.ty if isinstance(e.ty, MapTy) else MapTy(INT)
        kind = elem_kind(ty.elem)
        m = self.raw("ptr", f"call ptr @psd_map_new(i64 {kind}, i64 {max(len(e.pairs), 4)})")
        for key, val in e.pairs:
            gname, glen = self.cg.string_const_meta(key)
            kreg = self.raw("ptr", f"call ptr @psd_str_new(ptr {gname}, i64 {glen})")
            v, vty = self.eval(val)
            v = self.coerce(v, vty, ty.elem)
            slot = self.to_slot(v, ty.elem)
            self.emit(f"call void @psd_map_set(ptr {m}, ptr {kreg}, i64 {slot})")
            self.decref(STR, kreg)
            self.decref(ty.elem, v)
        return m, ty

    def eval_binop(self, e: BinOp) -> tuple[str, Type]:
        op = e.op

        if op in ("==", "!=") and (isinstance(e.left.ty, JsonTy) or isinstance(e.right.ty, JsonTy)):
            lv, lt = self.eval(e.left)
            rv, rt = self.eval(e.right)
            lv = self.coerce(lv, lt, JSON)
            rv = self.coerce(rv, rt, JSON)
            c = self.raw("i64", f"call i64 @psd_json_eq(ptr {lv}, ptr {rv})")
            self.decref(JSON, lv)
            self.decref(JSON, rv)
            b = self.raw("i1", f"icmp ne i64 {c}, 0")
            if op == "!=":
                b = self.raw("i1", f"xor i1 {b}, 1")
            return self.raw("i8", f"zext i1 {b} to i8"), BOOL

        if op in ("and", "or"):
            res = self.alloca("i8", "logic")
            lv, _ = self.eval(e.left)
            self.emit(f"store i8 {lv}, ptr {res}")
            c = self.raw("i1", f"icmp ne i8 {lv}, 0")
            rhs = self.new_label("lrhs")
            end = self.new_label("lend")
            if op == "and":
                self.emit(f"br i1 {c}, label %{rhs}, label %{end}")
            else:
                self.emit(f"br i1 {c}, label %{end}, label %{rhs}")
            self.start_label(rhs)
            rv, _ = self.eval(e.right)
            self.emit(f"store i8 {rv}, ptr {res}")
            self.emit(f"br label %{end}")
            self.start_label(end)
            out = self.raw("i8", f"load i8, ptr {res}")
            return out, BOOL

        lv, lt = self.eval(e.left)
        rv, rt = self.eval(e.right)

        # unify numerics
        if isinstance(lt, (IntTy, FloatTy)) and isinstance(rt, (IntTy, FloatTy)):
            if isinstance(lt, FloatTy) or isinstance(rt, FloatTy):
                lv = self.coerce(lv, lt, FLOAT)
                rv = self.coerce(rv, rt, FLOAT)
                lt = rt = FLOAT

        if op == "+" and isinstance(lt, StrTy):
            out = self.raw("ptr", f"call ptr @psd_str_concat(ptr {lv}, ptr {rv})")
            self.decref(STR, lv)
            self.decref(STR, rv)
            return out, STR

        if isinstance(lt, IntTy) and op in ("+", "-", "*", "/", "%"):
            if op == "/":
                return self.raw("i64", f"call i64 @psd_div_i64(i64 {lv}, i64 {rv})"), INT
            if op == "%":
                return self.raw("i64", f"call i64 @psd_mod_i64(i64 {lv}, i64 {rv})"), INT
            llvm_op = {"+": "add", "-": "sub", "*": "mul"}[op]
            return self.raw("i64", f"{llvm_op} i64 {lv}, {rv}"), INT

        if isinstance(lt, FloatTy) and op in ("+", "-", "*", "/", "%"):
            llvm_op = {"+": "fadd", "-": "fsub", "*": "fmul", "/": "fdiv", "%": "frem"}[op]
            return self.raw("double", f"{llvm_op} double {lv}, {rv}"), FLOAT

        if op in ("==", "!="):
            if isinstance(lt, StrTy):
                c64 = self.raw("i64", f"call i64 @psd_str_eq(ptr {lv}, ptr {rv})")
                self.decref(STR, lv)
                self.decref(STR, rv)
                c = self.raw("i1", f"icmp ne i64 {c64}, 0")
                if op == "!=":
                    c = self.raw("i1", f"xor i1 {c}, 1")
            elif isinstance(lt, FloatTy):
                # == is ordered (false for NaN); != is unordered (true for NaN),
                # matching IEEE-754 / Python / C behavior
                pred = "oeq" if op == "==" else "une"
                c = self.raw("i1", f"fcmp {pred} double {lv}, {rv}")
            else:  # int or bool
                c = self.raw("i1", f"icmp {'eq' if op == '==' else 'ne'} "
                                   f"{'i8' if isinstance(lt, BoolTy) else 'i64'} {lv}, {rv}")
            return self.raw("i8", f"zext i1 {c} to i8"), BOOL

        if op in ("<", "<=", ">", ">="):
            pred = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge"}[op]
            if isinstance(lt, FloatTy):
                c = self.raw("i1", f"fcmp o{pred} double {lv}, {rv}")
            else:
                c = self.raw("i1", f"icmp s{pred} i64 {lv}, {rv}")
            return self.raw("i8", f"zext i1 {c} to i8"), BOOL

        raise CodegenError(f"internal: bad binop {op} on {lt}/{rt}")

    def eval_index(self, e: Index) -> tuple[str, Type]:
        obj, obj_ty = self.eval(e.obj)
        idx, idx_ty = self.eval(e.idx)

        if isinstance(obj_ty, MapTy):
            slot = self.raw("i64", f"call i64 @psd_map_get(ptr {obj}, ptr {idx})")
            out = self.from_slot(slot, obj_ty.elem, owned=True)
            self.decref(obj_ty, obj)
            self.decref(idx_ty, idx)
            return out, obj_ty.elem

        if isinstance(obj_ty, JsonTy):
            if isinstance(idx_ty, StrTy):
                out = self.raw("ptr", f"call ptr @psd_json_get_str(ptr {obj}, ptr {idx})")
            else:
                out = self.raw("ptr", f"call ptr @psd_json_get_idx(ptr {obj}, i64 {idx})")
            self.decref(obj_ty, obj)
            self.decref(idx_ty, idx)
            return out, JSON

        elem_ty = obj_ty.elem
        slot = self.raw("i64", f"call i64 @psd_list_get(ptr {obj}, i64 {idx})")
        out = self.from_slot(slot, elem_ty, owned=True)
        self.decref(obj_ty, obj)
        return out, elem_ty

    # ---- calls -----------------------------------------------------------
    def eval_method(self, e: MethodCall):
        # module call alias.fn(...): static dispatch, receiver is not a value
        if getattr(e, "sig", None) is not None and isinstance(e.recv, Name):
            c = Call(line=e.line, col=e.col, func=e.name, args=e.args)
            c.sig = e.sig
            return self.eval_call(c)
        recv, rty = self.eval(e.recv)
        # json values accept str-style methods (delegating to the string value)
        if isinstance(rty, JsonTy) and e.name in ("upper", "lower", "trim", "contains",
                                                  "starts_with", "ends_with", "replace",
                                                  "split", "repeat", "to_int", "to_float"):
            s = self.raw("ptr", f"call ptr @psd_json_as_str(ptr {recv})")
            self.decref(JSON, recv)
            recv, rty = s, STR
        args = []
        for a in e.args:
            v, ty = self.eval(a)
            args.append((v, ty))
        name = e.name

        def cleanup():
            self.decref(rty, recv)
            for (v, ty) in args:
                self.decref(ty, v)

        def a0():
            return args[0][0] if args else ""

        # ---------------- str methods ----------------
        if isinstance(rty, StrTy):
            out, oty = "0", VOID
            if name == "upper":
                out, oty = self.raw("ptr", f"call ptr @psd_str_upper(ptr {recv})"), STR
            elif name == "lower":
                out, oty = self.raw("ptr", f"call ptr @psd_str_lower(ptr {recv})"), STR
            elif name == "trim":
                out, oty = self.raw("ptr", f"call ptr @psd_str_trim(ptr {recv})"), STR
            elif name == "len":
                out, oty = self.raw("i64", f"call i64 @psd_str_len(ptr {recv})"), INT
            elif name == "contains":
                out = self.raw("i64", f"call i64 @psd_str_contains(ptr {recv}, ptr {a0()})")
                out, oty = self.raw("i8", f"trunc i64 {out} to i8"), BOOL
            elif name == "starts_with":
                out = self.raw("i64", f"call i64 @psd_str_starts_with(ptr {recv}, ptr {a0()})")
                out, oty = self.raw("i8", f"trunc i64 {out} to i8"), BOOL
            elif name == "ends_with":
                out = self.raw("i64", f"call i64 @psd_str_ends_with(ptr {recv}, ptr {a0()})")
                out, oty = self.raw("i8", f"trunc i64 {out} to i8"), BOOL
            elif name == "replace":
                out = self.raw("ptr", f"call ptr @psd_str_replace(ptr {recv}, ptr {a0()}, ptr {args[1][0]})")
                oty = STR
            elif name == "repeat":
                out = self.raw("ptr", f"call ptr @psd_str_repeat(ptr {recv}, i64 {a0()})")
                oty = STR
            elif name == "to_int":
                out, oty = self.raw("i64", f"call i64 @psd_str_to_i64(ptr {recv})"), INT
            elif name == "to_float":
                out, oty = self.raw("double", f"call double @psd_str_to_f64(ptr {recv})"), FLOAT
            elif name == "split":
                # split into [str] via a runtime-free loop using contains?
                # simplest: runtime call psd_str_split not present -> build here
                out, oty = self.str_split(recv, a0()), ListTy(STR)
            else:
                raise CodegenError(f"internal: str.{name}")
            cleanup()
            return out, oty

        # ---------------- list methods ----------------
        if isinstance(rty, ListTy):
            out, oty = "0", VOID
            if name == "push":
                v, vty = args[0]
                v = self.coerce(v, vty, rty.elem)
                slot = self.to_slot(v, rty.elem)
                self.emit(f"call void @psd_list_push(ptr {recv}, i64 {slot})")
                self.decref(rty.elem, v)
                self.decref(rty, recv)
                return "0", VOID
            if name == "pop":
                slot = self.raw("i64", f"call i64 @psd_list_pop(ptr {recv})")
                out = self.from_slot(slot, rty.elem, owned=False)  # transferred
                self.decref(rty, recv)
                return out, rty.elem
            if name == "contains":
                v, vty = args[0]
                v = self.coerce(v, vty, rty.elem)
                slot = self.to_slot(v, rty.elem)
                c = self.raw("i64", f"call i64 @psd_list_contains(ptr {recv}, i64 {slot})")
                self.decref(rty.elem, v)
                self.decref(rty, recv)
                return self.raw("i8", f"trunc i64 {c} to i8"), BOOL
            if name == "reverse":
                self.emit(f"call void @psd_list_reverse(ptr {recv})")
                self.decref(rty, recv)
                return "0", VOID
            if name == "len":
                out = self.raw("i64", f"call i64 @psd_list_len(ptr {recv})")
                self.decref(rty, recv)
                return out, INT
            if name == "join":
                out = self.raw("ptr", f"call ptr @psd_list_join(ptr {recv}, ptr {a0()})")
                self.decref(rty, recv)
                self.decref(STR, args[0][0])
                return out, STR
            raise CodegenError(f"internal: list.{name}")

        # ---------------- map methods ----------------
        if isinstance(rty, MapTy):
            if name == "keys":
                out = self.raw("ptr", f"call ptr @psd_map_keys(ptr {recv})")
                cleanup()
                return out, ListTy(STR)
            if name == "values":
                out = self.raw("ptr", f"call ptr @psd_map_values(ptr {recv})")
                cleanup()
                return out, ListTy(rty.elem)
            if name == "has":
                c = self.raw("i64", f"call i64 @psd_map_has(ptr {recv}, ptr {a0()})")
                cleanup()
                return self.raw("i8", f"trunc i64 {c} to i8"), BOOL
            if name == "remove":
                self.emit(f"call void @psd_map_remove(ptr {recv}, ptr {a0()})")
                cleanup()
                return "0", VOID
            if name == "len":
                out = self.raw("i64", f"call i64 @psd_map_len(ptr {recv})")
                cleanup()
                return out, INT
            if name == "get":
                if len(args) == 1:
                    slot = self.raw("i64", f"call i64 @psd_map_get(ptr {recv}, ptr {a0()})")
                    out = self.from_slot(slot, rty.elem, owned=True)
                    cleanup()
                    return out, rty.elem
                # get(k, default): return default when the key is missing
                key = a0()
                default, dty = args[1]
                dval = self.coerce(default, dty, rty.elem)
                res = self.alloca(ir_ty(rty.elem), "map.get")
                has = self.raw("i64", f"call i64 @psd_map_has(ptr {recv}, ptr {key})")
                cond = self.raw("i1", f"icmp ne i64 {has}, 0")
                get_l = self.new_label("mget")
                def_l = self.new_label("mdef")
                end_l = self.new_label("mend")
                self.emit(f"br i1 {cond}, label %{get_l}, label %{def_l}")
                self.start_label(get_l)
                slot = self.raw("i64", f"call i64 @psd_map_get(ptr {recv}, ptr {key})")
                got = self.from_slot(slot, rty.elem, owned=True)
                self.emit(f"store {ir_ty(rty.elem)} {got}, ptr {res}")
                self.decref(rty.elem, dval)      # default unused on this path
                self.emit(f"br label %{end_l}")
                self.start_label(def_l)
                self.emit(f"store {ir_ty(rty.elem)} {dval}, ptr {res}")
                self.emit(f"br label %{end_l}")
                self.start_label(end_l)
                out = self.raw(ir_ty(rty.elem), f"load {ir_ty(rty.elem)}, ptr {res}")
                cleanup()
                return out, rty.elem
            raise CodegenError(f"internal: map.{name}")

        # ---------------- json methods ----------------
        if isinstance(rty, JsonTy):
            if name == "keys":
                out = self.raw("ptr", f"call ptr @psd_json_keys(ptr {recv})")
                cleanup()
                return out, ListTy(STR)
            if name == "has":
                c = self.raw("i64", f"call i64 @psd_json_has(ptr {recv}, ptr {a0()})")
                cleanup()
                return self.raw("i8", f"trunc i64 {c} to i8"), BOOL
            if name == "len":
                out = self.raw("i64", f"call i64 @psd_json_len(ptr {recv})")
                cleanup()
                return out, INT
            if name == "push":
                v, vty = args[0]
                v = self.coerce(v, vty, JSON)
                self.emit(f"call void @psd_json_push(ptr {recv}, ptr {v})")
                self.decref(JSON, v)
                self.decref(rty, recv)
                return "0", VOID
            raise CodegenError(f"internal: json.{name}")

        raise CodegenError(f"internal: methods on {rty}")

    def str_split(self, s: str, sep: str) -> str:
        """Split a string into [str] using str_contains + a C-free loop.

        Implemented with a runtime-free algorithm: repeatedly find the
        separator with a scanning loop emitted here would be heavy; instead
        we emit a tiny inline loop calling psd_str_split helper.
        """
        return self.raw("ptr", f"call ptr @psd_str_split(ptr {s}, ptr {sep})")

    # ---- calls -----------------------------------------------------------
    def eval_call(self, e: Call):
        name = e.func

        if name == "print":
            for i, a in enumerate(e.args):
                if i > 0:
                    self.emit("call void @psd_print_space()")
                v, ty = self.eval(a)
                if isinstance(ty, IntTy):
                    self.emit(f"call void @psd_print_i64(i64 {v})")
                elif isinstance(ty, FloatTy):
                    self.emit(f"call void @psd_print_f64(double {v})")
                elif isinstance(ty, BoolTy):
                    self.emit(f"call void @psd_print_bool(i8 {v})")
                elif isinstance(ty, JsonTy):
                    self.emit(f"call void @psd_print_json(ptr {v})")
                else:
                    self.emit(f"call void @psd_print_str(ptr {v})")
                if is_refcounted(ty):
                    self.decref(ty, v)
            self.emit("call void @psd_print_nl()")
            return "0", VOID

        if name == "len":
            v, ty = self.eval(e.args[0])
            fn = ("psd_str_len" if isinstance(ty, StrTy) else
                  "psd_map_len" if isinstance(ty, MapTy) else
                  "psd_json_len" if isinstance(ty, JsonTy) else "psd_list_len")
            out = self.raw("i64", f"call i64 @{fn}(ptr {v})")
            self.decref(ty, v)
            return out, INT

        if name == "push":
            lst, lst_ty = self.eval(e.args[0])
            v, vty = self.eval(e.args[1])
            v = self.coerce(v, vty, lst_ty.elem)
            slot = self.to_slot(v, lst_ty.elem)
            self.emit(f"call void @psd_list_push(ptr {lst}, i64 {slot})")
            self.decref(lst_ty, lst)
            if is_refcounted(vty):
                self.decref(vty, v)
            return "0", VOID

        if name == "pop":
            lst, lst_ty = self.eval(e.args[0])
            slot = self.raw("i64", f"call i64 @psd_list_pop(ptr {lst})")
            self.decref(lst_ty, lst)
            # pop TRANSFERS the element reference to us -> no incref
            out = self.from_slot(slot, lst_ty.elem, owned=False)
            return out, lst_ty.elem

        if name == "str":
            v, ty = self.eval(e.args[0])
            if isinstance(ty, StrTy):
                return v, STR
            if isinstance(ty, IntTy):
                return self.raw("ptr", f"call ptr @psd_str_from_i64(i64 {v})"), STR
            if isinstance(ty, FloatTy):
                return self.raw("ptr", f"call ptr @psd_str_from_f64(double {v})"), STR
            if isinstance(ty, BoolTy):
                return self.raw("ptr", f"call ptr @psd_str_from_bool(i8 {v})"), STR
            out = self.raw("ptr", f"call ptr @psd_json_display(ptr {v})")
            self.decref(JSON, v)
            return out, STR

        if name == "int":
            v, ty = self.eval(e.args[0])
            if isinstance(ty, IntTy):
                return v, INT
            if isinstance(ty, FloatTy):
                return self.raw("i64", f"call i64 @psd_float_to_i64(double {v})"), INT
            if isinstance(ty, BoolTy):
                return self.raw("i64", f"zext i8 {v} to i64"), INT
            if isinstance(ty, JsonTy):
                out = self.raw("i64", f"call i64 @psd_json_as_int(ptr {v})")
                self.decref(JSON, v)
                return out, INT
            out = self.raw("i64", f"call i64 @psd_str_to_i64(ptr {v})")
            self.decref(STR, v)
            return out, INT

        if name == "float":
            v, ty = self.eval(e.args[0])
            if isinstance(ty, FloatTy):
                return v, FLOAT
            if isinstance(ty, IntTy):
                return self.raw("double", f"sitofp i64 {v} to double"), FLOAT
            if isinstance(ty, BoolTy):
                return self.raw("double", f"uitofp i8 {v} to double"), FLOAT
            if isinstance(ty, JsonTy):
                out = self.raw("double", f"call double @psd_json_as_float(ptr {v})")
                self.decref(JSON, v)
                return out, FLOAT
            out = self.raw("double", f"call double @psd_str_to_f64(ptr {v})")
            self.decref(STR, v)
            return out, FLOAT

        # ---- v0.2: files / shell / http / json / process -------------------
        def call1(ret, decl):
            v, ty = self.eval(e.args[0])
            out = self.raw(ret, f"call {ret} {decl}(ptr {v})")
            self.decref(ty, v)
            return out

        if name == "read_file":
            return call1("ptr", "@psd_read_file"), STR

        if name == "http_get":
            return call1("ptr", "@psd_http_get"), STR

        if name == "exists":
            out = call1("i64", "@psd_exists")
            return self.raw("i8", f"trunc i64 {out} to i8"), BOOL

        if name == "delete":
            v, ty = self.eval(e.args[0])
            self.emit(f"call void @psd_delete(ptr {v})")
            self.decref(ty, v)
            return "0", VOID

        if name == "list_dir":
            return call1("ptr", "@psd_list_dir"), ListTy(STR)

        if name in ("run", "capture"):
            # argv-based, no shell
            v, ty = self.eval(e.args[0])
            if name == "run":
                out = self.raw("i64", f"call i64 @psd_run(ptr {v})")
                self.decref(ty, v)
                return out, INT
            out = self.raw("ptr", f"call ptr @psd_capture(ptr {v})")
            self.decref(ty, v)
            return out, STR

        if name in ("shell", "capture_shell"):
            # explicit shell execution (trust boundary!)
            v, ty = self.eval(e.args[0])
            fn = "@psd_shell" if name == "shell" else "@psd_capture_shell"
            if name == "shell":
                out = self.raw("i64", f"call i64 {fn}(ptr {v})")
                self.decref(ty, v)
                return out, INT
            out = self.raw("ptr", f"call ptr {fn}(ptr {v})")
            self.decref(ty, v)
            return out, STR

        if name in ("write_file", "append_file"):
            fn = "@psd_write_file" if name == "write_file" else "@psd_append_file"
            p1, t1 = self.eval(e.args[0])
            p2, t2 = self.eval(e.args[1])
            self.emit(f"call void {fn}(ptr {p1}, ptr {p2})")
            self.decref(t1, p1)
            self.decref(t2, p2)
            return "0", VOID

        if name == "http_post":
            u, tu = self.eval(e.args[0])
            b, tb = self.eval(e.args[1])
            c, tc = self.eval(e.args[2])
            out = self.raw("ptr", f"call ptr @psd_http_post(ptr {u}, ptr {b}, ptr {c})")
            self.decref(tu, u); self.decref(tb, b); self.decref(tc, c)
            return out, STR

        if name == "http_request":
            m0, t0 = self.eval(e.args[0])
            u, tu = self.eval(e.args[1])
            b, tb = self.eval(e.args[2])
            h, th = self.eval(e.args[3])
            out = self.raw("ptr", f"call ptr @psd_http_request(ptr {m0}, ptr {u}, ptr {b}, ptr {h})")
            self.decref(t0, m0); self.decref(tu, u); self.decref(tb, b); self.decref(th, h)
            return out, JSON

        if name == "json_parse":
            return call1("ptr", "@psd_json_parse"), JSON

        if name == "json_encode":
            v, ty = self.eval(e.args[0])
            v = self.coerce(v, ty, JSON)
            out = self.raw("ptr", f"call ptr @psd_json_encode(ptr {v})")
            self.decref(JSON, v)
            return out, STR

        # ---- recoverable errors (try_*) ---------------------------------
        if name in ("try_read_file", "try_int", "try_json_parse", "try_http_get"):
            fns = {"try_read_file": "@psd_try_read_file", "try_int": "@psd_try_int",
                   "try_json_parse": "@psd_try_json_parse",
                   "try_http_get": "@psd_try_http_get"}
            return call1("ptr", fns[name]), JSON

        if name == "try_write_file":
            p1, t1 = self.eval(e.args[0])
            p2, t2 = self.eval(e.args[1])
            out = self.raw("ptr", f"call ptr @psd_try_write_file(ptr {p1}, ptr {p2})")
            self.decref(t1, p1); self.decref(t2, p2)
            return out, JSON

        if name == "try_capture":
            v, ty = self.eval(e.args[0])
            if len(e.args) == 2:
                t2, _ = self.eval(e.args[1])
            else:
                t2 = "-1"
            out = self.raw("ptr", f"call ptr @psd_try_capture(ptr {v}, i64 {t2})")
            self.decref(ty, v)
            return out, JSON

        if name == "try_capture_shell":
            v, ty = self.eval(e.args[0])
            if len(e.args) == 2:
                t2, _ = self.eval(e.args[1])
            else:
                t2 = "-1"
            out = self.raw("ptr", f"call ptr @psd_try_capture_shell(ptr {v}, i64 {t2})")
            self.decref(ty, v)
            return out, JSON

        # ---- web serving / html / url / math ----------------------------
        if name in ("html_escape", "url_encode", "url_decode"):
            fns = {"html_escape": "@psd_html_escape", "url_encode": "@psd_url_encode",
                   "url_decode": "@psd_url_decode"}
            return call1("ptr", fns[name]), STR

        if name == "sqrt":
            v, ty = self.eval(e.args[0])
            if isinstance(ty, IntTy):
                v = self.raw("double", f"sitofp i64 {v} to double")
            out = self.raw("double", f"call double @psd_sqrt(double {v})")
            return out, FLOAT

        # ---- v0.4 capabilities -------------------------------------------
        if name in ("exp", "log"):
            v, ty = self.eval(e.args[0])
            if isinstance(ty, IntTy):
                v = self.raw("double", f"sitofp i64 {v} to double")
            fn = "@psd_exp" if name == "exp" else "@psd_log"
            return self.raw("double", f"call double {fn}(double {v})"), FLOAT

        if name == "pow":
            a, ta = self.eval(e.args[0])
            b, tb = self.eval(e.args[1])
            if isinstance(ta, IntTy):
                a = self.raw("double", f"sitofp i64 {a} to double")
            if isinstance(tb, IntTy):
                b = self.raw("double", f"sitofp i64 {b} to double")
            return self.raw("double", f"call double @psd_pow_f64(double {a}, double {b})"), FLOAT

        if name == "sleep_ms":
            v, _ = self.eval(e.args[0])
            self.emit(f"call void @psd_sleep_ms(i64 {v})")
            return "0", VOID

        if name == "time_ms":
            return self.raw("i64", "call i64 @psd_time_ms()"), INT

        if name == "random":
            return self.raw("double", "call double @psd_random()"), FLOAT

        if name == "rand_int":
            v, _ = self.eval(e.args[0])
            return self.raw("i64", f"call i64 @psd_rand_int(i64 {v})"), INT

        if name in ("term_clear", "term_home"):
            self.emit(f"call void @psd_{name}()")
            return "0", VOID

        if name == "read_key":
            return self.raw("ptr", "call ptr @psd_read_key()"), STR

        if name == "term_size":
            return self.raw("ptr", "call ptr @psd_term_size()"), JSON

        if name == "open_url":
            v, ty = self.eval(e.args[0])
            out = self.raw("i64", f"call i64 @psd_open_url(ptr {v})")
            self.decref(ty, v)
            return out, INT
        if name == "read_line":
            if len(e.args) == 1:
                v, ty = self.eval(e.args[0])
                out = self.raw("ptr", f"call ptr @psd_read_line(ptr {v})")
                self.decref(ty, v)
                return out, STR
            return self.raw("ptr", "call ptr @psd_read_line(ptr null)"), STR

        if name == "cwd":
            return self.raw("ptr", "call ptr @psd_cwd()"), STR

        if name == "make_dir":
            v, ty = self.eval(e.args[0])
            self.emit(f"call void @psd_make_dir(ptr {v})")
            self.decref(ty, v)
            return "0", VOID

        if name == "try_make_dir":
            return call1("ptr", "@psd_try_make_dir"), JSON

        if name == "try_float":
            return call1("ptr", "@psd_try_float"), JSON

        if name == "try_run":
            v, ty = self.eval(e.args[0])
            if len(e.args) == 2:
                t2, _ = self.eval(e.args[1])
            else:
                t2 = "-1"
            out = self.raw("ptr", f"call ptr @psd_try_run(ptr {v}, i64 {t2})")
            self.decref(ty, v)
            return out, JSON

        if name == "http_redirect":
            v, ty = self.eval(e.args[0])
            self.emit(f"call void @psd_http_redirect(ptr {v})")
            self.decref(ty, v)
            return "0", VOID

        if name == "http_listen_pool":
            p, _ = self.eval(e.args[0])
            if len(e.args) == 2:
                h, th = self.raw("ptr", "call ptr @psd_str_new(ptr null, i64 0)"), STR
                w, _ = self.eval(e.args[1])
            else:
                h, th = self.eval(e.args[1])
                w, _ = self.eval(e.args[2])
            out = self.raw("i64", f"call i64 @psd_http_listen_pool(i64 {p}, ptr {h}, i64 {w})")
            self.decref(th, h)
            return out, INT

        if name == "http_reply_file":
            p1, t1 = self.eval(e.args[0])
            p2, t2 = self.eval(e.args[1])
            self.emit(f"call void @psd_http_reply_file(ptr {p1}, ptr {p2})")
            self.decref(t1, p1); self.decref(t2, p2)
            return "0", VOID

        if name == "image_info":
            return call1("ptr", "@psd_image_info"), JSON

        if name == "image_process":
            p1, t1 = self.eval(e.args[0])
            p2, t2 = self.eval(e.args[1])
            p3, t3 = self.eval(e.args[2])
            a4, _ = self.eval(e.args[3])
            out = self.raw("ptr", f"call ptr @psd_image_process(ptr {p1}, ptr {p2}, ptr {p3}, i64 {a4})")
            self.decref(t1, p1); self.decref(t2, p2); self.decref(t3, p3)
            return out, JSON

        if name == "http_listen":
            p, _ = self.eval(e.args[0])
            if len(e.args) == 2:
                h, th = self.eval(e.args[1])
            else:
                h, th = self.raw("ptr", "call ptr @psd_str_new(ptr null, i64 0)"), STR
            out = self.raw("i64", f"call i64 @psd_http_listen(i64 {p}, ptr {h})")
            self.decref(th, h)
            return out, INT

        if name == "http_accept":
            return self.raw("ptr", "call ptr @psd_http_accept()"), JSON

        if name == "http_reply":
            s, _ = self.eval(e.args[0])
            c, tc = self.eval(e.args[1])
            b, tb = self.eval(e.args[2])
            self.emit(f"call void @psd_http_reply(i64 {s}, ptr {c}, ptr {b})")
            self.decref(tc, c); self.decref(tb, b)
            return "0", VOID

        if name == "args":
            return self.raw("ptr", "call ptr @psd_args()"), ListTy(STR)

        if name == "env":
            n0, tn0 = self.eval(e.args[0])
            if len(e.args) == 2:
                d, td = self.eval(e.args[1])
                out = self.raw("ptr", f"call ptr @psd_env(ptr {n0}, ptr {d})")
                self.decref(td, d)
            else:
                out = self.raw("ptr", f"call ptr @psd_env(ptr {n0}, ptr null)")
            self.decref(tn0, n0)
            return out, STR

        if name == "exit":
            v, ty = self.eval(e.args[0])
            self.emit(f"call void @psd_exit(i64 {v})")
            self.emit("unreachable")
            self.terminate()
            return "0", VOID

        # user function
        f = e.sig
        arg_regs = []
        arg_tys = []
        for a, p in zip(e.args, f.params):
            v, ty = self.eval(a)
            v = self.coerce(v, ty, p.ty)
            arg_regs.append((v, p.ty))        # after coercion the value IS p.ty
            arg_tys.append(p.ty)
        params_s = ", ".join(f"{ir_ty(p.ty)} {r}" for (r, _), p in zip(
            [(v, t) for v, t in arg_regs], f.params))
        ret_ty = f.ret_ty if f.ret_ty is not None else f.inferred_ret_ty
        rty = ir_ty(ret_ty)
        if isinstance(ret_ty, VoidTy):
            self.emit(f"call void @psd_f_{f.name}({params_s})")
            out = "0"
        else:
            out = self.raw(rty, f"call {rty} @psd_f_{f.name}({params_s})")
        for (v, ty) in arg_regs:
            if is_refcounted(ty):
                self.decref(ty, v)
        return out, ret_ty

    # ---- statements ------------------------------------------------------
    def emit_block(self, block: Block, new_scope: bool = True) -> None:
        if new_scope:
            self.push_scope()
        for st in block.stmts:
            if self.terminated:
                break
            self.emit_stmt(st)
        if new_scope and not self.terminated:
            self.pop_scope()
        elif new_scope:
            # scope cleanup on this path already happened via return/break/continue
            self.scopes.pop()

    def emit_stmt(self, st) -> None:
        if isinstance(st, Block):
            self.emit_block(st)

        elif isinstance(st, LetDecl):
            v, ty = self.eval(st.expr)
            if st.ann is not None and isinstance(st.ann, FloatTy) and isinstance(ty, IntTy):
                v = self.coerce(v, INT, FLOAT)
                ty = FLOAT
            final_ty = st.ann if st.ann is not None else ty
            if isinstance(final_ty, FloatTy) and isinstance(ty, IntTy):
                v = self.coerce(v, INT, FLOAT)
            addr, _, _ = self.declare(st.name, final_ty, owned=True)
            self.emit(f"store {ir_ty(final_ty)} {v}, ptr {addr}")

        elif isinstance(st, Assign):
            self.emit_assign(st)

        elif isinstance(st, ExprStmt):
            v, ty = self.eval(st.expr)
            if is_refcounted(ty):
                self.decref(ty, v)

        elif isinstance(st, Return):
            if st.expr is not None:
                v, ty = self.eval(st.expr)
                ret_ty = self.func.ret_ty or self.func.inferred_ret_ty or VOID
                v = self.coerce(v, ty, ret_ty)
                self.cleanup_scopes_until(0)
                self.cleanup_loop_temps()
                self.emit(f"ret {ir_ty(ret_ty)} {v}")
            else:
                self.cleanup_scopes_until(0)
                self.cleanup_loop_temps()
                self.emit("ret void")
            self.terminate()

        elif isinstance(st, If):
            self.emit_if(st)

        elif isinstance(st, While):
            self.emit_while(st)

        elif isinstance(st, For):
            self.emit_for(st)

        elif isinstance(st, Break):
            brk, _cont, depth, _owned = self.loops[-1]
            self.cleanup_scopes_until(depth)
            self.emit(f"br label %{brk}")
            self.terminate()

        elif isinstance(st, Continue):
            _brk, cont, depth, _owned = self.loops[-1]
            self.cleanup_scopes_until(depth)
            self.emit(f"br label %{cont}")
            self.terminate()

        else:
            raise CodegenError(f"internal: unknown stmt {type(st).__name__}")

    def emit_assign(self, st: Assign) -> None:
        t = st.target

        if isinstance(t, Name):
            addr, ty, _ = self.lookup(t.name)
            if st.op == "=":
                v, vty = self.eval(st.expr)
                v = self.coerce(v, vty, ty)
                if is_refcounted(ty):
                    old = self.raw("ptr", f"load ptr, ptr {addr}")
                    self.decref(ty, old)
                self.emit(f"store {ir_ty(ty)} {v}, ptr {addr}")
            else:
                cur = self.raw(ir_ty(ty), f"load {ir_ty(ty)}, ptr {addr}")
                if is_refcounted(ty):
                    self.incref(ty, cur)      # working copy for concat
                v, vty = self.eval(st.expr)
                nv, nty = self.apply_compound(st.op, cur, ty, v, vty)
                if is_refcounted(ty):
                    old = self.raw("ptr", f"load ptr, ptr {addr}")
                    self.decref(ty, old)
                self.emit(f"store {ir_ty(nty)} {nv}, ptr {addr}")

        elif isinstance(t, Index):
            obj, obj_ty = self.eval(t.obj)
            idx, idx_ty = self.eval(t.idx)

            if isinstance(obj_ty, MapTy):
                elem_ty = obj_ty.elem
                if st.op == "=":
                    v, vty = self.eval(st.expr)
                    v = self.coerce(v, vty, elem_ty)
                    slot = self.to_slot(v, elem_ty)
                    self.emit(f"call void @psd_map_set(ptr {obj}, ptr {idx}, i64 {slot})")
                    self.decref(elem_ty, v)
                else:
                    cur_slot = self.raw("i64", f"call i64 @psd_map_get(ptr {obj}, ptr {idx})")
                    cur = self.from_slot(cur_slot, elem_ty, owned=True)
                    v, vty = self.eval(st.expr)
                    nv, nty = self.apply_compound(st.op, cur, elem_ty, v, vty)
                    slot = self.to_slot(nv, elem_ty)
                    self.emit(f"call void @psd_map_set(ptr {obj}, ptr {idx}, i64 {slot})")
                    self.decref(nty, nv)
                self.decref(obj_ty, obj)
                self.decref(idx_ty, idx)
                return

            if isinstance(obj_ty, JsonTy):
                v, vty = self.eval(st.expr)
                v = self.coerce(v, vty, JSON)
                if isinstance(idx_ty, StrTy):
                    self.emit(f"call void @psd_json_set_str(ptr {obj}, ptr {idx}, ptr {v})")
                else:
                    self.emit(f"call void @psd_json_set_idx(ptr {obj}, i64 {idx}, ptr {v})")
                self.decref(JSON, v)
                self.decref(obj_ty, obj)
                self.decref(idx_ty, idx)
                return

            elem_ty = obj_ty.elem
            if st.op == "=":
                v, vty = self.eval(st.expr)
                v = self.coerce(v, vty, elem_ty)
                slot = self.to_slot(v, elem_ty)
                self.emit(f"call void @psd_list_set(ptr {obj}, i64 {idx}, i64 {slot})")
                self.decref(elem_ty, v)
            else:
                raw_slot = self.raw("i64", f"call i64 @psd_list_get(ptr {obj}, i64 {idx})")
                cur = self.from_slot(raw_slot, elem_ty, owned=True)
                v, vty = self.eval(st.expr)
                nv, nty = self.apply_compound(st.op, cur, elem_ty, v, vty)
                slot = self.to_slot(nv, elem_ty)
                self.emit(f"call void @psd_list_set(ptr {obj}, i64 {idx}, i64 {slot})")
                self.decref(nty, nv)
            self.decref(obj_ty, obj)
        else:
            raise CodegenError("internal: bad assignment target")

    def apply_compound(self, op: str, cur, cur_ty, rhs, rhs_ty):
        """Compute `cur <op> rhs` for += -= *= /= %="."""
        inner = op[0]
        if inner == "+" and isinstance(cur_ty, StrTy):
            out = self.raw("ptr", f"call ptr @psd_str_concat(ptr {cur}, ptr {rhs})")
            self.decref(STR, cur)
            self.decref(STR, rhs)
            return out, STR
        # numeric
        if isinstance(cur_ty, FloatTy) or isinstance(rhs_ty, FloatTy):
            a = self.coerce(cur, cur_ty, FLOAT)
            b = self.coerce(rhs, rhs_ty, FLOAT)
            llvm_op = {"+": "fadd", "-": "fsub", "*": "fmul", "/": "fdiv", "%": "frem"}[inner]
            return self.raw("double", f"{llvm_op} double {a}, {b}"), FLOAT
        llvm_op = {"+": "add", "-": "sub", "*": "mul"}.get(inner)
        if inner == "/":
            return self.raw("i64", f"call i64 @psd_div_i64(i64 {cur}, i64 {rhs})"), INT
        if inner == "%":
            return self.raw("i64", f"call i64 @psd_mod_i64(i64 {cur}, i64 {rhs})"), INT
        return self.raw("i64", f"{llvm_op} i64 {cur}, {rhs}"), INT

    def emit_if(self, st: If) -> None:
        cond, cond_ty = self.eval(st.cond)
        if isinstance(cond_ty, JsonTy):
            b = self.raw("i8", f"call i8 @psd_json_truthy(ptr {cond})")
            self.decref(JSON, cond)
            cond = b
        c = self.raw("i1", f"icmp ne i8 {cond}, 0")
        then_l = self.new_label("if_then")
        end_l = self.new_label("if_end")
        else_l = self.new_label("if_else") if st.orelse is not None else end_l
        self.emit(f"br i1 {c}, label %{then_l}, label %{else_l}")

        self.start_label(then_l)
        self.emit_block(st.body)
        if not self.terminated:
            self.emit(f"br label %{end_l}")
        then_term = self.terminated

        else_term = False
        if st.orelse is not None:
            self.start_label(else_l)
            if isinstance(st.orelse, Block):
                self.emit_block(st.orelse)
            else:
                self.emit_if(st.orelse)
            if not self.terminated:
                self.emit(f"br label %{end_l}")
            else_term = self.terminated

        self.start_label(end_l)
        # end is unreachable only if BOTH explicit branches terminate;
        # without an else, control can always fall through.
        if then_term and st.orelse is not None and else_term:
            self.force_emit("  unreachable")
            self.terminate()

    def emit_while(self, st: While) -> None:
        cond_l = self.new_label("wh_cond")
        body_l = self.new_label("wh_body")
        end_l = self.new_label("wh_end")

        self.emit(f"br label %{cond_l}")
        self.start_label(cond_l)
        cond, cond_ty = self.eval(st.cond)
        if isinstance(cond_ty, JsonTy):
            b = self.raw("i8", f"call i8 @psd_json_truthy(ptr {cond})")
            self.decref(JSON, cond)
            cond = b
        c = self.raw("i1", f"icmp ne i8 {cond}, 0")
        self.emit(f"br i1 {c}, label %{body_l}, label %{end_l}")

        self.start_label(body_l)
        self.push_scope()
        self.loops.append((end_l, cond_l, len(self.scopes) - 1, []))
        for s in st.body.stmts:
            if not self.terminated:
                self.emit_stmt(s)
        self.loops.pop()
        if not self.terminated:
            self.pop_scope()
            self.emit(f"br label %{cond_l}")
        else:
            self.scopes.pop()
        self.start_label(end_l)

    def emit_for(self, st: For) -> None:
        it = st.iter
        is_range = (
            isinstance(it, Call) and it.func == "range"
        )
        end_l = self.new_label("for_end")
        body_l = self.new_label("for_body")
        inc_l = self.new_label("for_inc")

        if is_range:
            args = it.args
            if len(args) == 1:
                start_v = "0"
                end_v, _ = self.eval(args[0])
            else:
                start_v, _ = self.eval(args[0])
                end_v, _ = self.eval(args[1])
            idx = self.alloca("i64", "for.index")
            self.emit(f"store i64 {start_v}, ptr {idx}")
            loop_owned = []
            json_iter = False
        else:
            lst, lst_ty = self.eval(it)
            lst_reg = lst
            json_iter = isinstance(lst_ty, JsonTy)
            if json_iter:
                n = self.raw("i64", f"call i64 @psd_json_len(ptr {lst_reg})")
            else:
                n = self.raw("i64", f"call i64 @psd_list_len(ptr {lst_reg})")
            idx = self.alloca("i64", "for.index")
            self.emit(f"store i64 0, ptr {idx}")
            loop_owned = [(lst_reg, lst_ty)]

        cond_l = self.new_label("for_cond")
        self.emit(f"br label %{cond_l}")
        self.start_label(cond_l)
        iv = self.raw("i64", f"load i64, ptr {idx}")
        if is_range:
            c = self.raw("i1", f"icmp slt i64 {iv}, {end_v}")
        else:
            c = self.raw("i1", f"icmp slt i64 {iv}, {n}")
        self.emit(f"br i1 {c}, label %{body_l}, label %{end_l}")

        self.start_label(body_l)
        self.push_scope()
        self.loops.append((end_l, inc_l, len(self.scopes) - 1, loop_owned))
        if is_range:
            addr, _, _ = self.declare(st.var, INT, owned=True)
            self.emit(f"store i64 {iv}, ptr {addr}")
        elif json_iter:
            val = self.raw("ptr", f"call ptr @psd_json_get_idx(ptr {lst_reg}, i64 {iv})")
            addr, _, _ = self.declare(st.var, JSON, owned=True)
            self.emit(f"store ptr {val}, ptr {addr}")
        else:
            slot = self.raw("i64", f"call i64 @psd_list_get(ptr {lst_reg}, i64 {iv})")
            val = self.from_slot(slot, st.var_ty, owned=True)
            addr, _, _ = self.declare(st.var, st.var_ty, owned=True)
            self.emit(f"store {ir_ty(st.var_ty)} {val}, ptr {addr}")
        for s in st.body.stmts:
            if not self.terminated:
                self.emit_stmt(s)
        self.loops.pop()
        if not self.terminated:
            self.pop_scope()
            self.emit(f"br label %{inc_l}")
        else:
            self.scopes.pop()

        self.start_label(inc_l)
        iv2 = self.raw("i64", f"load i64, ptr {idx}")
        iv3 = self.raw("i64", f"add i64 {iv2}, 1")
        self.emit(f"store i64 {iv3}, ptr {idx}")
        self.emit(f"br label %{cond_l}")

        self.start_label(end_l)
        if not is_range:
            self.decref(lst_ty, lst_reg)

    # ---- function --------------------------------------------------------
    def emit_function(self) -> str:
        f = self.func
        ret_ty = f.ret_ty if f.ret_ty is not None else f.inferred_ret_ty
        if ret_ty is None:
            ret_ty = VOID
        params = []
        for i, p in enumerate(f.params):
            params.append(f"{ir_ty(p.ty)} %p{i}")

        self.push_scope()
        for i, p in enumerate(f.params):
            addr, _, _ = self.declare(p.name, p.ty, owned=False)
            self.emit(f"store {ir_ty(p.ty)} %p{i}, ptr {addr}")

        self.emit_block(f.body, new_scope=True)

        if not self.terminated:
            self.pop_scope()
            if isinstance(ret_ty, VoidTy):
                self.emit("ret void")
            else:
                self.force_emit("  unreachable")
        else:
            if self.scopes:
                self.scopes.pop()

        head = (f"define {ir_ty(ret_ty)} @psd_f_{f.name}"
                f"({', '.join(params)}) {{\nentry:")
        allocas = "\n".join(self.allocas)
        body = "\n".join(self.body)
        return f"{head}\n{allocas}\n{body}\n}}\n"


class Codegen:
    def __init__(self, module: Module, triple: str = ""):
        self.module = module
        self.triple = triple
        self.strings: list[str] = []
        self.const_index: dict[str, str] = {}

    def string_const_meta(self, s: str) -> tuple[str, int]:
        """Return (global_name, byte_len) for a literal's backing constant.

        Literals live in read-only memory, so codegen must materialize an
        owned heap copy (psd_str_new) at each use site — the RC model treats
        every str value as an owned heap object.
        """
        if s not in self.const_index:
            name = f"@.str.{len(self.strings)}"
            self.const_index[s] = name
            self.strings.append(s)
        return self.const_index[s], len(s.encode("utf-8"))

    def generate(self) -> str:
        out: list[str] = []
        out.append("; generated by the PSD compiler")
        if self.triple:
            out.append(f'target triple = "{self.triple}"')
        out.append(RUNTIME_DECLS)

        for f in self.module.funcs:
            out.append(FuncEmitter(self, f).emit_function())

        # real entry point
        main_fn = next((f for f in self.module.funcs if f.name == "main"), None)
        if main_fn is not None:
            ret_ty = main_fn.ret_ty or main_fn.inferred_ret_ty or VOID
            head = ("define i32 @main(i32 %argc, ptr %argv) {\nentry:\n"
                    "  call void @psd_set_args(i32 %argc, ptr %argv)\n")
            if isinstance(ret_ty, VoidTy):
                out.append(head + "  call void @psd_f_main()\n  ret i32 0\n}\n")
            else:
                out.append(head + "  %v = call i64 @psd_f_main()\n"
                           "  %c = trunc i64 %v to i32\n  ret i32 %c\n}\n")

        str_defs = []
        for i, s in enumerate(self.strings):
            data = escape_bytes(s) + "\\00"
            n = len(s.encode("utf-8")) + 1
            str_defs.append(f'@.str.{i} = private unnamed_addr constant [{n} x i8] c"{data}"')
        out.append("\n".join(str_defs))

        return "\n".join(out) + "\n"


def generate(module: Module, triple: str = "") -> str:
    return Codegen(module, triple).generate()

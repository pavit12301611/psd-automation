"""AST node definitions for PSD.

Every expression node gets a `.ty` attribute filled in by the checker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Type:
    pass

@dataclass(frozen=True)
class IntTy(Type):
    def __str__(self) -> str: return "int"

@dataclass(frozen=True)
class FloatTy(Type):
    def __str__(self) -> str: return "float"

@dataclass(frozen=True)
class StrTy(Type):
    def __str__(self) -> str: return "str"

@dataclass(frozen=True)
class BoolTy(Type):
    def __str__(self) -> str: return "bool"

@dataclass(frozen=True)
class VoidTy(Type):
    def __str__(self) -> str: return "void"

@dataclass(frozen=True)
class ListTy(Type):
    elem: Type
    def __str__(self) -> str: return f"[{self.elem}]"

@dataclass(frozen=True)
class MapTy(Type):
    """Homogeneous map with str keys: {str: T}."""
    elem: Type
    def __str__(self) -> str: return f"{{str: {self.elem}}}"

@dataclass(frozen=True)
class JsonTy(Type):
    """Dynamic JSON value (null/bool/int/float/str/list/object)."""
    def __str__(self) -> str: return "json"

@dataclass(frozen=True)
class RangeTy(Type):
    """Internal pseudo-type for range(...) in for-loops."""
    def __str__(self) -> str: return "range"

INT = IntTy()
FLOAT = FloatTy()
STR = StrTy()
BOOL = BoolTy()
VOID = VoidTy()
JSON = JsonTy()
RANGE = RangeTy()


def is_refcounted(ty: Type) -> bool:
    return isinstance(ty, (StrTy, ListTy, MapTy, JsonTy))


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

@dataclass
class Expr:
    line: int = 0
    col: int = 0
    ty: Optional[Type] = field(default=None, compare=False)

@dataclass
class IntLit(Expr):
    value: int = 0

@dataclass
class FloatLit(Expr):
    value: float = 0.0

@dataclass
class BoolLit(Expr):
    value: bool = False

@dataclass
class StrInterp(Expr):
    # list of ("lit", str) or ("expr", Expr)
    parts: list = field(default_factory=list)

@dataclass
class ListLit(Expr):
    elems: list = field(default_factory=list)

@dataclass
class MapLit(Expr):
    # list of (key_text, Expr)
    pairs: list = field(default_factory=list)

@dataclass
class Name(Expr):
    name: str = ""

@dataclass
class BinOp(Expr):
    op: str = ""        # + - * / % == != < <= > >= and or
    left: Expr = None
    right: Expr = None

@dataclass
class UnaryOp(Expr):
    op: str = ""        # - !
    operand: Expr = None

@dataclass
class Call(Expr):
    func: str = ""
    args: list = field(default_factory=list)

@dataclass
class Index(Expr):
    obj: Expr = None
    idx: Expr = None

@dataclass
class MethodCall(Expr):
    recv: Expr = None
    name: str = ""
    args: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

@dataclass
class Stmt:
    line: int = 0
    col: int = 0

@dataclass
class LetDecl(Stmt):
    name: str = ""
    ann: Optional[Type] = None     # optional type annotation
    expr: Expr = None

@dataclass
class Assign(Stmt):
    target: Expr = None            # Name or Index
    op: str = "="                  # = += -= *= /= %=
    expr: Expr = None

@dataclass
class ExprStmt(Stmt):
    expr: Expr = None

@dataclass
class Return(Stmt):
    expr: Optional[Expr] = None

@dataclass
class If(Stmt):
    cond: Expr = None
    body: "Block" = None
    orelse: Optional["Block | If"] = None   # Block, nested If (elif/else-if), or None

@dataclass
class While(Stmt):
    cond: Expr = None
    body: "Block" = None

@dataclass
class For(Stmt):
    var: str = ""
    iter: Expr = None              # range(...) call or list expression
    body: "Block" = None

@dataclass
class Break(Stmt):
    pass

@dataclass
class Continue(Stmt):
    pass


@dataclass
class Block(Stmt):
    stmts: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------

@dataclass
class Param:
    name: str
    ty: Type
    line: int = 0
    col: int = 0

@dataclass
class FuncDef:
    name: str
    params: list                     # list[Param]
    ret_ty: Optional[Type]           # None = infer (or void)
    body: Block
    line: int = 0
    col: int = 0
    # filled by checker:
    inferred_ret_ty: Optional[Type] = field(default=None, compare=False)


@dataclass
class Import:
    """import "path.psd" [as alias]"""
    path: str = ""
    alias: str = ""          # module alias (explicit `as` name or file stem)
    line: int = 0
    col: int = 0


@dataclass
class FromImport:
    """from "path.psd" import a, b"""
    path: str = ""
    names: list = field(default_factory=list)   # list[str]
    line: int = 0
    col: int = 0


@dataclass
class Module:
    funcs: list = field(default_factory=list)   # list[FuncDef]
    imports: list = field(default_factory=list) # list[Import | FromImport]

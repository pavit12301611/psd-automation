"""Multi-file program loader.

Resolves `import "path.psd" [as alias]` and `from "path.psd" import f, g`
into one flat Module ready for the checker/codegen:

- Every imported module's functions are renamed `mN__name` (mangling), so
  modules can freely reuse function names. The entry file keeps plain names,
  so single-file programs are completely unaffected.
- Each module gets its own function table (own functions + `from` imports),
  giving file-scoped namespaces — a module's functions are its interface.
- Alias map per module supports `alias.fn(...)` calls.
- Imports are resolved relative to the importing file; a `std/` prefix
  resolves inside the compiler's bundled standard-library directory.
- Diamond imports load once; true cycles are a compile-time error.
"""

from __future__ import annotations

import os

from .ast_nodes import FromImport, Import, Module
from .lexer import Lexer
from .parser import Parser

STDLIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "std")


class LoaderError(Exception):
    def __init__(self, message: str, filename: str = "<import>",
                 line: int = 0, col: int = 0):
        super().__init__(message)
        self.message = message
        self.filename = filename
        self.line = line
        self.col = col

    def __str__(self) -> str:
        return (f"error: {self.message} "
                f"(file {self.filename}, line {self.line}, col {self.col})")


class Program:
    """A fully linked multi-file program."""

    def __init__(self):
        self.module = Module()                  # flat AST (all functions)
        self.tables: dict[str, dict[str, object]] = {}   # mod -> {short: FuncDef}
        self.aliases: dict[str, dict[str, str]] = {}     # mod -> {alias: mod}
        self.order: list = []                   # FuncDefs, dependencies first
        self.entry_mod: str = ""
        self.files: dict[str, str] = {}         # mod -> display path
        self.source_paths: list[str] = []       # abs paths, load order (cache keys)


class Loader:
    def __init__(self, entry_path: str):
        self.entry_path = os.path.abspath(entry_path)
        self.prog = Program()
        self._mod_of_file: dict[str, str] = {}
        self._visiting: list[str] = []          # abs paths, for cycle errors
        self._counter = 0

    # ---- path resolution -------------------------------------------------
    def resolve(self, path: str, base_dir: str, tok) -> str:
        if not path.endswith(".psd"):
            raise LoaderError(f"import path must end in '.psd': {path!r}",
                              tok.filename, tok.line, tok.col)
        if path.startswith("std/"):
            cand = os.path.join(STDLIB_DIR, path[len("std/"):])
        else:
            cand = os.path.join(base_dir, path)
        return os.path.abspath(cand)

    # ---- loading ---------------------------------------------------------
    def load(self) -> Program:
        self.load_file(self.entry_path, is_entry=True)
        return self.prog

    def load_file(self, abs_path: str, is_entry: bool = False) -> str:
        # cycle check MUST precede the reuse check: a file on the current
        # import stack is a true cycle, not a diamond
        if abs_path in self._visiting:
            chain = [os.path.basename(p) for p in self._visiting]
            chain.append(os.path.basename(abs_path))
            display = (os.path.relpath(abs_path)
                       if abs_path != self.entry_path else abs_path)
            raise LoaderError("import cycle detected: " + " -> ".join(chain),
                              display, 0, 0)
        if abs_path in self._mod_of_file:
            return self._mod_of_file[abs_path]      # diamond: load once
        display = (os.path.relpath(abs_path)
                   if abs_path != self.entry_path else abs_path)
        self.prog.source_paths.append(abs_path)
        self._visiting.append(abs_path)
        try:
            try:
                with open(abs_path) as fh:
                    src = fh.read()
            except OSError as e:
                raise LoaderError(f"cannot read {abs_path}: {e.strerror}",
                                  display, 0, 0)
            from .parser import parse
            module = parse(src, display)

            mod_id = "" if is_entry else f"m{self._counter}"
            if not is_entry:
                self._counter += 1
            self._mod_of_file[abs_path] = mod_id
            self.prog.files[mod_id] = display
            table: dict[str, object] = {}
            self.prog.tables[mod_id] = table
            self.prog.aliases.setdefault(mod_id, {})
            if is_entry:
                self.prog.entry_mod = mod_id

            # own functions -> table (with per-file duplicate check + mangling).
            # They enter `prog.order` only after this file's imports, so
            # imported return types are inferred before the callers check.
            own = []
            for f in module.funcs:
                if f.name in table:
                    raise LoaderError(f"duplicate function '{f.name}' in {display}",
                                      display, f.line, f.col)
                short = f.name
                if not is_entry:
                    f.name = f"{mod_id}__{f.name}"
                table[short] = f
                f.mod = mod_id
                self.prog.module.funcs.append(f)
                own.append(f)

            # imports
            base_dir = os.path.dirname(abs_path)
            for imp in module.imports:
                target = self.resolve(imp.path, base_dir, imp)
                if not os.path.exists(target):
                    raise LoaderError(f"cannot import {imp.path!r}: file not found",
                                      display, imp.line, imp.col)
                tid = self.load_file(target)
                if isinstance(imp, Import):
                    if imp.alias in self.prog.aliases[mod_id]:
                        raise LoaderError(
                            f"duplicate import alias '{imp.alias}'",
                            display, imp.line, imp.col)
                    self.prog.aliases[mod_id][imp.alias] = tid
                else:   # FromImport
                    for nm in imp.names:
                        f = self.prog.tables[tid].get(nm)
                        if f is None:
                            raise LoaderError(
                                f"{imp.path!r} has no function '{nm}'",
                                display, imp.line, imp.col)
                        if nm in table:
                            raise LoaderError(
                                f"conflicting import name '{nm}' — rename one of "
                                f"the functions or use 'import ... as' + calls",
                                display, imp.line, imp.col)
                        table[nm] = f
            self.prog.order.extend(own)
            return mod_id
        finally:
            self._visiting.pop()


def load_program(entry_path: str) -> Program:
    return Loader(entry_path).load()

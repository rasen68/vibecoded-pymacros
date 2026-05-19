"""PEP 638: Syntactic Macros support module.

This module provides the infrastructure for defining and using syntactic macros
in Python. Macros are compile-time transformations that operate on the AST.

Constants:
    STMT_MACRO     - A statement macro with an indented body
    SIBLING_MACRO  - A statement macro whose body is the next statement
    EXPR_MACRO     - An expression macro

Functions:
    macro_processor  - Decorator to create a macro processor tuple
    expand           - Expand macros in an AST tree
    compile_with_macros - Compile source with macro expansion
"""

import ast
import importlib
import copy

# Macro kind constants
STMT_MACRO = 1
SIBLING_MACRO = 2
EXPR_MACRO = 3

# Maximum macro expansion depth to prevent infinite recursion
_MAX_EXPANSION_DEPTH = 100


def macro_processor(kind, version, *additional_names):
    """Decorator to create a macro processor tuple.

    A macro processor is a 4-tuple of (func, kind, version, additional_names).

    Args:
        kind: One of STMT_MACRO, SIBLING_MACRO, or EXPR_MACRO
        version: Integer version for bytecode cache invalidation
        *additional_names: Names of additional macro parts (for multi-part macros)

    Returns:
        A decorator that wraps a function into a macro processor tuple.

    Example:
        @macro_processor(STMT_MACRO, 1)
        def my_macro(node):
            return ast.Module(body=node.body, type_ignores=[])
    """
    def deco(func):
        return (func, kind, version, additional_names)
    return deco


class MacroExpander(ast.NodeTransformer):
    """AST transformer that expands macro invocations.

    The expander maintains a per-scope macro registry and handles:
    - import! and from! for compile-time macro imports
    - Macro processor lookup and invocation
    - Recursion depth limiting
    """

    def __init__(self, registry=None, filename="<unknown>"):
        self._registry = dict(registry) if registry else {}
        self._filename = filename
        self._depth = 0

    def _resolve_import(self, node):
        """Handle import! and from! macro statements.

        import! dotted_name as name
            -> imports dotted_name module, registers all macro processors

        from! dotted_name import name [as alias]
            -> imports specific processor from module
        """
        macro_name = node.name  # e.g., "import!" or "from!"

        if macro_name == "import!":
            if not node.args:
                raise SyntaxError("import! requires a module name")
            # Get module name from dotted_name expression
            mod_name = ast.unparse(node.args[0])
            alias = node.asname if node.asname else mod_name.split(".")[-1]

            try:
                mod = importlib.import_module(mod_name)
            except ImportError as e:
                raise SyntaxError(
                    f"import!: cannot import module '{mod_name}': {e}"
                ) from None

            # Register all macro processors found in the module
            # Look for 4-tuples (func, kind, version, additional_names)
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if (isinstance(obj, tuple) and len(obj) == 4
                        and callable(obj[0])
                        and isinstance(obj[1], int)
                        and isinstance(obj[2], int)
                        and isinstance(obj[3], tuple)):
                    proc_name = f"{alias}!"
                    self._registry[proc_name] = obj

            # Also register the module itself under the alias for from!-style access
            self._registry[f"_module_{alias}"] = mod
            return []  # Remove the import! statement from the AST

        elif macro_name == "from!":
            if not node.args:
                raise SyntaxError("from! requires a module name")
            mod_name = ast.unparse(node.args[0])
            import_name = node.importname
            alias = node.asname if node.asname else import_name

            if not import_name:
                raise SyntaxError("from! requires 'import name'")

            try:
                mod = importlib.import_module(mod_name)
            except ImportError as e:
                raise SyntaxError(
                    f"from!: cannot import module '{mod_name}': {e}"
                ) from None

            proc = getattr(mod, import_name, None)
            if proc is None:
                raise SyntaxError(
                    f"from!: module '{mod_name}' has no attribute '{import_name}'"
                )

            if not (isinstance(proc, tuple) and len(proc) == 4):
                raise SyntaxError(
                    f"from!: '{import_name}' in '{mod_name}' is not a macro processor"
                )

            proc_name = f"{alias}!"
            self._registry[proc_name] = proc
            return []  # Remove the from! statement

        return None  # Not an import macro

    def _expand_macro_stmt(self, node):
        """Expand a MacroStmt by looking up and calling its processor."""
        name = node.name

        # Check for import!/from! first
        if name in ("import!", "from!"):
            return self._resolve_import(node)

        proc = self._registry.get(name)
        if proc is None:
            raise SyntaxError(f"unknown macro '{name}'")

        func, kind, version, additional_names = proc

        if kind == STMT_MACRO:
            if not node.body:
                raise SyntaxError(
                    f"statement macro '{name}' requires an indented body"
                )
            result = func(node)
        elif kind == SIBLING_MACRO:
            result = func(node)
        elif kind == EXPR_MACRO:
            raise SyntaxError(
                f"macro '{name}' is an expression macro, not a statement macro"
            )
        else:
            raise SyntaxError(f"invalid macro kind {kind} for '{name}'")

        if result is None:
            return []

        # The processor should return an AST node
        if isinstance(result, ast.mod):
            # If result is a Module, splice its body
            return result.body
        elif isinstance(result, ast.stmt):
            return [result]
        elif isinstance(result, list):
            return result
        else:
            raise SyntaxError(
                f"macro '{name}' processor returned invalid result: {type(result)}"
            )

    def _expand_macro_expr(self, node):
        """Expand a MacroExpr by looking up and calling its processor."""
        name = node.name
        proc = self._registry.get(name)
        if proc is None:
            raise SyntaxError(f"unknown macro '{name}'")

        func, kind, version, additional_names = proc

        if kind != EXPR_MACRO:
            raise SyntaxError(
                f"macro '{name}' is not an expression macro"
            )

        result = func(node)

        if not isinstance(result, ast.expr):
            raise SyntaxError(
                f"expression macro '{name}' must return an expr node, got {type(result)}"
            )

        return result

    def visit_Module(self, node):
        """Process module body, handling sibling macros."""
        node.body = self._process_stmt_list(node.body)
        return node

    def _process_stmt_list(self, stmts):
        """Process a list of statements, handling macro expansion and sibling macros."""
        result = []
        i = 0
        while i < len(stmts):
            stmt = stmts[i]
            if isinstance(stmt, ast.MacroStmt):
                name = stmt.name
                proc = self._registry.get(name)

                # Handle sibling macros: grab next statement as body
                if (proc is not None and proc[1] == SIBLING_MACRO
                        and not stmt.body):
                    if i + 1 < len(stmts):
                        stmt.body = [stmts[i + 1]]
                        i += 1  # Skip the sibling statement
                    else:
                        raise SyntaxError(
                            f"sibling macro '{name}' requires a following statement"
                        )

                self._depth += 1
                if self._depth > _MAX_EXPANSION_DEPTH:
                    raise SyntaxError("macro expansion limit exceeded")
                try:
                    expanded = self._expand_macro_stmt(stmt)

                    if expanded:
                        # Recursively process the expanded statements
                        # (macros can return macros). Keep depth incremented
                        # so recursive macro invocations are counted.
                        expanded = self._process_stmt_list(expanded)
                        result.extend(expanded)
                finally:
                    self._depth -= 1
            else:
                # Not a macro - recursively process child nodes
                stmt = self._visit_stmt_children(stmt)
                result.append(stmt)
            i += 1
        return result

    def _visit_stmt_children(self, node):
        """Visit child nodes of a statement, expanding any macros found."""
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                new_values = []
                changed = False
                for item in value:
                    if isinstance(item, ast.stmt):
                        if isinstance(item, ast.MacroStmt):
                            changed = True
                            # Need to process as list
                            expanded = self._process_stmt_list([item])
                            new_values.extend(expanded)
                        else:
                            new_item = self._visit_stmt_children(item)
                            new_values.append(new_item)
                            if new_item is not item:
                                changed = True
                    elif isinstance(item, ast.expr):
                        new_item = self._visit_expr(item)
                        new_values.append(new_item)
                        if new_item is not item:
                            changed = True
                    else:
                        new_values.append(item)
                if changed:
                    setattr(node, field, new_values)
            elif isinstance(value, ast.expr):
                new_value = self._visit_expr(value)
                if new_value is not value:
                    setattr(node, field, new_value)
            elif isinstance(value, ast.stmt):
                if isinstance(value, ast.MacroStmt):
                    expanded = self._process_stmt_list([value])
                    # Can't replace a single stmt with multiple stmts in a non-list field
                    if len(expanded) == 1:
                        setattr(node, field, expanded[0])
                    elif len(expanded) == 0:
                        setattr(node, field, ast.Pass(
                            lineno=value.lineno, col_offset=value.col_offset,
                            end_lineno=value.end_lineno, end_col_offset=value.end_col_offset))
                else:
                    new_value = self._visit_stmt_children(value)
                    if new_value is not value:
                        setattr(node, field, new_value)
        return node

    def _visit_expr(self, node):
        """Visit an expression node, expanding MacroExpr if found."""
        if isinstance(node, ast.MacroExpr):
            self._depth += 1
            if self._depth > _MAX_EXPANSION_DEPTH:
                raise SyntaxError("macro expansion limit exceeded")
            try:
                result = self._expand_macro_expr(node)
                # Recursively expand in case result contains more macros
                return self._visit_expr(result)
            finally:
                self._depth -= 1

        # Recursively visit child expressions
        for field, value in ast.iter_fields(node):
            if isinstance(value, ast.expr):
                new_value = self._visit_expr(value)
                if new_value is not value:
                    setattr(node, field, new_value)
            elif isinstance(value, list):
                new_values = []
                changed = False
                for item in value:
                    if isinstance(item, ast.expr):
                        new_item = self._visit_expr(item)
                        new_values.append(new_item)
                        if new_item is not item:
                            changed = True
                    else:
                        new_values.append(item)
                if changed:
                    setattr(node, field, new_values)
        return node


def expand(tree, registry=None, filename="<unknown>"):
    """Expand all macros in an AST tree.

    Args:
        tree: An ast.Module (or other mod node) to process
        registry: Optional dict mapping macro names (e.g., 'foo!') to
                  processor tuples (func, kind, version, additional_names)
        filename: Source filename for error messages

    Returns:
        The transformed AST tree with all macros expanded.

    Raises:
        SyntaxError: If a macro cannot be resolved, or if expansion depth
                     is exceeded.
    """
    expander = MacroExpander(registry=registry, filename=filename)
    tree = expander.visit_Module(tree)
    ast.fix_missing_locations(tree)
    return tree


def compile_with_macros(source, filename="<string>", mode="exec",
                        registry=None, flags=0):
    """Compile source code with macro expansion.

    This parses the source, expands macros, and compiles to a code object.

    Args:
        source: Source code string or AST
        filename: Source filename for error messages
        mode: Compilation mode ('exec', 'eval', 'single')
        registry: Optional dict mapping macro names to processor tuples
        flags: Compiler flags

    Returns:
        A code object ready for exec() or eval().
    """
    if isinstance(source, str):
        tree = ast.parse(source, filename=filename, mode=mode)
    elif isinstance(source, ast.AST):
        tree = source
    else:
        raise TypeError(f"expected str or AST, got {type(source).__name__}")

    tree = expand(tree, registry=registry, filename=filename)
    return compile(tree, filename, mode, flags=flags)

"""
PEP 638 macro examples: bijection! and macro_def!

Run with the patched CPython build:
    ./python macro_examples.py          (from the cpython/ directory)
    python3 macro_examples.py           (if macros is on sys.path)

Two demonstrations:
  1. bijection! defined directly with macro_processor()
  2. bijection! re-defined at compile time using the macro_def! meta-macro
"""

import ast
import sys
import textwrap

# Allow running from the cpython/ sibling directory without install.
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "data-annotation", "boxing", "cpython", "Lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import macros


# ---------------------------------------------------------------------------
# Demo 1 — bijection! defined with macro_processor()
# ---------------------------------------------------------------------------
#
# Usage:
#
#   bijection! color_to_code, code_to_color:
#       "red",   1
#       "green", 2
#       "blue",  3
#
# Each line in the body is a two-element tuple expression (a, b).
# The macro produces:
#   color_to_code = {"red": 1, "green": 2, "blue": 3}
#   code_to_color = {1: "red", 2: "green", 3: "blue"}

@macros.macro_processor(macros.STMT_MACRO, 1)
def bijection(node):
    """Generate two mirror-image dicts from compact key/value pair syntax."""
    # node.args: [fwd_name, rev_name]   (ast.Name nodes)
    # node.body: list of ast.Expr whose .value is an ast.Tuple with two elts
    if len(node.args) != 2:
        raise SyntaxError(
            f"bijection! requires exactly two name arguments, "
            f"got {len(node.args)}"
        )
    fwd_name, rev_name = node.args
    if not isinstance(fwd_name, ast.Name) or not isinstance(rev_name, ast.Name):
        raise SyntaxError("bijection! arguments must be plain identifiers")

    pairs = []
    for stmt in node.body:
        if not (isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Tuple)
                and len(stmt.value.elts) == 2):
            raise SyntaxError(
                "bijection! body lines must be two-element tuples: key, value"
            )
        k, v = stmt.value.elts
        pairs.append((k, v))

    if not pairs:
        raise SyntaxError("bijection! body must contain at least one pair")

    def _assign(name_node, keys, values):
        return ast.Assign(
            targets=[ast.Name(id=name_node.id, ctx=ast.Store())],
            value=ast.Dict(keys=list(keys), values=list(values)),
            type_comment=None,
        )

    return ast.Module(
        body=[
            _assign(fwd_name, (k for k, v in pairs), (v for k, v in pairs)),
            _assign(rev_name, (v for k, v in pairs), (k for k, v in pairs)),
        ],
        type_ignores=[],
    )


BIJECTION_REGISTRY = {"bijection!": bijection}

DEMO1_SRC = textwrap.dedent("""\
    bijection! color_to_code, code_to_color:
        "red",   1
        "green", 2
        "blue",  3
""")

print("=== Demo 1: bijection! (direct definition) ===")
code = macros.compile_with_macros(DEMO1_SRC, registry=BIJECTION_REGISTRY)
ns = {}
exec(code, ns)
print("color_to_code:", ns["color_to_code"])
print("code_to_color:", ns["code_to_color"])
assert ns["color_to_code"] == {"red": 1, "green": 2, "blue": 3}
assert ns["code_to_color"] == {1: "red", 2: "green", 3: "blue"}
print("OK\n")


# ---------------------------------------------------------------------------
# Demo 2 — macro_def! meta-macro
# ---------------------------------------------------------------------------
#
# macro_def! gives you a way to define a new macro at compile time using
# two code sections:
#
#   macro_def! name:
#       input!:
#           # Python statements that destructure `node` into local variables
#       output!:
#           # Python statements (must end with return) that build result AST
#
# Both sections execute with `ast` already in scope.
#
# Because macro_def! needs to mutate the active registry during expansion
# it is implemented as an ExtendedMacroExpander subclass rather than a
# plain macro_processor tuple.

class ExtendedMacroExpander(macros.MacroExpander):
    """MacroExpander that also understands macro_def!."""

    def _expand_macro_stmt(self, node):
        if node.name == "macro_def!":
            return self._define_macro(node)
        return super()._expand_macro_stmt(node)

    # -- helpers -------------------------------------------------------------

    def _compile_processor(self, macro_name, input_stmts, output_stmts):
        """Compile input/output AST statement lists into a STMT_MACRO tuple."""
        func_body = list(input_stmts) + list(output_stmts)
        if not func_body:
            func_body = [ast.Pass(lineno=1, col_offset=4,
                                  end_lineno=1, end_col_offset=8)]

        func_def = ast.FunctionDef(
            name="_proc",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg="node")],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=func_body,
            decorator_list=[],
            returns=None,
            type_comment=None,
        )
        mod = ast.Module(body=[func_def], type_ignores=[])
        ast.fix_missing_locations(mod)

        code = compile(mod, f"<macro_def:{macro_name}>", "exec")
        ns = {"ast": ast}
        exec(code, ns)
        return (ns["_proc"], macros.STMT_MACRO, 1, ())

    def _define_macro(self, node):
        """Process a macro_def! node, registering the new macro."""
        if not node.args:
            raise SyntaxError("macro_def! requires a name argument")

        name_node = node.args[0]
        if not isinstance(name_node, ast.Name):
            raise SyntaxError(
                f"macro_def! name must be a simple identifier, "
                f"got {ast.dump(name_node)}"
            )
        macro_name = name_node.id + "!"

        input_stmts: list = []
        output_stmts: list = []

        for stmt in node.body:
            if isinstance(stmt, ast.MacroStmt):
                if stmt.name == "input!":
                    input_stmts = list(stmt.body)
                elif stmt.name == "output!":
                    output_stmts = list(stmt.body)

        if not output_stmts:
            raise SyntaxError(
                f"macro_def! '{macro_name}' requires an output!: section"
            )

        self._registry[macro_name] = self._compile_processor(
            macro_name, input_stmts, output_stmts
        )
        return []  # remove macro_def! from the output AST


def compile_extended(source, filename="<string>"):
    """Parse, expand (with macro_def! support), and compile *source*."""
    tree = ast.parse(source, filename=filename)
    expander = ExtendedMacroExpander(filename=filename)
    tree = expander.visit_Module(tree)
    ast.fix_missing_locations(tree)
    return compile(tree, filename, "exec")


# ---------------------------------------------------------------------------
# Demo 2 source — bijection! defined via macro_def!, then used immediately
# ---------------------------------------------------------------------------

DEMO2_SRC = textwrap.dedent("""\
    macro_def! bijection:
        input!:
            if len(node.args) != 2:
                raise SyntaxError("bijection! needs two name arguments")
            fwd_name, rev_name = node.args
            pairs = [(s.value.elts[0], s.value.elts[1]) for s in node.body]
        output!:
            def _assign(name_node, keys, values):
                return ast.Assign(
                    targets=[ast.Name(id=name_node.id, ctx=ast.Store())],
                    value=ast.Dict(keys=list(keys), values=list(values)),
                    type_comment=None,
                )
            return ast.Module(
                body=[
                    _assign(fwd_name,
                            (k for k, v in pairs),
                            (v for k, v in pairs)),
                    _assign(rev_name,
                            (v for k, v in pairs),
                            (k for k, v in pairs)),
                ],
                type_ignores=[],
            )

    bijection! fruit_to_code, code_to_fruit:
        "apple",  10
        "banana", 20
        "cherry", 30
""")

print("=== Demo 2: bijection! defined via macro_def! ===")
code2 = compile_extended(DEMO2_SRC)
ns2 = {}
exec(code2, ns2)
print("fruit_to_code:", ns2["fruit_to_code"])
print("code_to_fruit:", ns2["code_to_fruit"])
assert ns2["fruit_to_code"] == {"apple": 10, "banana": 20, "cherry": 30}
assert ns2["code_to_fruit"] == {10: "apple", 20: "banana", 30: "cherry"}
print("OK\n")

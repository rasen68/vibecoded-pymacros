"""Tests for PEP 638: Syntactic Macros.

Tests cover:
- Tokenizer: MACRO_NAME token production, no regression on !=
- Parser: MacroStmt and MacroExpr AST node creation
- AST round-trip via unparse
- _ast node immutability
- macros module: constants, decorator, expansion
- End-to-end: identity macro, transforming macro, import!
- Error handling: unknown macros, infinite recursion
"""

import ast
import _ast
import io
import os
import sys
import token
import tokenize
import textwrap
import unittest

import macros


class TestTokenizer(unittest.TestCase):
    """AC-1, AC-2: MACRO_NAME token tests."""

    def test_macro_name_token(self):
        """AC-1: foo! is tokenized as MACRO_NAME."""
        toks = list(tokenize.tokenize(io.BytesIO(b'foo! bar').readline))
        macro_toks = [t for t in toks if t.type == token.MACRO_NAME]
        self.assertEqual(len(macro_toks), 1)
        self.assertEqual(macro_toks[0].string, 'foo!')

    def test_no_regression_notequal(self):
        """AC-2: != does not produce MACRO_NAME."""
        toks = list(tokenize.tokenize(io.BytesIO(b'a != b').readline))
        types = [t.type for t in toks if t.type not in
                 (token.ENCODING, token.NEWLINE, token.ENDMARKER)]
        self.assertNotIn(token.MACRO_NAME, types)

    def test_macro_name_various(self):
        """Various macro names are tokenized correctly."""
        for name in [b'import!', b'from!', b'my_macro!', b'x!']:
            toks = list(tokenize.tokenize(io.BytesIO(name + b' y').readline))
            macro_toks = [t for t in toks if t.type == token.MACRO_NAME]
            self.assertEqual(len(macro_toks), 1, f"Failed for {name}")
            self.assertEqual(macro_toks[0].string, name.decode())

    def test_identifier_notequal(self):
        """Identifier followed by != is NAME + NOTEQUAL."""
        toks = list(tokenize.tokenize(io.BytesIO(b'foo != bar').readline))
        types = [t.type for t in toks if t.type not in
                 (token.ENCODING, token.NEWLINE, token.ENDMARKER)]
        self.assertNotIn(token.MACRO_NAME, types)
        self.assertIn(token.NAME, types)
        # The tokenizer reports != as OP; use exact_type for NOTEQUAL
        exact_types = [t.exact_type for t in toks if t.type not in
                       (token.ENCODING, token.NEWLINE, token.ENDMARKER)]
        self.assertIn(token.NOTEQUAL, exact_types)

    def test_fstring_exclamation(self):
        """f-string conversion !r does not produce MACRO_NAME."""
        toks = list(tokenize.tokenize(io.BytesIO(b"f'{x!r}'").readline))
        types = [t.type for t in toks]
        self.assertNotIn(token.MACRO_NAME, types)


class TestParser(unittest.TestCase):
    """AC-3, AC-4, AC-5: Parser tests."""

    def test_macro_stmt_simple(self):
        """AC-3: foo! x parses as MacroStmt."""
        tree = ast.parse('foo! x')
        stmts = tree.body
        self.assertEqual(len(stmts), 1)
        self.assertIsInstance(stmts[0], ast.MacroStmt)
        self.assertEqual(stmts[0].name, 'foo!')

    def test_macro_expr(self):
        """AC-4: y = bar!(x) parses with MacroExpr."""
        tree = ast.parse('y = bar!(x)')
        assign = tree.body[0]
        self.assertIsInstance(assign.value, ast.MacroExpr)
        self.assertEqual(assign.value.name, 'bar!')

    def test_macro_stmt_with_body(self):
        """AC-5: Macro stmt with indented body."""
        src = textwrap.dedent('''
        with_logging!:
            x = 1
            y = 2
        ''')
        tree = ast.parse(src)
        ms = tree.body[0]
        self.assertIsInstance(ms, ast.MacroStmt)
        self.assertEqual(len(ms.body), 2)

    def test_macro_stmt_with_args(self):
        """Macro stmt with multiple args."""
        tree = ast.parse('foo! x, y')
        ms = tree.body[0]
        self.assertIsInstance(ms, ast.MacroStmt)
        self.assertEqual(len(ms.args), 2)

    def test_macro_stmt_paren_args(self):
        """Macro stmt with parenthesized args."""
        tree = ast.parse('foo!(x, y)')
        ms = tree.body[0]
        self.assertIsInstance(ms, ast.MacroStmt)
        self.assertEqual(len(ms.args), 2)

    def test_macro_stmt_no_args(self):
        """Macro stmt with no args."""
        tree = ast.parse('foo!()')
        ms = tree.body[0]
        self.assertIsInstance(ms, ast.MacroStmt)

    def test_macro_stmt_import_form(self):
        """import! mod as name syntax."""
        tree = ast.parse('import! mymod as mm')
        ms = tree.body[0]
        self.assertIsInstance(ms, ast.MacroStmt)
        self.assertEqual(ms.name, 'import!')
        self.assertEqual(ms.asname, 'mm')

    def test_macro_stmt_from_import_form(self):
        """from! mod import name syntax."""
        tree = ast.parse('from! mymod import mymacro')
        ms = tree.body[0]
        self.assertIsInstance(ms, ast.MacroStmt)
        self.assertEqual(ms.name, 'from!')
        self.assertEqual(ms.importname, 'mymacro')

    def test_macro_expr_in_assignment(self):
        """Macro expr on the right side of assignment."""
        tree = ast.parse('y = bar!(x)')
        assign = tree.body[0]
        self.assertIsInstance(assign, ast.Assign)
        self.assertIsInstance(assign.value, ast.MacroExpr)

    def test_macro_expr_in_call(self):
        """Macro expr as function argument."""
        tree = ast.parse('print(bar!(x))')
        call = tree.body[0].value
        self.assertIsInstance(call, ast.Call)
        self.assertIsInstance(call.args[0], ast.MacroExpr)


class TestASTRoundTrip(unittest.TestCase):
    """AC-6: AST unparse round-trip tests."""

    def test_macro_expr_roundtrip(self):
        """AC-6: y = bar!(x) round-trips through unparse."""
        src = 'y = bar!(x)'
        reparsed = ast.parse(ast.unparse(ast.parse(src)))
        self.assertIsInstance(reparsed.body[0].value, ast.MacroExpr)

    def test_macro_stmt_roundtrip(self):
        """Macro stmt round-trips through unparse."""
        src = 'foo! x'
        unparsed = ast.unparse(ast.parse(src))
        reparsed = ast.parse(unparsed)
        self.assertIsInstance(reparsed.body[0], ast.MacroStmt)

    def test_macro_stmt_body_roundtrip(self):
        """Macro stmt with body round-trips."""
        src = textwrap.dedent('''
        with_logging!:
            x = 1
        ''').strip()
        unparsed = ast.unparse(ast.parse(src))
        reparsed = ast.parse(unparsed)
        ms = reparsed.body[0]
        self.assertIsInstance(ms, ast.MacroStmt)
        self.assertTrue(len(ms.body) >= 1)


class TestASTImmutability(unittest.TestCase):
    """AC-7: _ast node immutability tests."""

    def test_constant_immutable(self):
        """AC-7: _ast.Constant nodes are immutable after _freeze()."""
        n = _ast.Constant(value=42, lineno=1, col_offset=0,
                          end_lineno=1, end_col_offset=2)
        n._freeze()
        with self.assertRaises(AttributeError):
            n.value = 99

    def test_name_immutable(self):
        """_ast.Name nodes are immutable after _freeze()."""
        n = _ast.Name(id='x', ctx=_ast.Load(), lineno=1, col_offset=0,
                      end_lineno=1, end_col_offset=1)
        n._freeze()
        with self.assertRaises(AttributeError):
            n.id = 'y'

    def test_parsed_ast_mutable(self):
        """AST nodes from ast.parse() remain mutable."""
        tree = ast.parse('x = 1')
        tree.body[0].value = ast.Constant(value=2)

    def test_mutable_before_freeze(self):
        """_ast nodes are mutable before _freeze() is called."""
        n = _ast.Constant(value=42, lineno=1, col_offset=0,
                          end_lineno=1, end_col_offset=2)
        n.value = 99  # Should work before freeze
        self.assertEqual(n.value, 99)


class TestMacrosModule(unittest.TestCase):
    """AC-8: macros module tests."""

    def test_constants(self):
        """macros module has the required constants."""
        self.assertTrue(hasattr(macros, 'STMT_MACRO'))
        self.assertTrue(hasattr(macros, 'SIBLING_MACRO'))
        self.assertTrue(hasattr(macros, 'EXPR_MACRO'))

    def test_macro_processor_decorator(self):
        """AC-8: macro_processor creates proper tuples."""
        @macros.macro_processor(macros.STMT_MACRO, 1)
        def my_proc(node):
            return node
        self.assertIsInstance(my_proc, tuple)
        self.assertEqual(len(my_proc), 4)
        self.assertEqual(my_proc[1], macros.STMT_MACRO)
        self.assertEqual(my_proc[2], 1)
        self.assertEqual(my_proc[3], ())

    def test_macro_processor_with_additional_names(self):
        """macro_processor with additional_names."""
        @macros.macro_processor(macros.STMT_MACRO, 1, "else_macro")
        def my_proc(node, else_node):
            return node
        self.assertEqual(my_proc[3], ("else_macro",))


class TestIdentityMacro(unittest.TestCase):
    """AC-9: End-to-end identity macro test."""

    def test_identity_macro(self):
        """AC-9: Identity macro passes body through unchanged."""
        @macros.macro_processor(macros.STMT_MACRO, 1)
        def noop(node):
            return ast.Module(body=node.body, type_ignores=[])

        registry = {'noop!': noop}
        src = textwrap.dedent('''
        noop!:
            x = 42
        ''')

        code = macros.compile_with_macros(src, registry=registry)
        ns = {}
        exec(code, ns)
        self.assertEqual(ns['x'], 42)


class TestTransformingMacro(unittest.TestCase):
    """AC-10: End-to-end transforming macro test."""

    def test_sibling_double_macro(self):
        """AC-10: double! sibling macro doubles a variable."""
        @macros.macro_processor(macros.SIBLING_MACRO, 1)
        def double(node):
            body = node.body
            if not body:
                return ast.Pass()
            assign = body[0]
            if isinstance(assign, ast.Assign):
                target = assign.targets[0]
                double_assign = ast.Assign(
                    targets=[ast.Name(id=target.id, ctx=ast.Store())],
                    value=ast.BinOp(
                        left=ast.Name(id=target.id, ctx=ast.Load()),
                        op=ast.Mult(),
                        right=ast.Constant(value=2)),
                    type_comment=None)
                return ast.Module(body=[assign, double_assign], type_ignores=[])
            return ast.Module(body=body, type_ignores=[])

        registry = {'double!': double}
        src = 'double! x\nx = 5\n'

        code = macros.compile_with_macros(src, registry=registry)
        ns = {}
        exec(code, ns)
        self.assertEqual(ns['x'], 10)


class TestErrorHandling(unittest.TestCase):
    """AC-11, AC-12: Error handling tests."""

    def test_unknown_macro_syntax_error(self):
        """AC-11: Unknown macro raises SyntaxError."""
        with self.assertRaises(SyntaxError) as cm:
            compile('unknown_macro! x', '<test>', 'exec')
        self.assertIn('unknown_macro', str(cm.exception))

    def test_infinite_recursion_guard(self):
        """AC-12: Infinite macro recursion is caught."""
        @macros.macro_processor(macros.STMT_MACRO, 1)
        def recursive(node):
            return ast.MacroStmt(
                name='recursive!',
                args=[],
                importname=None,
                asname=None,
                body=[ast.Pass(lineno=1, col_offset=0,
                               end_lineno=1, end_col_offset=4)],
                lineno=1, col_offset=0, end_lineno=1, end_col_offset=10)

        registry = {'recursive!': recursive}
        src = textwrap.dedent('''
        recursive!:
            pass
        ''')

        with self.assertRaises(SyntaxError) as cm:
            macros.compile_with_macros(src, registry=registry)
        self.assertIn('macro expansion limit exceeded', str(cm.exception))


class TestImportMacro(unittest.TestCase):
    """AC-14: import! predefined macro tests."""

    def setUp(self):
        """Create a temporary macro module for testing."""
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.macro_file = os.path.join(self.tmpdir, 'test_macro_mod.py')
        with open(self.macro_file, 'w') as f:
            f.write(textwrap.dedent('''
                import macros
                import ast

                @macros.macro_processor(macros.STMT_MACRO, 1)
                def test_macro_mod(node):
                    """Identity macro for testing."""
                    return ast.Module(body=node.body, type_ignores=[])
            '''))
        sys.path.insert(0, self.tmpdir)

    def tearDown(self):
        sys.path.remove(self.tmpdir)
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_import_macro(self):
        """AC-14: import! loads and registers macro processor."""
        src = textwrap.dedent('''
        import! test_macro_mod as mm
        mm!:
            pass
        ''')

        tree = ast.parse(src)
        tree = macros.expand(tree)
        code = compile(tree, '<test>', 'exec')
        exec(code)

    def test_from_import_macro(self):
        """from! loads a specific processor."""
        src = textwrap.dedent('''
        from! test_macro_mod import test_macro_mod as mm
        mm!:
            x = 99
        ''')

        tree = ast.parse(src)
        tree = macros.expand(tree)
        code = compile(tree, '<test>', 'exec')
        ns = {}
        exec(code, ns)
        self.assertEqual(ns['x'], 99)


class TestExprMacro(unittest.TestCase):
    """Expression macro tests."""

    def test_expr_macro_expansion(self):
        """Expression macros expand in expression context."""
        @macros.macro_processor(macros.EXPR_MACRO, 1)
        def const42(node):
            return ast.Constant(value=42)

        registry = {'const42!': const42}
        src = 'y = const42!()'

        code = macros.compile_with_macros(src, registry=registry)
        ns = {}
        exec(code, ns)
        self.assertEqual(ns['y'], 42)

    def test_expr_macro_with_args(self):
        """Expression macros receive args."""
        @macros.macro_processor(macros.EXPR_MACRO, 1)
        def add1(node):
            if node.args:
                return ast.BinOp(
                    left=node.args[0],
                    op=ast.Add(),
                    right=ast.Constant(value=1))
            return ast.Constant(value=1)

        registry = {'add1!': add1}
        src = 'y = add1!(10)'

        code = macros.compile_with_macros(src, registry=registry)
        ns = {}
        exec(code, ns)
        self.assertEqual(ns['y'], 11)


if __name__ == '__main__':
    unittest.main()

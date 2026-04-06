# PEP 638: Syntactic Macros - Implementation Progress

## Phases

### Phase 1: Token (MACRO_NAME)
- [ ] Add MACRO_NAME to Grammar/Tokens
- [ ] Modify C lexer to emit MACRO_NAME when identifier followed by `!`
- [ ] Run `make regen-token` and rebuild
- [ ] Verify AC-1 and AC-2

### Phase 2: AST Definition
- [ ] Add MacroStmt and MacroExpr to Python.asdl
- [ ] Add StmtExpr to Python.asdl
- [ ] Run `make regen-ast` and rebuild

### Phase 3: Grammar (Parser)
- [ ] Add macro_stmt rule to python.gram
- [ ] Add macro_expr rule to python.gram  
- [ ] Wire into compound_stmt and atom rules
- [ ] Run `make regen-pegen` and rebuild
- [ ] Verify AC-3, AC-4, AC-5

### Phase 4: AST support (ast.py, unparse)
- [ ] Update Lib/ast.py for MacroStmt, MacroExpr, StmtExpr unparse
- [ ] Verify AC-6

### Phase 5: Symbol Table
- [ ] Add MacroStmt_kind and MacroExpr_kind to symtable.c

### Phase 6: Compiler (macro registry + expansion)
- [ ] Add macro registry data structure
- [ ] Implement import! and from! predefined macros
- [ ] Implement macro expansion with recursion guard
- [ ] Verify AC-9, AC-10, AC-11, AC-12, AC-14

### Phase 7: macros module
- [ ] Create Lib/macros.py
- [ ] Verify AC-8

### Phase 8: Bytecode invalidation
- [ ] Bump MAGIC_NUMBER
- [ ] Verify AC-7

### Phase 9: Tests
- [ ] Create Lib/test/test_macros.py
- [ ] Run existing test suite (AC-13)

### Phase 10: Documentation
- [ ] Doc/whatsnew/3.15.rst
- [ ] Doc/reference/compound_stmts.rst
- [ ] Doc/reference/expressions.rst

## Status: Starting Phase 1 - Tokenizer

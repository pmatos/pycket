
from pycket.ast_visitor import ASTVisitor
from pycket.env         import SymList
from pycket.interpreter import (
    App,
    Begin,
    Begin0,
    BeginForSyntax,
    CaseLambda,
    Cell,
    CellRef,
    DefineValues,
    If,
    Lambda,
    Let,
    Letrec,
    LexicalVar,
    Module,
    ModuleVar,
    Quote,
    QuoteSyntax,
    Require,
    SetBang,
    ToplevelVar,
    VariableReference,
    WithContinuationMark,
    variable_set,
)

class AssignConvertVisitor(ASTVisitor):

    def visit_cell_ref(self, ast, vars, env_structure):
        assert isinstance(ast, CellRef)
        return CellRef(ast.sym, env_structure)

    def visit_lexical_var(self, ast, vars, env_structure):
        assert isinstance(ast, LexicalVar)
        if ast in vars:
            return CellRef(ast.sym, env_structure)
        else:
            return LexicalVar(ast.sym, env_structure)

    def visit_variable_reference(self, ast, vars, env_structure):
        assert isinstance(ast, VariableReference)
        v = ast.var
        if isinstance(v, LexicalVar) and v in vars:
            return VariableReference(v, ast.path, True)
        # top-level variables are always mutable
        if isinstance(v, ToplevelVar):
            return VariableReference(v, ast.path, True)
        else:
            return ast

    def visit_lambda(self, ast, vars, env_structure):
        assert isinstance(ast, Lambda)
        local_muts = variable_set()
        for b in ast.body:
            local_muts.update(b.mutated_vars())
        new_lets = []
        new_vars = vars.copy()
        for i in ast.args.elems:
            li = LexicalVar(i)
            if li in new_vars:
                del new_vars[li]
            if li in local_muts:
                new_lets.append(i)
        for k, v in local_muts.iteritems():
            new_vars[k] = v
        if new_lets:
            sub_env_structure = SymList(new_lets, ast.args)
        else:
            sub_env_structure = ast.args
        new_body = [b.visit(self, new_vars, sub_env_structure) for b in ast.body]
        if new_lets:
            cells = [Cell(LexicalVar(v, ast.args)) for v in new_lets]
            new_body = [Let(sub_env_structure, [1] * len(new_lets), cells, new_body)]
        return Lambda(ast.formals, ast.rest, ast.args, ast.frees, new_body,
                      ast.sourceinfo, env_structure, sub_env_structure)

    def visit_letrec(self, ast, vars, env_structure):
        assert isinstance(ast, Letrec)
        local_muts = variable_set()
        for b in ast.body:
            local_muts.update(b.mutated_vars())
        for b in ast.rhss:
            local_muts.update(b.mutated_vars())
        for v in ast.args.elems:
            lv = LexicalVar(v)
            local_muts[lv] = None
        new_vars = vars.copy()
        for k, v in local_muts.iteritems():
            new_vars[k] = v
        sub_env_structure = SymList(ast.args.elems, env_structure)
        new_rhss = [rhs.visit(self, new_vars, sub_env_structure) for rhs in ast.rhss]
        new_body = [b.visit(self, new_vars, sub_env_structure) for b in ast.body]
        return Letrec(sub_env_structure, ast.counts, new_rhss, new_body)

    def visit_let(self, ast, vars, env_structure):
        assert isinstance(ast, Let)
        sub_env_structure = SymList(ast.args.elems, env_structure)
        local_muts = variable_set()
        for b in ast.body:
            local_muts.update(b.mutated_vars())
        new_vars = vars.copy()
        for k, v in local_muts.iteritems():
            new_vars[k] = v
        ast, sub_env_structure, env_structures, remove_num_envs = ast._compute_remove_num_envs(
            new_vars, sub_env_structure)

        new_rhss = [None] * len(ast.rhss)
        offset = 0
        variables = ast.args.elems
        for i, rhs in enumerate(ast.rhss):
            new_rhs = rhs.visit(self, vars, env_structures[i])
            count = ast.counts[i]
            need_cell_flags = [LexicalVar(variables[offset+j]) in local_muts for j in range(count)]
            if True in need_cell_flags:
                new_rhs = Cell(new_rhs, need_cell_flags)
            new_rhss[i] = new_rhs
            offset += count

        body_env_structure = env_structures[-1]

        new_body = [b.visit(self, new_vars, body_env_structure) for b in ast.body]
        result = Let(sub_env_structure, ast.counts, new_rhss, new_body, remove_num_envs)
        return result

    def visit_define_values(self, ast, vars, env_structure):
        assert isinstance(ast, DefineValues)
        need_cell_flags = [(ModuleVar(i, None, i) in vars) for i in ast.names]
        if True in need_cell_flags:
            return DefineValues(ast.names,
                                Cell(ast.rhs.visit(self, vars, env_structure),
                                     need_cell_flags),
                                ast.display_names)
        else:
            return DefineValues(ast.names,
                                ast.rhs.visit(self, vars, env_structure),
                                ast.display_names)

    def visit_module(self, ast, vars, env_structure):
        """
        This method must return the original module due to the use of the shared
        module table.
        """
        assert isinstance(ast, Module)
        local_muts = ast.mod_mutated_vars()
        ast.body = [b.visit(self, local_muts, None) for b in ast.body]
        return ast

def assign_convert(ast, vistor=None):
    if visitor is None:
        visitor = AssignConvertVisitor()
    return ast.visit(visitor, variable_set(), None)


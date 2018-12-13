from pycket import interpreter as interp
from pycket import values, values_string, vector, util
from pycket.prims.correlated import W_Correlated
from pycket.error import SchemeException
from pycket.hash import simple, equal, base
from pycket.assign_convert import assign_convert

"""
Funcions inside are:

- to_rpython_list

- ast_to_sexp

- def_vals_to_ast
- lam_to_ast
- let_like_to_ast
- is_val_type
- is_imported
- sexp_to_ast

- create_toplevel_linklet_vars
- deserialize_loop
"""

def to_rpython_list(r_list, open_correlated=False, reverse=False):
    # assumes r_list is proper
    length = 0
    acc = r_list
    while(acc is not values.w_null):
        length += 1
        acc = acc.cdr()
    acc = r_list
    py_ls = [None]*length
    for n in range(length):
        a = acc.car().get_obj() if (open_correlated and isinstance(acc.car(), W_Correlated)) else acc.car()
        if reverse:
            py_ls[length-n-1] = a
        else:
            py_ls[n] = a
        acc = acc.cdr()
    return py_ls, length

def ast_to_sexp(form):
    from pycket.prims.linklet import W_Linklet, W_LinkletBundle, W_LinkletDirectory

    #util.console_log("ast->sexp is called with form : %s" % form.tostring(), 8)

    if is_val_type(form, extra=[vector.W_Vector, base.W_HashTable, values.W_List, values.W_Symbol]):
        return form
    elif isinstance(form, W_Linklet):
        l_sym = values.W_Symbol.make("linklet")

        name = form.get_name() # W_Symbol
        importss = form.get_importss() # rlist of rdict of W_Symbol:W_Symbol
        exports = form.get_exports() # rdict
        body_forms = form.get_forms() # rlist of ASTs

        importss_rlist = [None]*len(importss)
        for index, rdict in enumerate(importss):
            len_dict = len(rdict)
            importss_inst = [None]*len_dict
            i = 0
            for k, v in rdict.iteritems():
                importss_inst[i] = values.W_Cons.make(k, values.W_Cons.make(v, values.w_null))
                i += 1
            importss_rlist[index] = values.to_list(importss_inst)
        importss_list = values.to_list(importss_rlist)

        exports_rlist = [None]*len(exports)
        i = 0
        for k, v in exports.iteritems():
            exports_rlist[i] = values.W_Cons.make(k, values.W_Cons.make(v, values.w_null))
            i += 1

        exports_list = values.to_list(exports_rlist)

        body_forms_rlist = [None]*len(body_forms)
        for index, ast_form in enumerate(body_forms):
            body_forms_rlist[index] = ast_form.to_sexp()

        linklet_rlist = [l_sym, name, importss_list, exports_list] + body_forms_rlist
        linklet_s_exp = values.to_list(linklet_rlist)

        return linklet_s_exp
    elif isinstance(form, W_LinkletBundle) or isinstance(form, W_LinkletDirectory):
        bd_sym = None
        if isinstance(form, W_LinkletBundle):
            bd_sym = values.W_Symbol.make(":B:")
        else:
            bd_sym = values.W_Symbol.make(":D:")

        mapping = form.get_mapping()
        l = mapping.length()
        keys = [None]*l
        vals = [None]*l

        if isinstance(mapping, equal.W_EqualHashTable):
            i = 0
            for k, v in mapping.hash_items():
                keys[i] = k
                vals[i] = ast_to_sexp(v)
                i += 1

            return values.W_Cons.make(bd_sym, equal.W_EqualHashTable(keys, vals, immutable=True))
        elif isinstance(mapping, simple.W_EqImmutableHashTable):
            i = 0
            for k, v in mapping.iteritems():
                keys[i] = k
                vals[i] = ast_to_sexp(v)
                i += 1

            return values.W_Cons.make(bd_sym, simple.make_simple_immutable_table(simple.W_EqImmutableHashTable, keys, vals))
        else:
            raise SchemeException("Something wrong with the bundle/directory mapping : %s" % mapping.tostring())
    else:
        return form.to_sexp()

def def_vals_to_ast(def_vals_sexp, exports, linkl_toplevels, linkl_imports):
    # FIXME : get the length from to_rpython_list
    if not len(to_rpython_list(def_vals_sexp)) == 3:
        raise SchemeException("defs_vals_to_ast : unhandled define-values form : %s" % def_vals_sexp.tostring())

    names = def_vals_sexp.cdr().car()
    names_ls = to_rpython_list(names, open_correlated=True)

    the_name = names_ls[0].variable_name() if len(names_ls) > 0 else ""
    body = sexp_to_ast(def_vals_sexp.cdr().cdr().car(), [], exports, linkl_toplevels, linkl_imports, cell_ref=[], name=the_name)

    return interp.DefineValues(names_ls, body, names_ls)

def lam_to_ast(lam_sexp, lex_env, exports, linkl_toplevels, linkl_imports, cell_ref, name=""):
    from pycket.expand import SourceInfo

    lam_sexp_elements = to_rpython_list(lam_sexp)
    l = len(lam_sexp_elements)
    if not (l == 3 or l == 2):
        raise SchemeException("lam_to_ast : unhandled lambda form : %s" % lam_sexp.tostring())

    if lam_sexp.car() is values.W_Symbol.make("lambda"):
        lam_sexp = lam_sexp.cdr()

    formals_ = lam_sexp.car()
    rest = None
    formals = values.w_null
    if isinstance(formals_, values.W_Symbol):
        # check for a "rest"
        rest = formals_
        lex_env.append(rest)
    else:
        while (formals_ is not values.w_null):
            if isinstance(formals_, values.W_Symbol):
                rest = formals_
                lex_env.append(formals_)
                break
            elif formals_.car() is values.W_Symbol.make("."):
                # another check for a "rest"
                if formals_.cdr() is values.w_null:
                    raise SchemeException("lam_to_ast : invalid lambda form : %s" % lam_sexp.tostring())
                rest = formals_.cdr().car()
                lex_env.append(rest)
                break
            formals = values.W_Cons.make(formals_.car(), formals)
            formals_ = formals_.cdr()

    formals_ls = to_rpython_list(formals)
    formals_ls.reverse() # FIXME : refactor the double reverse

    for f in formals_ls:
        if f in cell_ref:
            cell_ref.remove(f)

    body = sexp_to_ast(lam_sexp.cdr().car(), formals_ls + lex_env, exports, linkl_toplevels, linkl_imports, cell_ref=[], name=name)
    dummy = 1
    return interp.make_lambda(formals_ls, rest, [body], SourceInfo(dummy, dummy, dummy, dummy, name))

def let_like_to_ast(let_sexp, lex_env, exports, linkl_toplevels, linkl_imports, is_letrec, cell_ref):

    let_ls = to_rpython_list(let_sexp)

    # just a sanity check
    if not (let_ls[0] is values.W_Symbol.make("let-values") or (let_ls[0] is values.W_Symbol.make("letrec-values") and is_letrec)):
        raise SchemeException("let_to_ast : unhandled let form : %s" % let_sexp.tostring())

    varss_rhss = to_rpython_list(let_ls[1])

    varss_list = [None] * len(varss_rhss)
    rhss_list = [None] * len(varss_rhss)
    cells_for_the_body = list(cell_ref) if is_letrec else cell_ref
    cells_for_the_rhss = list(cell_ref) if is_letrec else cell_ref

    if is_letrec:
        # populate lex_env // cell_refs for rhss ahead of time
        for rhs in varss_rhss: # rhs : ((id ...) rhs-expr)
            ids = to_rpython_list(rhs.car(), open_correlated=True) # (id ...)
            cells_for_the_rhss += ids
            lex_env += ids #[i.get_obj() if isinstance(i, W_Correlated) else i for i in ids]

    num_ids = 0
    i = 0
    for w_vars_rhss in varss_rhss:
        #varr = [v.get_obj() if isinstance(v, W_Correlated) else v for v in to_rpython_list(w_vars_rhss.car())]
        varr = to_rpython_list(w_vars_rhss.car(), open_correlated=True)
        varss_list[i] = varr

        rhsr = sexp_to_ast(w_vars_rhss.cdr().car(), lex_env, exports, linkl_toplevels, linkl_imports, cell_ref=[])
        rhss_list[i] = rhsr
        i += 1
        num_ids += len(varr)

    ids = [None] * num_ids
    index = 0
    for vars_ in varss_list:
        for var_ in vars_:
            ids[index] = var_ # W_Symbol
            index += 1

    let_body_ls = let_ls[2:]
    body_ls = [None]*len(let_body_ls)

    for index, b in enumerate(let_body_ls):
        body_ls[index] = sexp_to_ast(b, ids + lex_env, exports, linkl_toplevels, linkl_imports, cell_ref=[])

    if len(varss_rhss) == 0:
        return interp.Begin.make(body_ls)

    if is_letrec:
        return interp.make_letrec(list(varss_list), list(rhss_list), body_ls)
    else:
        return interp.make_let(varss_list, rhss_list, body_ls)

def is_val_type(form, extra=[]):
    val_types = [values.W_Number,
                 values.W_Bool,
                 values_string.W_String,
                 values.W_ImmutableBytes,
                 values.W_Character] + extra
    for t in val_types:
        if isinstance(form, t):
            return True
    return False

def is_imported(id_sym, linkl_importss):
    for imp_index, imports_dict in enumerate(linkl_importss):
        for ext_id, int_id in imports_dict.iteritems():
            if id_sym is int_id:
                return imp_index, ext_id
    return -1, None

def sexp_to_ast(form, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref=[], name=""):

    #util.console_log("sexp->ast is called with form : %s" % form.tostring(), 8)
    if isinstance(form, W_Correlated):
        return sexp_to_ast(form.get_obj(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
    elif is_val_type(form):
        return interp.Quote(form)
    elif isinstance(form, values.W_Symbol):
        imp_index, renamed_sym = is_imported(form, linkl_importss)
        if imp_index >= 0:
            return interp.LinkletImportedVar(form, import_index=imp_index, import_rename=renamed_sym, constance=values.W_Symbol.make("constant"))
        elif form in cell_ref:
            return interp.CellRef(form)
        elif form in lex_env:
            return interp.LexicalVar(form)
        elif (form in linkl_toplevels) or (form in exports):
            if form in linkl_toplevels:
                # defined toplevel linklet var
                return linkl_toplevels[form]
            else:
                # exported uninitialized linklet var
                return interp.LinkletExpUninitVar(form)
        else:
            # kernel primitive ModuleVar
            return interp.ModuleVar(form, "#%kernel", form, None)
    elif isinstance(form, values.W_List):
        if form.car() is values.W_Symbol.make("begin"):
            return interp.Begin.make([sexp_to_ast(f, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name) for f in to_rpython_list(form.cdr())])
        elif form.car() is values.W_Symbol.make("p+"):
            path_str = form.cdr().car().tostring()
            return interp.Quote(values.W_Path(path_str))
        elif form.car() is values.W_Symbol.make("begin0"):
            fst = sexp_to_ast(form.cdr().car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            rst = [sexp_to_ast(f, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name) for f in to_rpython_list(form.cdr().cdr())]
            if len(rst) == 0:
                return fst
            else:
                return interp.Begin0.make(fst, rst)
        # elif form.car() is values.W_Symbol.make("define-values"):
        #     return def_vals_to_ast(form, exports, linkl_toplevels, linkl_importss)
        elif form.car() is values.W_Symbol.make("with-continuation-mark"):
            if len(to_rpython_list(form)) != 4:
                raise SchemeException("Unrecognized with-continuation-mark form : %s" % form.tostring())
            key = sexp_to_ast(form.cdr().car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            val = sexp_to_ast(form.cdr().cdr().car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            body = sexp_to_ast(form.cdr().cdr().cdr().car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            return interp.WithContinuationMark(key, val, body)
        elif form.car() is values.W_Symbol.make("#%variable-reference"):
            if form.cdr() is values.w_null: # (variable-reference)
                return interp.VariableReference(None, None)
            elif form.cdr().cdr() is values.w_null: # (variable-reference id)
                if isinstance(form.cdr().car(), values.W_Symbol):
                    var = sexp_to_ast(form.cdr().car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
                    return interp.VariableReference(var, "dummy-path.rkt") # FIXME
                elif isinstance(form.cdr().car(), values.W_Fixnum):
                    # because we're 'writing' variable-reference with is_mutable information
                    is_mut = False
                    if form.cdr().car().toint() != 0:
                        is_mut = True
                    return interp.VariableReference(None, None, is_mut)
                else:
                    raise SchemeException("Invalid variable-reference form : %s -- arg type : %s" % (form.tostring(), form.cdr().car()))
            elif form.cdr().cdr().cdr() is values.w_null: # (variable-reference 1 2)
                raise SchemeException("Unhandled variable-reference form : %s" % (form.tostring()))
            else:
                # This is to handle varrefs serialized by Pycket
                # no Racket varref has more than 1 argument
                var_ = form.cdr().car()
                path_ = form.cdr().cdr().car()
                mut_ = form.cdr().cdr().cdr().car()
                var = None
                path = None
                mut = False

                if var_ is not values.w_false:
                    var = sexp_to_ast(var_, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)

                if isinstance(path_, values.W_Object) and path_ is not values.w_false:
                    path = path_.tostring()
                elif isinstance(path_, str):
                    path = path_

                if mut_ is values.w_true:
                    mut = True

                return interp.VariableReference(var, path, mut)

        elif form.car() is values.W_Symbol.make("case-lambda"):
            maybe_rec_sym_part = values.w_null
            if form.cdr() is not values.w_null:
                maybe_rec_sym_part = form.cdr().car() # (recursive-sym <sym>)
            rec_sym = None
            new_lex_env = lex_env
            lams_part = form.cdr()

            if isinstance(maybe_rec_sym_part, values.W_Cons) and maybe_rec_sym_part is not values.w_null:
                if maybe_rec_sym_part.car() is values.W_Symbol.make("recursive-sym"):
                    # then we're reading a caselam that we wrote
                    lams_part = form.cdr().cdr()
                    if maybe_rec_sym_part.cdr() is not values.w_null:
                        rec_sym = maybe_rec_sym_part.cdr().car()
                        new_lex_env = lex_env + [rec_sym]

            lams = [lam_to_ast(f, new_lex_env, exports, linkl_toplevels, linkl_importss, True, cell_ref, name) for f in to_rpython_list(lams_part)]
            return interp.CaseLambda(lams, rec_sym)
        elif form.car() is values.W_Symbol.make("lambda"):
            return interp.CaseLambda([lam_to_ast(form, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)])
        elif form.car() is values.W_Symbol.make("let-values"):
            return let_like_to_ast(form, lex_env, exports, linkl_toplevels, linkl_importss, False, cell_ref)
        elif form.car() is values.W_Symbol.make("letrec-values"):
            return let_like_to_ast(form, lex_env, exports, linkl_toplevels, linkl_importss, True, cell_ref)
        elif form.car() is values.W_Symbol.make("set!"):
            index, rename = is_imported(form.cdr().car(), linkl_importss)
            if index != -1:
                raise SchemeException("cannot mutate imported variable : %s" % form.tostring())
            cr = cell_ref
            target = form.cdr().car()
            if target in lex_env:
                cr = [target] if not cr else [target] + cr
            var = sexp_to_ast(form.cdr().car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref=cr, name=name)
            rhs = sexp_to_ast(form.cdr().cdr().car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            assert isinstance(var, interp.Var)
            return interp.SetBang(var, rhs)
        elif form.car() is values.W_Symbol.make("quote"):
            if form.cdr() is values.w_null or form.cdr().cdr() is not values.w_null:
                raise SchemeException("malformed quote form : %s" % form.tostring())
            return interp.Quote(form.cdr().car())
        elif form.car() is values.W_Symbol.make("if"):
            tst_w = form.cdr().car()
            thn_w = form.cdr().cdr().car()
            els_w = form.cdr().cdr().cdr().car()
            tst = sexp_to_ast(tst_w, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            thn = sexp_to_ast(thn_w, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            els = sexp_to_ast(els_w, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name)
            return interp.If.make(tst, thn, els)
        else:
            form_rator = sexp_to_ast(form.car(), lex_env, exports, linkl_toplevels, linkl_importss, cell_ref)

            rands_ls = to_rpython_list(form.cdr())
            rands = [sexp_to_ast(r, lex_env, exports, linkl_toplevels, linkl_importss, cell_ref, name) for r in rands_ls]

            return interp.App.make(form_rator, rands)
    else:
        raise SchemeException("Don't know what to do with this form yet : %s" % form.tostring())

dir_sym = values.W_Symbol.make(":D:")
bundle_sym = values.W_Symbol.make(":B:")
linklet_sym = values.W_Symbol.make("linklet")

def looks_like_linklet(sexp):
    # (linklet () () ...)
    # we know the sexp is not w_null

    # pre-check
    ls = to_rpython_list(sexp)
    if sexp.car() is not linklet_sym or len(ls) < 3:
        return False

    # check the imports/exports
    _imports = sexp.cdr().car()
    _exports = sexp.cdr().cdr().car()
    # FIXME : also check the imports and exports' inner structures
    if not isinstance(_imports, values.W_List) or not isinstance(_exports, values.W_List):
        return False

    return True

def get_imports_from_w_importss_sexp(w_importss):
    importss_acc = to_rpython_list(w_importss)
    importss_list = [None]*len(importss_acc)
    for index, importss_current in enumerate(importss_acc):
        inner_acc = {}
        while (importss_current is not values.w_null):
            c = importss_current.car()
            if isinstance(c, values.W_Symbol):
                inner_acc[c] = c
            elif isinstance(c, values.W_List):
                if c.cdr().cdr() is not values.w_null:
                    raise SchemeException("Unhandled renamed import form : %s" % c.tostring())
                external_id = c.car().get_obj() if isinstance(c.car(), W_Correlated) else c.car()
                internal_id = c.cdr().car().get_obj() if isinstance(c.cdr().car(), W_Correlated) else c.cdr().car()

                assert isinstance(external_id, values.W_Symbol) and isinstance(internal_id, values.W_Symbol)
                inner_acc[external_id] = internal_id
            elif isinstance(c, W_Correlated):
                cc = c.get_obj()
                inner_acc[cc] = cc
            else:
                raise SchemeException("uncrecognized import : %s" % c.tostring())

            importss_current = importss_current.cdr()

        importss_list[index] = inner_acc
    return importss_list

def get_exports_from_w_exports_sexp(w_exports):
    exports = {}
    r_exports = to_rpython_list(w_exports)

    for exp in r_exports:
        if isinstance(exp, values.W_WrappedConsProper):
            car = exp.car()
            internal_name = car.get_obj() if isinstance(car, W_Correlated) else car
            cadr =  exp.cdr().car()
            external_name = cadr.get_obj() if isinstance(cadr, W_Correlated) else cadr
            exports[internal_name] = external_name
        else:
            exports[exp] = exp.get_obj() if isinstance(exp, W_Correlated) else exp
    return exports

def process_w_body_sexp(w_body, importss_list, exports):
    body_forms_ls = to_rpython_list(w_body, open_correlated=True)
    toplevel_defined_linklet_vars = {}

    _body_forms = [None]*len(body_forms_ls)
    for index, b in enumerate(body_forms_ls):
        if isinstance(b, values.W_List) and  b.car() is values.W_Symbol.make("define-values"):
            ids = b.cdr().car()
            ids_ls = to_rpython_list(ids, open_correlated=True)
            for id in ids_ls:
                #id = id_.get_obj() if isinstance(id_, W_Correlated) else id_
                if id in toplevel_defined_linklet_vars:
                    raise SchemeException("duplicate binding name : %s" % id.tostring())
                toplevel_defined_linklet_vars[id] = interp.LinkletDefinedVar(id)

            ast = def_vals_to_ast(b, exports, toplevel_defined_linklet_vars, importss_list)
        else:
            ast = sexp_to_ast(b, [], exports, toplevel_defined_linklet_vars, importss_list)
        _body_forms[index] = ast
    return _body_forms

def deserialize_loop(sexp):
    from pycket.prims.linklet import W_Linklet, W_LinkletBundle, W_LinkletDirectory
    from pycket.env import w_global_config

    #util.console_log("deserialize_loop -- s-exp : %s -- %s" % (sexp, sexp.tostring()), 8)
    if isinstance(sexp, values.W_Cons):
        #util.console_log("it's a W_Cons", 8)
        c = sexp.car()
        #util.console_log("c is : %s" % c.tostring(), 8)
        if c is dir_sym:
            #util.console_log("dir_sym", 8)
            dir_map = sexp.cdr()
            return W_LinkletDirectory(deserialize_loop(dir_map))
        elif c is bundle_sym:
            #util.console_log("bundle_sym", 8)
            bundle_map = sexp.cdr()
            return W_LinkletBundle(deserialize_loop(bundle_map))
        elif looks_like_linklet(sexp):
            #util.console_log("linklet_sym", 8)
            # Unify this with compile_linklet
            if isinstance(sexp.cdr().car(), values.W_List):
                w_name = values.W_Symbol.make("anonymous")
                w_importss = sexp.cdr().car()
                w_exports = sexp.cdr().cdr().car()
                w_body = sexp.cdr().cdr().cdr()
            else:
                w_name = sexp.cdr().car()
                w_importss = sexp.cdr().cdr().car()
                w_exports = sexp.cdr().cdr().cdr().car()
                w_body = sexp.cdr().cdr().cdr().cdr()

            #util.console_log("-- w_name : %s\n-- w_imports : %s\n-- w_exports : %s\n-- w_body : %s" % (w_name.tostring(), w_importss.tostring(), w_exports.tostring(), w_body.tostring()), 8)

            importss_list = get_imports_from_w_importss_sexp(w_importss)

            #util.console_log("imports are done", 8)

            # Process the exports
            exports = get_exports_from_w_exports_sexp(w_exports)
            #util.console_log("exports are done", 8)

            # Process the body
            _body_forms = process_w_body_sexp(w_body, importss_list, exports)

            #util.console_log("body forms -> ASTs are done, postprocessing begins...", 8)

            body_forms = [None]*len(_body_forms)
            for i, bf in enumerate(_body_forms):
                with util.PerfRegion("assign-convert-deserialize"):
                    b_form = assign_convert(bf)
                body_forms[i] = b_form

            #util.console_log("body forms are done", 8)

            return W_Linklet(w_name, importss_list, exports, body_forms)
        else:
            #util.console_log("ELSE", 8)
            is_improper = False
            new_rev = values.w_null
            while sexp is not values.w_null:
                if isinstance(sexp, values.W_Cons):
                    new_rev = values.W_Cons.make(deserialize_loop(sexp.car()), new_rev)
                    sexp = sexp.cdr()
                else:
                    is_improper = True
                    new_rev = values.W_Cons.make(deserialize_loop(sexp), new_rev)
                    sexp = values.w_null
            # double reverse
            # FIXME : do this without the double space
            new = values.w_null
            if is_improper:
                new = new_rev.car()
                new_rev = new_rev.cdr()

            while new_rev is not values.w_null:
                new = values.W_Cons.make(new_rev.car(), new)
                new_rev = new_rev.cdr()

            return new
    elif isinstance(sexp, simple.W_EqImmutableHashTable):
        #util.console_log("it's a W_EqImmutableHashTable", 8)
        l = sexp.length()
        keys = [None]*l
        vals = [None]*l
        i = 0
        for k, v in sexp.iteritems():
            keys[i] = k
            vals[i] = deserialize_loop(v)
            i += 1

        return simple.make_simple_immutable_table(simple.W_EqImmutableHashTable, keys, vals)
    elif isinstance(sexp, equal.W_EqualHashTable):
        #util.console_log("it's a W_EqualHashTable", 8)
        l = sexp.length()
        keys = [None]*l
        vals = [None]*l
        i = 0
        for k, v in sexp.hash_items():
            keys[i] = k
            vals[i] = deserialize_loop(v)
            i += 1

        return equal.W_EqualHashTable(keys, vals, immutable=True)
    elif isinstance(sexp, vector.W_Vector):
        #util.console_log("it's a W_Vector", 8)
        new = [None]*sexp.length()
        items = sexp.get_strategy().ref_all(sexp)
        for index, obj in enumerate(items):
            new[index] = deserialize_loop(obj)

        return vector.W_Vector.fromelements(new, sexp.immutable())
    else:
        #util.console_log("it's something else", 8)
        return sexp

""" Implementation of linklets

class W_LinkletInstance(W_Object)
class W_Linklet(object)
class W_LinkletBundle(W_Object)
class W_LinkletDirectory(W_Object)
 """

#! /usr/bin/env python
# -*- coding: utf-8 -*-
#

from pycket.interpreter import *
from pycket.assign_convert import assign_convert
from pycket.values import *
from pycket.vector import W_Vector
from pycket.values_string import W_String
from pycket.values_parameter import top_level_config
from pycket.error import SchemeException
from pycket import pycket_json
from pycket.prims.expose import prim_env, expose, default, expose_val, prim_src
from pycket.prims.general import make_pred
from pycket.prims.correlated import W_Correlated
from pycket.prims.vector import vector
from pycket.AST import AST
from pycket.cont import Prompt, NilCont, continuation, loop_label
from pycket.prims.control import default_error_escape_handler, default_uncaught_exception_handler
from pycket.hash.base import W_HashTable
from pycket.hash.simple import W_EqImmutableHashTable, make_simple_immutable_table
from pycket.util import PerfRegion, PerfRegionCPS

from pycket.ast_vs_sexp import *

class W_LinkletVar(W_Object):

    _immutable_fields_ = ["w_value?", "sym", "constance"]

    def __init__(self, sym, w_value=None, constance=values.w_false):
        self.sym = sym
        self.w_value = w_value
        self.constance = constance #f (mutable), 'constant, or 'consistent (always the same shape)

    def tostring(self):
        val_str = self.get_value_direct().tostring() if self.w_value else "NO-VAL"
        return "LinkletVar(%s:%s)" % (self.sym.tostring(), val_str)

    def write(self, port, env):
        from pycket.prims.input_output import write_loop
        write_loop(self.sym, port, env)

    def is_constant(self):
        # FIXME : investigate 'consistent
        if self.constance is values.w_false or self.constance is values.W_Symbol.make("consistent"):
            return False
        elif self.constance is values.W_Symbol.make("constant"):
            return True
        else:
            raise SchemeException("Something's wrong with the constance : %s" % self.constance.tostring())

    def set_bang(self, w_val):
        self.w_value.set_val(w_val)

    def get_value_direct(self):
        w_res = self.get_value_unstripped()
        if isinstance(w_res, values.W_Cell):
            return w_res.get_val()
        return w_res

    # get the value possibly with the W_Cell
    def get_value_unstripped(self):
        w_res = self.w_value
        if w_res is None:
            raise SchemeException("Reference to an uninitialized variable : %s" % self.sym.tostring())
        return w_res

    def is_uninitialized(self):
        return self.w_value is None

class W_Uninitialized(W_Object):
    errorname = "uninitialized"
    _attrs_ = []
    def __init__(self):
        pass
    def tostring(self):
        return "#<uninitialized>"

w_uninitialized = W_Uninitialized()

class W_LinkletInstance(W_Object):

    _attrs_ = ["name", "vars", "exports", "data"]
    _immutable_fields_ = ["name", "data"]

    def __init__(self, name, vars, exports, data=w_false):
        self.name = name # W_Symbol (for debugging)
        self.vars = vars # {W_Symbol:LinkletVar}
        self.exports = exports
        self.data = data #

    def tostring(self):

        vars_str = " ".join(["(%s : %s)" % (name.tostring(), var.tostring()) for name, var in self.vars.iteritems()])
        exports_str = "".join(["(%s . %s)" % (int_name.tostring(), ext_name.tostring()) for int_name, ext_name in self.exports.iteritems()])
        data_str = self.data.tostring()
        return "(linklet-instance %s (%s) (%s) %s)" % (self.name, data_str, exports_str, vars_str)

    def get_name(self):
        return self.name

    def get_data(self):
        return self.data

    def get_vars(self):
        return self.vars

    def get_var_names(self):
        return self.vars.keys()

    def set_exports(self, exports):
        self.exports = exports

    def has_var(self, var_name):
        return var_name in self.vars

    # for tests
    def is_var_uninitialized(self, name):
        var = self.get_var(name)
        return var.is_uninitialized()

    def check_var_exists(self, name):
        if not self.has_var(name) and name not in self.exports:
            raise SchemeException("Reference to an undefined variable : %s" % name.tostring())

    def get_var(self, name):
        self.check_var_exists(name)

        if name not in self.vars:
            return self.vars[self.exports[name]]
        return self.vars[name]

    def add_var(self, name, var):
        if name in self.vars and not self.is_var_uninitialized(name):
            raise SchemeException("Already : %s" % name.tostring())
        self.vars[name] = var

    def overwrite_var(self, name, value):
        self.check_var_exists(name)
        self.vars[name].set_bang(value)

    def set_var(self, name, val, mode):
        self.check_var_exists(name)
        self.vars[name].set(val, None, mode)

    def provide_all_exports_to_prim_env(self, excludes=[]):
        for name, var in self.vars.iteritems():
            if name not in excludes:
                prim_env[name] = var.get_value_direct()
                prim_src[name.variable_name()] = 'linklet'

    def lookup_var_value(self, name):
        var = self.get_var(name)
        return var.get_value_direct()

    def lookup_var_cell(self, name):
        var = self.get_var(name)
        return var.get_value_unstripped()

class W_LinkletBundle(W_Object):
    # Information in a linklet bundle is keyed by either a symbol or a fixnum

    _attrs_ = _immutable_fields_ = ["bundle_mapping"]

    def __init__(self,bundle_mapping):
        self.bundle_mapping = bundle_mapping

    def get_mapping(self):
        return self.bundle_mapping

    def tostring(self):
        mapping = self.bundle_mapping
        return "BUNDLE : %s" % mapping.tostring()

our_vm_bytes = values.W_Bytes.from_string("pycket")

@expose("linklet-virtual-machine-bytes", [])
def vm_bytes():
    return our_vm_bytes

w_pycket_sym = values.W_Symbol.make("pycket")

# FIXME: control initialization of this from command line using -W
expose_val("current-compile-target-machine", values_parameter.W_Parameter(w_pycket_sym))


@expose("compile-target-machine?", [values.W_Symbol])
def compile_machine_target_p(v):
    return values.W_Bool.make(v is w_pycket_sym)

@expose("hash->linklet-bundle", [W_Object])
def hash_to_linklet_bundle(content):
    return W_LinkletBundle(content)

@expose("linklet-bundle->hash", [W_LinkletBundle])
def linklet_bundle_to_hash(linkl_bundle):
    return linkl_bundle.bundle_mapping

class W_LinkletDirectory(W_Object):

    # When a Racket module has submodules, the linklet bundles for the
    # module and the submodules are grouped together in a linklet
    # directory. A linklet directory can have nested linklet
    # directories. A linklet directory can be equivalently viewed as a
    # mapping from a lists of symbols to a linklet bundle.

    _attrs_ = _immutable_fields_ = ["dir_mapping"]

    def __init__(self,dir_mapping):
        self.dir_mapping = dir_mapping

    def get_mapping(self):
        return self.dir_mapping

    def tostring(self):
        mapping = self.dir_mapping
        return "DIRECTORY : %s" % mapping.tostring()

@expose("hash->linklet-directory", [W_Object])
def hash_to_linklet_directory(content):
    return W_LinkletDirectory(content)

@expose("linklet-directory->hash", [W_LinkletDirectory])
def linklet_directory_to_hash(linkl_directory):
    return linkl_directory.dir_mapping

@continuation
def finish_perf_region_cont(label, env, cont, _vals):
    from pycket.util import finish_perf_region
    finish_perf_region(label)
    return return_value(_vals, env, cont)

@continuation
def instantiate_val_cont(forms, index, gensym_count, return_val, target, exports, env, cont, _vals):
    if index >= len(forms):
        if return_val:
            return return_value(_vals, env, cont)
        else:
            return return_value(target, env, cont)

    # there's more
    return instantiate_loop(forms, index, gensym_count, return_val, target, exports, env, cont)

@continuation
def instantiate_def_cont(form, forms, index, gensym_count, return_val, target, exports, env, cont, _vals):

    values = _vals.get_all_values()
    len_values = len(values)
    if len(form.names) != len_values:
        raise SchemeException("%s -- expected %s values but got %s" % (form.tostring(), str(len(form.names)), str(len_values)))

    for i in range(len_values):
        name = form.names[i]
        value = values[i]

        # modify target
        ext_name = exports[name] if name in exports else name
        c = W_Cell(value)
        env.toplevel_env().toplevel_set(name, c, already_celled=True)

        var = W_LinkletVar(ext_name, c)
        if name in exports or not target.has_var(ext_name): # variable is definitely going into target
            if target.has_var(ext_name) and not target.is_var_uninitialized(ext_name):
                target.overwrite_var(ext_name, value)
            else:
                target.add_var(ext_name, var)
        elif external_of_an_export(name, exports):
            gensym_count += 1
            ex_name = W_Symbol.make_unreadable(name.tostring() + "." + str(gensym_count))
            target.add_var(ex_name, var)

    return return_value(w_void, env, instantiate_val_cont(forms, index + 1, gensym_count, return_val, target, exports, env, cont))

@loop_label
def instantiate_loop(forms, index, gensym_count, return_val, target, exports, env, cont):
    form = forms[index]
    if isinstance(form, DefineValues):
        return form.rhs, env, instantiate_def_cont(form, forms, index, gensym_count, return_val, target, exports, env, cont)
    else:
        return form, env, instantiate_val_cont(forms, index + 1, gensym_count, return_val, target, exports, env, cont)

class W_Linklet(W_Object):

    _immutable_fields_ = ["name", "importss[*]", "exports", "forms"]

    def __init__(self, name, importss, exports, all_forms):
        self.name = name # W_Symbol -- for debugging
        """ importss -- list of dictionaries (for each import instance) of symbols
        [...,{W_Symbol:W_Symbol},...]
        [...,{exported_by_the_instance:referenced_in_self_forms},...]

        if not renamed, then it has it's own name as the value (thanks RPython!)
        """
        self.importss = importss
        self.exports = exports # dictionary of W_Symbols
        # {internal_id(W_Symbol):external_id(W_Symbol)}
        # again, may be the same if it's not renamed

        self.forms = all_forms # [..., AST ,...]

    def get_name(self):
        return self.name

    def get_importss(self):
        return self.importss

    def get_exports(self):
        return self.exports

    def get_forms(self):
        return self.forms

    def tostring(self):
        forms_str = " ".join([f.tostring() for f in self.forms])
        importss_ls = [None]*len(self.importss)

        for index, imp_dict in enumerate(self.importss):
            importss_ls[index] = "(" + "".join(["(%s . %s)" % (ext_name.tostring(), int_name.tostring()) for ext_name, int_name in imp_dict.iteritems()]) + ")"

        importss_str = "".join(importss_ls)

        exports_str = "".join(["(%s %s)" % (int_name.tostring(), ext_name.tostring()) for int_name, ext_name in self.exports.iteritems()])

        return "(linklet %s (%s) (%s) %s)" % (self.name.tostring(), importss_str, exports_str, forms_str)

    def instantiate(self, w_imported_instances, config, prompt=False, target=None, env=None, cont=None):

        l_importss = len(self.importss)
        l_given_instances = len(w_imported_instances)

        if l_importss != l_given_instances:
            raise SchemeException("Required %s instances but given %s" % (str(l_importss), str(l_given_instances)))

        return_val = True
        if not target:
            target = W_LinkletInstance(self.name, {}, self.exports)
            return_val = False
        else:
            target.set_exports(self.exports)

        """Instantiates the linklet:

        --- Prep the environment and the continuation for the
            evaluation of linklet forms
        --- Process the imports, get them into the toplevel
            environment
        --- Collect the ids defined in the self linklet's forms
        --- Uninitialize the undefined exports in the linklet into the
            target (if it doesn't already have it)
        --- Evaluate linklet forms
        --- Return target instance and return value (None if a target
            is given to instantiate)

        """

        """
        Prep the environment and the continuation
        Put the target into the environment
        """
        # Discard the current environment and create a new one at
        # every instantiation This is crucial because linklet
        # variables are put into the toplevel environment, and
        # replacing the W_Cell (not overwriting its value) with
        # another one makes the corresponding target to lose the link
        # to that variable (whenever the target's variable is
        # modified, the one in the environment has to be updated too)
        from pycket.env import ToplevelEnv
        env = ToplevelEnv(config, current_linklet_instance=target, import_instances=w_imported_instances)

        if not cont:
            cont = NilCont()
            cont.update_cm(parameterization_key, top_level_config)
            cont.update_cm(exn_handler_key, default_uncaught_exception_handler)

            if prompt: # REMOVABLE
                raise SchemeException("assumption failed; didn't get a continuation as a parameter, but got prompt : #t")

        if prompt:
            Prompt(w_default_continuation_prompt_tag, None, env, cont)

        """
        Process the imports, get them into the toplevel environment
        """
        # for instance_index, imports_dict in enumerate(self.importss):
        #     for ext_name, int_name in imports_dict.iteritems():
        #         # (linklet (((x x1))) () (define-values (x) 14) (+ x1 x))
        #         if int_name in self.exports:
        #             raise SchemeException("export duplicates import : %s" % int_name.tostring())

        #         imported_var = w_imported_instances[instance_index].get_var(ext_name)
        #         if imported_var.is_uninitialized():
        #             raise SchemeException("Trying to import a variable that is uninitialized : %s" % ext_name.tostring())

        #         # imports never get into the target
        #         # put the into the toplevel env
        #         env.toplevel_env().toplevel_set(int_name, imported_var.get_value_direct())

        """
        Collect the ids defined in the given linklet's forms
        """
        linklet_defined_names = []
        for b in self.forms:
            if isinstance(b, DefineValues):
                linklet_defined_names += b.names

        """
        Uninitialize the undefined exports -- name, undef
        """
        for internal_name, external_name in self.exports.iteritems():
            # Defined name ids are changed in compilation based on renames
            if external_name not in linklet_defined_names:
                if not target.has_var(external_name):
                    target.add_var(external_name, W_LinkletVar(external_name))

        if len(self.forms) == 0:
            # no need for any evaluation, just return the instance or the value
            if return_val:
                return return_value(w_void, env, cont)
            else:
                return return_value(target, env, cont)

        return instantiate_loop(self.forms, 0, 0, return_val, target, self.exports, env, cont)

    @staticmethod # json_file_name -> W_Linklet
    def load_linklet(json_file_name, loader, set_version=False):
        from pycket.expand import readfile_rpython, getkey
        from pycket.util import console_log
        """ Expands and loads a linklet from a JSON file"""
        with PerfRegion("json-load"):
            data = readfile_rpython(json_file_name)
            json = pycket_json.loads(data)
            console_log("Finished reading JSON from %s" % json_file_name, 2)
            assert json.is_object
            json_python_dict = json.value_object()
            assert "linklet" in json_python_dict
            linklet_dict = getkey(json_python_dict, "linklet", type='o')
            assert "exports" in linklet_dict and "body" in linklet_dict

            # list of JsonObject
            exports_list = getkey(linklet_dict, "exports", type='a')

        if set_version:
            from pycket.util import console_log
            from pycket.env import w_version

            conf = getkey(linklet_dict, "config", type='o')
            ver = conf['version'].value_string()
            console_log("Setting the version to %s" % ver)
            w_version.set_version(ver)

        exports = {}
        for exp in exports_list:
            if exp.is_array:
                arr = exp.value_array()
                defined_name = W_Symbol.make(arr[0].value_object()['quote'].value_object()['toplevel'].value_string())
                exported_name = W_Symbol.make(arr[1].value_object()['quote'].value_object()['toplevel'].value_string())

                exports[defined_name] = exported_name
            else:
                exp_str = exp.value_object()['quote'].value_object()['toplevel'].value_string()
                exp_sym = W_Symbol.make(exp_str)

                exports[exp_sym] = exp_sym

        imports_list = getkey(linklet_dict, "importss", type='a', default=[])

        importss = [None]*len(imports_list) # list of dict

        if "importss" in linklet_dict:
            for index, imports in enumerate(imports_list):
                arr = imports.value_array()
                # bootstrap linklets have no imports at all
                # this is only for debugging purposes
                instance_imports = {}
                for id_str in arr:
                    sym = W_Symbol.make(id_str.value_object()['quote'].value_object()['toplevel'].value_string())
                    instance_imports[sym] = sym
                importss[index] = instance_imports

        console_log("Converting linklet forms to AST ...", 2)

        with PerfRegion("json-to-ast"):
            all_forms = []
            for body_form in getkey(linklet_dict, "body", type='a'):
                form_2 = loader.to_ast(body_form)
                form_1 = Context.normalize_term(form_2)
                # if form_1.tostring() != form_2.tostring():
                #     import pdb;pdb.set_trace()
                form = assign_convert(form_1)
                all_forms.append(form)

        console_log("Finished converting linklet forms to AST ...", 2)

        config = {}
        config_obj = getkey(linklet_dict, "config", type='o')
        if config_obj is not None:
            for k, v in config_obj.iteritems():
                config[k] = v.value_string()

        return W_Linklet(W_Symbol.make(json_file_name), importss, exports, all_forms), config


"""
 (define-values (1/read-compiled-linklet) (hash-ref linklet-primitive-table 'read-compiled-linklet #f))
 (define-values (1/instance-unset-variable!) (hash-ref linklet-primitive-table 'instance-unset-variable! #f))
 (define-values (1/variable-reference?) (hash-ref linklet-primitive-table 'variable-reference? #f))
 (define-values (1/variable-reference-constant?) (hash-ref linklet-primitive-table 'variable-reference-constant? #f))
"""

make_pred("linklet?", W_Linklet)

make_pred("instance?", W_LinkletInstance)

def external_of_an_export(sym, exports):
    # checks if the given sym is used as an external name
    # for an internally defined variable
    for int_name, ext_name in exports.iteritems():
        if sym is ext_name and int_name is not ext_name:
            return True
    return False

@expose("compile-linklet", [W_Object, default(W_Object, w_false), default(W_Object, w_false), default(W_Object, w_false), default(W_Object, w_false)], simple=False)
def compile_linklet(form, name, import_keys, get_import, options, env, cont):
    from pycket.util import console_log
    console_log("compiling linklet : %s %s" % (name.tostring(), form.tostring()), 3)
    with PerfRegionCPS("compile-linklet"):
        cont_ = finish_perf_region_cont("compile-linklet", env, cont)
        return do_compile_linklet(form, name, import_keys, get_import, options, env, cont_)

def do_compile_linklet(form, name, import_keys, get_import, options, env, cont):

    if isinstance(form, W_WrappedConsProper): # s-expr
        # read it and create an AST, put it in a W_Linklet and return
        if not isinstance(form.car(), W_Symbol) or "linklet" != form.car().tostring():
            raise SchemeException("Malformed s-expr. Expected a linklet, got %s" % form.tostring())
        else:
            # Process the imports
            w_importss = form.cdr().car()
            importss_list = get_imports_from_w_importss_sexp(w_importss)

            # Process the exports
            w_exports = form.cdr().cdr().car()
            exports = get_exports_from_w_exports_sexp(w_exports)

            # Process the body
            w_body = form.cdr().cdr().cdr()
            with PerfRegion("compile-sexp-to-ast"):
                _body_forms, _body_length = process_w_body_sexp(w_body, importss_list, exports)

            # Postprocess the body
            body_forms = [None]*_body_length
            for i, bf in enumerate(_body_forms):
                with PerfRegion("compile-normalize"):
                    b_form = Context.normalize_term(bf)
                with PerfRegion("compile-assign-convert"):
                    b_form = assign_convert(b_form)
                body_forms[i] = b_form

            if name is w_false:
                w_name = W_Symbol.make("ad-hoc")
            else:
                w_name = name

            linkl = W_Linklet(w_name, importss_list, exports, body_forms)
            if import_keys is w_false:
                return return_value_direct(linkl, env, cont)
            else:
                return return_multi_vals(Values.make([linkl, import_keys]), env, cont)

    elif isinstance(form, W_Linklet):
        if import_keys is w_false:
            return return_value_direct(form, env, cont)
        else:
            return return_multi_vals(Values.make([form, import_keys]), env, cont)

    else: # correlated
        # take the AST from the correlated and put it in a W_Linklet and return
        raise SchemeException("NYI")

    ##################################
    ##### The optional import-keys and get-import arguments support cross-linklet optimization.
    ##################################
    # (Pdb) import_keys
    # <pycket.vector.W_Vector object at 0x000000001baba608>
    # (Pdb) import_keys.tostring()
    # '#(#f #f #f)'

    # (Pdb) get_import
    # <W_Closure1AsEnvSize3 ['#<procedure:get-module-linklet-info_0:321>', '#<namespace>', '#<procedure:intern-module-use_0:321>'] <pycket.env.ToplevelEnv object at 0x0000000002b8c1e0>>
    ##################################

    ##### As long as serializable? is true, the resulting linklet can be marshaled to and from a byte stream when it is part of a linklet bundle.

    # (Pdb) serializable_huh
    # <pycket.values.W_Bool object at 0x00000000024e8640>
    ##################################

@expose("instance-name", [W_LinkletInstance])
def instance_name(l_inst):
    return l_inst.get_name()

@expose("instantiate-linklet", [W_Linklet, W_List, default(W_Object, w_false), default(W_Object, w_true)], simple=False)
def instantiate_linklet(linkl, import_instances, target_instance, use_prompt, env, cont):
    from pycket.util import console_log
    console_log("instantiating linklet : %s" % linkl.name.tostring(), 3)

    prompt = False
    if use_prompt is not w_false: # use-prompt? : any/c = #t - what happens when it is 3 ?
        prompt = True

    im_list, im_length = to_rpython_list(import_instances)
    expected = len(linkl.importss)

    if expected != im_length:
        raise SchemeException("The number of instances in import-instances must match the number of import sets in linklet. Expected %s but got %s" % (expected, im_length))

    if target_instance is None or target_instance is w_false:
        target = None
    elif isinstance(target_instance, W_LinkletInstance):
        target = target_instance
    else:
        raise SchemeException("Expected #f or instance? as target-instance, got : %s" % target_instance)
    with PerfRegionCPS("instantiate-linklet"):
        cont_ = finish_perf_region_cont("instantiate-linklet", env, cont)
        return linkl.instantiate(im_list, env.toplevel_env()._pycketconfig, prompt, target, env, cont_)

@expose("linklet-import-variables", [W_Linklet])
def linklet_import_variables(linkl):
    importss_py_lst = linkl.importss
    importss = w_null
    for imp_dict in importss_py_lst:
        imp_inner = w_null
        for ext_name, int_name in imp_dict.iteritems():
            imp_inner = W_Cons.make(ext_name, imp_inner)
        importss = W_Cons.make(imp_inner, importss)
    return importss

@expose("linklet-export-variables", [W_Linklet])
def linklet_export_variables(linkl):
    exports = w_null
    for ext_name in linkl.exports.values():
        exports = W_Cons.make(ext_name, exports)
    return exports

@expose("instance-variable-names", [W_LinkletInstance])
def instance_variable_names(inst):
    return get_instance_variable_names(inst)
# to be able to call it without prim_env indirection
def get_instance_variable_names(inst):
    names = w_null
    for name in inst.get_var_names():
        names = W_Cons.make(name, names)

    return names

make_pred("linklet-directory?", W_LinkletDirectory)

make_pred("linklet-bundle?", W_LinkletBundle)

# for internal use
def is_directory(v):
    return isinstance(v, W_LinkletDirectory)
def is_bundle(v):
    return isinstance(v, W_LinkletBundle)

@expose("make-instance") #FIXME: [W_Object, W_Object, [W_Symbol, W_Object] ....]
def make_instance(args): # name, data, *vars_vals
    with PerfRegion("make-instance"):
        name = args[0] # W_Symbol
        data = w_false
        mode = w_false

        from pycket.util import console_log
        if "'" in name.tostring():
            console_log("making instance : %s" % name.tostring(), 2)
        else:
            console_log("making instance : %s" % name.tostring(), 3)

        if len(args) <= 2:
            data = args[1]
            vars_vals = []
        else:
            data = args[1]
            mode = args[2]
            vars_vals = args[3:]

        # check if the vars and vals match
        if ((len(vars_vals) % 2) != 0):
            raise SchemeException("Variable names and values do not match : %s" % vars_vals)

        vars_vals_dict = {}
        for i in range(0, len(vars_vals), 2):
            n = vars_vals[i]
            v = vars_vals[i+1]
            vars_vals_dict[n] = W_LinkletVar(n, W_Cell(v), mode)

        return W_LinkletInstance(name, vars_vals_dict, {}, data)

@expose("recompile-linklet", [W_Linklet, default(W_Object, None), default(W_Object, w_false), default(W_Object, None)], simple=False)
def recompile_linklet(linkl, name, import_keys, get_import, env, cont):
    if import_keys is not None:
        return return_multi_vals(Values.make([linkl, import_keys]), env, cont)
    else:
        return return_value(linkl, env, cont)

@expose("instance-variable-value", [W_LinkletInstance, W_Symbol, default(W_Object, None)], simple=False)
def instance_variable_value(instance, name, fail_k, env, cont):
    if not instance.has_var(name) or instance.get_var(name).is_uninitialized():
        if fail_k is not None and fail_k.iscallable():
            return fail_k.call([], env, cont)
        else:
            raise SchemeException("key %s not found in the instance %s" % (name.tostring(), instance.name.tostring()))

    var = instance.get_var(name)
    return return_value(var.get_value_direct(), env, cont)

@expose("instance-describe-variable!", [W_LinkletInstance, W_Symbol, W_Object])
def instance_describe_variable(inst, name, desc_v):
    return w_void

@expose("instance-data", [W_LinkletInstance])
def instance_data(inst):
    return inst.get_data()

@expose("eval-linklet", [W_Linklet])
def eval_linklet(l):
    return l

@expose("instance-set-variable-value!", [W_LinkletInstance, W_Symbol, W_Object, default(W_Object, w_false)])
def instance_set_variable_value(instance, name, val, mode):
    if instance.has_var(name) and not instance.is_var_uninitialized(name):
        v = instance.get_var(name)
        if v.is_constant():
            raise SchemeException("Cannot mutate a constant : %s" % name.tostring())
        # FIXME : change to be constant?
        instance.overwrite_var(name, val)
    else:
        instance.add_var(name, W_LinkletVar(name, W_Cell(val), mode))

    return w_void

@expose("primitive->compiled-position", [W_Object])
def prim_to_compiled_pos(prim):
    return w_false

@expose("compiled-position->primitive", [W_Object])
def compiled_pos_to_prim(pos):
    return w_false

@expose("primitive-in-category?", [W_Object, W_Object])
def prim_in_category(sym, cat):
    return w_false

@expose("variable-reference->instance", [W_VariableReference, default(W_Bool, w_false)])
def var_ref_to_instance(varref, ref_site):

    if ref_site is w_true:
        return varref.get_instance()
    v = varref.varref.var
    if not v:
        return w_false # anonymous
    else:
        return varref.get_instance()

@expose("variable-reference-from-unsafe?", [W_VariableReference])
def var_ref_from_unsafe_huh(varref):
    """Returns #t if the module of the variable reference itself (not
    necessarily a referenced variable) is compiled in unsafe mode, #f
    otherwise.
    """
    return varref.is_unsafe()

@continuation
def read_linklet_cont(env, cont, _vals):
    from pycket.util import finish_perf_region
    bundle_map = check_one_val(_vals)
    finish_perf_region("fasl->s-exp")
    if not isinstance(bundle_map, W_HashTable):
        raise SchemeException("got something that is not a table: %s"%bundle_map.tostring())
    with PerfRegion("s-exp->ast"):
        return return_value(deserialize_loop(bundle_map), env, cont)

@expose("read-linklet-bundle-hash", [values.W_InputPort], simple=False)
def read_linklet_bundle_hash(in_port, env, cont):
    from pycket.util import console_log
    from pycket.racket_entry import get_primitive
    fasl_to_s_exp = get_primitive("fasl->s-exp")
    with PerfRegionCPS("fasl->s-exp"):
        return fasl_to_s_exp.call([in_port, values.w_true], env, read_linklet_cont(env, cont))

@expose("write-linklet-bundle-hash", [W_EqImmutableHashTable, values.W_OutputPort], simple=False)
def write_linklet_bundle_hash(ht, out_port, env, cont):
    from pycket.util import console_log
    from pycket.racket_entry import get_primitive
    console_log("BUNDLE AST TO BE SERIALIZED: %s" % ht.tostring(), 7)

    with PerfRegion("ast->sexp"):
        l = ht.length()
        keys = [None]*l
        vals = [None]*l

        i = 0
        for k, v in ht.iteritems():
            keys[i] = k
            vals[i] = ast_to_sexp(v)
            i += 1

        bundle_s_exp = make_simple_immutable_table(W_EqImmutableHashTable, keys, vals)

    console_log("WRITING BUNDLE SEXP : %s" % bundle_s_exp.tostring(), 7)

    s_exp_to_fasl = get_primitive("s-exp->fasl")
    with PerfRegionCPS("s-exp->fasl"):
        return s_exp_to_fasl.call([bundle_s_exp, out_port, values.w_false], env,
                                  finish_perf_region_cont("s-exp->fasl", env, cont))

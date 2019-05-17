#! /usr/bin/env python
# -*- coding: utf-8 -*-


FASL_GRAPH_DEF_TYPE = 1
FASL_GRAPH_REF_TYPE = 2

FASL_FALSE_TYPE = 3
FASL_TRUE_TYPE = 4
FASL_NULL_TYPE = 5
FASL_VOID_TYPE = 6
FASL_EOF_TYPE = 7

FASL_INTEGER_TYPE = 8
FASL_FLONUM_TYPE = 9
FASL_SINGLE_FLONUM_TYPE = 10
FASL_RATIONAL_TYPE = 11
FASL_COMPLEX_TYPE = 12
FASL_CHAR_TYPE = 13

FASL_SYMBOL_TYPE = 14
FASL_UNREADABLE_SYMBOL_TYPE = 15
FASL_UNINTERNED_SYMBOL_TYPE = 16
FASL_KEYWORD_TYPE = 17
FASL_STRING_TYPE = 18
FASL_IMMUTABLE_STRING_TYPE = 19
FASL_BYTES_TYPE = 20
FASL_IMMUTABLE_BYTES_TYPE = 21
FASL_PATH_TYPE = 22
FASL_RELATIVE_PATH_TYPE = 23

FASL_PREGEXP_TYPE = 24
FASL_REGEXP_TYPE = 25
FASL_BYTE_PREGEXP = 26
FASL_BYTE_REGEXP_TYPE = 27

FASL_LIST_TYPE = 28
FASL_LIST_STAR_TYPE = 29
FASL_PAIR_TYPE = 30
FASL_VECTOR_TYPE = 31
FASL_IMMUTABLE_VECTOR_TYPE = 32
FASL_BOX_TYPE = 33
FASL_IMMUTABLE_BOX_TYPE = 34
FASL_PREFAB_TYPE = 35
FASL_HASH_TYPE = 36
FASL_IMMUTABLE_HASH_TYPE = 37

FASL_SRCLOC = 38

FASL_EXTFLONUM_TYPE = 39

# 100 to 255 is used for small integers:
FASL_SMALL_INTEGER_START = 100

#################################################

FASL_LOWEST_SMALL_INTEGER = -10
FASL_HIGHEST_SMALL_INTEGER = 255 - ((FASL_SMALL_INTEGER_START - FASL_LOWEST_SMALL_INTEGER) - 1)
FASL_PREFIX = "racket/fasl:"
FASL_PREFIX_LENGTH = len(FASL_PREFIX)

FASL_HASH_EQ_VARIANT = 0
FASL_HASH_EQUAL_VARIANT = 1
FASL_HASH_EQV_VARIANT = 2

from rpython.rlib             import streamio as sio
import os

def fasl_to_sexp_file(file_name):
    stream = sio.open_file_as_stream(file_name, "rb", buffering=2**21)
    return fasl_to_sexp(stream)

def fasl_to_sexp(stream):

    prefix = stream.read(FASL_PREFIX_LENGTH)
    if prefix != FASL_PREFIX:
        raise Exception("unrecognized prefix")

    shared_count = read_fasl_integer_stream(stream)
    shared = [None]*shared_count

    length = read_fasl_integer_stream(stream)
    # read the entire thing and work with a byte-string and a position
    fasl_string = stream.read(length)
    pos = 0
    sexp, pos = fasl_to_sexp_recursive(fasl_string, pos)
    return sexp

# let's not worry about the CPS'in this right now
# we probably won't have any sexp deeper than the stack anyways
def fasl_to_sexp_recursive(fasl_string, pos):
    #from pycket.interpreter import *
    from pycket import values as v
    #from pycket.values import to_list, W_Symbol, W_Fixnum, w_false, w_true, w_null, w_void, eof_object
    from pycket.values_string import W_String
    from pycket.values_regex import W_Regexp, W_PRegexp, W_ByteRegexp, W_BytePRegexp

    typ, pos = read_byte_no_eof(fasl_string, pos)

    if typ == FASL_FALSE_TYPE:
        return v.w_false, pos
    elif typ == FASL_TRUE_TYPE:
        return v.w_true, pos
    elif typ == FASL_NULL_TYPE:
        return v.w_null, pos
    elif typ == FASL_VOID_TYPE:
        return v.w_void, pos
    elif typ == FASL_EOF_TYPE:
        return v.eof_object, pos
    elif typ == FASL_INTEGER_TYPE:
        num, pos = read_fasl_integer(fasl_string, pos)
        return v.W_Fixnum(num), pos
    elif typ == FASL_FLONUM_TYPE:
        from pycket.prims.numeric import float_bytes_to_real
        num_str, pos = read_bytes_exactly(fasl_string, pos, 8)
        return float_bytes_to_real(num_str, v.w_false), pos
    elif typ == FASL_SINGLE_FLONUM_TYPE:
        from pycket.prims.numeric import float_bytes_to_real
        num_str, pos = read_bytes_exactly(fasl_string, pos, 4)
        real = float_bytes_to_real(num_str, v.w_false)
        return real.arith_exact_inexact(), pos
    elif typ == FASL_EXTFLONUM_TYPE:
        from pycket.prims.string import _str2num
        bstr_len, pos = read_fasl_integer(fasl_string, pos)
        num_str, pos = read_bytes_exactly(fasl_string, pos, bstr_len)
        return _str2num(W_String.fromstr_utf8(num_str).as_str_utf8(), 10), pos
    elif typ == FASL_RATIONAL_TYPE:
        num, pos = fasl_to_sexp_recursive(fasl_string, pos)
        den, pos = fasl_to_sexp_recursive(fasl_string, pos)
        return v.W_Rational.make(num, den), pos
    elif typ == FASL_COMPLEX_TYPE:
        re, pos = fasl_to_sexp_recursive(fasl_string, pos)
        im, pos = fasl_to_sexp_recursive(fasl_string, pos)
        return v.W_Complex.from_real_pair(re, im), pos
    elif typ == FASL_CHAR_TYPE:
        _chr, pos = read_fasl_integer(fasl_string, pos)
        return v.W_Character(unichr(_chr)), pos
    elif typ == FASL_SYMBOL_TYPE:
        sym_str, pos = read_fasl_string(fasl_string, pos)
        return v.W_Symbol.make(sym_str), pos
    elif typ == FASL_UNREADABLE_SYMBOL_TYPE:
        sym_str, pos = read_fasl_string(fasl_string, pos)
        return v.W_Symbol.make_unreadable(sym_str), pos
    elif typ == FASL_UNINTERNED_SYMBOL_TYPE:
        sym_str, pos = read_fasl_string(fasl_string, pos)
        return v.W_Symbol(sym_str), pos
    elif typ == FASL_KEYWORD_TYPE:
        key_str, pos = read_fasl_string(fasl_string, pos)
        return v.W_Keyword.make(key_str), pos
    elif typ == FASL_STRING_TYPE:
        str_str, pos = read_fasl_string(fasl_string, pos)
        return W_String.make(str_str), pos
    elif typ == FASL_IMMUTABLE_STRING_TYPE:
        str_str, pos = read_fasl_string(fasl_string, pos)
        return W_String.make(str_str).make_immutable(), pos
    elif typ == FASL_BYTES_TYPE:
        byts, pos = read_fasl_bytes(fasl_string, pos)
        return v.W_Bytes.from_string(byts, immutable=False), pos
    elif typ == FASL_IMMUTABLE_BYTES_TYPE:
        byts, pos = read_fasl_bytes(fasl_string, pos)
        return v.W_Bytes.from_string(byts), pos
    elif typ == FASL_PATH_TYPE:
        byts, pos = read_fasl_bytes(fasl_string, pos)
        return v.W_Path(byts), pos
    elif typ == FASL_RELATIVE_PATH_TYPE: # FIXME: check this
        byts, pos = read_fasl_bytes(fasl_string, pos)
        return v.W_Path(byts), pos

    elif typ == FASL_PREGEXP_TYPE:
        str_str, pos = read_fasl_string(fasl_string, pos)
        return W_PRegexp(str_str), pos
    elif typ == FASL_REGEXP_TYPE:
        str_str, pos = read_fasl_string(fasl_string, pos)
        return W_Regexp(str_str), pos
    elif typ == FASL_BYTE_PREGEXP:
        str_str, pos = read_fasl_string(fasl_string, pos)
        return W_BytePRegexp(str_str), pos
    elif typ == FASL_BYTE_REGEXP_TYPE:
        str_str, pos = read_fasl_string(fasl_string, pos)
        import pdb;pdb.set_trace()
        return W_ByteRegexp(str_str), pos

    elif typ == FASL_LIST_TYPE:
        list_len, pos = read_fasl_integer(fasl_string, pos)
        lst_chunk = fasl_string[pos:pos+list_len]
        lst = [None]*list_len
        for i in range(list_len):
            element, pos = fasl_to_sexp_recursive(fasl_string, pos)
            lst[i] = element
        return to_list(lst), pos
    else:
        if typ >= FASL_SMALL_INTEGER_START:
            return W_Fixnum((typ-FASL_SMALL_INTEGER_START)+FASL_LOWEST_SMALL_INTEGER), pos
        else:
            raise Exception("unrecognized fasl tag : %s" % typ)

def read_fasl_string(fasl_string, pos):
    sym_len, pos = read_fasl_integer(fasl_string, pos)
    return read_bytes_exactly(fasl_string, pos, sym_len)
    # TODO: check utf-8

def read_fasl_bytes(fasl_string, pos):
    bytes_len, pos = read_fasl_integer(fasl_string, pos)
    return read_bytes_exactly(fasl_string, pos, bytes_len)

def read_byte_no_eof(fasl_string, pos):
    return ord(fasl_string[pos]), pos+1

def read_byte_no_eof_stream(stream):
    b = stream.read(1)
    if not b:
        raise Exception("truncated stream - got eof")
    return b

def read_bytes_exactly(fasl_string, pos, n):
    if pos+n > len(fasl_string):
        raise Exception("truncated stream")
    return fasl_string[pos:pos+n], pos+n

def fasl_integer_inner(b):
    if b <= 127:
        return b
    elif b >= 132:
        raise Exception("NYI")

def read_fasl_integer(fasl_string, pos):
    b, new_pos = read_byte_no_eof(fasl_string, pos)
    return fasl_integer_inner(b), new_pos

def read_fasl_integer_stream(stream):
    b = ord(read_byte_no_eof_stream(stream))
    return fasl_integer_inner(b)

import numpy as np
import pytest

import theano
from theano.graph import fg
from theano.graph.basic import Apply, Constant, Variable
from theano.graph.op import COp
from theano.graph.type import CType
from theano.link.basic import PerformLinker
from theano.link.c.basic import CLinker, DualLinker, OpWiseCLinker


def as_variable(x):
    assert isinstance(x, Variable)
    return x


class TDouble(CType):
    def filter(self, data, strict=False, allow_downcast=False):
        return float(data)

    def c_declare(self, name, sub, check_input=True):
        return f"double {name}; void* {name}_bad_thing;"

    def c_init(self, name, sub):
        return f"""
        {name} = 0;
        {name}_bad_thing = malloc(100000);
        //printf("Initializing {name}\
");
        """

    def c_literal(self, data):
        return str(data)

    def c_extract(self, name, sub, check_input=True, **kwargs):
        return """
        if (!PyFloat_Check(py_%(name)s)) {
            PyErr_SetString(PyExc_TypeError, "not a double!");
            %(fail)s
        }
        %(name)s = PyFloat_AsDouble(py_%(name)s);
        %(name)s_bad_thing = NULL;
        //printf("Extracting %(name)s\\n");
        """ % dict(
            locals(), **sub
        )

    def c_sync(self, name, sub):
        return f"""
        Py_XDECREF(py_{name});
        py_{name} = PyFloat_FromDouble({name});
        if (!py_{name})
            py_{name} = Py_None;
        //printf("Syncing {name}\
");
        """

    def c_cleanup(self, name, sub):
        return f"""
        //printf("Cleaning up {name}\
");
        if ({name}_bad_thing)
            free({name}_bad_thing);
        """

    def c_code_cache_version(self):
        return (1,)

    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))


tdouble = TDouble()


def double(name):
    return Variable(tdouble, None, None, name=name)


class MyOp(COp):

    __props__ = ("nin", "name")

    def __init__(self, nin, name):
        self.nin = nin
        self.name = name

    def make_node(self, *inputs):
        assert len(inputs) == self.nin
        inputs = list(map(as_variable, inputs))
        for input in inputs:
            if input.type is not tdouble:
                raise Exception("Error 1")
        outputs = [double(self.name + "_R")]
        return Apply(self, inputs, outputs)

    def __str__(self):
        return self.name

    def perform(self, node, inputs, out_):
        (out,) = out_
        out[0] = self.impl(*inputs)

    def c_code_cache_version(self):
        return (1,)


# class Unary(MyOp):
#    def __init__(self):
#        MyOp.__init__(self, 1, self.__class__.__name__)


class Binary(MyOp):
    def __init__(self):
        MyOp.__init__(self, 2, self.__class__.__name__)


class Add(Binary):
    def c_code(self, node, name, inp, out, sub):
        x, y = inp
        (z,) = out
        return f"{z} = {x} + {y};"

    def impl(self, x, y):
        return x + y


add = Add()


class BadSub(Binary):
    def c_code(self, node, name, inp, out, sub):
        x, y = inp
        (z,) = out
        return f"{z} = {x} - {y};"

    def impl(self, x, y):
        return -10  # erroneous (most of the time)


bad_sub = BadSub()


class Mul(Binary):
    def c_code(self, node, name, inp, out, sub):
        x, y = inp
        (z,) = out
        return f"{z} = {x} * {y};"

    def impl(self, x, y):
        return x * y


mul = Mul()


class Div(Binary):
    def c_code(self, node, name, inp, out, sub):
        x, y = inp
        (z,) = out
        return f"{z} = {x} / {y};"

    def impl(self, x, y):
        return x / y


div = Div()


def inputs():
    x = double("x")
    y = double("y")
    z = double("z")
    return x, y, z


def Env(inputs, outputs):
    e = fg.FunctionGraph(inputs, outputs)
    return e


################
# Test CLinker #
################


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_clinker_straightforward():
    x, y, z = inputs()
    e = add(mul(add(x, y), div(x, y)), bad_sub(bad_sub(x, y), z))
    lnk = CLinker().accept(Env([x, y, z], [e]))
    fn = lnk.make_function()
    assert fn(2.0, 2.0, 2.0) == 2.0


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_clinker_literal_inlining():
    x, y, z = inputs()
    z = Constant(tdouble, 4.12345678)
    e = add(mul(add(x, y), div(x, y)), bad_sub(bad_sub(x, y), z))
    lnk = CLinker().accept(Env([x, y], [e]))
    fn = lnk.make_function()
    assert abs(fn(2.0, 2.0) + 0.12345678) < 1e-9
    code = lnk.code_gen()
    # print "=== Code generated ==="
    # print code
    assert "4.12345678" in code  # we expect the number to be inlined


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_clinker_literal_cache():
    mode = theano.Mode(linker="c")

    A = theano.tensor.matrix()
    input1 = theano.tensor.vector()

    normal_svd = np.array(
        [
            [5.936276e01, -4.664007e-07, -2.56265e-06],
            [-4.664007e-07, 9.468691e-01, -3.18862e-02],
            [-2.562651e-06, -3.188625e-02, 1.05226e00],
        ],
        dtype=theano.config.floatX,
    )

    orientationi = np.array(
        [59.36276866, 1.06116353, 0.93797339], dtype=theano.config.floatX
    )

    for out1 in [A - input1[0] * np.identity(3), input1[0] * np.identity(3)]:
        benchmark = theano.function(
            inputs=[A, input1], outputs=[out1], on_unused_input="ignore", mode=mode
        )

        out1 = benchmark(normal_svd, orientationi)


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_clinker_single_node():
    x, y, z = inputs()
    node = add.make_node(x, y)
    lnk = CLinker().accept(Env(node.inputs, node.outputs))
    fn = lnk.make_function()
    assert fn(2.0, 7.0) == 9


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_clinker_dups():
    # Testing that duplicate inputs are allowed.
    x, y, z = inputs()
    e = add(x, x)
    lnk = CLinker().accept(Env([x, x], [e]))
    fn = lnk.make_function()
    assert fn(2.0, 2.0) == 4
    # note: for now the behavior of fn(2.0, 7.0) is undefined


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_clinker_not_used_inputs():
    # Testing that unused inputs are allowed.
    x, y, z = inputs()
    e = add(x, y)
    lnk = CLinker().accept(Env([x, y, z], [e]))
    fn = lnk.make_function()
    assert fn(2.0, 1.5, 1.0) == 3.5


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_clinker_dups_inner():
    # Testing that duplicates are allowed inside the graph
    x, y, z = inputs()
    e = add(mul(y, y), add(x, z))
    lnk = CLinker().accept(Env([x, y, z], [e]))
    fn = lnk.make_function()
    assert fn(1.0, 2.0, 3.0) == 8.0


######################
# Test OpWiseCLinker #
######################

# slow on linux, but near sole test and very central
def test_opwiseclinker_straightforward():
    x, y, z = inputs()
    e = add(mul(add(x, y), div(x, y)), bad_sub(bad_sub(x, y), z))
    lnk = OpWiseCLinker().accept(Env([x, y, z], [e]))
    fn = lnk.make_function()
    if theano.config.cxx:
        assert fn(2.0, 2.0, 2.0) == 2.0
    else:
        # The python version of bad_sub always return -10.
        assert fn(2.0, 2.0, 2.0) == -6


def test_opwiseclinker_constant():
    x, y, z = inputs()
    x = Constant(tdouble, 7.2, name="x")
    e = add(mul(x, y), mul(y, z))
    lnk = OpWiseCLinker().accept(Env([y, z], [e]))
    fn = lnk.make_function()
    res = fn(1.5, 3.0)
    assert res == 15.3


class MyExc(Exception):
    pass


def _my_checker(x, y):
    if x[0] != y[0]:
        raise MyExc("Output mismatch.", {"performlinker": x[0], "clinker": y[0]})


###################
# Test DualLinker #
###################


def test_duallinker_straightforward():
    x, y, z = inputs()
    e = add(mul(x, y), mul(y, z))  # add and mul are correct in C and in Python
    lnk = DualLinker(checker=_my_checker).accept(Env([x, y, z], [e]))
    fn = lnk.make_function()
    res = fn(7.2, 1.5, 3.0)
    assert res == 15.3


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_duallinker_mismatch():
    x, y, z = inputs()
    # bad_sub is correct in C but erroneous in Python
    e = bad_sub(mul(x, y), mul(y, z))
    g = Env([x, y, z], [e])
    lnk = DualLinker(checker=_my_checker).accept(g)
    fn = lnk.make_function()

    # good
    assert CLinker().accept(g).make_function()(1.0, 2.0, 3.0) == -4.0
    # good
    assert OpWiseCLinker().accept(g).make_function()(1.0, 2.0, 3.0) == -4.0

    # (purposely) wrong
    assert PerformLinker().accept(g).make_function()(1.0, 2.0, 3.0) == -10.0

    with pytest.raises(MyExc):
        # this runs OpWiseCLinker and PerformLinker in parallel and feeds
        # variables of matching operations to _my_checker to verify that they
        # are the same.
        fn(1.0, 2.0, 3.0)


################################
# Test that failure code works #
################################


class AddFail(Binary):
    def c_code(self, node, name, inp, out, sub):
        x, y = inp
        (z,) = out
        fail = sub["fail"]
        return f"""{z} = {x} + {y};
            PyErr_SetString(PyExc_RuntimeError, "failing here");
            {fail};"""

    def impl(self, x, y):
        return x + y


add_fail = AddFail()


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_c_fail_error():
    x, y, z = inputs()
    x = Constant(tdouble, 7.2, name="x")
    e = add_fail(mul(x, y), mul(y, z))
    lnk = OpWiseCLinker().accept(Env([y, z], [e]))
    fn = lnk.make_function()
    with pytest.raises(RuntimeError):
        fn(1.5, 3.0)


@pytest.mark.skipif(
    not theano.config.cxx, reason="G++ not available, so we need to skip this test."
)
def test_shared_input_output():
    # Test bug reported on the mailing list by Alberto Orlandi
    # https://groups.google.com/d/topic/theano-users/6dLaEqc2R6g/discussion
    # The shared variable is both an input and an output of the function.
    inc = theano.tensor.iscalar("inc")
    state = theano.shared(0)
    state.name = "state"
    linker = CLinker()
    mode = theano.Mode(linker=linker)
    f = theano.function([inc], state, updates=[(state, state + inc)], mode=mode)
    g = theano.function([inc], state, updates=[(state, state + inc)])

    # Initial value
    f0 = f(0)
    g0 = g(0)
    assert f0 == g0 == 0, (f0, g0)

    # Increment state via f, returns the previous value.
    f2 = f(2)
    assert f2 == f0, (f2, f0)
    f0 = f(0)
    g0 = g(0)
    assert f0 == g0 == 2, (f0, g0)

    # Increment state via g, returns the previous value
    g3 = g(3)
    assert g3 == g0, (g3, g0)
    f0 = f(0)
    g0 = g(0)
    assert f0 == g0 == 5, (f0, g0)

    vstate = theano.shared(np.zeros(3, dtype="int32"))
    vstate.name = "vstate"
    fv = theano.function([inc], vstate, updates=[(vstate, vstate + inc)], mode=mode)
    gv = theano.function([inc], vstate, updates=[(vstate, vstate + inc)])

    # Initial value
    fv0 = fv(0)
    gv0 = gv(0)
    assert np.all(fv0 == 0), fv0
    assert np.all(gv0 == 0), gv0

    # Increment state via f, returns the previous value.
    fv2 = fv(2)
    assert np.all(fv2 == fv0), (fv2, fv0)
    fv0 = fv(0)
    gv0 = gv(0)
    assert np.all(fv0 == 2), fv0
    assert np.all(gv0 == 2), gv0

    # Increment state via g, returns the previous value
    gv3 = gv(3)
    assert np.all(gv3 == gv0), (gv3, gv0)
    fv0 = fv(0)
    gv0 = gv(0)
    assert np.all(fv0 == 5), fv0
    assert np.all(gv0 == 5), gv0

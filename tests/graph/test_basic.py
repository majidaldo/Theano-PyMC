import pickle
from itertools import count

import numpy as np
import pytest

from theano import shared, tensor
from theano.graph.basic import (
    Apply,
    Variable,
    ancestors,
    applys_between,
    as_string,
    clone,
    equal_computations,
    general_toposort,
    graph_inputs,
    io_toposort,
    is_in_ancestors,
    list_of_nodes,
    orphans_between,
    vars_between,
    walk,
)
from theano.graph.op import Op
from theano.graph.type import Type


class MyType(Type):
    def __init__(self, thingy):
        self.thingy = thingy

    def filter(self, *args, **kwargs):
        raise NotImplementedError()

    def __eq__(self, other):
        return isinstance(other, MyType) and other.thingy == self.thingy

    def __str__(self):
        return f"R{self.thingy}"

    def __repr__(self):
        return f"R{self.thingy}"


def MyVariable(thingy):
    return Variable(MyType(thingy), None, None)


class MyOp(Op):

    __props__ = ()

    def make_node(self, *inputs):
        for input in inputs:
            assert isinstance(input, Variable)
            assert isinstance(input.type, MyType)
        outputs = [MyVariable(sum(input.type.thingy for input in inputs))]
        return Apply(self, list(inputs), outputs)

    def perform(self, *args, **kwargs):
        raise NotImplementedError("No Python implementation available.")


MyOp = MyOp()


class X:
    def leaf_formatter(self, leaf):
        return str(leaf.type)

    def node_formatter(self, node, argstrings):
        return f"{node.op}({', '.join(argstrings)})"

    def str(self, inputs, outputs):
        return as_string(
            inputs,
            outputs,
            leaf_formatter=self.leaf_formatter,
            node_formatter=self.node_formatter,
        )


class TestStr(X):
    def test_as_string(self):
        r1, r2 = MyVariable(1), MyVariable(2)
        node = MyOp.make_node(r1, r2)
        s = self.str([r1, r2], node.outputs)
        assert s == ["MyOp(R1, R2)"]

    def test_as_string_deep(self):
        r1, r2, r5 = MyVariable(1), MyVariable(2), MyVariable(5)
        node = MyOp.make_node(r1, r2)
        node2 = MyOp.make_node(node.outputs[0], r5)
        s = self.str([r1, r2, r5], node2.outputs)
        assert s == ["MyOp(MyOp(R1, R2), R5)"]

    def test_multiple_references(self):
        r1, r2, r5 = MyVariable(1), MyVariable(2), MyVariable(5)
        node = MyOp.make_node(r1, r2)
        node2 = MyOp.make_node(node.outputs[0], node.outputs[0])
        assert self.str([r1, r2, r5], node2.outputs) == ["MyOp(*1 -> MyOp(R1, R2), *1)"]

    def test_cutoff(self):
        r1, r2 = MyVariable(1), MyVariable(2)
        node = MyOp.make_node(r1, r2)
        node2 = MyOp.make_node(node.outputs[0], node.outputs[0])
        assert self.str(node.outputs, node2.outputs) == ["MyOp(R3, R3)"]
        assert self.str(node2.inputs, node2.outputs) == ["MyOp(R3, R3)"]


class TestClone(X):
    def test_accurate(self):
        r1, r2 = MyVariable(1), MyVariable(2)
        node = MyOp.make_node(r1, r2)
        _, new = clone([r1, r2], node.outputs, False)
        assert self.str([r1, r2], new) == ["MyOp(R1, R2)"]

    def test_copy(self):
        r1, r2, r5 = MyVariable(1), MyVariable(2), MyVariable(5)
        node = MyOp.make_node(r1, r2)
        node2 = MyOp.make_node(node.outputs[0], r5)
        _, new = clone([r1, r2, r5], node2.outputs, False)
        assert (
            node2.outputs[0].type == new[0].type and node2.outputs[0] is not new[0]
        )  # the new output is like the old one but not the same object
        assert node2 is not new[0].owner  # the new output has a new owner
        assert new[0].owner.inputs[1] is r5  # the inputs are not copied
        assert (
            new[0].owner.inputs[0].type == node.outputs[0].type
            and new[0].owner.inputs[0] is not node.outputs[0]
        )  # check that we copied deeper too

    def test_not_destructive(self):
        # Checks that manipulating a cloned graph leaves the original unchanged.
        r1, r2, r5 = MyVariable(1), MyVariable(2), MyVariable(5)
        node = MyOp.make_node(MyOp.make_node(r1, r2).outputs[0], r5)
        _, new = clone([r1, r2, r5], node.outputs, False)
        new_node = new[0].owner
        new_node.inputs = [MyVariable(7), MyVariable(8)]
        assert self.str(graph_inputs(new_node.outputs), new_node.outputs) == [
            "MyOp(R7, R8)"
        ]
        assert self.str(graph_inputs(node.outputs), node.outputs) == [
            "MyOp(MyOp(R1, R2), R5)"
        ]

    def test_constant(self):
        r1, r2, r5 = MyVariable(1), MyVariable(2), MyVariable(5)
        node = MyOp.make_node(MyOp.make_node(r1, r2).outputs[0], r5)
        _, new = clone([r1, r2, r5], node.outputs, False)
        new_node = new[0].owner
        new_node.inputs = [MyVariable(7), MyVariable(8)]
        c1 = tensor.constant(1.5)

        i, o = clone([c1], [c1])
        assert i[0] is not c1 and o[0] is not c1

        i, o = clone([c1], [c1], False)
        assert i[0] is c1 and o[0] is c1

        i, o = clone([c1], [c1], True, False)
        assert i[0] is not c1 and o[0] is not c1

        i, o = clone([c1], [c1], False, True)
        assert i[0] is c1 and o[0] is c1


def prenode(obj):
    if isinstance(obj, Variable):
        if obj.owner:
            return [obj.owner]
    if isinstance(obj, Apply):
        return obj.inputs


class TestToposort:
    def test_simple(self):
        # Test a simple graph
        r1, r2, r5 = MyVariable(1), MyVariable(2), MyVariable(5)
        o = MyOp(r1, r2)
        o.name = "o1"
        o2 = MyOp(o, r5)
        o2.name = "o2"

        clients = {}
        res = general_toposort([o2], prenode, clients=clients)

        assert clients == {
            o2.owner: [o2],
            o: [o2.owner],
            r5: [o2.owner],
            o.owner: [o],
            r1: [o.owner],
            r2: [o.owner],
        }
        assert res == [r5, r2, r1, o.owner, o, o2.owner, o2]

        with pytest.raises(ValueError):
            general_toposort(
                [o2], prenode, compute_deps_cache=lambda x: None, deps_cache=None
            )

        res = io_toposort([r5], [o2])
        assert res == [o.owner, o2.owner]

    def test_double_dependencies(self):
        # Test a graph with double dependencies
        r1, r5 = MyVariable(1), MyVariable(5)
        o = MyOp.make_node(r1, r1)
        o2 = MyOp.make_node(o.outputs[0], r5)
        all = general_toposort(o2.outputs, prenode)
        assert all == [r5, r1, o, o.outputs[0], o2, o2.outputs[0]]

    def test_inputs_owners(self):
        # Test a graph where the inputs have owners
        r1, r5 = MyVariable(1), MyVariable(5)
        o = MyOp.make_node(r1, r1)
        r2b = o.outputs[0]
        o2 = MyOp.make_node(r2b, r2b)
        all = io_toposort([r2b], o2.outputs)
        assert all == [o2]

        o2 = MyOp.make_node(r2b, r5)
        all = io_toposort([r2b], o2.outputs)
        assert all == [o2]

    def test_not_connected(self):
        # Test a graph which is not connected
        r1, r2, r3, r4 = MyVariable(1), MyVariable(2), MyVariable(3), MyVariable(4)
        o0 = MyOp.make_node(r1, r2)
        o1 = MyOp.make_node(r3, r4)
        all = io_toposort([r1, r2, r3, r4], o0.outputs + o1.outputs)
        assert all == [o1, o0] or all == [o0, o1]

    def test_io_chain(self):
        # Test inputs and outputs mixed together in a chain graph
        r1, r2 = MyVariable(1), MyVariable(2)
        o0 = MyOp.make_node(r1, r2)
        o1 = MyOp.make_node(o0.outputs[0], r1)
        all = io_toposort([r1, o0.outputs[0]], [o0.outputs[0], o1.outputs[0]])
        assert all == [o1]

    def test_outputs_clients(self):
        # Test when outputs have clients
        r1, r2, r4 = MyVariable(1), MyVariable(2), MyVariable(4)
        o0 = MyOp.make_node(r1, r2)
        MyOp.make_node(o0.outputs[0], r4)
        all = io_toposort([], o0.outputs)
        assert all == [o0]


class TestEval:
    def setup_method(self):
        self.x, self.y = tensor.scalars("x", "y")
        self.z = self.x + self.y
        self.w = 2 * self.z

    def test_eval(self):
        assert self.w.eval({self.x: 1.0, self.y: 2.0}) == 6.0
        assert self.w.eval({self.z: 3}) == 6.0
        assert hasattr(self.w, "_fn_cache"), "variable must have cache after eval"
        assert not hasattr(
            pickle.loads(pickle.dumps(self.w)), "_fn_cache"
        ), "temporary functions must not be serialized"


class TestAutoName:
    def test_auto_name(self):
        # Get counter value
        autoname_id = next(Variable.__count__)
        Variable.__count__ = count(autoname_id)
        r1, r2 = MyVariable(1), MyVariable(2)
        assert r1.auto_name == "auto_" + str(autoname_id)
        assert r2.auto_name == "auto_" + str(autoname_id + 1)

    def test_constant(self):
        # Get counter value
        autoname_id = next(Variable.__count__)
        Variable.__count__ = count(autoname_id)
        r1 = tensor.constant(1.5)
        assert r1.auto_name == "auto_" + str(autoname_id), (
            r1.auto_name,
            "auto_" + str(autoname_id),
        )

        r3 = tensor.constant(1.6)
        assert r3.auto_name == "auto_" + str(autoname_id + 1)

    def test_tensorvariable(self):
        # Get counter value
        autoname_id = next(Variable.__count__)
        Variable.__count__ = count(autoname_id)
        r1 = tensor.TensorType(dtype="int32", broadcastable=())("myvar")
        r2 = tensor.TensorVariable(tensor.TensorType(dtype="int32", broadcastable=()))
        r3 = shared(np.random.randn(3, 4))
        assert r1.auto_name == "auto_" + str(autoname_id)
        assert r2.auto_name == "auto_" + str(autoname_id + 1)
        assert r3.auto_name == "auto_" + str(autoname_id + 2)

    def test_clone(self):
        # Get counter value
        autoname_id = next(Variable.__count__)
        Variable.__count__ = count(autoname_id)
        r1 = MyVariable(1)
        r2 = r1.clone()
        assert r1.auto_name == "auto_" + str(autoname_id)
        assert r2.auto_name == "auto_" + str(autoname_id + 1)


def test_equal_computations():

    a, b = tensor.iscalars(2)

    with pytest.raises(ValueError):
        equal_computations([a], [a, b])

    assert equal_computations([a], [a])
    assert equal_computations([tensor.as_tensor(1)], [tensor.as_tensor(1)])
    assert not equal_computations([b], [a])
    assert not equal_computations([tensor.as_tensor(1)], [tensor.as_tensor(2)])

    assert equal_computations([2], [2])
    assert equal_computations([np.r_[2, 1]], [np.r_[2, 1]])
    assert equal_computations([np.r_[2, 1]], [tensor.as_tensor(np.r_[2, 1])])
    assert equal_computations([tensor.as_tensor(np.r_[2, 1])], [np.r_[2, 1]])

    assert not equal_computations([2], [a])
    assert not equal_computations([np.r_[2, 1]], [a])
    assert not equal_computations([a], [2])
    assert not equal_computations([a], [np.r_[2, 1]])

    c = tensor.type_other.NoneConst
    assert equal_computations([c], [c])

    m = tensor.matrix()
    max_argmax1 = tensor.max_and_argmax(m)
    max_argmax2 = tensor.max_and_argmax(m)
    assert equal_computations(max_argmax1, max_argmax2)


def test_walk():

    r1, r2, r3 = MyVariable(1), MyVariable(2), MyVariable(3)
    o1 = MyOp(r1, r2)
    o1.name = "o1"
    o2 = MyOp(r3, o1)
    o2.name = "o2"

    def expand(r):
        if r.owner:
            return r.owner.inputs

    res = walk([o2], expand, bfs=True, return_children=False)
    res_list = list(res)
    assert res_list == [o2, r3, o1, r1, r2]

    res = walk([o2], expand, bfs=False, return_children=False)
    res_list = list(res)
    assert res_list == [o2, o1, r2, r1, r3]

    res = walk([o2], expand, bfs=True, return_children=True)
    res_list = list(res)
    assert res_list == [
        (o2, [r3, o1]),
        (r3, None),
        (o1, [r1, r2]),
        (r1, None),
        (r2, None),
    ]


def test_ancestors():

    r1, r2, r3 = MyVariable(1), MyVariable(2), MyVariable(3)
    o1 = MyOp(r1, r2)
    o1.name = "o1"
    o2 = MyOp(r3, o1)
    o2.name = "o2"

    res = ancestors([o2], blockers=None)
    res_list = list(res)
    assert res_list == [o2, r3, o1, r1, r2]

    res = ancestors([o2], blockers=None)
    assert r3 in res
    res_list = list(res)
    assert res_list == [o1, r1, r2]

    res = ancestors([o2], blockers=[o1])
    res_list = list(res)
    assert res_list == [o2, r3, o1]


def test_graph_inputs():

    r1, r2, r3 = MyVariable(1), MyVariable(2), MyVariable(3)
    o1 = MyOp(r1, r2)
    o1.name = "o1"
    o2 = MyOp(r3, o1)
    o2.name = "o2"

    res = graph_inputs([o2], blockers=None)
    res_list = list(res)
    assert res_list == [r3, r1, r2]


def test_variables_and_orphans():

    r1, r2, r3 = MyVariable(1), MyVariable(2), MyVariable(3)
    o1 = MyOp(r1, r2)
    o1.name = "o1"
    o2 = MyOp(r3, o1)
    o2.name = "o2"

    vars_res = vars_between([r1, r2], [o2])
    orphans_res = orphans_between([r1, r2], [o2])

    vars_res_list = list(vars_res)
    orphans_res_list = list(orphans_res)
    assert vars_res_list == [o2, o1, r3, r2, r1]
    assert orphans_res_list == [r3]


def test_ops():

    r1, r2, r3, r4 = MyVariable(1), MyVariable(2), MyVariable(3), MyVariable(4)
    o1 = MyOp(r1, r2)
    o1.name = "o1"
    o2 = MyOp(r3, r4)
    o2.name = "o2"
    o3 = MyOp(r3, o1, o2)
    o3.name = "o3"

    res = applys_between([r1, r2], [o3])
    res_list = list(res)
    assert res_list == [o3.owner, o2.owner, o1.owner]


def test_list_of_nodes():

    r1, r2, r3 = MyVariable(1), MyVariable(2), MyVariable(3)
    o1 = MyOp(r1, r2)
    o1.name = "o1"
    o2 = MyOp(r3, o1)
    o2.name = "o2"

    res = list_of_nodes([r1, r2], [o2])
    assert res == [o2.owner, o1.owner]


def test_is_in_ancestors():

    r1, r2, r3 = MyVariable(1), MyVariable(2), MyVariable(3)
    o1 = MyOp(r1, r2)
    o1.name = "o1"
    o2 = MyOp(r3, o1)
    o2.name = "o2"

    assert is_in_ancestors(o2.owner, o1.owner)


@pytest.mark.xfail(reason="Not implemented")
def test_io_connection_pattern():
    raise AssertionError()


@pytest.mark.xfail(reason="Not implemented")
def test_view_roots():
    raise AssertionError()

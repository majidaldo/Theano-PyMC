#!/usr/bin/env python
# Theano tutorial
# Solution to Exercise in section 'Extending Theano'
import unittest

import theano
from theano.graph.basic import Apply
from theano.graph.op import Op


# 1. Op returns x * y


class ProdOp(Op):
    def make_node(self, x, y):
        x = theano.tensor.as_tensor_variable(x)
        y = theano.tensor.as_tensor_variable(y)
        outdim = x.ndim
        output = theano.tensor.TensorType(
            dtype=theano.scalar.upcast(x.dtype, y.dtype), broadcastable=[False] * outdim
        )()
        return Apply(self, inputs=[x, y], outputs=[output])

    def perform(self, node, inputs, output_storage):
        x, y = inputs
        z = output_storage[0]
        z[0] = x * y

    def infer_shape(self, fgraph, node, i0_shapes):
        return [i0_shapes[0]]

    def grad(self, inputs, output_grads):
        return [output_grads[0] * inputs[1], output_grads[0] * inputs[0]]


# 2. Op returns x + y and x - y


class SumDiffOp(Op):
    def make_node(self, x, y):
        x = theano.tensor.as_tensor_variable(x)
        y = theano.tensor.as_tensor_variable(y)
        outdim = x.ndim
        output1 = theano.tensor.TensorType(
            dtype=theano.scalar.upcast(x.dtype, y.dtype), broadcastable=[False] * outdim
        )()
        output2 = theano.tensor.TensorType(
            dtype=theano.scalar.upcast(x.dtype, y.dtype), broadcastable=[False] * outdim
        )()
        return Apply(self, inputs=[x, y], outputs=[output1, output2])

    def perform(self, node, inputs, output_storage):
        x, y = inputs
        z1, z2 = output_storage
        z1[0] = x + y
        z2[0] = x - y

    def infer_shape(self, fgraph, node, i0_shapes):
        return [i0_shapes[0], i0_shapes[0]]

    def grad(self, inputs, output_grads):
        og1, og2 = output_grads
        if og1 is None:
            og1 = theano.tensor.zeros_like(og2)
        if og2 is None:
            og2 = theano.tensor.zeros_like(og1)
        return [og1 + og2, og1 - og2]


# 3. Testing apparatus

import numpy as np

from tests import unittest_tools as utt
from theano import function, printing, tensor
from theano.graph.basic import Apply
from theano.graph.op import Op


class TestProdOp(utt.InferShapeTester):

    rng = np.random.RandomState(43)

    def setup_method(self):
        super().setup_method()
        self.op_class = ProdOp  # case 1

    def test_perform(self):
        x = theano.tensor.matrix()
        y = theano.tensor.matrix()
        f = theano.function([x, y], self.op_class()(x, y))
        x_val = np.random.rand(5, 4)
        y_val = np.random.rand(5, 4)
        out = f(x_val, y_val)
        assert np.allclose(x_val * y_val, out)

    def test_gradient(self):
        utt.verify_grad(
            self.op_class(),
            [np.random.rand(5, 4), np.random.rand(5, 4)],
            n_tests=1,
            rng=TestProdOp.rng,
        )

    def test_infer_shape(self):
        x = tensor.dmatrix()
        y = tensor.dmatrix()

        self._compile_and_check(
            [x, y],
            [self.op_class()(x, y)],
            [np.random.rand(5, 6), np.random.rand(5, 6)],
            self.op_class,
        )


class TestSumDiffOp(utt.InferShapeTester):

    rng = np.random.RandomState(43)

    def setup_method(self):
        super().setup_method()
        self.op_class = SumDiffOp

    def test_perform(self):
        x = theano.tensor.matrix()
        y = theano.tensor.matrix()
        f = theano.function([x, y], self.op_class()(x, y))
        x_val = np.random.rand(5, 4)
        y_val = np.random.rand(5, 4)
        out = f(x_val, y_val)
        assert np.allclose([x_val + y_val, x_val - y_val], out)

    def test_gradient(self):
        def output_0(x, y):
            return self.op_class()(x, y)[0]

        def output_1(x, y):
            return self.op_class()(x, y)[1]

        utt.verify_grad(
            output_0,
            [np.random.rand(5, 4), np.random.rand(5, 4)],
            n_tests=1,
            rng=TestSumDiffOp.rng,
        )
        utt.verify_grad(
            output_1,
            [np.random.rand(5, 4), np.random.rand(5, 4)],
            n_tests=1,
            rng=TestSumDiffOp.rng,
        )

    def test_infer_shape(self):
        x = tensor.dmatrix()
        y = tensor.dmatrix()

        # adapt the choice of the next instruction to the op under test

        self._compile_and_check(
            [x, y],
            self.op_class()(x, y),
            [np.random.rand(5, 6), np.random.rand(5, 6)],
            self.op_class,
        )


import numpy as np

# as_op exercice
import theano
from theano.compile.ops import as_op


def infer_shape_numpy_dot(fgraph, node, input_shapes):
    ashp, bshp = input_shapes
    return [ashp[:-1] + bshp[-1:]]


@as_op(
    itypes=[theano.tensor.fmatrix, theano.tensor.fmatrix],
    otypes=[theano.tensor.fmatrix],
    infer_shape=infer_shape_numpy_dot,
)
def numpy_add(a, b):
    return np.add(a, b)


def infer_shape_numpy_add_sub(fgraph, node, input_shapes):
    ashp, bshp = input_shapes
    # Both inputs should have that same shape, so we just return one of them.
    return [ashp[0]]


@as_op(
    itypes=[theano.tensor.fmatrix, theano.tensor.fmatrix],
    otypes=[theano.tensor.fmatrix],
    infer_shape=infer_shape_numpy_add_sub,
)
def numpy_add(a, b):
    return np.add(a, b)


@as_op(
    itypes=[theano.tensor.fmatrix, theano.tensor.fmatrix],
    otypes=[theano.tensor.fmatrix],
    infer_shape=infer_shape_numpy_add_sub,
)
def numpy_sub(a, b):
    return np.sub(a, b)


if __name__ == "__main__":
    unittest.main()

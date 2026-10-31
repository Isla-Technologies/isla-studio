"""Small neural networks in NumPy: what a trained policy is made of.

A policy is a multilayer perceptron: layers of weighted sums, each followed by a
smooth bend (ELU), turning what the robot senses into what its motors do. A
running normalizer first rescales every input to a similar size, and training
nudges the weights with Adam. NumPy alone runs them, so the robot's computer
needs nothing more to run a policy. Training on an NVIDIA GPU keeps the same
arrays in CuPy instead; every function here works on either.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from .device import module_of, to_numpy

Array = NDArray[np.floating]


def _orthogonal(rows: int, cols: int, gain: float, rng: np.random.Generator) -> NDArray:
    """A (rows, cols) matrix with orthonormal rows or columns, times ``gain``."""
    a = rng.standard_normal((max(rows, cols), min(rows, cols)))
    q, r = np.linalg.qr(a)
    q *= np.sign(np.diag(r))
    if rows < cols:
        q = q.T
    return gain * q[:rows, :cols]


def elu(x: Array) -> Array:
    """x where positive, else e^x - 1; the larger of the two is always the right one."""
    xp = module_of(x)
    out = xp.minimum(x, 0)
    xp.exp(out, out=out)  # Five times quicker than expm1 in single precision.
    out -= 1
    return xp.maximum(x, out, out=out)


class MLP:
    """Layers of ``x @ weight + bias``, with ELU between them but not after the last."""

    def __init__(
        self,
        sizes: Sequence[int],
        rng: np.random.Generator | None = None,
        output_gain: float = 1.0,
        dtype=np.float32,
        xp=np,
    ) -> None:
        if len(sizes) < 2:
            raise ValueError("A network needs an input and an output size")
        rng = rng if rng is not None else np.random.default_rng()
        self.sizes = tuple(int(size) for size in sizes)
        self.weights: list[Array] = []
        self.biases: list[Array] = []
        last = len(self.sizes) - 2
        for index, (a, b) in enumerate(zip(self.sizes[:-1], self.sizes[1:], strict=True)):
            gain = output_gain if index == last else math.sqrt(2.0)
            self.weights.append(xp.asarray(_orthogonal(a, b, gain, rng).astype(dtype)))
            self.biases.append(xp.zeros(b, dtype))

    @property
    def params(self) -> list[Array]:
        """Every weight and bias, in order; training updates these arrays in place."""
        return [p for pair in zip(self.weights, self.biases, strict=True) for p in pair]

    def __call__(self, x: Array) -> Array:
        for weight, bias in zip(self.weights[:-1], self.biases[:-1], strict=True):
            x = elu(x @ weight + bias)
        return x @ self.weights[-1] + self.biases[-1]

    def _scratch(self, rows: int, dtype) -> dict[str, list[Array]]:
        """Arrays reused by every training pass of this many rows.

        Fresh arrays this large cost more to allocate than to compute with, so
        :meth:`forward` and :meth:`backward` return these, overwritten next time.
        """
        xp = module_of(self.weights[0])
        buffers = self.__dict__.setdefault("_buffers", {})
        key = (rows, np.dtype(dtype).str)
        scratch = buffers.get(key)
        if scratch is None:
            buffers.clear()  # Keep only the latest size.
            scratch = {
                "outputs": [xp.empty((rows, w.shape[1]), dtype) for w in self.weights],
                "grads": [xp.empty((rows, w.shape[0]), dtype) for w in self.weights],
                "work": [xp.empty((rows, w.shape[0]), dtype) for w in self.weights],
                "params": [xp.empty_like(p, dtype=dtype) for p in self.params],
            }
            buffers[key] = scratch
        return scratch

    def forward(self, x: Array) -> tuple[Array, list[Array]]:
        """The output, and what :meth:`backward` needs: each layer's input."""
        xp = module_of(x)
        scratch = self._scratch(x.shape[0], np.result_type(x.dtype, self.weights[0].dtype))
        inputs = [x]
        last = len(self.weights) - 1
        for index, (weight, bias) in enumerate(zip(self.weights, self.biases, strict=True)):
            out = scratch["outputs"][index]
            xp.matmul(x, weight, out=out)
            out += bias
            if index < last:
                work = scratch["work"][index + 1]
                xp.minimum(out, 0, out=work)
                xp.exp(work, out=work)
                work -= 1
                xp.maximum(out, work, out=out)  # ELU, in place.
                inputs.append(out)
            x = out
        return x, inputs

    def backward(self, grad: Array, inputs: list[Array]) -> list[Array]:
        """Gradients of every parameter, in :attr:`params` order, from the output's."""
        xp = module_of(grad)
        scratch = self._scratch(grad.shape[0], np.result_type(grad.dtype, inputs[0].dtype))
        params = scratch["params"]
        for layer in range(len(self.weights) - 1, -1, -1):
            x = inputs[layer]
            xp.sum(grad, axis=0, out=params[2 * layer + 1])
            xp.matmul(x.T, grad, out=params[2 * layer])
            if layer:
                below = scratch["grads"][layer]
                xp.matmul(grad, self.weights[layer].T, out=below)
                # ELU's slope: 1 where its input was positive, else its output plus 1.
                work = scratch["work"][layer]
                xp.add(x, 1, out=work)
                xp.minimum(work, 1, out=work)
                below *= work
                grad = below
        return list(params)

    def __deepcopy__(self, memo) -> MLP:
        copy = MLP.__new__(MLP)
        copy.sizes = self.sizes
        copy.weights = [w.copy() for w in self.weights]
        copy.biases = [b.copy() for b in self.biases]
        return copy

    def numpy(self) -> MLP:
        """A copy on this computer, as a policy keeps it, wherever this one trains."""
        copy = MLP.__new__(MLP)
        copy.sizes = self.sizes
        copy.weights = [np.array(to_numpy(w), dtype=np.float32) for w in self.weights]
        copy.biases = [np.array(to_numpy(b), dtype=np.float32) for b in self.biases]
        return copy

    def state(self, prefix: str) -> dict[str, Array]:
        state = {}
        for index, (weight, bias) in enumerate(zip(self.weights, self.biases, strict=True)):
            state[f"{prefix}.{index}.weight"] = weight
            state[f"{prefix}.{index}.bias"] = bias
        return state

    @classmethod
    def from_state(cls, state: dict[str, Array], prefix: str) -> MLP:
        weights = []
        index = 0
        while f"{prefix}.{index}.weight" in state:
            weights.append((state[f"{prefix}.{index}.weight"], state[f"{prefix}.{index}.bias"]))
            index += 1
        if not weights:
            raise ValueError(f"No layers named {prefix}")
        network = cls.__new__(cls)
        network.weights = [np.array(w, dtype=np.float32) for w, _ in weights]
        network.biases = [np.array(b, dtype=np.float32) for _, b in weights]
        network.sizes = (network.weights[0].shape[0], *(w.shape[1] for w in network.weights))
        return network


class Normalizer:
    """Running mean and spread of inputs; scales each to mean 0 and spread 1."""

    def __init__(self, size: int, clip: float = 10.0, xp=np) -> None:
        self.xp = xp
        self.mean = xp.zeros(size)
        self.var = xp.ones(size)
        self.count = 0.0
        self.clip = clip

    def update(self, batch: Array) -> None:
        """Fold a batch of inputs, one per row, into the running statistics."""
        batch = self.xp.asarray(batch, dtype=np.float64).reshape(-1, self.mean.size)
        count = batch.shape[0]
        if count == 0:
            return
        mean, var = batch.mean(axis=0), batch.var(axis=0)
        total = self.count + count
        delta = mean - self.mean
        self.mean = self.mean + delta * (count / total)
        self.var = (
            self.var * self.count + var * count + delta**2 * (self.count * count / total)
        ) / total
        self.count = total

    @property
    def std(self) -> NDArray[np.float64]:
        return self.xp.sqrt(self.var + 1e-8)

    def __call__(self, x: Array) -> NDArray[np.float32]:
        xp = self.xp
        scaled = (xp.asarray(x, dtype=np.float64) - self.mean) / self.std
        return xp.clip(scaled, -self.clip, self.clip).astype(np.float32)

    def state(self, prefix: str) -> dict[str, NDArray]:
        return {
            f"{prefix}.mean": self.mean,
            f"{prefix}.var": self.var,
            f"{prefix}.count": np.array(self.count),
        }

    def load_state(self, state: dict[str, NDArray], prefix: str) -> None:
        self.mean = self.xp.asarray(to_numpy(state[f"{prefix}.mean"]), dtype=np.float64)
        self.var = self.xp.asarray(to_numpy(state[f"{prefix}.var"]), dtype=np.float64)
        self.count = float(state[f"{prefix}.count"])


_ADAM_KERNELS: dict = {}


def _adam_kernel(xp):
    """Adam's step for one parameter as a single GPU kernel: launching is the cost there."""
    kernel = _ADAM_KERNELS.get(xp)
    if kernel is None:
        kernel = _ADAM_KERNELS[xp] = xp.ElementwiseKernel(
            "T grad, T beta1, T beta2, T scale, T eps",
            "T param, T first, T second",
            """
            first = beta1 * first + (1 - beta1) * grad;
            second = beta2 * second + (1 - beta2) * grad * grad;
            param -= scale * first / (sqrt(second) + eps);
            """,
            "isla_adam_step",
        )
    return kernel


class Adam:
    """Adam: each parameter steps by its gradient's running average over its spread."""

    def __init__(
        self, params: Sequence[Array], betas: tuple[float, float] = (0.9, 0.999), eps=1e-8
    ) -> None:
        self.params = list(params)
        self.betas = betas
        self.eps = eps
        self.xp = xp = module_of(self.params[0])
        self.first = [xp.zeros_like(p) for p in self.params]
        self.second = [xp.zeros_like(p) for p in self.params]
        self.steps = 0

    def step(self, grads: Sequence[Array], learning_rate: float) -> None:
        beta1, beta2 = self.betas
        self.steps += 1
        scale = learning_rate * math.sqrt(1 - beta2**self.steps) / (1 - beta1**self.steps)
        if self.xp is not np:
            kernel = _adam_kernel(self.xp)
            for param, grad, first, second in zip(
                self.params, grads, self.first, self.second, strict=True
            ):
                kind = param.dtype.type
                numbers = (kind(beta1), kind(beta2), kind(scale), kind(self.eps))
                kernel(grad, *numbers, param, first, second)
            return
        for param, grad, first, second in zip(
            self.params, grads, self.first, self.second, strict=True
        ):
            first *= beta1
            first += (1 - beta1) * grad
            second *= beta2
            second += (1 - beta2) * grad * grad
            param -= (scale * first / (self.xp.sqrt(second) + self.eps)).astype(param.dtype)

    def state(self) -> dict[str, NDArray]:
        state = {"adam.steps": np.array(self.steps)}
        for index, (first, second) in enumerate(zip(self.first, self.second, strict=True)):
            state[f"adam.{index}.first"] = first
            state[f"adam.{index}.second"] = second
        return state

    def load_state(self, state: dict[str, NDArray]) -> None:
        self.steps = int(state["adam.steps"])
        for index in range(len(self.params)):
            self.first[index][...] = self.xp.asarray(to_numpy(state[f"adam.{index}.first"]))
            self.second[index][...] = self.xp.asarray(to_numpy(state[f"adam.{index}.second"]))


def clip_norm(grads: list[Array], limit: float) -> float:
    """Scale gradients down, in place, so their combined length is at most ``limit``."""
    xp = module_of(grads[0])
    if xp is np:
        norm = math.sqrt(float(sum(np.sum(g.astype(np.float64) ** 2) for g in grads)))
    else:  # One dot product each and one sum: launching kernels is what costs on a GPU.
        squares = xp.stack([xp.vdot(g.ravel(), g.ravel()) for g in grads])
        norm = math.sqrt(float(squares.astype(np.float64).sum()))
    if norm > limit > 0:
        for g in grads:
            g *= limit / (norm + 1e-6)
    return norm

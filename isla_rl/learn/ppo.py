"""Proximal policy optimisation (PPO): how the robot learns from its own tries.

The policy (actor) picks motor targets and adds a little random exploration.
Many simulated robots try it at once; each move earns a reward. The critic
learns to predict the rewards still to come, so each move can be judged as
better or worse than expected (its advantage). PPO then makes better moves
likelier, but only by a small, clipped step each time, so learning stays
steady. This is the algorithm legged-robot teams use: Unitree, ETH's
legged_gym and rsl_rl, and MuJoCo Playground among them. It runs on the
processor, or on an NVIDIA GPU with its arrays in CuPy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .device import CPU, Random, array_module, module_of, to_numpy
from .network import MLP, Adam, Normalizer, clip_norm

_LOG_2PI = math.log(2 * math.pi)


@dataclass(frozen=True, slots=True)
class PPOSettings:
    """How the learner learns. The defaults follow legged robot practice (rsl_rl)."""

    hidden: tuple[int, ...] = (256, 128, 64)
    learning_rate: float = 1e-3
    desired_kl: float = 0.01  # The learning rate adapts to change the policy this much.
    clip: float = 0.2
    value_coef: float = 1.0
    entropy_coef: float = 0.01
    gamma: float = 0.99
    lam: float = 0.95
    epochs: int = 5
    minibatches: int = 4
    max_grad_norm: float = 1.0
    init_std: float = 1.0
    min_std: float = 0.02
    # The entropy bonus raises the action noise a little every update. Once the noisy actions
    # reach the clip, nothing pushes back and it runs away (a jump run reached 20), so it is capped.
    max_std: float = 1.0

    def __post_init__(self) -> None:
        if not self.hidden or any(size < 1 for size in self.hidden):
            raise ValueError("Every hidden layer needs at least one unit")
        for name in ("learning_rate", "clip", "init_std", "min_std", "max_std"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")
        if self.max_std < self.min_std:
            raise ValueError("max_std must be at least min_std")
        if not (0 < self.gamma <= 1 and 0 <= self.lam <= 1):
            raise ValueError("gamma must be in (0, 1] and lam in [0, 1]")
        if self.epochs < 1 or self.minibatches < 1:
            raise ValueError("PPO needs at least one epoch and one minibatch")


class Rollout:
    """What the robots did over ``steps`` moves: inputs, actions, and what they earned."""

    def __init__(self, steps: int, envs: int, obs: int, critic: int, actions: int, xp=np) -> None:
        self.obs = xp.zeros((steps, envs, obs), np.float32)
        self.critic_obs = xp.zeros((steps, envs, critic), np.float32)
        self.actions = xp.zeros((steps, envs, actions), np.float32)
        self.means = xp.zeros((steps, envs, actions), np.float32)
        self.log_probs = xp.zeros((steps, envs), np.float32)
        self.values = xp.zeros((steps, envs), np.float32)
        self.rewards = xp.zeros((steps, envs), np.float32)
        self.dones = xp.zeros((steps, envs), np.float32)
        self.advantages = xp.zeros((steps, envs), np.float32)
        self.returns = xp.zeros((steps, envs), np.float32)
        self.step = 0

    @property
    def full(self) -> bool:
        return self.step == self.obs.shape[0]


def advantages(
    rewards: NDArray,
    values: NDArray,
    dones: NDArray,
    last_values: NDArray,
    gamma: float,
    lam: float,
) -> NDArray:
    """Generalised advantage estimates (GAE), for ``steps`` by ``envs`` arrays."""
    xp = module_of(values)
    result = xp.zeros_like(values)
    running = xp.zeros_like(last_values)
    for step in range(values.shape[0] - 1, -1, -1):
        following = last_values if step == values.shape[0] - 1 else values[step + 1]
        alive = 1.0 - dones[step]
        delta = rewards[step] + gamma * following * alive - values[step]
        running = delta + gamma * lam * alive * running
        result[step] = running
    return result


class PPO:
    """An actor, a critic, their input normalizers, and the optimiser training them."""

    def __init__(
        self,
        obs_size: int,
        critic_size: int,
        action_size: int,
        settings: PPOSettings | None = None,
        seed: int | None = None,
        device: str = CPU,
    ) -> None:
        self.settings = settings = settings if settings is not None else PPOSettings()
        self.device = device
        self.xp = xp = array_module(device)
        start = np.random.default_rng(seed)  # The first weights: the same on either device.
        self.rng = Random(start) if xp is np else Random.seeded(seed, xp)
        hidden = settings.hidden
        self.actor = MLP([obs_size, *hidden, action_size], start, output_gain=0.01, xp=xp)
        self.critic = MLP([critic_size, *hidden, 1], start, output_gain=1.0, xp=xp)
        self.log_std = xp.full(action_size, math.log(settings.init_std), np.float32)
        self.obs_normalizer = Normalizer(obs_size, xp=xp)
        self.critic_normalizer = Normalizer(critic_size, xp=xp)
        self.optimizer = Adam([*self.actor.params, *self.critic.params, self.log_std])
        self.learning_rate = settings.learning_rate
        self.updates = 0

    @property
    def std(self) -> NDArray[np.float32]:
        return self.xp.exp(self.log_std)

    def normalize(self, obs: NDArray, critic_obs: NDArray) -> tuple[NDArray, NDArray]:
        """Scale inputs with the statistics so far, then fold these inputs into them."""
        scaled = self.obs_normalizer(obs), self.critic_normalizer(critic_obs)
        self.obs_normalizer.update(obs)
        self.critic_normalizer.update(critic_obs)
        return scaled

    def act(self, obs: NDArray) -> tuple[NDArray, NDArray, NDArray]:
        """Explore: actions, their log-probabilities, and the policy's means, for scaled obs."""
        mean = self.actor(obs)
        std = self.std
        noise = self.rng.normal(mean.shape)
        actions = mean + std * noise
        log_probs = (
            -0.5 * self.xp.sum(noise * noise, axis=1)
            - self.log_std.sum()
            - 0.5 * _LOG_2PI * (mean.shape[1])
        )
        return actions, log_probs.astype(np.float32), mean

    def value(self, critic_obs: NDArray) -> NDArray:
        return self.critic(critic_obs)[:, 0]

    def finish(self, rollout: Rollout, last_critic_obs: NDArray) -> None:
        """Work out each move's advantage and return once the rollout is full."""
        last = self.value(last_critic_obs)
        s = self.settings
        rollout.advantages[:] = advantages(
            rollout.rewards, rollout.values, rollout.dones, last, s.gamma, s.lam
        )
        rollout.returns[:] = rollout.advantages + rollout.values

    def losses(
        self, batch: dict[str, NDArray], old_std: NDArray
    ) -> tuple[dict[str, float], list[NDArray]]:
        """The loss parts on a minibatch, and the gradient of their weighted sum.

        ``kl`` among the parts says how far the policy has moved from the one that
        acted (``old_std`` is its spread); it takes no part in the gradient.
        """
        s, xp = self.settings, self.xp
        count = batch["obs"].shape[0]
        mean, actor_inputs = self.actor.forward(batch["obs"])
        std = self.std
        kl = xp.sum(
            xp.log(std / old_std)
            + (old_std**2 + (batch["means"] - mean) ** 2) / (2.0 * std**2)
            - 0.5,
            axis=1,
        )
        z = (batch["actions"] - mean) / std
        log_probs = -0.5 * xp.sum(z * z, axis=1) - self.log_std.sum() - 0.5 * _LOG_2PI * z.shape[1]
        ratio = xp.exp(xp.clip(log_probs - batch["log_probs"], -20.0, 20.0))
        advantage = batch["advantages"]
        unclipped = ratio * advantage
        clipped = xp.clip(ratio, 1 - s.clip, 1 + s.clip) * advantage
        surrogate = -float(xp.mean(xp.minimum(unclipped, clipped)))
        # Only moves whose unclipped term is the smaller one pass a gradient.
        passing = (unclipped <= clipped).astype(np.float32)
        d_log_probs = -(ratio * advantage * passing) / count
        d_mean = d_log_probs[:, None] * z / std
        d_log_std = xp.sum(d_log_probs[:, None] * (z * z - 1.0), axis=0)
        entropy = float(xp.sum(self.log_std) + 0.5 * (1.0 + _LOG_2PI) * self.log_std.size)
        d_log_std = d_log_std - s.entropy_coef

        value, critic_inputs = self.critic.forward(batch["critic_obs"])
        value = value[:, 0]
        old = batch["values"]
        moved = value - old
        value_clipped = old + xp.clip(moved, -s.clip, s.clip)
        error, error_clipped = value - batch["returns"], value_clipped - batch["returns"]
        use_plain = error**2 >= error_clipped**2
        value_loss = float(xp.mean(xp.where(use_plain, error**2, error_clipped**2)))
        inside = xp.abs(moved) < s.clip
        d_value = xp.where(use_plain, 2 * error, 2 * error_clipped * inside) * (
            s.value_coef / count
        )

        grads = [
            *self.actor.backward(d_mean.astype(np.float32), actor_inputs),
            *self.critic.backward(d_value[:, None].astype(np.float32), critic_inputs),
            d_log_std.astype(np.float32),
        ]
        parts = {
            "surrogate": surrogate,
            "value": value_loss,
            "entropy": entropy,
            "loss": surrogate + s.value_coef * value_loss - s.entropy_coef * entropy,
            "kl": float(xp.mean(kl)),
        }
        return parts, grads

    def update(self, rollout: Rollout) -> dict[str, float]:
        """Improve the actor and critic from a finished rollout; returns averages."""
        s = self.settings
        flat = {
            "obs": rollout.obs.reshape(-1, rollout.obs.shape[-1]),
            "critic_obs": rollout.critic_obs.reshape(-1, rollout.critic_obs.shape[-1]),
            "actions": rollout.actions.reshape(-1, rollout.actions.shape[-1]),
            "means": rollout.means.reshape(-1, rollout.means.shape[-1]),
            "log_probs": rollout.log_probs.reshape(-1),
            "values": rollout.values.reshape(-1),
            "returns": rollout.returns.reshape(-1),
            "advantages": rollout.advantages.reshape(-1),
        }
        advantage = flat["advantages"]
        flat["advantages"] = ((advantage - advantage.mean()) / (advantage.std() + 1e-8)).astype(
            np.float32
        )
        old_std = self.std.copy()
        size = flat["obs"].shape[0]
        chunk = max(1, size // s.minibatches)
        totals = {"surrogate": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0}
        count = 0
        for _ in range(s.epochs):
            order = self.rng.permutation(size)
            for start in range(0, chunk * s.minibatches, chunk):
                pick = order[start : start + chunk]
                batch = {key: value[pick] for key, value in flat.items()}
                parts, grads = self.losses(batch, old_std)
                kl = parts["kl"]
                if s.desired_kl > 0:
                    if kl > 2.0 * s.desired_kl:
                        self.learning_rate = max(1e-5, self.learning_rate / 1.5)
                    elif 0.0 < kl < s.desired_kl / 2.0:
                        self.learning_rate = min(1e-2, self.learning_rate * 1.5)
                clip_norm(grads, s.max_grad_norm)
                self.optimizer.step(grads, self.learning_rate)
                self.xp.clip(self.log_std, math.log(s.min_std), math.log(s.max_std), out=self.log_std)
                for key in totals:
                    totals[key] += parts[key]
                count += 1
        self.updates += 1
        result = {key: value / count for key, value in totals.items()}
        result["learning_rate"] = self.learning_rate
        result["std"] = float(self.std.mean())
        return result

    # Saving and restoring everything, to continue training later.

    def state(self) -> dict[str, NDArray]:
        state = {
            **self.actor.state("actor"),
            **self.critic.state("critic"),
            "log_std": self.log_std,
            **self.obs_normalizer.state("obs_normalizer"),
            **self.critic_normalizer.state("critic_normalizer"),
            **self.optimizer.state(),
            "learning_rate": np.array(self.learning_rate),
            "updates": np.array(self.updates),
        }
        return state

    def load_state(self, state: dict[str, NDArray]) -> None:
        """Continue from saved arrays, from training on either device."""
        xp = self.xp
        state = {key: to_numpy(value) for key, value in state.items()}
        for mine, saved in zip(
            self.actor.params, MLP.from_state(state, "actor").params, strict=True
        ):
            if mine.shape != saved.shape:
                raise ValueError("The saved actor has another shape")
            mine[...] = xp.asarray(saved)
        for mine, saved in zip(
            self.critic.params, MLP.from_state(state, "critic").params, strict=True
        ):
            if mine.shape != saved.shape:
                raise ValueError("The saved critic has another shape")
            mine[...] = xp.asarray(saved)
        self.log_std[...] = xp.asarray(state["log_std"])
        self.obs_normalizer.load_state(state, "obs_normalizer")
        self.critic_normalizer.load_state(state, "critic_normalizer")
        self.optimizer.load_state(state)
        self.learning_rate = float(state["learning_rate"])
        self.updates = int(state["updates"])

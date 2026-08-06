"""
Strict reproducibility controls. Call set_global_seeds() at the top of
every training / evaluation / analysis script — before any model or
environment is instantiated.
"""
from __future__ import annotations

import os
import random
from contextlib import contextmanager
from typing import Iterator

import numpy as np
import torch


def set_global_seeds(seed: int, deterministic: bool = True) -> None:
    """Lock all sources of randomness.

    Args:
        seed: integer seed
        deterministic: if True, enforce bit-exact determinism (slower)
    """
    seed = int(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        # Required by torch.use_deterministic_algorithms when using CUDA
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        try:
            torch.use_deterministic_algorithms(True, warn_only=True)
        except AttributeError:
            pass  # old torch
    else:
        torch.backends.cudnn.deterministic = False
        torch.backends.cudnn.benchmark = True


def seeded_generator(seed: int) -> torch.Generator:
    """Return a torch.Generator seeded to `seed`. Use for per-sample RNG
    in DataLoaders: DataLoader(..., generator=seeded_generator(seed))."""
    g = torch.Generator()
    g.manual_seed(int(seed))
    return g


@contextmanager
def temporary_seed(seed: int) -> Iterator[None]:
    """Context manager that restores the previous RNG state on exit.
    Useful for running a deterministic eval inside a non-deterministic
    training loop."""
    py_state = random.getstate()
    np_state = np.random.get_state()
    torch_state = torch.get_rng_state()
    cuda_state = (
        torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
    )
    try:
        set_global_seeds(seed, deterministic=True)
        yield
    finally:
        random.setstate(py_state)
        np.random.set_state(np_state)
        torch.set_rng_state(torch_state)
        if cuda_state is not None:
            torch.cuda.set_rng_state_all(cuda_state)

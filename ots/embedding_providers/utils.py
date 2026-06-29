from __future__ import annotations

import math
from collections.abc import Sequence


def normalize_vector(vector: Sequence[float]) -> list[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        return values
    return [value / norm for value in values]


def normalize_vectors(vectors: Sequence[Sequence[float]]) -> list[list[float]]:
    return [normalize_vector(vector) for vector in vectors]

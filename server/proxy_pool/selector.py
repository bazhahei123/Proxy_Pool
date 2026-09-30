from __future__ import annotations

import time
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

from .models import ProxyNodeConfig, SelectorConfig


@dataclass
class OriginState:
    statuses: deque[int] = field(default_factory=lambda: deque(maxlen=8))
    cooldown_until: float = 0.0


class ProxySelector:
    def __init__(self, nodes: list[ProxyNodeConfig], config: SelectorConfig):
        self.nodes = [n for n in nodes if n.enabled]
        self.config = config
        self._index = 0
        self._uses = 0
        self._origin: dict[tuple[str, str], OriginState] = defaultdict(OriginState)

    def _eligible(self, node: ProxyNodeConfig, origin: str, excluded: set[str]) -> bool:
        if node.id in excluded:
            return False
        return self._origin[(node.id, origin)].cooldown_until <= time.monotonic()

    def choose(self, origin: str, excluded: Iterable[str] = ()) -> ProxyNodeConfig | None:
        if not self.nodes:
            return None
        excluded_set = set(excluded)
        if self.config.mode == "random":
            eligible = [node for node in self.nodes if self._eligible(node, origin, excluded_set)]
            return random.choice(eligible) if eligible else None
        size = len(self.nodes)
        for offset in range(size):
            idx = (self._index + offset) % size
            node = self.nodes[idx]
            if self._eligible(node, origin, excluded_set):
                self._index = idx
                return node
        return None

    def choose_random(self, origin: str, excluded: Iterable[str]) -> ProxyNodeConfig | None:
        """Pick a different eligible node for a block-response retry."""
        excluded_set = set(excluded)
        eligible = [node for node in self.nodes if self._eligible(node, origin, excluded_set)]
        return random.choice(eligible) if eligible else None

    def finish_logical_request(self, initial_node_id: str) -> None:
        if self.config.mode != "fixed_count":
            return
        self._uses += 1
        if self._uses >= self.config.rotate_after:
            self._index = (self._index + 1) % max(len(self.nodes), 1)
            self._uses = 0

    def record(self, node_id: str, origin: str, status: int) -> None:
        self._origin[(node_id, origin)].statuses.append(status)

    def cooldown(self, node_id: str, origin: str) -> None:
        self._origin[(node_id, origin)].cooldown_until = time.monotonic() + self.config.cooldown_seconds

    def status_history(self, node_id: str, origin: str) -> tuple[int, ...]:
        return tuple(self._origin[(node_id, origin)].statuses)

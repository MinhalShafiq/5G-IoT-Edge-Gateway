"""Abstract base class for placement policies.

Each policy implements ``evaluate`` which scores a candidate placement given
the current task and resource map.  The scheduling engine runs all registered
policies and picks the placement with the highest score.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scheduler.services.resource_monitor import NodeResources
    from scheduler.services.scheduler_engine import InferenceTask, TaskPlacement


class PlacementPolicy(ABC):
    """Base class that all placement policies must extend."""

    @abstractmethod
    def evaluate(
        self,
        task: "InferenceTask",
        resources: dict[str, "NodeResources"],
    ) -> "TaskPlacement":
        """Evaluate the task against the current resource map.

        Returns a ``TaskPlacement`` with a ``score`` between 0.0 and 1.0.
        The scheduling engine selects the placement with the highest score
        across all policies.
        """
        ...

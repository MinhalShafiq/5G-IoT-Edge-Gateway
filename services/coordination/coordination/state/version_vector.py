"""Version vectors for causal ordering of distributed updates.

A version vector (also known as a vector clock) tracks the causal history
of updates across a set of distributed nodes.  Each node maintains a
monotonically increasing counter; when two vectors are compared the
relationship tells us whether one causally precedes the other or the
updates are concurrent (and may need conflict resolution).
"""

from __future__ import annotations

from copy import deepcopy


class VersionVector:
    """Tracks causal history of updates across distributed nodes."""

    def __init__(self) -> None:
        self._clock: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def increment(self, node_id: str) -> int:
        """Increment the counter for *node_id* and return the new value."""
        self._clock[node_id] = self._clock.get(node_id, 0) + 1
        return self._clock[node_id]

    def merge(self, other: VersionVector) -> None:
        """Merge another vector clock by taking the max of each component."""
        for node_id, version in other._clock.items():
            self._clock[node_id] = max(self._clock.get(node_id, 0), version)

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def dominates(self, other: VersionVector) -> bool:
        """Return ``True`` if this vector causally dominates (happened-after) *other*.

        ``self`` dominates ``other`` iff every component of ``self`` is >=
        the corresponding component in ``other`` **and** at least one
        component is strictly greater.
        """
        all_nodes = set(self._clock.keys()) | set(other._clock.keys())
        at_least_one_greater = False

        for node_id in all_nodes:
            self_val = self._clock.get(node_id, 0)
            other_val = other._clock.get(node_id, 0)
            if self_val < other_val:
                return False
            if self_val > other_val:
                at_least_one_greater = True

        return at_least_one_greater

    def concurrent_with(self, other: VersionVector) -> bool:
        """Return ``True`` if neither vector dominates the other.

        Concurrent vectors represent independent, potentially conflicting
        updates that need application-level resolution.
        """
        return not self.dominates(other) and not other.dominates(self) and self != other

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, int]:
        """Serialize to a plain dict."""
        return dict(self._clock)

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> VersionVector:
        """Deserialize from a plain dict."""
        vv = cls()
        vv._clock = dict(data)
        return vv

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionVector):
            return NotImplemented
        # Two vectors are equal if every component matches (missing == 0)
        all_nodes = set(self._clock.keys()) | set(other._clock.keys())
        return all(
            self._clock.get(n, 0) == other._clock.get(n, 0)
            for n in all_nodes
        )

    def __repr__(self) -> str:
        return f"VersionVector({self._clock!r})"

    def copy(self) -> VersionVector:
        """Return a deep copy of this vector."""
        vv = VersionVector()
        vv._clock = deepcopy(self._clock)
        return vv

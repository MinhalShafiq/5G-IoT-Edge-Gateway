"""Coordination service configuration."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Coordination service settings.

    Extends BaseServiceSettings with cluster coordination configuration
    including leader election, gossip protocol, and heartbeat tuning.
    """

    service_name: str = "coordination"

    # Node identity
    node_id: str = ""
    node_address: str = "localhost:50052"

    # Server ports
    grpc_port: int = 50052
    http_port: int = 8003

    # Heartbeat
    heartbeat_interval_seconds: float = 2.0
    heartbeat_timeout_seconds: float = 10.0

    # Leader election
    leader_lock_ttl_seconds: int = 15

    # Gossip protocol
    gossip_interval_seconds: float = 2.0
    gossip_fanout: int = 3  # number of random peers to ping per round

    # Cluster bootstrap
    seed_nodes: list[str] = []  # initial cluster members to contact

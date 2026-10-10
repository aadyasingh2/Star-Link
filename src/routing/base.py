from abc import ABC, abstractmethod

class Router(ABC):
    """Abstract base class for all routing protocols.
    All routers must expose the same public API and be attachable to a
    RoutingEngine via ``attach_engine``.
    """

    def __init__(self, config):
        self.config = config
        self._control_message_count = 0
        self._control_bytes_count = 0
        self._engine = None

    @property
    def control_message_count(self):
        return self._control_message_count

    @property
    def control_bytes_count(self):
        return self._control_bytes_count

    @property
    def boot_message_count(self):
        return getattr(self, '_boot_message_count', 0)

    @property
    def dropped_message_count(self):
        return getattr(self, '_dropped_message_count', 0)

    def attach_engine(self, engine):
        """Engine assigns a back-reference for scheduling events."""
        self._engine = engine
        # expose public attribute for compatibility
        self.engine = engine

    def begin_batch(self):
        """Begin a batch of initial topology changes."""
        pass

    def end_batch(self):
        """Finish a batch of initial topology changes."""
        pass

    @abstractmethod
    def compute_route(self, src, dst, t):
        """Instantaneous route resolution at time *t*.
        Must be side‑effect free. Returns a list of nodes (including src & dst)
        or ``None`` if the route is black‑holed or contains a loop.
        """
        pass

    @abstractmethod
    def compute_route_hop(self, node, dst, t):
        """Return the next hop from *node* toward *dst* at time *t*.
        Return ``None`` if no next hop is known.
        """
        pass

    @abstractmethod
    def reset(self):
        """Reset all internal state (used between simulation runs)."""
        self._control_message_count = 0
        self._control_bytes_count = 0
        pass

    @abstractmethod
    def handle_link_up(self, u, v, delay, t):
        pass

    @abstractmethod
    def handle_link_down(self, u, v, t):
        pass

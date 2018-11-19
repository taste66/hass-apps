"""
This module implements the StatisticsZone class for collecting statistics.
"""

import typing as T
if T.TYPE_CHECKING:
    # pylint: disable=cyclic-import,unused-import
    import uuid
    from .app import SchedyApp
    from .room import Room

import voluptuous as vol

from .. import common


StatisticalValueType = T.Union[float, int, str]


class StatisticalParameter:
    """A parameter to be collected."""

    name = "dummy"
    config_schema = vol.Schema(object)

    def __init__(self, name: str, cfg: T.Dict, app: "SchedyApp") -> None:
        self.name = name
        self.cfg = cfg
        self.app = app

        self.rooms = []  # type: T.List[Room]

        self._update_state_timer = None  # type: T.Optional[uuid.UUID]
        self._last_state = None  # type: T.Optional[T.Dict[str, StatisticalValueType]]

    def __repr__(self) -> str:
        return "<StatisticalParameter {}>".format(self.name)

    def __str__(self) -> str:
        return "SP:{}".format(self.cfg.get("friendly_name", self.name))

    def generate_entries(self) -> T.Dict[str, StatisticalValueType]:  # pylint: disable=no-self-use
        """Should generate the entries to be added to the zone."""

        return {}

    def _do_update_state(self) -> None:
        """Collects the statistics and writes them to Home Assistant."""

        self._update_state_timer = None

        attrs = self.generate_entries()

        unchanged = attrs == self._last_state
        if unchanged:
            self.log("Unchanged HA state: attributes={}"
                     .format(attrs),
                     level="DEBUG")
            return
        self.log("Sending new HA state: attributes={}"
                 .format(attrs),
                 level="DEBUG", prefix=common.LOG_PREFIX_OUTGOING)

        entity_id = "schedy.{}_stats_{}".format(self.app.name, self.name)
        self.app.set_state(entity_id, state="", attributes=attrs)
        self._last_state = attrs

    def initialize(self) -> None:
        """Fetches the Room objects."""

        self.log("Initializing statistical parameter (name={})."
                 .format(repr(self.name)),
                 level="DEBUG")

        if not self.rooms:
            self.log("No rooms configured.", level="WARNING")

        self.update_stats()

    def log(self, msg: str, *args: T.Any, **kwargs: T.Any) -> None:
        """Prefixes the zone to log messages."""
        msg = "[{}] {}".format(self, msg)
        self.app.log(msg, *args, **kwargs)

    def update_stats(self) -> None:
        """Registers a timer for sending statistics to HA in 3 seconds."""

        if self._update_state_timer:
            self.log("Statistics update  pending already.",
                     level="DEBUG")
            return

        self.log("Going to update statistics in 3 seconds.",
                 level="DEBUG")
        self._update_state_timer = self.app.run_in(
            lambda *a, **kw: self._do_update_state(), 3
        )


class MinAvgMaxParameter(StatisticalParameter):
    """A parameter that automatically calculates min/avg/max."""

    @staticmethod
    def calculate_min_avg_max(
            values: T.Iterable[T.Union[float, int, "WeightedValue"]]
    ) -> T.Dict[str, StatisticalValueType]:
        """Returns the minimum, average and maximum of all given values."""

        numeric_values = []
        weighted_sum = 0.0
        weights_sum = 0.0
        for value in values:
            if isinstance(value, WeightedValue):
                numeric_values.append(value.value)
                weighted_sum += value.value * value.weight
                weights_sum += value.weight
            else:
                numeric_values.append(value)
                weighted_sum += value
                weights_sum += 1

        _min = min([v for v in numeric_values]) if values else 0.0
        _avg = weighted_sum / weights_sum if values else 0.0
        _max = max([v for v in numeric_values]) if values else 0.0
        return {"min": _min, "avg": _avg, "max": _max}

    def collect(self) -> T.Iterable[T.Union[float, int, "WeightedValue"]]:  # pylint: disable=no-self-use
        """Should collect the implementation-specific values."""

        return []

    def generate_entries(self) -> T.Dict[str, StatisticalValueType]:
        """Generates min/avg/max entries to be added to the zone."""

        return self.calculate_min_avg_max(self.collect())


class WeightedValue:
    """A measured value having a weight for proper average calculation."""

    def __init__(self, value: float, weight: float) -> None:
        self.value = value
        self.weight = weight

    def __repr__(self) -> str:
        return "{}(weight={})".format(self.value, self.weight)

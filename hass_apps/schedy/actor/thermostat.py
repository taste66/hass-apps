"""
This module implements the thermostat actor.
"""

import typing as T
if T.TYPE_CHECKING:
    # pylint: disable=cyclic-import,unused-import
    from ..room import Room

import functools
import voluptuous as vol

from ... import common
from .. import stats
from .base import ActorBase


# allowed types of values to initialize Temp() with
TempValueType = T.Union[float, int, str, "Off", "Temp"]


class Off:
    """A special value Temp() may be initialized with in order to turn
    a thermostat off."""

    def __add__(self, other: T.Any) -> "Off":
        return self

    def __eq__(self, other: T.Any) -> bool:
        return isinstance(other, Off)

    def __hash__(self) -> int:
        return hash(str(self))

    def __neg__(self) -> "Off":
        return self

    def __repr__(self) -> str:
        return "OFF"

    def __sub__(self, other: T.Any) -> "Off":
        return self

OFF = Off()

@functools.total_ordering
class Temp:
    """A class holding a temperature value."""

    def __init__(self, temp_value: T.Any) -> None:
        if isinstance(temp_value, Temp):
            # Just copy the value over.
            parsed = self.parse_temp(temp_value.value)
        else:
            parsed = self.parse_temp(temp_value)

        if parsed is None:
            raise ValueError("{} is no valid temperature"
                             .format(repr(temp_value)))

        self.value = parsed  # type: T.Union[float, Off]

    def __add__(self, other: T.Any) -> "Temp":
        if isinstance(other, (float, int)):
            other = type(self)(other)
        elif not isinstance(other, type(self)):
            raise TypeError("can't add {} and {}"
                            .format(repr(type(self)), repr(type(other))))

        # OFF + something is OFF
        if self.is_off or other.is_off:
            return type(self)(OFF)

        return type(self)(self.value + other.value)

    def __eq__(self, other: T.Any) -> bool:
        return isinstance(other, Temp) and self.value == other.value

    def __float__(self) -> float:
        if isinstance(self.value, float):
            return self.value
        raise ValueError("{} has no numeric value.".format(repr(self)))

    def __hash__(self) -> int:
        return hash(str(self))

    def __lt__(self, other: T.Any) -> bool:
        if isinstance(other, (float, int)):
            other = Temp(other)

        if type(self) is not type(other):
            raise TypeError("can't compare {} and {}"
                            .format(repr(type(self)), repr(type(other))))

        if not self.is_off and other.is_off:
            return False
        if self.is_off and not other.is_off or \
           self.value < other.value:
            return True
        return False

    def __neg__(self) -> "Temp":
        return Temp(-self.value)  # pylint: disable=invalid-unary-operand-type

    def __repr__(self) -> str:
        if isinstance(self.value, (float, int)):
            return "{}°".format(self.value)
        return "{}".format(self.value)

    def __sub__(self, other: T.Any) -> "Temp":
        return self.__add__(-other)

    @property
    def is_off(self) -> bool:
        """Tells whether this temperature means OFF."""

        return isinstance(self.value, Off)

    @staticmethod
    def parse_temp(value: T.Any) -> T.Union[float, Off, None]:
        """Converts the given value to a valid temperature of type float
        or Off.
        If value is a string, all whitespace is removed first.
        If conversion is not possible, None is returned."""

        if isinstance(value, str):
            value = "".join(value.split())
            if value.upper() == "OFF":
                return OFF

        if isinstance(value, Off):
            return OFF

        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def serialize(self) -> str:
        """Converts the temperature into a string that Temp can be
        initialized with again later."""

        if self.is_off:
            return "OFF"
        return str(self.value)


TEMP_SCHEMA = vol.Schema(vol.All(
    vol.Any(float, int, Off, vol.All(str, lambda v: v.upper(), "OFF")),
    lambda v: Temp(v),  # pylint: disable=unnecessary-lambda
))

CONFIG_SCHEMA = vol.Schema({
    vol.Optional("delta", default=0):
        vol.All(TEMP_SCHEMA, vol.NotIn([Temp(OFF)])),
    vol.Optional("min_temp", default=None): vol.Any(
        vol.All(TEMP_SCHEMA, vol.NotIn([Temp(OFF)])),
        None,
    ),
    vol.Optional("max_temp", default=None): vol.Any(
        vol.All(TEMP_SCHEMA, vol.NotIn([Temp(OFF)])),
        None,
    ),
    vol.Optional("off_temp", default=OFF): TEMP_SCHEMA,
    vol.Optional("supports_opmodes", default=True): bool,
    vol.Optional("opmode_on", default="heat"): str,
    vol.Optional("opmode_off", default="off"): str,
    vol.Optional(
        "opmode_on_service", default="climate/set_operation_mode"
    ): vol.All(str, lambda v: v.replace(".", "/")),
    vol.Optional(
        "opmode_off_service", default="climate/set_operation_mode"
    ): vol.All(str, lambda v: v.replace(".", "/")),
    vol.Optional("opmode_on_service_attr", default="operation_mode"):
        vol.Any(str, None),
    vol.Optional("opmode_off_service_attr", default="operation_mode"):
        vol.Any(str, None),
    vol.Optional("opmode_state_attr", default="operation_mode"): str,
    vol.Optional(
        "target_temp_service", default="climate/set_temperature"
    ): vol.All(str, lambda v: v.replace(".", "/")),
    vol.Optional("target_temp_service_attr", default="temperature"): str,
    vol.Optional("target_temp_state_attr", default="temperature"): str,
    vol.Optional(
        "current_temp_state_attr", default="current_temperature"
    ): vol.Any(str, None),
}, extra=True)


class TempDeltaParameter(stats.MinAvgMaxParameter):
    """The difference between """

    name = "temp_delta"
    config_schema = vol.Schema({
        vol.Optional("off_value", default=0): vol.Any(float, int, None),
        vol.Optional("factors", default=dict): vol.All(
            lambda v: v or {},
            {vol.Extra: vol.All(
                vol.Any(float, int), vol.Range(min=0, min_included=False),
            )},
        ),
        vol.Optional("weights", default=dict): vol.All(
            lambda v: v or {},
            {vol.Extra: vol.All(vol.Any(float, int), vol.Range(min=0))},
        ),
    }, extra=True)

    def collect(self) -> T.Iterable[stats.WeightedValue]:
        """Collects the difference between target and current temperature
        for all thermostats."""

        off_value = self.cfg["off_value"]
        values = []
        for room in self.rooms:
            for therm in filter(lambda a: a.is_initialized, room.actors):
                assert isinstance(therm, ThermostatActor)
                weight = self.cfg["weights"].get(therm.entity_id, 1)
                if weight == 0:
                    # ignore this thermostat
                    continue

                if therm.current_temp is None or \
                   therm.current_value is None or \
                   therm.current_temp.is_off or \
                   therm.current_value.is_off:
                    if off_value is None:
                        # thermostats that are off should be excluded
                        continue
                    temp_delta = float(off_value)
                else:
                    temp_delta = float(therm.current_value -
                                       therm.current_temp)
                    factor = self.cfg["factors"].get(therm.entity_id, 1)
                    temp_delta *= factor
                value = stats.WeightedValue(temp_delta, weight)
                self.log("Value for {} in {} is {}".format(therm, room, value),
                         level="DEBUG")
                values.append(value)

        return values

    def initialize(self) -> None:
        """Listens for changes of current and target temperature."""

        super().initialize()

        handler = lambda *a, **kw: self.update_stats()
        for room in self.rooms:
            for therm in room.actors:
                self.log("Listening for temperature changes of {} in {}."
                         .format(therm, room),
                         level="DEBUG")
                therm.events.on("current_temp_changed", handler)
                therm.events.on("value_changed", handler)


class ThermostatActor(ActorBase):
    """A thermostat to be controlled by Schedy."""

    name = "thermostat"
    config_schema = CONFIG_SCHEMA
    stats_param_types = [TempDeltaParameter]

    def __init__(self, *args: T.Any, **kwargs: T.Any) -> None:
        super().__init__(*args, **kwargs)
        self._current_temp = None  # type: T.Optional[Temp]

    def check_config_plausibility(self, state: dict) -> None:
        """Is called during initialization to warn the user about some
        possible common configuration mistakes."""

        if not state:
            self.log("Thermostat couldn't be found.", level="WARNING")
            return

        required_attrs = [self.cfg["target_temp_state_attr"]]
        if self.cfg["supports_opmodes"]:
            required_attrs.append(self.cfg["opmode_state_attr"])
        for attr in required_attrs:
            if attr not in state:
                self.log("Thermostat has no attribute named {}. "
                         "Available attributes are {}. "
                         "Please check your config!"
                         .format(repr(attr), list(state.keys())),
                         level="WARNING")

        temp_attrs = [self.cfg["target_temp_state_attr"]]
        if self.cfg["current_temp_state_attr"]:
            temp_attrs.append(self.cfg["current_temp_state_attr"])
        for attr in temp_attrs:
            value = state.get(attr)
            try:
                value = float(value)  # type: ignore
            except (TypeError, ValueError):
                self.log("The value {} for attribute {} is no valid "
                         "temperature value. "
                         "Please check your config!"
                         .format(repr(value), repr(attr)),
                         level="WARNING")

        allowed_opmodes = state.get("operation_list")
        if not self.cfg["supports_opmodes"]:
            if allowed_opmodes:
                self.log("Operation mode support has been disabled, "
                         "but the following modes seem to be supported: {} "
                         "Maybe disabling it was a mistake?"
                         .format(allowed_opmodes),
                         level="WARNING")
            return

        if self.cfg["opmode_state_attr"] != "operation_mode":
            # we can't rely on operation_list in this case
            return
        if not allowed_opmodes:
            self.log("Attributes for thermostat contain no "
                     "'operation_list', Consider disabling "
                     "operation mode support.",
                     level="WARNING")
            return
        for opmode in (self.cfg["opmode_on"], self.cfg["opmode_off"]):
            if opmode not in allowed_opmodes:
                self.log("Thermostat doesn't seem to support the "
                         "operation mode {}, supported modes are: {}. "
                         "Please check your config!"
                         .format(opmode, allowed_opmodes),
                         level="WARNING")

    @property
    def current_temp(self) -> T.Optional[Temp]:
        """Returns the current temperature as measured by the thermostat."""

        return self._current_temp

    @staticmethod
    def deserialize_value(value: str) -> Temp:
        """Deserializes by calling validate_value()."""

        return ThermostatActor.validate_value(value)

    def do_send(self) -> None:
        """Sends self._wanted_value to the thermostat."""

        target_temp = self._wanted_value
        if target_temp.is_off:
            opmode = self.cfg["opmode_off"]
            temp = None
        else:
            opmode = self.cfg["opmode_on"]
            temp = target_temp
        if not self.cfg["supports_opmodes"]:
            opmode = None

        self.log("Setting temperature = {}, operation mode = {}."
                 .format("<unset>" if temp is None else temp,
                         "<unset>" if opmode is None else repr(opmode)),
                 level="DEBUG", prefix=common.LOG_PREFIX_OUTGOING)

        if opmode is not None:
            if opmode == self.cfg["opmode_on"]:
                opmode_service = self.cfg["opmode_on_service"]
                opmode_service_attr = self.cfg["opmode_on_service_attr"]
            else:
                opmode_service = self.cfg["opmode_off_service"]
                opmode_service_attr = self.cfg["opmode_off_service_attr"]
            attrs = {"entity_id": self.entity_id}
            if opmode_service_attr:
                attrs[opmode_service_attr] = opmode
            self.app.call_service(opmode_service, **attrs)

        if temp is not None:
            attrs = {"entity_id": self.entity_id,
                     self.cfg["target_temp_service_attr"]: temp.value}
            self.app.call_service(self.cfg["target_temp_service"], **attrs)

    def filter_set_value(self, value: Temp) -> T.Optional[Temp]:
        """Preprocesses the given target temperature for setting on this
        thermostat. This algorithm will try best to achieve the closest
        possible temperature supported by this particular thermostat.
        The return value is either the temperature to set or None,
        if nothing has to be sent."""

        if value.is_off:
            value = self.cfg["off_temp"]

        if not value.is_off:
            value = value + self.cfg["delta"]
            if isinstance(self.cfg["min_temp"], Temp) and \
               value < self.cfg["min_temp"]:
                value = self.cfg["min_temp"]
            elif isinstance(self.cfg["max_temp"], Temp) and \
                 value > self.cfg["max_temp"]:
                value = self.cfg["max_temp"]
        elif not self.cfg["supports_opmodes"]:
            self.log("Not turning off because it doesn't support "
                     "operation modes.",
                     level="WARNING")
            self.log("Consider defining an off_temp in the actor "
                     "configuration for these cases.",
                     level="WARNING")
            return None

        return value

    def notify_state_changed(self, attrs: dict) -> T.Any:
        """Is called when the thermostat's state changes.
        This method fetches both the current and target temperature from
        the thermostat and reacts accordingly."""

        _target_temp = None  # type: T.Optional[TempValueType]
        if self.cfg["supports_opmodes"]:
            opmode = attrs.get(self.cfg["opmode_state_attr"])
            self.log("Attribute {} is {}."
                     .format(repr(self.cfg["opmode_state_attr"]), repr(opmode)),
                     level="DEBUG", prefix=common.LOG_PREFIX_INCOMING)
            if opmode == self.cfg["opmode_off"]:
                _target_temp = OFF
            elif opmode != self.cfg["opmode_on"]:
                self.log("Unknown operation mode, ignoring thermostat.",
                         level="ERROR")
                return None
        else:
            opmode = None

        if _target_temp is None:
            _target_temp = attrs.get(self.cfg["target_temp_state_attr"])
            self.log("Attribute {} is {}."
                     .format(repr(self.cfg["target_temp_state_attr"]),
                             repr(_target_temp)),
                     level="DEBUG", prefix=common.LOG_PREFIX_INCOMING)

        try:
            target_temp = Temp(_target_temp)
        except ValueError:
            self.log("Invalid target temperature, ignoring thermostat.",
                     level="ERROR")
            return None

        current_temp_attr = self.cfg["current_temp_state_attr"]
        if current_temp_attr:
            _current_temp = attrs.get(current_temp_attr)
            self.log("Attribute {} is {}."
                     .format(repr(current_temp_attr), repr(_current_temp)),
                     level="DEBUG", prefix=common.LOG_PREFIX_INCOMING)
            try:
                current_temp = Temp(_current_temp)  # type: T.Optional[Temp]
            except ValueError:
                self.log("Invalid current temperature, not updating it.",
                         level="ERROR")
            else:
                if current_temp != self._current_temp:
                    self._current_temp = current_temp
                    self.events.trigger(
                        "current_temp_changed", self, current_temp
                    )

        if target_temp != self._current_value:
            self.log("Received target temperature of {}."
                     .format(str(target_temp)),
                     prefix=common.LOG_PREFIX_INCOMING)

        return target_temp

    @classmethod
    def prepare_eval_environment(cls, env: T.Dict[str, T.Any]) -> None:
        """Adds Temp, OFF etc. to the dict used as environment for
        expression evaluation."""

        env.update({
            "OFF": OFF,
            "Temp": Temp,
        })

    @staticmethod
    def serialize_value(value: Temp) -> str:
        """Wrapper around Temp.serialize()."""

        if not isinstance(value, Temp):
            raise ValueError(
                "can only serialize Temp objects, not {}".format(repr(value))
            )
        return value.serialize()

    @staticmethod
    def validate_value(value: T.Any) -> Temp:
        """Ensures the given value is a valid temperature."""

        return Temp(value)

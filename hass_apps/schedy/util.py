"""
Utility functions that are used all around Schedy.
"""

import types
import typing as T

import collections
import datetime
import re


# matches any character that is not allowed in Python variable names
INVALID_VAR_NAME_CHAR_PATTERN = re.compile(r"[^0-9A-Za-z_]")
# regexp pattern matching a range like 3-7 without spaces
RANGE_PATTERN = re.compile(r"^(\d+)\-(\d+)$")
# strftime-compatible format string for military time
TIME_FORMAT = "%H:%M:%S"
# regular expression for time formats, group 1 is hours, group 2 is minutes,
# optional group 3 is seconds
TIME_REGEXP = re.compile(r"^ *([01]?\d|2[0-3]) *\: *([0-5]\d) *(?:\: *([0-5]\d) *)?$")


class RangingSet(set):
    """A set for integers that forms nice ranges in its __repr__,
    perfectly suited for the expansion of range strings."""

    def __repr__(self) -> str:
        if not self:
            return "{}"

        # fall back to legacy representation when non-ints are found
        for item in self:
            if not isinstance(item, int):
                return super().__repr__()

        nums = sorted(self)  # type: T.List[int]
        ranges = collections.OrderedDict()  # type: T.Dict[int, int]
        range_start = nums[0]
        ranges[range_start] = range_start
        for num in nums[1:]:
            if num - 1 != ranges[range_start]:
                range_start = num
            ranges[range_start] = num

        return "{{{}}}".format(", ".join(
            [str(start) if start == end else "{}-{}".format(start, end)
             for start, end in ranges.items()]
        ))


def build_date_from_constraint(
        constraint: T.Dict[str, int], default_date: datetime.date,
        direction: int = 0
) -> datetime.date:
    """Builds and returns a datetime.date object from the given constraint,
    taking missing values from the given default_date.
    In case the date is not valid (e.g. 2017-02-29), a ValueError is
    raised, unless a number has been given for direction, in which case
    the next/previous valid date will be chosen, depending on the sign
    of direction."""

    fields = {}
    for field in ("year", "month", "day"):
        fields[field] = constraint.get(field, getattr(default_date, field))

    while True:
        try:
            return datetime.date(**fields)
        except ValueError:
            if direction > 0:
                fields["day"] += 1
            elif direction < 0:
                fields["day"] -= 1
            else:
                raise

            # handle month/year transitions correctly
            if fields["day"] < 1:
                fields["day"] = 31
                fields["month"] -= 1
            elif fields["day"] > 31:
                fields["day"] = 1
                fields["month"] += 1
            if fields["month"] < 1:
                fields["month"] = 12
                fields["year"] -= 1
            elif fields["month"] > 12:
                fields["month"] = 1
                fields["year"] += 1

def compile_expression(expr: str) -> types.CodeType:
    """Compiles strings to code objects.
    Strings with one or more newlines are assumed to contain whole
    statements already.
    Strings without newlines are treated as simple expressions and
    "result = " is prepended to them before compilation."""

    if "\n" not in expr:
        expr = "result = {}".format(expr)
    return compile(expr, "expr", "exec")

def deep_merge_dicts(source: dict, dest: dict) -> None:
    """Inserts missing items from source into dest, descending into
    child dictionaries as well."""

    for key, value in source.items():
        if isinstance(value, dict):
            node = dest.setdefault(key, {})
            if isinstance(node, dict):
                deep_merge_dicts(value, node)
        else:
            dest.setdefault(key, value)

def escape_var_name(name: str) -> str:
    """Converts the given string to a valid Python variable name.
    All unsupported characters are replaced by "_". If name would
    start with a digit, "_" is put infront."""

    name = INVALID_VAR_NAME_CHAR_PATTERN.sub("_", name)
    digits = tuple([str(i) for i in range(10)])
    if name.startswith(digits):
        name = "_" + name
    return name

def expand_range_string(range_string: T.Union[float, int, str]) -> T.Set[int]:
    """Expands strings of the form '1,2-4,9,11-12 to set(1,2,3,4,9,11,12).
    Any whitespace is ignored. If a float or int is given instead of a
    string, a set containing only that, converted to int, is returned."""

    if isinstance(range_string, (float, int)):
        return RangingSet([int(range_string)])

    numbers = RangingSet()
    for part in "".join(range_string.split()).split(","):
        match = RANGE_PATTERN.match(part)
        if match is not None:
            for i in range(int(match.group(1)), int(match.group(2)) + 1):
                numbers.add(i)
        else:
            numbers.add(int(part))
    return numbers

def format_time(when: datetime.time, format_str: str = TIME_FORMAT) -> str:
    """Returns a string representing the given datetime.time object.
    If no strftime-compatible format is provided, the default is used."""

    return when.strftime(format_str)

def normalize_dict_key(
        obj: dict, dest_key: T.Any, *alt_keys: T.Any,
        keep_alt_keys: bool = False
) -> None:
    """If dest_key is missing in the dict obj but one of the alt_keys
    is found instead, the value of the first alt_key is moved over
    to dest_key.
    All alt_keys are deleted from dict obj unless keep_alt_keys is True."""

    for alt_key in alt_keys:
        if alt_key in obj:
            if dest_key not in obj:
                obj[dest_key] = obj[alt_key]
            if not keep_alt_keys:
                del obj[alt_key]

def parse_time_string(time_str: str) -> datetime.time:
    """Parses a string recognizable by TIME_REGEXP format into
    a datetime.time object. If the string has an invalid format, a
    ValueError is raised."""

    match = TIME_REGEXP.match(time_str)
    if match is None:
        raise ValueError("time string {} has an invalid format"
                         .format(repr(time_str)))
    components = [int(comp) for comp in match.groups() if comp is not None]
    return datetime.time(*components)  # type: ignore

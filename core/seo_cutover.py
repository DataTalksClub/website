"""The explicit application indexing switch for the reviewed apex cutover."""

from core.bootstrap import parse_bool


def noindex_for_apex_cutover(value: str | None) -> bool:
    return not parse_bool("DTC_APEX_CUTOVER", value, default=False)

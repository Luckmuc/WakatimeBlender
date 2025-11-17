"""Wrapper for running the legacy Wakatime CLI under modern Python."""
import runpy
import sys


_COMPAT_ATTRS = (
    "Mapping",
    "MutableMapping",
    "Sequence",
    "MutableSequence",
    "MutableSet",
    "Callable",
)


def _patch_collections() -> None:
    try:
        import collections
        import collections.abc as abc
    except Exception:
        return

    for attr in _COMPAT_ATTRS:
        if not hasattr(collections, attr):
            try:
                setattr(collections, attr, getattr(abc, attr))
            except AttributeError:
                continue


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Missing CLI path for Wakatime wrapper")

    cli_path = sys.argv[1]
    _patch_collections()

    # Present the original CLI path to the legacy script.
    sys.argv = [cli_path] + sys.argv[2:]
    runpy.run_path(cli_path, run_name="__main__")


if __name__ == "__main__":
    main()

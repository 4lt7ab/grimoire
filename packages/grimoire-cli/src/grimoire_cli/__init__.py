from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("4lt7ab-grimoire-cli")
except PackageNotFoundError:  # editable install before metadata exists
    __version__ = "0.0.0"

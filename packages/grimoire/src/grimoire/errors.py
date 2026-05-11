class GrimoireError(Exception):
    """Base for all errors raised by grimoire."""


class SchemaVersionError(GrimoireError):
    """The file's schema version disagrees with the library's `SCHEMA_VERSION`."""


class GrimoireMismatch(GrimoireError):
    """The supplied embedder's model or dimension disagrees with the file's lock."""


class GrimoireNotFound(GrimoireError):
    """The path does not exist or is not an initialized grimoire."""

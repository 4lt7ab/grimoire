class GrimoireError(Exception):
    """Base exception for all grimoire errors."""


class GrimoireMismatch(GrimoireError):
    """An embedder's model or dimension does not match the stored grimoire."""


class GrimoireNotFound(GrimoireError):
    """The path does not point to an existing grimoire file."""


class SchemaVersionError(GrimoireError):
    """The database file's schema version does not match what the library expects."""


class InvalidEmbedder(GrimoireError):
    """An embedder reported a model or dimension outside the allowed shape."""


class DatabaseExists(GrimoireError):
    """A database with this name already exists in the mount."""


class InvalidMount(GrimoireError):
    """A mount path or database name is malformed or reserved."""


class MountDestroyed(GrimoireError):
    """An operation was attempted on a Mount handle that has been destroyed."""

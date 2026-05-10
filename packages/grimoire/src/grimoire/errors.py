class GrimoireError(Exception):
    """Base exception for all grimoire errors."""


class GrimoireMismatch(GrimoireError):
    """An embedder's model or dimension does not match the stored grimoire."""


class GrimoireNotFound(GrimoireError):
    """No grimoire database exists at the requested location."""


class GrimoireDestroyed(GrimoireError):
    """An operation was attempted on a Grimoire handle that has been destroyed."""


class SchemaVersionError(GrimoireError):
    """The database file's schema version does not match what the library expects."""


class InvalidEmbedder(GrimoireError):
    """An embedder reported a model or dimension outside the allowed shape."""


class InvalidMount(GrimoireError):
    """A mount path or database name is malformed or reserved."""


class MountNotFound(GrimoireError):
    """No mount directory exists at the requested location."""


class MountDestroyed(GrimoireError):
    """An operation was attempted on a Mount handle that has been destroyed."""

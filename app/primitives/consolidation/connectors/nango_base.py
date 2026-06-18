from app.primitives.consolidation.connectors.base import BaseConnector
from app.primitives.nango import client as nango


class NangoConnector(BaseConnector):
    """
    Abstract base for all Nango-managed source connectors.
    Subclasses implement list_items / fetch_text / fetch_file using the token
    returned by _get_token(), which Nango auto-refreshes before returning.
    """

    def __init__(self, connection_id: str, provider: str):
        self.connection_id = connection_id
        self.provider = provider
        self._token: str | None = None

    async def _get_token(self) -> str:
        if self._token is None:
            self._token = await nango.get_token(self.connection_id, self.provider)
        return self._token

    async def fetch_file(self, item) -> str:
        raise NotImplementedError(f"{self.__class__.__name__} does not support binary file downloads")

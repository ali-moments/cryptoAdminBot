from app.telegram.parsers.base import BaseParser


class ParserManager:
    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}

    def register(
        self,
        parser_name: str,
        parser: BaseParser,
    ) -> None:
        self._parsers[parser_name] = parser

    def unregister(
        self,
        parser_name: str,
    ) -> None:
        self._parsers.pop(parser_name, None)

    def get(
        self,
        parser_name: str,
    ) -> BaseParser | None:
        return self._parsers.get(parser_name)

"""Safe, transport-neutral Q&A errors."""

from __future__ import annotations


class QnaError(Exception):
    def __init__(
        self, status: int, code: str, message: str = "The Q&A request is invalid."
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


class QnaNotFound(QnaError):
    def __init__(self) -> None:
        super().__init__(404, "not_found", "The Q&A resource was not found.")


class QnaArchived(QnaError):
    def __init__(self) -> None:
        super().__init__(410, "archived", "This Q&A session has been archived.")

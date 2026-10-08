"""主干的异常类型。API 层把它们映射为文档 9.1 的错误码。"""

from __future__ import annotations


class AirError(Exception):
    code = "INTERNAL"
    http_status = 500

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class StaleApproval(AirError):
    code = "STALE_VERSION"
    http_status = 409


class StaleQuestion(AirError):
    code = "STALE_VERSION"
    http_status = 409


class StaleVersion(AirError):
    code = "STALE_VERSION"
    http_status = 409


class InvalidState(AirError):
    code = "INVALID_STATE"
    http_status = 409


class NotFound(AirError):
    code = "NOT_FOUND"
    http_status = 404


class PermissionDenied(AirError):
    code = "PERMISSION_DENIED"
    http_status = 403


class ValidationFailed(AirError):
    code = "VALIDATION"
    http_status = 422


class ProjectLocked(AirError):
    code = "INVALID_STATE"
    http_status = 409

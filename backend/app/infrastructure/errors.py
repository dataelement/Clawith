"""Bounded application errors; transport adapters own their HTTP representation."""


class DomainError(Exception):
    code = "domain_error"


class InvalidInput(DomainError):
    code = "invalid_input"


class NotFound(DomainError):
    code = "not_found"


class AccessDenied(DomainError):
    code = "access_denied"


class Conflict(DomainError):
    code = "conflict"

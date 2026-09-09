from __future__ import annotations


class EngineError(Exception):
    code = -32000
    default_message = "Engine operation failed"

    def __init__(self, message: str | None = None, *, kind: str, detail: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.kind = kind
        self.detail = detail

    def as_rpc_error(self) -> dict:
        data = {"kind": self.kind}
        if self.detail:
            data["detail"] = self.detail
        return {"code": self.code, "message": self.message, "data": data}


class InvalidParamsError(EngineError):
    code = -32602
    default_message = "Invalid params"

    def __init__(self, detail: str) -> None:
        super().__init__(kind="invalid_params", detail=detail)


class MethodNotFoundError(EngineError):
    code = -32601
    default_message = "Method not found"

    def __init__(self) -> None:
        super().__init__(kind="method_not_found")


class InvalidRequestError(EngineError):
    code = -32600
    default_message = "Invalid Request"

    def __init__(self, detail: str) -> None:
        super().__init__(kind="invalid_request", detail=detail)


class DomainError(EngineError):
    def __init__(self, message: str, *, kind: str, detail: str | None = None) -> None:
        super().__init__(message, kind=kind, detail=detail)

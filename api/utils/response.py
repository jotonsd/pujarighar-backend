import logging
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class ApiResponse(Response):
    """
    Standard API response wrapper.

    Usage:
        return ApiResponse(message="Success", data=..., status_code=200)
        return ApiResponse(message="Error",   errors=..., status_code=400)
        return ApiResponse(message="List",    data=[...], pagination={...})
    """

    def __init__(
        self,
        message: str = None,
        data=None,
        status_code: int = 200,
        errors=None,
        pagination: dict = None,
        **kwargs,
    ):
        body = {
            "status":  "success" if status_code < 400 else "error",
            "message": message,
            "data":    data if data is not None else {},
            "errors":  errors,
        }
        if pagination is not None:
            body["pagination"] = pagination

        super().__init__(data=body, status=status_code, **kwargs)


def api_error(e, locale_hint: str = '') -> 'ApiResponse':
    """
    Convert any exception — especially DRF ValidationError with
    message_bn / message_en — into a clean ApiResponse(400).
    """
    if isinstance(e, ValidationError) and isinstance(e.detail, dict):
        detail   = e.detail
        msg_bn   = str(detail.get('message_bn', 'ব্যর্থ হয়েছে'))
        msg_en   = str(detail.get('message_en', 'Action failed'))
        return ApiResponse(
            message=msg_bn if locale_hint == 'bn' else msg_en,
            errors={'message_bn': msg_bn, 'message_en': msg_en},
            status_code=400,
        )
    return ApiResponse(message=str(e), errors=str(e), status_code=400)


def custom_exception_handler(exc, context):
    """Replace DRF's default error format with ApiResponse shape."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    raw = response.data
    if isinstance(raw, dict):
        first_key = next(iter(raw), None)
        msg = raw.get(first_key, "")
        if isinstance(msg, list):
            msg = msg[0]
        message = str(msg)
    elif isinstance(raw, list):
        message = str(raw[0]) if raw else "An error occurred"
    else:
        message = str(raw)

    code = {400: "VALIDATION_ERROR", 401: "AUTHENTICATION_REQUIRED",
            403: "PERMISSION_DENIED", 404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED"}.get(response.status_code, "ERROR")

    response.data = {
        "status":  "error",
        "message": message,
        "data":    {},
        "errors":  {"code": code, "details": raw},
    }
    return response

"""应用异常与错误响应模块。

负责统一定义业务异常、错误响应结构和 FastAPI 异常处理器，确保不同来源的错误都能
按一致的 JSON 结构返回给前端，OpenAPI 里也能复用同一套错误声明。
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.models.error import ErrorInfo, ErrorResponse


class AppError(Exception):
    """描述可直接映射成 API 响应的业务异常。

    业务层可以抛出这个异常，把 HTTP 状态码、稳定错误码和附加上下文一并交给统一异常处理器。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | list | str | None = None,
    ) -> None:
        """保存标准化错误信息，方便异常处理器直接返回。

        Args:
            status_code: 对应的 HTTP 状态码。
            code: 面向调用方的稳定错误码。
            message: 面向调用方的错误描述。
            details: 可选的附加上下文，可为字典、列表或字符串。
        """
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def not_found_error(resource: str, identifier: str | None = None) -> AppError:
    """构造资源不存在时用的标准异常。

    Args:
        resource: 资源名称，例如 `document` 或 `collection`。
        identifier: 资源标识，可选。

    Returns:
        统一格式的 404 业务异常对象。
    """
    message = f'{resource} not found'
    details = {'resource': resource}
    if identifier is not None:
        details['identifier'] = identifier
    return AppError(status_code=404, code=f'{resource}_not_found', message=message, details=details)


def bad_request_error(code: str, message: str, details: dict | list | str | None = None) -> AppError:
    """构造 400 请求错误异常。

    Args:
        code: 稳定错误码。
        message: 对调用方可见的错误描述。
        details: 可选的附加错误详情。

    Returns:
        统一格式的 400 业务异常对象。
    """
    return AppError(status_code=400, code=code, message=message, details=details)


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """生成 FastAPI 路由声明要用的错误响应描述。

    Args:
        *status_codes: 需要在 OpenAPI 中声明的 HTTP 状态码列表。

    Returns:
        可直接传给 FastAPI 路由装饰器 `responses` 参数的映射。
    """
    responses: dict[int | str, dict[str, Any]] = {}
    for status_code in status_codes:
        example = _error_response_example(status_code)
        responses[status_code] = {
            'model': ErrorResponse,
            'description': _default_message(status_code),
            'content': {
                'application/json': {
                    'example': example,
                }
            },
        }
    return responses


def register_exception_handlers(app: FastAPI) -> None:
    """给应用注册统一的异常处理器。

    Args:
        app: 需要挂载异常处理器的 FastAPI 应用实例。
    """

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """处理业务层主动抛出的标准异常。

        Args:
            request: 当前请求对象。
            exc: 业务层抛出的标准化异常。

        Returns:
            统一结构的 JSON 错误响应。
        """
        return JSONResponse(
            status_code=exc.status_code,
            content=_serialize_error(
                request=request,
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        """兼容 FastAPI 原生 HTTP 异常格式。

        Args:
            request: 当前请求对象。
            exc: FastAPI 或上游框架抛出的 HTTP 异常。

        Returns:
            转换后的统一 JSON 错误响应。
        """
        detail = exc.detail
        if isinstance(detail, dict):
            # 如果上游已经给了结构化 detail，这里就尽量直接复用，别再重新拼一套。
            code = str(detail.get('code') or _default_code(exc.status_code))
            message = str(detail.get('message') or detail.get('detail') or _default_message(exc.status_code))
            details = detail.get('details')
        else:
            code = _default_code(exc.status_code)
            message = str(detail or _default_message(exc.status_code))
            details = None

        return JSONResponse(
            status_code=exc.status_code,
            content=_serialize_error(
                request=request,
                status_code=exc.status_code,
                code=code,
                message=message,
                details=details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """把请求校验错误转换为统一结构。

        Args:
            request: 当前请求对象。
            exc: 请求体验证失败异常。

        Returns:
            包含字段级校验详情的 JSON 错误响应。
        """
        details = [
            {
                'loc': [str(item) for item in error.get('loc', ())],
                'message': error.get('msg'),
                'type': error.get('type'),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_serialize_error(
                request=request,
                status_code=422,
                code='validation_error',
                message='request validation failed',
                details=details,
            ),
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        """处理参数或状态不合法导致的值错误。

        Args:
            request: 当前请求对象。
            exc: 普通值错误异常。

        Returns:
            对应的 400 JSON 错误响应。
        """
        return JSONResponse(
            status_code=400,
            content=_serialize_error(
                request=request,
                status_code=400,
                code='bad_request',
                message=str(exc),
                details=None,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """兜底处理未预期异常，避免把栈信息直接暴露出去。

        Args:
            request: 当前请求对象。
            exc: 任何未被前面分支捕获的异常。

        Returns:
            对外隐藏内部实现细节的 500 JSON 错误响应。
        """
        return JSONResponse(
            status_code=500,
            content=_serialize_error(
                request=request,
                status_code=500,
                code='internal_server_error',
                message='internal server error',
                details={'reason': str(exc)},
            ),
        )


def _serialize_error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | list[Any] | str | None,
) -> dict[str, Any]:
    """把错误信息封装成统一响应体。

    Args:
        request: 当前请求对象，用于提取访问路径。
        status_code: HTTP 状态码。
        code: 稳定错误码。
        message: 错误描述。
        details: 额外错误详情。

    Returns:
        可直接序列化为 JSON 的统一错误响应字典。
    """
    payload = ErrorResponse(
        error=ErrorInfo(
            code=code,
            message=message,
            details=details,
        ),
        path=request.url.path,
    )
    body = payload.model_dump(mode='json')
    body['status_code'] = status_code
    return body


def _default_code(status_code: int) -> str:
    """根据状态码推导默认错误码。

    Args:
        status_code: HTTP 状态码。

    Returns:
        对应状态码的默认错误码字符串。
    """
    if status_code == 404:
        return 'not_found'
    if status_code == 403:
        return 'forbidden'
    if status_code == 401:
        return 'unauthorized'
    if status_code == 409:
        return 'conflict'
    if status_code == 400:
        return 'bad_request'
    return 'http_error'


def _default_message(status_code: int) -> str:
    """根据状态码生成默认错误描述。

    Args:
        status_code: HTTP 状态码。

    Returns:
        基于标准状态码短语推导出的默认消息。
    """
    try:
        return HTTPStatus(status_code).phrase.lower()
    except ValueError:
        return 'request failed'


def _error_response_example(status_code: int) -> dict[str, Any]:
    """返回 OpenAPI 文档中展示的错误响应示例。

    Args:
        status_code: 需要生成示例的 HTTP 状态码。

    Returns:
        适用于 OpenAPI 示例展示的错误响应字典。
    """
    examples = {
        400: {
            'error': {
                'code': 'bad_request',
                'message': 'request payload is invalid',
                'details': {
                    'field': 'collection_name',
                },
            },
            'path': '/api/v1/example',
            'timestamp': '2026-05-28T00:00:00Z',
            'status_code': 400,
        },
        404: {
            'error': {
                'code': 'resource_not_found',
                'message': 'resource not found',
                'details': {
                    'resource': 'resource',
                    'identifier': 'demo',
                },
            },
            'path': '/api/v1/example/demo',
            'timestamp': '2026-05-28T00:00:00Z',
            'status_code': 404,
        },
        422: {
            'error': {
                'code': 'validation_error',
                'message': 'request validation failed',
                'details': [
                    {
                        'loc': ['body', 'chunk_size'],
                        'message': 'Input should be greater than or equal to 100',
                        'type': 'greater_than_equal',
                    }
                ],
            },
            'path': '/api/v1/example',
            'timestamp': '2026-05-28T00:00:00Z',
            'status_code': 422,
        },
        500: {
            'error': {
                'code': 'internal_server_error',
                'message': 'internal server error',
                'details': {
                    'reason': 'unexpected failure',
                },
            },
            'path': '/api/v1/example',
            'timestamp': '2026-05-28T00:00:00Z',
            'status_code': 500,
        },
    }
    return examples.get(
        status_code,
        {
            'error': {
                'code': _default_code(status_code),
                'message': _default_message(status_code),
                'details': None,
            },
            'path': '/api/v1/example',
            'timestamp': '2026-05-28T00:00:00Z',
            'status_code': status_code,
        },
    )

"""
Handler global de exceções do DRF.

Todas as respostas de erro saem no mesmo envelope, o que simplifica o frontend:

    {
      "error": {
        "code": "validation_error",
        "message": "Os dados enviados são inválidos.",
        "details": {"title": ["O título é obrigatório."]}
      }
    }

Além do que o DRF já trata, converte:
- django.core.exceptions.ValidationError (vinda de model.full_clean) -> 400,
  renomeando campos do model para os nomes da API (team -> team_id etc.)
- IntegrityError (corrida que escapou da validação) -> 409
- ProtectedError (apagar algo referenciado) -> 409
- qualquer outra exceção -> 500 com mensagem genérica (o traceback vai pro log)
"""

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("api.errors")

# Nomes de campo do model -> nomes expostos na API.
MODEL_TO_API_FIELD = {
    "team": "team_id",
    "assignee": "assignee_id",
    "parent_issue": "parent_issue_id",
    "__all__": "non_field_errors",
}


class Conflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "O recurso foi alterado por outra operação. Tente novamente."
    default_code = "conflict"


def _django_validation_to_drf(exc: DjangoValidationError) -> exceptions.ValidationError:
    if hasattr(exc, "error_dict"):
        detail = {
            MODEL_TO_API_FIELD.get(field, field): messages
            for field, messages in exc.message_dict.items()
        }
    else:
        detail = {"non_field_errors": exc.messages}
    return exceptions.ValidationError(detail)


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = _django_validation_to_drf(exc)
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    elif isinstance(exc, ProtectedError):
        exc = Conflict("Este recurso ainda é referenciado por outros registros.")
    elif isinstance(exc, IntegrityError):
        logger.warning("IntegrityError convertido em 409: %s", exc)
        exc = Conflict()

    response = exception_handler(exc, context)

    if response is None:
        # Erro inesperado. Em DEBUG deixa o Django mostrar o traceback.
        if settings.DEBUG:
            return None
        logger.exception("Erro não tratado na API", exc_info=exc)
        return Response(
            {"error": {"code": "server_error", "message": "Erro interno do servidor.", "details": None}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, exceptions.ValidationError):
        code = "validation_error"
        message = "Os dados enviados são inválidos."
        details = response.data
    else:
        code = getattr(exc, "default_code", "error")
        detail = response.data.get("detail") if isinstance(response.data, dict) else response.data
        message = str(detail)
        details = None

    response.data = {"error": {"code": code, "message": message, "details": details}}
    return response

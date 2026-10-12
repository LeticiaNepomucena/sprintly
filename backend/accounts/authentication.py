"""
Autenticação por cabeçalho `X-User-Id`, só para desenvolvimento e testes.

Qualquer cliente pode se passar por qualquer usuário com ela, então fica
desligada a menos que ALLOW_X_USER_ID_AUTH=True (o padrão segue DEBUG).
Em produção use JWT: Authorization: Bearer <token>.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication


class XUserIdAuthentication(BaseAuthentication):
    header = "HTTP_X_USER_ID"

    def authenticate(self, request):
        if not getattr(settings, "ALLOW_X_USER_ID_AUTH", False):
            return None
        raw = request.META.get(self.header)
        if raw is None:
            return None
        try:
            user_id = int(raw)
        except (TypeError, ValueError):
            raise exceptions.AuthenticationFailed("Cabeçalho X-User-Id inválido.")
        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed("Usuário não encontrado ou inativo.")
        return (user, None)

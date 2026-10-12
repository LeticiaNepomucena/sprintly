from django.db.models import Count
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.reverse import reverse

from . import services
from .models import Issue, Team
from .serializers import (
    ActivityLogSerializer,
    IssueCreateSerializer,
    IssueSerializer,
    IssueUpdateSerializer,
    TeamSerializer,
)


class TeamViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Times são poucos; a lista não é paginada (alimenta selects no frontend)."""

    queryset = Team.objects.all()
    serializer_class = TeamSerializer
    pagination_class = None


class IssueViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    POST   /api/issues/                cria (201 + issue completa)
    GET    /api/issues/                lista com filtros
    GET    /api/issues/{id}/           detalhe
    PATCH  /api/issues/{id}/           atualização parcial
    GET    /api/issues/{id}/activity/  histórico

    PUT e DELETE não são expostos de propósito.
    """

    serializer_class = IssueSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = (
            Issue.objects.select_related("team", "creator", "assignee", "parent_issue")
            .annotate(sub_issue_count=Count("sub_issues"))
            .order_by("-created_at", "-id")
        )
        if self.action == "list":
            qs = self._apply_filters(qs)
        return qs

    # --- filtros -------------------------------------------------------------

    @staticmethod
    def _parse_choices(raw, allowed, field, errors):
        """Aceita valor único ou lista separada por vírgula: ?status=todo,done"""
        values = [v.strip() for v in raw.split(",") if v.strip()]
        invalid = [v for v in values if v not in allowed]
        if invalid:
            errors[field] = [
                f"Valor inválido: {', '.join(invalid)}. Opções: {', '.join(allowed)}."
            ]
        return values

    def _apply_filters(self, qs):
        params = self.request.query_params
        errors = {}

        if raw := params.get("status"):
            values = self._parse_choices(raw, Issue.Status.values, "status", errors)
            qs = qs.filter(status__in=values)

        if raw := params.get("priority"):
            values = self._parse_choices(raw, Issue.Priority.values, "priority", errors)
            qs = qs.filter(priority__in=values)

        # ?team=3 ou ?team=ENG
        if raw := params.get("team"):
            qs = qs.filter(team_id=int(raw)) if raw.isdigit() else qs.filter(team__key=raw.upper())

        # ?assignee=me | none | <id>
        if raw := params.get("assignee"):
            if raw == "me":
                qs = qs.filter(assignee=self.request.user)
            elif raw == "none":
                qs = qs.filter(assignee__isnull=True)
            elif raw.isdigit():
                qs = qs.filter(assignee_id=int(raw))
            else:
                errors["assignee"] = ["Use 'me', 'none' ou o id do usuário."]

        # ?parent=none (só issues de topo) | <id> (sub-issues de uma issue)
        if raw := params.get("parent"):
            if raw == "none":
                qs = qs.filter(parent_issue__isnull=True)
            elif raw.isdigit():
                qs = qs.filter(parent_issue_id=int(raw))
            else:
                errors["parent"] = ["Use 'none' ou o id da issue pai."]

        if raw := params.get("label"):
            qs = qs.filter(labels__contains=[raw])

        if errors:
            raise ValidationError(errors)
        return qs

    # --- escrita -------------------------------------------------------------

    def create(self, request, *args, **kwargs):
        serializer = IssueCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)

        # O criador vem SEMPRE da autenticação, nunca do payload.
        issue = services.create_issue(creator=request.user, **serializer.validated_data)

        # Relê com select_related/annotate para devolver os dados aninhados.
        issue = self.get_queryset().get(pk=issue.pk)
        data = IssueSerializer(issue, context=self.get_serializer_context()).data
        location = reverse("issue-detail", args=[issue.pk], request=request)
        return Response(data, status=status.HTTP_201_CREATED, headers={"Location": location})

    def partial_update(self, request, *args, **kwargs):
        issue = self.get_object()
        serializer = IssueUpdateSerializer(
            issue, data=request.data, partial=True, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        services.update_issue(issue=issue, actor=request.user, changes=serializer.validated_data)

        issue = self.get_queryset().get(pk=issue.pk)
        return Response(IssueSerializer(issue, context=self.get_serializer_context()).data)

    # --- histórico -----------------------------------------------------------

    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        issue = self.get_object()
        logs = issue.activity.select_related("actor")
        page = self.paginate_queryset(logs)
        serializer = ActivityLogSerializer(page if page is not None else logs, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

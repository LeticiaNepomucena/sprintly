from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.core.validators import RegexValidator
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from common.serializers import StrictFieldsMixin

from .models import TEAM_KEY_REGEX, ActivityLog, Issue, Team

User = get_user_model()


# --- leitura -----------------------------------------------------------------


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "display_name", "avatar_url"]
        read_only_fields = fields


class UpperCaseCharField(serializers.CharField):
    def to_internal_value(self, data):
        return super().to_internal_value(data).upper()


class TeamSerializer(StrictFieldsMixin, serializers.ModelSerializer):
    key = UpperCaseCharField(
        max_length=10,
        validators=[
            RegexValidator(
                TEAM_KEY_REGEX,
                "A chave deve ter de 2 a 10 caracteres: letras maiúsculas e "
                "dígitos, começando por uma letra (ex.: ENG).",
            ),
            UniqueValidator(queryset=Team.objects.all(), message="Já existe um time com essa chave."),
        ],
    )

    class Meta:
        model = Team
        fields = ["id", "name", "key", "created_at"]
        read_only_fields = ["id", "created_at"]


class IssueParentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Issue
        fields = ["id", "identifier", "title", "status"]
        read_only_fields = fields


class IssueSerializer(serializers.ModelSerializer):
    """Representação completa devolvida por todos os endpoints de issue."""

    team = TeamSerializer(read_only=True)
    creator = UserSummarySerializer(read_only=True)
    assignee = UserSummarySerializer(read_only=True)
    parent_issue = IssueParentSerializer(read_only=True)
    sub_issue_count = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = [
            "id",
            "identifier",
            "number",
            "title",
            "description",
            "status",
            "priority",
            "labels",
            "team",
            "creator",
            "assignee",
            "parent_issue",
            "sub_issue_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_sub_issue_count(self, obj) -> int:
        annotated = getattr(obj, "sub_issue_count", None)
        return annotated if annotated is not None else obj.sub_issues.count()


class ActivityLogSerializer(serializers.ModelSerializer):
    actor = UserSummarySerializer(read_only=True)

    class Meta:
        model = ActivityLog
        fields = ["id", "action", "actor", "changes", "created_at"]
        read_only_fields = fields


# --- escrita -----------------------------------------------------------------


class IssueWriteBaseSerializer(StrictFieldsMixin, serializers.Serializer):
    """Campos comuns a criação e edição.

    `creator`, `identifier` e `number` não aparecem aqui de propósito: são
    definidos pelo servidor e o StrictFieldsMixin rejeita tentativas de enviá-los.
    """

    title = serializers.CharField(
        max_length=255,
        error_messages={
            "required": "O título é obrigatório.",
            "blank": "O título não pode ficar em branco.",
            "null": "O título não pode ser nulo.",
            "max_length": "O título pode ter no máximo {max_length} caracteres.",
        },
    )
    description = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False, max_length=50_000
    )
    status = serializers.ChoiceField(choices=Issue.Status.choices, required=False)
    priority = serializers.ChoiceField(choices=Issue.Priority.choices, required=False)
    assignee_id = serializers.PrimaryKeyRelatedField(
        source="assignee",
        queryset=User.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        error_messages={
            "does_not_exist": "Usuário {pk_value} não encontrado ou inativo.",
            "incorrect_type": "assignee_id deve ser um inteiro.",
        },
    )
    labels = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        max_length=20,
        error_messages={"max_length": "No máximo {max_length} labels por issue."},
    )
    parent_issue_id = serializers.PrimaryKeyRelatedField(
        source="parent_issue",
        queryset=Issue.objects.select_related("team"),
        required=False,
        allow_null=True,
        error_messages={
            "does_not_exist": "Issue pai {pk_value} não encontrada.",
            "incorrect_type": "parent_issue_id deve ser um inteiro.",
        },
    )

    def validate_labels(self, value):
        # Remove duplicadas (sem diferenciar maiúsculas) mantendo a ordem.
        seen, result = set(), []
        for label in value:
            norm = label.casefold()
            if norm not in seen:
                seen.add(norm)
                result.append(label)
        return result


class IssueCreateSerializer(IssueWriteBaseSerializer):
    team_id = serializers.PrimaryKeyRelatedField(
        source="team",
        queryset=Team.objects.all(),
        error_messages={
            "required": "O time (team_id) é obrigatório.",
            "null": "O time (team_id) é obrigatório.",
            "does_not_exist": "Time {pk_value} não existe.",
            "incorrect_type": "team_id deve ser um inteiro.",
        },
    )


class IssueUpdateSerializer(IssueWriteBaseSerializer):
    def to_internal_value(self, data):
        if isinstance(data, Mapping) and "team_id" in data:
            raise serializers.ValidationError(
                {"team_id": ["Uma issue não pode mudar de time (o identificador mudaria)."]}
            )
        return super().to_internal_value(data)

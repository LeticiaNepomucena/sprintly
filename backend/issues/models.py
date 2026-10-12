"""
Modelos de domínio: Team, Issue e ActivityLog.

Decisões principais:
- O número sequencial vive num contador desnormalizado em Team (issue_counter).
  Ele é incrementado com UPDATE ... SET issue_counter = issue_counter + 1 dentro
  de uma transação (ver services.create_issue). O UPDATE trava a linha do time,
  então criações concorrentes no mesmo time são serializadas e nunca geram
  números duplicados. Se a transação falhar, o incremento é revertido junto,
  e nenhum número é "queimado".
- O identificador (ENG-42) é gravado na própria issue e tem índice único.
  Por isso a chave de um time é imutável depois de criada.
- As regras que o banco consegue garantir sozinho viram CheckConstraint/
  UniqueConstraint. As que precisam de consultas (mesmo time do pai, ciclos)
  ficam em Issue.clean(), chamado pela camada de serviço.
"""

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

TEAM_KEY_REGEX = r"^[A-Z][A-Z0-9]{1,9}$"
MAX_PARENT_DEPTH = 50


class Team(models.Model):
    name = models.CharField(max_length=120)
    key = models.CharField(
        max_length=10,
        unique=True,
        validators=[
            RegexValidator(
                TEAM_KEY_REGEX,
                "A chave deve ter de 2 a 10 caracteres: letras maiúsculas e "
                "dígitos, começando por uma letra (ex.: ENG, PROD).",
            )
        ],
        help_text="Prefixo dos identificadores das issues (ex.: ENG). Imutável.",
    )
    # Último número emitido para este time. Só a camada de serviço altera.
    issue_counter = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.CheckConstraint(
                condition=Q(key__regex=TEAM_KEY_REGEX), name="team_key_format"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.key:
            self.key = self.key.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.key} · {self.name}"


class Issue(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "Todo"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="issues")
    number = models.PositiveIntegerField(editable=False)
    identifier = models.CharField(max_length=24, unique=True, editable=False)

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.TODO
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM
    )

    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_issues",
    )
    # PROTECT: o histórico de autoria não pode sumir apagando um usuário.
    # Desative usuários (is_active=False) em vez de apagá-los.
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_issues",
    )
    labels = models.JSONField(default=list, blank=True)
    # SET_NULL: apagar a issue pai promove as sub-issues a issues de topo,
    # em vez de apagar trabalho em cascata.
    parent_issue = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sub_issues",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "number"], name="issue_unique_number_per_team"
            ),
            models.CheckConstraint(
                condition=~Q(title=""), name="issue_title_not_empty"
            ),
            models.CheckConstraint(
                condition=Q(status__in=Status.values), name="issue_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(priority__in=Priority.values),
                name="issue_priority_valid",
            ),
            models.CheckConstraint(
                condition=~Q(parent_issue=F("id")), name="issue_not_own_parent"
            ),
        ]
        indexes = [
            # Quadro Kanban: "issues do time X agrupadas por status".
            models.Index(fields=["team", "status"], name="issue_team_status_idx"),
            models.Index(fields=["assignee", "status"], name="issue_assignee_status_idx"),
            models.Index(fields=["priority"], name="issue_priority_idx"),
            # Filtro por label: labels @> '["bug"]'
            GinIndex(fields=["labels"], name="issue_labels_gin", opclasses=["jsonb_path_ops"]),
        ]

    def __str__(self):
        return f"{self.identifier} {self.title}"

    # --- validação de domínio -------------------------------------------------

    def clean(self):
        super().clean()
        errors = {}

        if self.title is not None and not self.title.strip():
            errors["title"] = "O título não pode ficar em branco."

        if not isinstance(self.labels, list) or not all(
            isinstance(label, str) for label in self.labels
        ):
            errors["labels"] = "Labels deve ser uma lista de textos."

        if self.parent_issue_id:
            if self.pk and self.parent_issue_id == self.pk:
                errors["parent_issue"] = "Uma issue não pode ser pai de si mesma."
            elif self.team_id and self.parent_issue.team_id != self.team_id:
                errors["parent_issue"] = "A issue pai deve pertencer ao mesmo time."
            elif self.pk and self._parent_chain_contains_self():
                errors["parent_issue"] = (
                    "Essa vinculação criaria um ciclo na hierarquia de issues."
                )

        if errors:
            raise ValidationError(errors)

    def _parent_chain_contains_self(self) -> bool:
        """Sobe a cadeia de pais a partir do novo pai e procura por self.

        Também falha de forma segura se a hierarquia for absurdamente profunda
        ou se já houver um ciclo pré-existente nos dados.
        """
        seen = set()
        current_id = self.parent_issue_id
        while current_id is not None:
            if current_id == self.pk or current_id in seen:
                return True
            if len(seen) >= MAX_PARENT_DEPTH:
                return True
            seen.add(current_id)
            current_id = (
                Issue.objects.filter(pk=current_id)
                .values_list("parent_issue_id", flat=True)
                .first()
            )
        return False


class ActivityLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        STATUS_CHANGED = "status_changed", "Status changed"

    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="activity")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="activity",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    # Formato: {"campo": {"from": <antes>, "to": <depois>}}
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["issue", "-created_at"], name="activity_issue_created_idx"),
        ]

    def __str__(self):
        return f"{self.issue_id} {self.action} @ {self.created_at:%Y-%m-%d %H:%M}"

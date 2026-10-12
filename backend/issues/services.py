"""
Camada de serviço: toda escrita de Issue passa por aqui.

Por que não usar signals para o Activity Log? Porque o log precisa saber
QUEM fez a alteração e O QUE mudou (antes/depois). Um post_save não tem o
usuário nem o estado anterior. Aqui temos os dois, e a escrita da issue e
do log acontece na mesma transação.
"""

from django.db import transaction
from django.db.models import F

from .models import ActivityLog, Issue, Team

WRITABLE_FIELDS = {
    "title",
    "description",
    "status",
    "priority",
    "assignee",
    "labels",
    "parent_issue",
}

# Campos registrados no log, pelo nome usado na API (attname = coluna).
TRACKED_ATTRS = (
    "title",
    "description",
    "status",
    "priority",
    "assignee_id",
    "labels",
    "parent_issue_id",
)

# A descrição pode ser longa; no log só registramos que ela mudou.
SUMMARY_ONLY_ATTRS = {"description"}


def _check_fields(fields):
    unexpected = set(fields) - WRITABLE_FIELDS
    if unexpected:
        raise TypeError(f"Campos não graváveis: {sorted(unexpected)}")


def _snapshot(issue: Issue) -> dict:
    snap = {}
    for attr in TRACKED_ATTRS:
        value = getattr(issue, attr)
        snap[attr] = list(value) if isinstance(value, list) else value
    return snap


def _diff(before: dict, after: dict) -> dict:
    changes = {}
    for attr in TRACKED_ATTRS:
        if before[attr] == after[attr]:
            continue
        if attr in SUMMARY_ONLY_ATTRS:
            changes[attr] = {"changed": True}
        else:
            changes[attr] = {"from": before[attr], "to": after[attr]}
    return changes


def _next_issue_number(team: Team) -> int:
    """Reserva o próximo número do time de forma segura sob concorrência.

    O UPDATE atômico trava a linha do time até o fim da transação. Outra
    transação criando issue no mesmo time espera, e então lê o valor já
    incrementado. Precisa ser chamado dentro de transaction.atomic().
    """
    updated = Team.objects.filter(pk=team.pk).update(issue_counter=F("issue_counter") + 1)
    if not updated:
        raise Team.DoesNotExist(f"Time {team.pk} não existe.")
    return Team.objects.values_list("issue_counter", flat=True).get(pk=team.pk)


@transaction.atomic
def create_issue(*, creator, team: Team, **fields) -> Issue:
    _check_fields(fields)

    number = _next_issue_number(team)
    issue = Issue(
        team=team,
        number=number,
        identifier=f"{team.key}-{number}",
        creator=creator,
        **fields,
    )
    # Valida campos, regras de domínio (clean) e constraints antes do INSERT.
    # Se algo falhar, a exceção desfaz a transação inteira, incluindo o
    # incremento do contador.
    issue.full_clean()
    issue.save()

    initial = _snapshot(issue)
    ActivityLog.objects.create(
        issue=issue,
        actor=creator,
        action=ActivityLog.Action.CREATED,
        changes={
            attr: ({"changed": True} if attr in SUMMARY_ONLY_ATTRS else {"from": None, "to": value})
            for attr, value in initial.items()
            if value not in (None, "", [])
        },
    )
    return issue


@transaction.atomic
def update_issue(*, issue: Issue, actor, changes: dict) -> Issue:
    _check_fields(changes)
    if not changes:
        return issue

    # Relê com lock para que duas edições simultâneas não percam o diff.
    issue = Issue.objects.select_for_update().get(pk=issue.pk)
    before = _snapshot(issue)

    for field, value in changes.items():
        setattr(issue, field, value)
    issue.full_clean()
    issue.save()

    diff = _diff(before, _snapshot(issue))
    if not diff:
        return issue

    status_change = diff.pop("status", None)
    if status_change:
        ActivityLog.objects.create(
            issue=issue,
            actor=actor,
            action=ActivityLog.Action.STATUS_CHANGED,
            changes={"status": status_change},
        )
    if diff:
        ActivityLog.objects.create(
            issue=issue,
            actor=actor,
            action=ActivityLog.Action.UPDATED,
            changes=diff,
        )
    return issue

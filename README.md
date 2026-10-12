# Sprintly — backend (Django + DRF + PostgreSQL)

## Rodando

```bash
docker compose up -d db                     # PostgreSQL 16 na porta 5432
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && set -a && . ./.env && set +a
python manage.py makemigrations accounts issues
python manage.py migrate
python manage.py createsuperuser
python manage.py test issues                # 24 testes da API
python manage.py runserver
```

## Estrutura

```
backend/
  config/      settings, urls (rotas da API + JWT)
  accounts/    User customizado + autenticação X-User-Id (só dev)
  issues/
    models.py       Team, Issue, ActivityLog + constraints
    services.py     create_issue / update_issue (contador, validação, log)
    serializers.py  leitura (aninhada) separada de escrita (create/update)
    views.py        IssueViewSet, TeamViewSet
    tests.py
  common/      handler de erros, paginação, StrictFieldsMixin
```

## Fluxo do `POST /api/issues/`

1. `IssueCreateSerializer` valida o formato: título obrigatório e não vazio,
   `team_id` existente, choices válidas, labels (máx. 20, sem duplicatas),
   e **rejeita campos desconhecidos** (`creator`, `identifier`...).
2. `services.create_issue` abre uma transação, incrementa `Team.issue_counter`
   com um `UPDATE` atômico (trava a linha do time), monta `ENG-<n>`,
   roda `full_clean()` (regras de domínio: pai no mesmo time, sem ciclos)
   e grava a issue + o `ActivityLog` de criação.
3. Qualquer erro desfaz tudo, inclusive o número reservado.
4. A view relê a issue com `select_related` e responde `201` com time,
   criador, responsável e pai aninhados, mais o cabeçalho `Location`.

## Envelope de erro

```json
{
  "error": {
    "code": "validation_error",
    "message": "Os dados enviados são inválidos.",
    "details": { "title": ["O título é obrigatório."] }
  }
}
```

| Situação | Status | code |
|---|---|---|
| Payload inválido / regra de domínio | 400 | `validation_error` |
| Sem credenciais / token inválido | 401 | `not_authenticated` / `authentication_failed` |
| Issue inexistente | 404 | `not_found` |
| Corrida que violou constraint no banco | 409 | `conflict` |
| Erro inesperado (fora de DEBUG) | 500 | `server_error` |

## Autenticação

- **JWT** (padrão): `POST /api/auth/token/` com `username`/`password` →
  `Authorization: Bearer <access>`.
- **`X-User-Id: <id>`**: só quando `ALLOW_X_USER_ID_AUTH=true` (padrão = DEBUG).
  Permite se passar por qualquer usuário; nunca ligue em produção.

## Filtros do `GET /api/issues/`

`status=todo,in_progress` · `priority=high` · `team=ENG` ou `team=3` ·
`assignee=me|none|<id>` · `parent=none|<id>` · `label=bug` · `page`, `page_size` (máx. 200)

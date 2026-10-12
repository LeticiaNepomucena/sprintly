from django.contrib import admin

from .models import ActivityLog, Issue, Team


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "issue_counter", "created_at")
    readonly_fields = ("issue_counter", "created_at")

    def get_readonly_fields(self, request, obj=None):
        # A chave é imutável depois de criada (os identificadores dependem dela).
        return self.readonly_fields + (("key",) if obj else ())


class ActivityInline(admin.TabularInline):
    model = ActivityLog
    extra = 0
    can_delete = False
    readonly_fields = ("action", "actor", "changes", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    # Edições pelo admin não passam pela camada de serviço e não geram log;
    # por isso o admin é somente leitura para issues.
    list_display = ("identifier", "title", "status", "priority", "assignee", "team")
    list_filter = ("team", "status", "priority")
    search_fields = ("identifier", "title")
    inlines = [ActivityInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

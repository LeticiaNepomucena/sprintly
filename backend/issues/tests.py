from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from .models import ActivityLog, Issue, Team

User = get_user_model()
URL = "/api/issues/"


class BaseIssueTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ana", password="senha-forte-123")
        cls.other = User.objects.create_user(username="bruno", password="senha-forte-123")
        cls.eng = Team.objects.create(name="Engineering", key="ENG")
        cls.prod = Team.objects.create(name="Product", key="prod")  # vira PROD no save

    def setUp(self):
        self.client.force_authenticate(self.user)

    def create(self, **payload):
        payload.setdefault("team_id", self.eng.pk)
        return self.client.post(URL, payload, format="json")


class IssueCreateTests(BaseIssueTest):
    def test_creates_issue_with_sequential_identifier(self):
        r1 = self.create(title="Primeira")
        r2 = self.create(title="Segunda", priority="high", labels=["bug", "Bug", "api"])

        self.assertEqual(r1.status_code, 201, r1.data)
        self.assertEqual(r1.data["identifier"], "ENG-1")
        self.assertEqual(r2.data["identifier"], "ENG-2")
        self.assertEqual(r2.data["labels"], ["bug", "api"])  # duplicata removida
        self.assertEqual(r1.data["team"]["key"], "ENG")
        self.assertEqual(r1.data["creator"]["username"], "ana")
        self.assertEqual(r1.data["status"], "todo")
        self.assertTrue(r1["Location"].endswith(f"/api/issues/{r1.data['id']}/"))

    def test_sequence_is_independent_per_team(self):
        self.create(title="a")
        r = self.create(title="b", team_id=self.prod.pk)
        self.assertEqual(r.data["identifier"], "PROD-1")

    def test_title_is_required_and_not_blank(self):
        for payload in ({}, {"title": ""}, {"title": "   "}, {"title": None}):
            r = self.create(**payload)
            self.assertEqual(r.status_code, 400, payload)
            self.assertEqual(r.data["error"]["code"], "validation_error")
            self.assertIn("title", r.data["error"]["details"])

    def test_team_must_exist(self):
        r = self.create(title="x", team_id=99999)
        self.assertEqual(r.status_code, 400)
        self.assertIn("team_id", r.data["error"]["details"])

    def test_team_is_required(self):
        r = self.client.post(URL, {"title": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("team_id", r.data["error"]["details"])

    def test_invalid_choices_are_rejected(self):
        r = self.create(title="x", status="blocked", priority="urgent")
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.data["error"]["details"])
        self.assertIn("priority", r.data["error"]["details"])

    def test_server_controlled_fields_cannot_be_sent(self):
        r = self.create(title="x", creator=self.other.pk, identifier="ENG-999")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.data["error"]["details"]), {"creator", "identifier"})

    def test_creator_comes_from_authentication(self):
        r = self.create(title="x", assignee_id=self.other.pk)
        self.assertEqual(r.data["creator"]["id"], self.user.pk)
        self.assertEqual(r.data["assignee"]["id"], self.other.pk)

    def test_unauthenticated_returns_401(self):
        r = APIClient().post(URL, {"title": "x", "team_id": self.eng.pk}, format="json")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.data["error"]["code"], "not_authenticated")

    @override_settings(ALLOW_X_USER_ID_AUTH=True)
    def test_x_user_id_header_authentication(self):
        client = APIClient()
        ok = client.post(URL, {"title": "x", "team_id": self.eng.pk}, format="json",
                         HTTP_X_USER_ID=str(self.other.pk))
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.data["creator"]["username"], "bruno")

        bad = client.post(URL, {"title": "x", "team_id": self.eng.pk}, format="json",
                          HTTP_X_USER_ID="999999")
        self.assertEqual(bad.status_code, 401)

    @override_settings(ALLOW_X_USER_ID_AUTH=False)
    def test_x_user_id_header_ignored_when_disabled(self):
        r = APIClient().post(URL, {"title": "x", "team_id": self.eng.pk}, format="json",
                             HTTP_X_USER_ID=str(self.user.pk))
        self.assertEqual(r.status_code, 401)

    def test_sub_issue_is_linked_to_parent(self):
        parent = self.create(title="Épico").data
        child = self.create(title="Filha", parent_issue_id=parent["id"])
        self.assertEqual(child.status_code, 201)
        self.assertEqual(child.data["parent_issue"]["identifier"], parent["identifier"])

    def test_sub_issue_must_belong_to_same_team(self):
        parent = self.create(title="Épico").data
        r = self.create(title="Filha", team_id=self.prod.pk, parent_issue_id=parent["id"])
        self.assertEqual(r.status_code, 400)
        self.assertIn("parent_issue_id", r.data["error"]["details"])

    def test_failed_creation_does_not_consume_a_number(self):
        parent = self.create(title="PROD", team_id=self.prod.pk).data
        bad = self.create(title="x", parent_issue_id=parent["id"])  # pai de outro time
        self.assertEqual(bad.status_code, 400)
        ok = self.create(title="y")
        self.assertEqual(ok.data["identifier"], "ENG-1")

    def test_creation_writes_activity_log(self):
        r = self.create(title="Logada", priority="high")
        log = ActivityLog.objects.get(issue_id=r.data["id"])
        self.assertEqual(log.action, ActivityLog.Action.CREATED)
        self.assertEqual(log.actor, self.user)
        self.assertEqual(log.changes["priority"], {"from": None, "to": "high"})


class IssueUpdateTests(BaseIssueTest):
    def setUp(self):
        super().setUp()
        self.issue = self.create(title="Original").data

    def patch(self, issue_id=None, **payload):
        return self.client.patch(f"{URL}{issue_id or self.issue['id']}/", payload, format="json")

    def test_status_change_is_logged_separately(self):
        r = self.patch(status="in_progress", title="Renomeada")
        self.assertEqual(r.status_code, 200, r.data)
        actions = dict(ActivityLog.objects.filter(issue_id=self.issue["id"]).values_list("action", "changes"))
        self.assertEqual(actions["status_changed"], {"status": {"from": "todo", "to": "in_progress"}})
        self.assertEqual(actions["updated"], {"title": {"from": "Original", "to": "Renomeada"}})

    def test_noop_patch_does_not_log(self):
        self.patch(title="Original")
        self.assertEqual(ActivityLog.objects.filter(issue_id=self.issue["id"]).count(), 1)

    def test_team_cannot_change(self):
        r = self.patch(team_id=self.prod.pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("team_id", r.data["error"]["details"])

    def test_issue_cannot_be_its_own_parent(self):
        r = self.patch(parent_issue_id=self.issue["id"])
        self.assertEqual(r.status_code, 400)

    def test_parent_cycle_is_rejected(self):
        child = self.create(title="Filha", parent_issue_id=self.issue["id"]).data
        r = self.patch(parent_issue_id=child["id"])  # pai passaria a ser filho da filha
        self.assertEqual(r.status_code, 400)
        self.assertIn("parent_issue_id", r.data["error"]["details"])

    def test_blank_title_rejected_on_update(self):
        r = self.patch(title="  ")
        self.assertEqual(r.status_code, 400)

    def test_unknown_issue_returns_404(self):
        r = self.patch(issue_id=999999, title="x")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.data["error"]["code"], "not_found")


class IssueListTests(BaseIssueTest):
    def setUp(self):
        super().setUp()
        self.create(title="a", status="todo", priority="low")
        self.create(title="b", status="done", priority="high")
        self.create(title="c", team_id=self.prod.pk, status="in_progress")

    def titles(self, query):
        r = self.client.get(f"{URL}?{query}")
        self.assertEqual(r.status_code, 200, r.data)
        return sorted(i["title"] for i in r.data["results"])

    def test_filters(self):
        self.assertEqual(self.titles("status=todo,done"), ["a", "b"])
        self.assertEqual(self.titles("priority=high"), ["b"])
        self.assertEqual(self.titles("team=prod"), ["c"])
        self.assertEqual(self.titles(f"team={self.eng.pk}"), ["a", "b"])

    def test_invalid_filter_returns_400(self):
        r = self.client.get(f"{URL}?status=blocked")
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.data["error"]["details"])

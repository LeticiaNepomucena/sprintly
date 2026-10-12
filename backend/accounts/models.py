from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Usuário customizado desde o início: trocar AUTH_USER_MODEL depois é doloroso."""

    display_name = models.CharField(max_length=120, blank=True)
    avatar_url = models.URLField(blank=True)

    def __str__(self):
        return self.display_name or self.username

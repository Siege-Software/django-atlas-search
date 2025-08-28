from django.apps import AppConfig


class DjangoAtlasSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_atlas_search"

    def ready(self):
        import django_atlas_search.signals

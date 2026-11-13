from importlib import import_module

from django.apps import AppConfig


class ConnectorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connectors"

    def ready(self) -> None:
        import_module("connectors.ashby")
        import_module("connectors.greenhouse")
        import_module("connectors.lever")
        import_module("connectors.workable")

from importlib import import_module

from django.apps import AppConfig


class ConnectorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connectors"

    def ready(self) -> None:
        import_module("connectors.bamboohr")
        import_module("connectors.ashby")
        import_module("connectors.breezy")
        import_module("connectors.freshteam")
        import_module("connectors.greenhouse")
        import_module("connectors.lever")
        import_module("connectors.personio")
        import_module("connectors.pinpoint")
        import_module("connectors.recruitee")
        import_module("connectors.rippling")
        import_module("connectors.smartrecruiters")
        import_module("connectors.teamtailor")
        import_module("connectors.workable")

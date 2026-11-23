from importlib import import_module

from django.apps import AppConfig


class ConnectorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connectors"

    def ready(self) -> None:
        import_module("connectors.bamboohr")
        import_module("connectors.ashby")
        import_module("connectors.breezy")
        import_module("connectors.comeet")
        import_module("connectors.dover")
        import_module("connectors.freshteam")
        import_module("connectors.gem")
        import_module("connectors.greenhouse")
        import_module("connectors.hibob")
        import_module("connectors.jazzhr")
        import_module("connectors.jobvite")
        import_module("connectors.join_com")
        import_module("connectors.lever")
        import_module("connectors.manatal")
        import_module("connectors.personio")
        import_module("connectors.pinpoint")
        import_module("connectors.polymer")
        import_module("connectors.recruitee")
        import_module("connectors.recooty")
        import_module("connectors.rippling")
        import_module("connectors.smartrecruiters")
        import_module("connectors.teamtailor")
        import_module("connectors.workday")
        import_module("connectors.workable")
        import_module("connectors.zoho_recruit")

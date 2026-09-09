from django.apps import AppConfig


class WebsiteConfig(AppConfig):
    name = "website"
    verbose_name = "Website"

    def ready(self):
        import website.checks  # noqa: F401  (registers the deployment checks)

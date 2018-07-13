from nose.tools import set_trace
import importlib
import contextlib
import datetime
from collections import defaultdict
from model import ExternalIntegration
from config import CannotLoadConfiguration

class Analytics(object):

    def __init__(self, _db):
        self.sitewide_providers = []
        self.library_providers = defaultdict(list)
        self.initialization_exceptions = {}

        # Find a list of all the ExternalIntegrations set up with a
        # goal of analytics.
        integrations = _db.query(ExternalIntegration).filter(ExternalIntegration.goal==ExternalIntegration.ANALYTICS_GOAL)
        # Turn each integration into an analytics provider.
        for integration in integrations:
            try:
                provider_module = importlib.import_module(integration.protocol)
                provider_class = getattr(provider_module, "Provider", None)
                if provider_class:
                    if not integration.libraries:
                        provider = provider_class(integration)
                        self.sitewide_providers.append(provider)
                    else:
                        for library in integration.libraries:
                            provider = provider_class(integration, library)
                            self.library_providers[library.id].append(provider)
                else:
                    self.initialization_exceptions[integration.id] = "Module %s does not have Provider defined." % integration.protocol
            except (ImportError, CannotLoadConfiguration), e:
                self.initialization_exceptions[integration.id] = e

    def collect_event(self, library, license_pool, event_type, time=None, **kwargs):
        if not time:
            time = datetime.datetime.utcnow()
        providers = list(self.sitewide_providers)
        if library:
            providers.extend(self.library_providers[library.id])
        for provider in providers:
            provider.collect_event(library, license_pool, event_type, time, **kwargs)

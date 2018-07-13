import contextlib
import datetime
from nose.tools import set_trace
import os
import json
import logging
import copy
from sqlalchemy.engine.url import make_url
from flask_babel import lazy_gettext as _

from facets import FacetConstants
from entrypoint import EntryPoint

from sqlalchemy.exc import ArgumentError

from util import LanguageCodes

# It's convenient for other modules import IntegrationException
# from this module, alongside CannotLoadConfiguration.
from util.http import IntegrationException


class CannotLoadConfiguration(IntegrationException):
    """The current configuration of an external integration, or of the
    site as a whole, is in an incomplete or inconsistent state.

    This is more specific than a base IntegrationException because it
    assumes the problem is evident just by looking at the current
    configuration, with no need to actually talk to the foreign
    server.
    """
    pass


@contextlib.contextmanager
def temp_config(new_config=None, replacement_classes=None):
    old_config = Configuration.instance
    replacement_classes = replacement_classes or [Configuration]
    if new_config is None:
        new_config = copy.deepcopy(old_config)
    try:
        for c in replacement_classes:
            c.instance = new_config
        yield new_config
    finally:
        for c in replacement_classes:
            c.instance = old_config

@contextlib.contextmanager
def empty_config(replacement_classes=None):
    with temp_config({}, replacement_classes) as i:
        yield i


class Configuration(object):

    log = logging.getLogger("Configuration file loader")

    instance = None

    # Environment variables that contain URLs to the database
    DATABASE_TEST_ENVIRONMENT_VARIABLE = 'SIMPLIFIED_TEST_DATABASE'
    DATABASE_PRODUCTION_ENVIRONMENT_VARIABLE = 'SIMPLIFIED_PRODUCTION_DATABASE'

    # The version of the app.
    APP_VERSION = 'app_version'
    VERSION_FILENAME = '.version'
    NO_APP_VERSION_FOUND = object()

    # Logging stuff
    LOGGING_LEVEL = "level"
    LOGGING_FORMAT = "format"
    LOG_FORMAT_TEXT = "text"
    LOG_FORMAT_JSON = "json"

    # Logging
    LOGGING = "logging"
    LOG_LEVEL = "level"
    DATABASE_LOG_LEVEL = "database_level"
    LOG_OUTPUT_TYPE = "output"
    LOG_DATA_FORMAT = "format"

    DATA_DIRECTORY = "data_directory"

    # ConfigurationSetting key for the base url of the app.
    BASE_URL_KEY = u'base_url'

    # Policies, mostly circulation specific
    POLICIES = "policies"
    LANES_POLICY = "lanes"

    # Lane policies
    DEFAULT_OPDS_FORMAT = "verbose_opds_entry"

    ANALYTICS_POLICY = "analytics"

    LOCALIZATION_LANGUAGES = "localization_languages"

    # Integrations
    URL = "url"
    NAME = "name"
    TYPE = "type"
    INTEGRATIONS = "integrations"
    DATABASE_INTEGRATION = u"Postgres"
    DATABASE_PRODUCTION_URL = "production_url"
    DATABASE_TEST_URL = "test_url"

    CONTENT_SERVER_INTEGRATION = u"Content Server"

    AXIS_INTEGRATION = "Axis 360"
    ONECLICK_INTEGRATION = "OneClick"
    OVERDRIVE_INTEGRATION = "Overdrive"
    THREEM_INTEGRATION = "3M"

    # ConfigurationSetting key for a CDN's mirror domain
    CDN_MIRRORED_DOMAIN_KEY = u'mirrored_domain'

    UNINITIALIZED_CDNS = object()

    # The names of the site-wide configuration settings that determine
    # feed cache time.
    NONGROUPED_MAX_AGE_POLICY = "default_nongrouped_feed_max_age"
    GROUPED_MAX_AGE_POLICY = "default_grouped_feed_max_age"

    # The name of the per-library configuration policy that controls whether
    # books may be put on hold.
    ALLOW_HOLDS = "allow_holds"

    # Each library may set a minimum quality for the books that show
    # up in the 'featured' lanes that show up on the front page.
    MINIMUM_FEATURED_QUALITY = "minimum_featured_quality"

    # Each library may configure the maximum number of books in the
    # 'featured' lanes.
    FEATURED_LANE_SIZE = "featured_lane_size"

    # Each facet group has two associated per-library keys: one
    # configuring which facets are enabled for that facet group, and
    # one configuring which facet is the default.
    ENABLED_FACETS_KEY_PREFIX = "facets_enabled_"
    DEFAULT_FACET_KEY_PREFIX = "facets_default_"

    # The name of the per-library per-patron authentication integration
    # regular expression used to derive a patron's external_type from
    # their authorization_identifier.
    EXTERNAL_TYPE_REGULAR_EXPRESSION = 'external_type_regular_expression'

    WEBSITE_URL = u'website'

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"

    # The default value to put into the 'app' field of JSON-format logs,
    # unless LOG_APP_NAME overrides it.
    DEFAULT_APP_NAME = 'simplified'

    # Settings for the integration with protocol=INTERNAL_LOGGING
    LOG_LEVEL = 'log_level'
    LOG_APP_NAME = 'log_app'
    DATABASE_LOG_LEVEL = 'database_log_level'
    LOG_LEVEL_UI = [
        { "key": DEBUG, "label": _("Debug") },
        { "key": INFO, "label": _("Info") },
        { "key": WARN, "label": _("Warn") },
        { "key": ERROR, "label": _("Error") },
    ]

    SITEWIDE_SETTINGS = [
        {
            "key": NONGROUPED_MAX_AGE_POLICY,
            "label": _("Cache time for paginated OPDS feeds"),
        },
        {
            "key": GROUPED_MAX_AGE_POLICY,
            "label": _("Cache time for grouped OPDS feeds"),
        },
        {
            "key": BASE_URL_KEY,
            "label": _("Base url of the application"),
        },
        {
            "key": LOG_LEVEL, "label": _("Log Level"), "type": "select",
            "options": LOG_LEVEL_UI, "default": INFO,
        },
        {
            "key": LOG_APP_NAME, "label": _("Application name"),
            "description": _("Log messages originating from this application will be tagged with this name. If you run multiple instances, giving each one a different application name will help you determine which instance is having problems."),
            "default": DEFAULT_APP_NAME,
        },
        {
            "key": DATABASE_LOG_LEVEL, "label": _("Database Log Level"),
            "type": "select", "options": LOG_LEVEL_UI,
            "description": _("Database logs are extremely verbose, so unless you're diagnosing a database-related problem, it's a good idea to set a higher log level for database messages."),
            "default": WARN,
        },
    ]

    LIBRARY_SETTINGS = [
        {
            "key": WEBSITE_URL,
            "label": _("URL of the library's website"),
            "description": _("The library's main website, e.g. \"https://www.nypl.org/\" (not this Circulation Manager's URL).")
        },
        {
            "key": ALLOW_HOLDS,
            "label": _("Allow books to be put on hold"),
            "type": "select",
            "options": [
                { "key": "true", "label": _("Allow holds") },
                { "key": "false", "label": _("Disable holds") },
            ],
            "default": "true",
        },
        { "key": EntryPoint.ENABLED_SETTING,
          "label": _("Enabled entry points"),
          "description": _("Patrons will see the selected entry points at the top level and in search results."),
          "type": "list",
          "options": [
              { "key": entrypoint.INTERNAL_NAME,
                "label": EntryPoint.DISPLAY_TITLES.get(entrypoint) }
              for entrypoint in EntryPoint.ENTRY_POINTS
          ],
          "default": [x.INTERNAL_NAME for x in EntryPoint.DEFAULT_ENABLED],
        },
        {
            "key": FEATURED_LANE_SIZE,
            "label": _("Maximum number of books in the 'featured' lanes"),
            "type": "number",
            "default": 15,
        },
        {
            "key": MINIMUM_FEATURED_QUALITY,
            "label": _("Minimum quality for books that show up in 'featured' lanes"),
            "description": _("Between 0 and 1."),
            "default": 0.65,
        },
    ] + [
        { "key": ENABLED_FACETS_KEY_PREFIX + group,
          "label": description,
          "type": "list",
          "options": [
              { "key": facet, "label": FacetConstants.FACET_DISPLAY_TITLES.get(facet) }
              for facet in FacetConstants.FACETS_BY_GROUP.get(group)
          ],
          "default": FacetConstants.FACETS_BY_GROUP.get(group),
        } for group, description in FacetConstants.GROUP_DESCRIPTIONS.iteritems()
    ] + [
        { "key": DEFAULT_FACET_KEY_PREFIX + group,
          "label": _("Default %(group)s", group=display_name),
          "type": "select",
          "options": [
              { "key": facet, "label": FacetConstants.FACET_DISPLAY_TITLES.get(facet) }
              for facet in FacetConstants.FACETS_BY_GROUP.get(group)
          ],
          "default": FacetConstants.DEFAULT_FACET.get(group)
        } for group, display_name in FacetConstants.GROUP_DISPLAY_TITLES.iteritems()
    ]

    # This is set once data is loaded from the database and inserted into
    # the Configuration object.
    LOADED_FROM_DATABASE = 'loaded_from_database'

    @classmethod
    def loaded_from_database(cls):
        """Has the site configuration been loaded from the database yet?"""
        return cls.instance and cls.instance.get(
            cls.LOADED_FROM_DATABASE, False
        )

    # General getters

    @classmethod
    def get(cls, key, default=None):
        if cls.instance is None:
            raise ValueError("No configuration object loaded!")
        return cls.instance.get(key, default)

    @classmethod
    def required(cls, key):
        if cls.instance:
            value = cls.get(key)
            if value is not None:
                return value
        raise ValueError(
            "Required configuration variable %s was not defined!" % key
        )

    @classmethod
    def integration(cls, name, required=False):
        """Find an integration configuration by name."""
        integrations = cls.get(cls.INTEGRATIONS, {})
        v = integrations.get(name, {})
        if not v and required:
            raise ValueError(
                "Required integration '%s' was not defined! I see: %r" % (
                    name, ", ".join(sorted(integrations.keys()))
                )
            )
        return v

    @classmethod
    def integration_url(cls, name, required=False):
        """Find the URL to an integration."""
        integration = cls.integration(name, required=required)
        v = integration.get(cls.URL, None)
        if not v and required:
            raise ValueError(
                "Integration '%s' did not define a required 'url'!" % name
            )
        return v

    @classmethod
    def cdns(cls):
        from model import ExternalIntegration
        cdns = cls.integration(ExternalIntegration.CDN)
        if cdns == cls.UNINITIALIZED_CDNS:
            raise CannotLoadConfiguration(
                'CDN configuration has not been loaded from the database'
            )
        return cdns

    @classmethod
    def policy(cls, name, default=None, required=False):
        """Find a policy configuration by name."""
        v = cls.get(cls.POLICIES, {}).get(name, default)
        if not v and required:
            raise ValueError(
                "Required policy %s was not defined!" % name
            )
        return v

    # More specific getters.

    @classmethod
    def database_url(cls, test=False):
        """Find the database URL configured for this site.

        For compatibility with old configurations, we will look in the
        site configuration first.

        If it's not there, we will look in the appropriate environment
        variable.
        """
        # To avoid expensive mistakes, test and production databases
        # are always configured with separate keys.
        if test:
            config_key = cls.DATABASE_TEST_URL
            environment_variable = cls.DATABASE_TEST_ENVIRONMENT_VARIABLE
        else:
            config_key = cls.DATABASE_PRODUCTION_URL
            environment_variable = cls.DATABASE_PRODUCTION_ENVIRONMENT_VARIABLE

        # Check the legacy location (the config file) first.
        url = None
        database_integration = cls.integration(cls.DATABASE_INTEGRATION)
        if database_integration:
            url = database_integration.get(config_key)

        # If that didn't work, check the new location (the environment
        # variable).
        if not url:
            url = os.environ.get(environment_variable)
        if not url:
            raise CannotLoadConfiguration(
                "Database URL was not defined in environment variable (%s) or configuration file." % environment_variable
            )

        url_obj = None
        try:
            url_obj = make_url(url)
        except ArgumentError, e:
            # Improve the error message by giving a guide as to what's
            # likely to work.
            raise ArgumentError(
                "Bad format for database URL (%s). Expected something like postgres://[username]:[password]@[hostname]:[port]/[database name]" %
                url
            )

        # Calling __to_string__ will hide the password.
        logging.info("Connecting to database: %s" % url_obj.__to_string__())
        return url

    @classmethod
    def app_version(cls):
        """Returns the git version of the app, if a .version file exists."""
        if cls.instance == None:
            return

        version = cls.get(cls.APP_VERSION, None)
        if version:
            # The version has been set in Configuration before.
            return version

        root_dir = os.path.join(os.path.split(__file__)[0], "..")
        version_file = os.path.join(root_dir, cls.VERSION_FILENAME)

        version = cls.NO_APP_VERSION_FOUND
        if os.path.exists(version_file):
            with open(version_file) as f:
                version = f.readline().strip() or version

        cls.instance[cls.APP_VERSION] = version
        return version

    @classmethod
    def data_directory(cls):
        return cls.get(cls.DATA_DIRECTORY)

    @classmethod
    def load_cdns(cls, _db, config_instance=None):
        from model import ExternalIntegration as EI
        cdns = _db.query(EI).filter(EI.goal==EI.CDN_GOAL).all()
        cdn_integration = dict()
        for cdn in cdns:
            cdn_integration[cdn.setting(cls.CDN_MIRRORED_DOMAIN_KEY).value] = cdn.url

        config_instance = config_instance or cls.instance
        integrations = config_instance.setdefault(cls.INTEGRATIONS, {})
        integrations[EI.CDN] = cdn_integration

    @classmethod
    def localization_languages(cls):
        languages = cls.policy(cls.LOCALIZATION_LANGUAGES, default=["eng"])
        return [LanguageCodes.three_to_two[l] for l in languages]

    # The last time the database configuration is known to have changed.
    SITE_CONFIGURATION_LAST_UPDATE = "site_configuration_last_update"

    # The last time we *checked* whether the database configuration had
    # changed.
    LAST_CHECKED_FOR_SITE_CONFIGURATION_UPDATE = "last_checked_for_site_configuration_update"

    # A sitewide configuration setting controlling *how often* to check
    # whether the database configuration has changed.
    SITE_CONFIGURATION_TIMEOUT = 'site_configuration_timeout'

    # The name of the service associated with a Timestamp that tracks
    # the last time the site's configuration changed in the database.
    SITE_CONFIGURATION_CHANGED = "Site Configuration Changed"

    @classmethod
    def last_checked_for_site_configuration_update(cls):
        """When was the last time we actually checked when the database
        was updated?
        """
        return cls.instance.get(
            cls.LAST_CHECKED_FOR_SITE_CONFIGURATION_UPDATE, None
        )

    @classmethod
    def site_configuration_last_update(cls, _db, known_value=None,
                                       timeout=None):
        """Check when the site configuration was last updated.

        Updates Configuration.instance[Configuration.SITE_CONFIGURATION_LAST_UPDATE].
        It's the application's responsibility to periodically check
        this value and reload the configuration if appropriate.

        :param known_value: We know when the site configuration was
        last updated--it's this timestamp. Use it instead of checking
        with the database.

        :param timeout: We will only call out to the database once in
        this number of seconds. If we are asked again before this
        number of seconds elapses, we will assume site configuration
        has not changed.

        :return: a datetime object.
        """
        now = datetime.datetime.utcnow()

        if _db and timeout is None:
            from model import ConfigurationSetting
            timeout = ConfigurationSetting.sitewide(
                _db, cls.SITE_CONFIGURATION_TIMEOUT
            ).value
        if timeout is None:
            timeout = 600

        last_check = cls.instance.get(
            cls.LAST_CHECKED_FOR_SITE_CONFIGURATION_UPDATE
        )

        if (not known_value
            and last_check and (now - last_check).total_seconds() < timeout):
            # We went to the database less than [timeout] seconds ago.
            # Assume there has been no change.
            return cls._site_configuration_last_update()

        # Ask the database when was the last time the site
        # configuration changed. Specifically, this is the last time
        # site_configuration_was_changed() (defined in model.py) was
        # called.
        if not known_value:
            from model import Timestamp
            known_value = Timestamp.value(
                _db, cls.SITE_CONFIGURATION_CHANGED, None
            )
        if not known_value:
            # The site configuration has never changed.
            last_update = None
        else:
            last_update = known_value

        # Update the Configuration object's record of the last update time.
        cls.instance[cls.SITE_CONFIGURATION_LAST_UPDATE] = last_update

        # Whether that record changed or not, the time at which we
        # _checked_ is going to be set to the current time.
        cls.instance[cls.LAST_CHECKED_FOR_SITE_CONFIGURATION_UPDATE] = now

        return last_update

    @classmethod
    def _site_configuration_last_update(cls):
        """Get the raw SITE_CONFIGURATION_LAST_UPDATE value,
        without any attempt to find a fresher value from the database.
        """
        return cls.instance.get(cls.SITE_CONFIGURATION_LAST_UPDATE, None)

    @classmethod
    def load(cls, _db=None):
        """Load additional site configuration from a config file.

        This is being phased out in favor of taking all configuration from a
        database.
        """
        cfv = 'SIMPLIFIED_CONFIGURATION_FILE'
        config_path = os.environ.get(cfv)
        if config_path:
            try:
                cls.log.info("Loading configuration from %s", config_path)
                configuration = cls._load(open(config_path).read())
            except Exception, e:
                raise CannotLoadConfiguration(
                    "Error loading configuration file %s: %s" % (
                        config_path, e)
                )
        else:
            configuration = cls._load('{}')
        cls.instance = configuration

        cls.app_version()
        if _db:
            cls.load_cdns(_db)
            cls.instance[cls.LOADED_FROM_DATABASE] = True
            for parent in cls.__bases__:
                if parent.__name__.endswith('Configuration'):
                    parent.load(_db)
        else:
            if not cls.integration('CDN'):
                cls.instance.setdefault(cls.INTEGRATIONS, {})[
                    'CDN'] = cls.UNINITIALIZED_CDNS

        return configuration

    @classmethod
    def _load(cls, str):
        lines = [x for x in str.split("\n")
                 if not (x.strip().startswith("#") or x.strip().startswith("//"))]
        return json.loads("\n".join(lines))

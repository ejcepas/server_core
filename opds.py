
from collections import (
    defaultdict,
)

from urlparse import urlparse, urljoin
import copy
import datetime
import feedparser
import logging
import md5
import os
import random
import re
import site
import sys
import time
import urllib

from nose.tools import set_trace

from sqlalchemy.orm.query import Query
from sqlalchemy.sql.expression import func
from sqlalchemy.orm.session import Session

import requests

from lxml import builder, etree

from cdn import cdnify
from config import Configuration
from classifier import Classifier
from model import (
    BaseMaterializedWork,
    CachedFeed,
    ConfigurationSetting,
    CustomList,
    CustomListEntry,
    DataSource,
    Hyperlink,
    Resource,
    Identifier,
    Edition,
    Measurement,
    Subject,
    Work,
)
from lane import (
    Facets,
    Lane,
    Pagination,
    WorkList,
)
from util.opds_writer import (
    AtomFeed,
    OPDSFeed, 
    OPDSMessage,
)

class UnfulfillableWork(Exception):
    """Raise this exception when it turns out a Work currently cannot be
    fulfilled through any means, *and* this is a problem sufficient to
    cancel the creation of an <entry> for the Work.

    For commercial works, this might be because the collection
    contains no licenses. For open-access works, it might be because
    none of the delivery mechanisms could be mirrored.
    """

class Annotator(object):
    """The Annotator knows how to present an OPDS feed in a specific
    application context.
    """

    opds_cache_field = Work.simple_opds_entry.name

    @classmethod
    def annotate_work_entry(cls, work, active_license_pool, edition, 
                            identifier, feed, entry):
        """Make any custom modifications necessary to integrate this
        OPDS entry into the application's workflow.
        """
        if active_license_pool:
            provider_name_attr = "{%s}ProviderName" % AtomFeed.BIBFRAME_NS
            kwargs = {provider_name_attr : active_license_pool.data_source.name}
            data_source_tag = AtomFeed.makeelement(
                "{%s}distribution" % AtomFeed.BIBFRAME_NS,
                **kwargs
            )
            entry.extend([data_source_tag])

    @classmethod
    def annotate_feed(cls, feed, lane):
        """Make any custom modifications necessary to integrate this
        OPDS feed into the application's workflow.
        """
        pass

    @classmethod
    def group_uri(cls, work, license_pool, identifier):
        return None, ""

    @classmethod
    def rating_tag(cls, type_uri, value):
        """Generate a schema:Rating tag for the given type and value."""
        rating_tag = AtomFeed.makeelement(AtomFeed.schema_("Rating"))
        value_key = AtomFeed.schema_('ratingValue')
        rating_tag.set(value_key, "%.4f" % value)
        if type_uri:
            type_key = AtomFeed.schema_('additionalType')
            rating_tag.set(type_key, type_uri)
        return rating_tag

    @classmethod
    def cover_links(cls, work):
        """Return all links to be used as cover links for this work.

        In a distribution application, each work will have only one
        link. In a content server-type application, each work may have
        a large number of links.

        :return: A 2-tuple (thumbnail_links, full_links)

        """
        thumbnails = []
        full = []
        if work:
            _db = Session.object_session(work)
            if work.cover_thumbnail_url:
                thumbnails = [cdnify(work.cover_thumbnail_url)]

            if work.cover_full_url:
                full = [cdnify(work.cover_full_url)]
        return thumbnails, full

    @classmethod
    def categories(cls, work):
        """Return all relevant classifications of this work.

        :return: A dictionary mapping 'scheme' URLs to dictionaries of
        attribute-value pairs.

        Notable attributes: 'term', 'label', 'http://schema.org/ratingValue'
        """
        if not work:
            return {}

        categories = {}

        fiction_term = None
        if work.fiction == True:
            fiction_term = 'Fiction'
        elif work.fiction == False:
            fiction_term = 'Nonfiction'
        if fiction_term:
            fiction_scheme = Subject.SIMPLIFIED_FICTION_STATUS
            categories[fiction_scheme] = [
                dict(term=fiction_scheme + fiction_term,
                     label=fiction_term)
            ]

        simplified_genres = []
        for wg in work.work_genres:
            simplified_genres.append(wg.genre.name)

        if simplified_genres:
            categories[Subject.SIMPLIFIED_GENRE] = [
                dict(term=Subject.SIMPLIFIED_GENRE + urllib.quote(x),
                     label=x)
                for x in simplified_genres
            ]

        # Add the appeals as a category of schema
        # http://librarysimplified.org/terms/appeal
        schema_url = AtomFeed.SIMPLIFIED_NS + "appeals/"
        appeals = []
        categories[schema_url] = appeals
        for name, value in (
                (Work.CHARACTER_APPEAL, work.appeal_character),
                (Work.LANGUAGE_APPEAL, work.appeal_language),
                (Work.SETTING_APPEAL, work.appeal_setting),
                (Work.STORY_APPEAL, work.appeal_story),
        ):
            if value:
                appeal = dict(term=schema_url + name, label=name)
                weight_field = AtomFeed.schema_("ratingValue")
                appeal[weight_field] = value
                appeals.append(appeal)

        # Add the audience as a category of schema
        # http://schema.org/audience
        if work.audience:
            audience_uri = AtomFeed.SCHEMA_NS + "audience"
            categories[audience_uri] = [
                dict(term=work.audience, label=work.audience)
            ]

        # Any book can have a target age, but the target age
        # is only relevant for childrens' and YA books.
        audiences_with_target_age = (
            Classifier.AUDIENCE_CHILDREN, Classifier.AUDIENCE_YOUNG_ADULT
        )
        if (work.target_age and work.audience in audiences_with_target_age):
            uri = Subject.uri_lookup[Subject.AGE_RANGE]
            target_age = work.target_age_string
            if target_age:
                categories[uri] = [dict(term=target_age, label=target_age)]

        return categories

    @classmethod
    def authors(cls, work, license_pool, edition, identifier):
        """Create one or more <author> tags for the given work."""
        authors = list()
        listed = set()
        for author in edition.author_contributors:
            name = author.display_name or author.sort_name
            name_key = name.lower()
            if name_key in listed:
                continue

            authors.append(AtomFeed.author(AtomFeed.name(name)))
            listed.add(name_key)

        if authors:
            return authors
        return [AtomFeed.author(AtomFeed.name(""))]

    @classmethod
    def series(cls, series_name, series_position):
        """Generate a schema:Series tag for the given name and position."""
        if not series_name:
            return None
        series_details = dict()
        series_details['name'] = series_name
        if series_position:
            series_details[AtomFeed.schema_('position')] = unicode(series_position)
        series_tag = AtomFeed.makeelement(AtomFeed.schema_("Series"), **series_details)
        return series_tag

    @classmethod
    def content(cls, work):
        """Return an HTML summary of this work."""
        summary = ""
        if work: 
            if work.summary_text != None:
                summary = work.summary_text
            elif work.summary and work.summary.content:
                work.summary_text = work.summary.content
                summary = work.summary_text
        return summary

    @classmethod
    def lane_id(cls, lane):
        return cls.featured_feed_url(lane)

    @classmethod
    def work_id(cls, work):
        return work.presentation_edition.primary_identifier.urn

    @classmethod
    def permalink_for(cls, work, license_pool, identifier):
        """In the absence of any specific URLs, the best we can do
        is a URN.
        """
        return identifier.urn

    @classmethod
    def lane_url(cls, lane):
        raise NotImplementedError()
    
    @classmethod
    def feed_url(cls, lane, facets=None, pagination=None):
        raise NotImplementedError()

    @classmethod
    def groups_url(cls, lane):
        raise NotImplementedError()

    @classmethod
    def search_url(cls, lane, query, pagination):
        raise NotImplementedError()

    @classmethod
    def default_lane_url(cls):
        raise NotImplementedError()

    @classmethod
    def featured_feed_url(cls, lane, order=None):
        raise NotImplementedError()

    @classmethod
    def facet_url(cls, facets):
        return None

    @classmethod
    def active_licensepool_for(cls, work):
        """Which license pool would be/has been used to issue a license for
        this work?
        """
        active_license_pool = None

        if not work:
            return None

        if isinstance(work, BaseMaterializedWork):
            # Active license pool is preloaded from database.
            return work.license_pool

        # The active license pool is the one that *would* be
        # associated with a loan, were a loan to be issued right
        # now.
        for p in work.license_pools:
            if p.superceded:
                continue
            edition = p.presentation_edition
            if p.open_access:
                if p.best_open_access_link:
                    active_license_pool = p
                    # We have an unlimited source for this book.
                    # There's no need to keep looking.
                    break
            elif edition and edition.title and p.licenses_owned > 0:
                active_license_pool = p
        return active_license_pool

    def sort_works_for_groups_feed(self, works, **kwargs):
        return works


class VerboseAnnotator(Annotator):
    """The default Annotator for machine-to-machine integration.

    This Annotator describes all categories and authors for the book
    in great detail.
    """

    opds_cache_field = Work.verbose_opds_entry.name

    @classmethod
    def annotate_work_entry(cls, work, license_pool, edition, identifier, feed,
                            entry):
        """Add a quality rating to the work.
        """
        for type_uri, value in [
                (Measurement.QUALITY, work.quality),
                (None, work.rating),
                (Measurement.POPULARITY, work.popularity),
        ]:
            if value:
                entry.append(cls.rating_tag(type_uri, value))

    @classmethod
    def categories(cls, work):
        """Send out _all_ categories for the work.

        (So long as the category type has a URI associated with it in
        Subject.uri_lookup.)
        """
        _db = Session.object_session(work)
        by_scheme_and_term = dict()
        identifier_ids = work.all_identifier_ids()
        classifications = Identifier.classifications_for_identifier_ids(
            _db, identifier_ids)
        for c in classifications:
            subject = c.subject
            if subject.type in Subject.uri_lookup:
                scheme = Subject.uri_lookup[subject.type]
                term = subject.identifier
                weight_field = AtomFeed.schema_("ratingValue")
                key = (scheme, term)
                if not key in by_scheme_and_term:
                    value = dict(term=subject.identifier)
                    if subject.name:
                        value['label'] = subject.name
                    value[weight_field] = 0
                    by_scheme_and_term[key] = value
                by_scheme_and_term[key][weight_field] += c.weight

        # Collapse by_scheme_and_term to by_scheme
        by_scheme = defaultdict(list)
        for (scheme, term), value in by_scheme_and_term.items():
            by_scheme[scheme].append(value)
        by_scheme.update(super(VerboseAnnotator, cls).categories(work))
        return by_scheme

    @classmethod
    def authors(cls, work, license_pool, edition, identifier):
        """Create a detailed <author> tag for each author."""
        return [cls.detailed_author(author)
                for author in edition.author_contributors]

    @classmethod
    def detailed_author(cls, contributor):
        """Turn a Contributor into a detailed <author> tag."""
        children = []
        children.append(AtomFeed.name(contributor.display_name or ""))
        sort_name = AtomFeed.makeelement("{%s}sort_name" % AtomFeed.SIMPLIFIED_NS)
        sort_name.text = contributor.sort_name

        children.append(sort_name)

        if contributor.family_name:
            family_name = AtomFeed.makeelement(AtomFeed.schema_("family_name"))
            family_name.text = contributor.family_name
            children.append(family_name)

        if contributor.wikipedia_name:
            wikipedia_name = AtomFeed.makeelement(
                "{%s}wikipedia_name" % AtomFeed.SIMPLIFIED_NS)
            wikipedia_name.text = contributor.wikipedia_name
            children.append(wikipedia_name)

        if contributor.viaf:
            viaf_tag = AtomFeed.makeelement(AtomFeed.schema_("sameas"))
            viaf_tag.text = "http://viaf.org/viaf/%s" % contributor.viaf
            children.append(viaf_tag)

        if contributor.lc:
            lc_tag = AtomFeed.makeelement(AtomFeed.schema_("sameas"))
            lc_tag.text = "http://id.loc.gov/authorities/names/%s" % contributor.lc
            children.append(lc_tag)


        return AtomFeed.author(*children)



class AcquisitionFeed(OPDSFeed):

    FACET_REL = "http://opds-spec.org/facet"
    FEED_CACHE_TIME = int(Configuration.get('default_feed_cache_time', 600))
    NO_CACHE = object()

    @classmethod
    def groups(cls, _db, title, url, lane, annotator,
               cache_type=None, force_refresh=False):
        """The acquisition feed for 'featured' items from a given lane's
        sublanes, organized into per-lane groups.

        :return: CachedFeed (if use_cache is True) or unicode
        """
        cached = None
        use_cache = cache_type != cls.NO_CACHE
        if use_cache:
            cache_type = cache_type or CachedFeed.GROUPS_TYPE
            cached, usable = CachedFeed.fetch(
                _db,
                lane=lane,
                type=cache_type,
                facets=None,
                pagination=None,
                annotator=annotator,
                force_refresh=force_refresh
            )
            if usable:
                return cached

        works_and_lanes = lane.groups(_db)
        if not works_and_lanes:
            # We did not find enough works for a groups feed.
            # Instead we need to display a flat feed--the
            # contents of what would have been the 'all' feed.
            #
            # Generate a page-type feed that is filed as a
            # groups-type feed so it will show up when the client
            # asks for it.
            cached = cls.page(
                _db, title, url, lane, annotator,
                cache_type=cache_type,
                force_refresh=force_refresh,
            )
            return cached

        all_works = []
        for work, sublane in works_and_lanes:
            if sublane==lane:
                # We are looking at the groups feed for (e.g.)
                # "Science Fiction", and we're seeing a book
                # that is featured within "Science Fiction" itself
                # rather than one of the sublanes. 
                #
                # We want to assign this work to a group called "All
                # Science Fiction" and point its 'group URI' to 
                # the linear feed of the "Science Fiction" lane
                # (as opposed to the groups feed, which is where we
                # are now).
                v = dict(
                    lane=lane,
                    label=lane.display_name_for_all,
                    link_to_list_feed=True,
                )
            else:
                # We are looking at the groups feed for (e.g.)
                # "Science Fiction", and we're seeing a book
                # that is featured within one of its sublanes,
                # such as "Space Opera".
                #
                # We want to assign this work to a group derived
                # from the sublane.
                v = dict(lane=sublane)
            annotator.lanes_by_work[work].append(v)
            all_works.append(work)

        all_works = annotator.sort_works_for_groups_feed(all_works)
        feed = AcquisitionFeed(_db, title, url, all_works, annotator)

        cls.add_breadcrumb_links(feed, lane, annotator)        
        annotator.annotate_feed(feed, lane)

        content = unicode(feed)
        if cached and use_cache:
            cached.update(_db, content)
            return cached
        return content

    @classmethod
    def page(cls, _db, title, url, lane, annotator,
             cache_type=None, facets=None, pagination=None,
             force_refresh=False
    ):
        """Create a feed representing one page of works from a given lane.

        :return: CachedFeed (if use_cache is True) or unicode
        """
        if isinstance(lane, Lane):
            library = lane.library
        elif isinstance(lane, WorkList):
            library = lane.get_library(_db)
        else:
            library = None
        facets = facets or Facets.default(library)
        pagination = pagination or Pagination.default()

        cached = None
        use_cache = cache_type != cls.NO_CACHE
        if use_cache:
            cache_type = cache_type or CachedFeed.PAGE_TYPE
            cached, usable = CachedFeed.fetch(
                _db,
                lane=lane,
                type=cache_type,
                facets=facets,
                pagination=pagination,
                annotator=annotator,
                force_refresh=force_refresh
            )
            if usable:
                return cached

        works_q = lane.works(_db, facets, pagination)
        if not works_q:
            # The Lane believes that creating this feed is a bad idea.
            works = []
        else:
            works = works_q.all()
        feed = cls(_db, title, url, works, annotator)

        # Add URLs to change faceted views of the collection.
        for args in cls.facet_links(annotator, facets):
            OPDSFeed.add_link_to_feed(feed=feed.feed, **args)

        if len(works) > 0 and pagination.has_next_page:
            # There are works in this list. Add a 'next' link.
            OPDSFeed.add_link_to_feed(feed=feed.feed, rel="next", href=annotator.feed_url(lane, facets, pagination.next_page))

        if pagination.offset > 0:
            OPDSFeed.add_link_to_feed(feed=feed.feed, rel="first", href=annotator.feed_url(lane, facets, pagination.first_page))

        previous_page = pagination.previous_page
        if previous_page:
            OPDSFeed.add_link_to_feed(feed=feed.feed, rel="previous", href=annotator.feed_url(lane, facets, previous_page))

        cls.add_breadcrumb_links(feed, lane, annotator)
        
        annotator.annotate_feed(feed, lane)

        content = unicode(feed)
        if cached and use_cache:
            cached.update(_db, content)
            return cached
        return content

    @classmethod
    def add_breadcrumb_links(self, feed, lane, annotator):
        # Add "up" link and breadcrumbs
        top_level_title = annotator.top_level_title() or "Collection Home"
        parent = None
        if isinstance(lane, Lane):
            parent = lane.parent
        if parent and parent.display_name:
            parent_title = parent.display_name
        else:
            parent_title = top_level_title
        if parent:
            up_uri = annotator.lane_url(parent)
            OPDSFeed.add_link_to_feed(feed=feed.feed, href=up_uri, rel="up", title=parent_title)
            feed.add_breadcrumbs(lane, annotator)

        OPDSFeed.add_link_to_feed(feed=feed.feed, rel='start', href=annotator.default_lane_url(), title=top_level_title)


    @classmethod
    def search(cls, _db, title, url, lane, search_engine, query, pagination=None,
               annotator=None
    ):
        results = lane.search(
            _db, query, search_engine, pagination=pagination
        )
        opds_feed = AcquisitionFeed(_db, title, url, results, annotator=annotator)
        AcquisitionFeed.add_link_to_feed(feed=opds_feed.feed, rel='start', href=annotator.default_lane_url(), title=annotator.top_level_title())

        if len(results) > 0:
            # There are works in this list. Add a 'next' link.
            AcquisitionFeed.add_link_to_feed(feed=opds_feed.feed, rel="next", href=annotator.search_url(lane, query, pagination.next_page))

        if pagination.offset > 0:
            AcquisitionFeed.add_link_to_feed(feed=opds_feed.feed, rel="first", href=annotator.search_url(lane, query, pagination.first_page))

        previous_page = pagination.previous_page
        if previous_page:
            AcquisitionFeed.add_link_to_feed(feed=opds_feed.feed, rel="previous", href=annotator.search_url(lane, query, previous_page))

        # Add "up" link and breadcrumbs
        AcquisitionFeed.add_link_to_feed(feed=opds_feed.feed, rel="up", href=annotator.lane_url(lane), title=str(lane.display_name))
        opds_feed.add_breadcrumbs(lane, annotator, include_lane=True)

        annotator.annotate_feed(opds_feed, lane)
        return unicode(opds_feed)

    @classmethod
    def single_entry(cls, _db, work, annotator, force_create=False):
        """Create a single-entry OPDS document for one specific work."""
        feed = cls(_db, '', '', [], annotator=annotator)
        if not isinstance(work, Edition) and not work.presentation_edition:
            return None
        entry = feed.create_entry(work, even_if_no_license_pool=True,
                                  force_create=force_create)

        # Since this <entry> tag is going to be the root of an XML
        # document it's essential that it include an up-to-date nsmap,
        # even if it was generated from an old cached <entry> tag that
        # had an older nsmap.
        if isinstance(entry, etree._Element) and not 'drm' in entry.nsmap:
            # This workaround (creating a brand new tag) is necessary
            # because the nsmap attribute is immutable. See
            # https://bugs.launchpad.net/lxml/+bug/555602
            nsmap = entry.nsmap
            nsmap['drm'] = AtomFeed.DRM_NS
            new_root = etree.Element(entry.tag, nsmap=nsmap)
            new_root[:] = entry[:]
            entry = new_root
        return entry

    @classmethod
    def error_message(cls, identifier, error_status, error_message):
        """Turn an error result into an OPDSMessage suitable for
        adding to a feed.
        """
        return OPDSMessage(identifier.urn, error_status, error_message)

    @classmethod
    def facet_links(self, annotator, facets):
        for group, value, new_facets, selected, in facets.facet_groups:
            url = annotator.facet_url(new_facets)
            if not url:
                continue
            group_title = str(Facets.GROUP_DISPLAY_TITLES[group])
            facet_title = str(Facets.FACET_DISPLAY_TITLES[value])
            link = dict(href=url, title=facet_title)
            link['rel'] = self.FACET_REL
            link['{%s}facetGroup' % AtomFeed.OPDS_NS] = group_title
            if selected:
                link['{%s}activeFacet' % AtomFeed.OPDS_NS] = "true"
            yield link

    CACHE_FOREVER = 'forever'

    NONGROUPED_MAX_AGE_POLICY = Configuration.NONGROUPED_MAX_AGE_POLICY
    DEFAULT_NONGROUPED_MAX_AGE = 1200

    GROUPED_MAX_AGE_POLICY = Configuration.GROUPED_MAX_AGE_POLICY
    DEFAULT_GROUPED_MAX_AGE = CACHE_FOREVER
            
    @classmethod
    def grouped_max_age(cls, _db):
        "The maximum cache time for a grouped acquisition feed."
        value = ConfigurationSetting.sitewide(
            _db, cls.GROUPED_MAX_AGE_POLICY).int_value
        if value is None:
            value = cls.DEFAULT_GROUPED_MAX_AGE
        return value

    @classmethod
    def nongrouped_max_age(cls, _db):
        "The maximum cache time for a non-grouped acquisition feed."
        value = ConfigurationSetting.sitewide(
            _db, cls.NONGROUPED_MAX_AGE_POLICY).int_value
        if value is cls.CACHE_FOREVER:
            logging.error(
                "Non-grouped acquisition feed cannot be cached forever."
            )
            value = None
        if value is None:
            value = cls.DEFAULT_NONGROUPED_MAX_AGE
        return value
            
    def __init__(self, _db, title, url, works, annotator=None,
                 precomposed_entries=[]):
        """Turn a list of works, messages, and precomposed <opds> entries
        into a feed.
        """
        if not annotator:
            annotator = Annotator()
        self.annotator = annotator

        super(AcquisitionFeed, self).__init__(title, url)

        for work in works:
            self.add_entry(work)

        # Add the precomposed entries and the messages.
        for entry in precomposed_entries:
            if isinstance(entry, OPDSMessage):
                entry = entry.tag
            self.feed.append(entry)

    def add_entry(self, work):
        """Attempt to create an OPDS <entry>. If successful, append it to
        the feed.
        """
        entry = self.create_entry(work)

        if entry is not None:
            if isinstance(entry, OPDSMessage):
                entry = entry.tag
            self.feed.append(entry)
        return entry

    def create_entry(self, work, even_if_no_license_pool=False,
                     force_create=False, use_cache=True):
        """Turn a work into an entry for an acquisition feed."""
        identifier = None
        if isinstance(work, Edition):
            active_edition = work
            identifier = active_edition.primary_identifier
            active_license_pool = None
            work = None
        else:
            active_license_pool = self.annotator.active_licensepool_for(work)
            if not work:
                # We have a license pool but no work. Most likely we don't have
                # metadata for this work yet.
                return None

            if isinstance(work, BaseMaterializedWork):
                identifier = work.license_pool.identifier
                active_edition = None
            elif active_license_pool:
                identifier = active_license_pool.identifier
                active_edition = active_license_pool.presentation_edition
            elif work.presentation_edition:
                active_edition = work.presentation_edition
                identifier = active_edition.primary_identifier

        # There's no reason to present a book that has no active license pool.
        if not identifier:
            logging.warn("%r HAS NO IDENTIFIER", work)
            return None

        if not active_license_pool and not even_if_no_license_pool:
            logging.warn("NO ACTIVE LICENSE POOL FOR %r", work)
            return self.error_message(
                identifier,
                403,
                "I've heard about this work but have no active licenses for it."
            )

        if not active_edition and not isinstance(work, BaseMaterializedWork):
            logging.warn("NO ACTIVE EDITION FOR %r", active_license_pool)
            return self.error_message(
                identifier,
                403,
                "I've heard about this work but have no metadata for it."
            )

        try:
            return self._create_entry(work, active_license_pool, active_edition,
                                      identifier, force_create, use_cache)
        except UnfulfillableWork, e:
            logging.info(
                "Work %r is not fulfillable, refusing to create an <entry>.",
                work,
            )
            return self.error_message(
                identifier, 
                403,
                "I know about this work but can offer no way of fulfilling it."
            )
        except Exception, e:
            logging.error(
                "Exception generating OPDS entry for %r", work,
                exc_info = e
            )
            return None

    def _create_entry(self, work, license_pool, edition, identifier,
                      force_create=False, use_cache=True):
        xml = None
        field = self.annotator.opds_cache_field
        if field and work and not force_create and use_cache:
            xml = getattr(work, field)

        if xml:
            xml = etree.fromstring(xml)
        else:
            if isinstance(work, BaseMaterializedWork):
                raise Exception(
                    "Cannot build an OPDS entry for a MaterializedWork.")
            xml = self._make_entry_xml(work, license_pool, edition, identifier)
            data = etree.tostring(xml)
            if field and use_cache:
                setattr(work, field, data)

        self.annotator.annotate_work_entry(
            work, license_pool, edition, identifier, self, xml)
            
        group_uri, group_title = self.annotator.group_uri(
            work, license_pool, identifier)
        if group_uri:
            self.add_link_to_entry(
                xml, rel=OPDSFeed.GROUP_REL, href=group_uri,
                title=group_title)

        return xml

    def _make_entry_xml(self, work, license_pool, edition, identifier):

        if work and not edition:
            edition = work.presentation_edition

        # Find the .epub link
        epub_href = None
        p = None

        links = []
        cover_quality = 0
        qualities = []
        if work:
            qualities.append(("Work quality", work.quality))
        full_url = None

        thumbnail_urls, full_urls = self.annotator.cover_links(work)
        for rel, urls in (
                (Hyperlink.IMAGE, full_urls),
                (Hyperlink.THUMBNAIL_IMAGE, thumbnail_urls)):
            for url in urls:
                image_type = "image/png"
                if url.endswith(".jpeg") or url.endswith(".jpg"):
                    image_type = "image/jpeg"
                elif url.endswith(".gif"):
                    image_type = "image/gif"
                links.append(AtomFeed.link(rel=rel, href=url, type=image_type))
           

        permalink = self.annotator.permalink_for(work, license_pool, identifier)
        content = self.annotator.content(work)
        if isinstance(content, str):
            content = content.decode("utf8")

        content_type = 'html'
        kw = {}
        if edition.medium:
            additional_type = Edition.medium_to_additional_type.get(
                edition.medium)
            if not additional_type:
                logging.warn("No additionalType for medium %s",
                             edition.medium)
            additional_type_field = AtomFeed.schema_("additionalType")
            kw[additional_type_field] = additional_type

        entry = AtomFeed.entry(
            AtomFeed.id(permalink),
            AtomFeed.title(edition.title or OPDSFeed.NO_TITLE),
            **kw
        )
        if edition.subtitle:
            subtitle_tag = AtomFeed.makeelement(AtomFeed.schema_("alternativeHeadline"))
            subtitle_tag.text = edition.subtitle
            entry.append(subtitle_tag)

        author_tags = self.annotator.authors(work, license_pool, edition, identifier)
        entry.extend(author_tags)

        if edition.series:
            entry.extend([self.annotator.series(edition.series, edition.series_position)])

        if content:
            entry.extend([AtomFeed.summary(content, type=content_type)])

        entry.extend([
            AtomFeed.updated(AtomFeed._strftime(datetime.datetime.utcnow())),
        ])

        permanent_work_id_tag = AtomFeed.makeelement("{%s}pwid" % AtomFeed.SIMPLIFIED_NS)
        permanent_work_id_tag.text = edition.permanent_work_id
        entry.append(permanent_work_id_tag)

        entry.extend(links)

        categories_by_scheme = self.annotator.categories(work)
        category_tags = []
        for scheme, categories in categories_by_scheme.items():
            for category in categories:
                if isinstance(category, basestring):
                    category = dict(term=category)
                category = dict(map(unicode, (k, v)) for k, v in category.items())
                category_tag = AtomFeed.category(scheme=scheme, **category)
                category_tags.append(category_tag)
        entry.extend(category_tags)

        # print " ID %s TITLE %s AUTHORS %s" % (tag, work.title, work.authors)
        language = edition.language_code
        if language:
            language_tag = AtomFeed.makeelement("{%s}language" % AtomFeed.DCTERMS_NS)
            language_tag.text = language
            entry.append(language_tag)

        if edition.publisher:
            publisher_tag = AtomFeed.makeelement("{%s}publisher" % AtomFeed.DCTERMS_NS)
            publisher_tag.text = edition.publisher
            entry.extend([publisher_tag])

        # We use Atom 'published' for the date the book first became
        # available to people using this application.
        now = datetime.datetime.utcnow()
        today = datetime.date.today()
        if license_pool and license_pool.availability_time:
            avail = license_pool.availability_time
            if isinstance(avail, datetime.datetime):
                avail = avail.date()
            if avail <= today:
                availability_tag = AtomFeed.makeelement("published")
                # TODO: convert to local timezone.
                availability_tag.text = AtomFeed._strftime(license_pool.availability_time)
                entry.extend([availability_tag])

        # Entry.issued is the date the ebook came out, as distinct
        # from Entry.published (which may refer to the print edition
        # or some original edition way back when).
        #
        # For Dublin Core 'created' we use Entry.issued if we have it
        # and Entry.published if not. In general this means we use
        # issued date for Gutenberg and published date for other
        # sources.
        #
        # We use dc:created instead of dc:issued because dc:issued is
        # commonly conflated with atom:published.
        #
        # For the date the book was added to our collection we use
        # atom:published.
        issued = edition.issued or edition.published
        if (isinstance(issued, datetime.datetime) 
            or isinstance(issued, datetime.date)):
            issued_already = False
            if isinstance(issued, datetime.datetime):
                issued_already = (issued <= now)
            elif isinstance(issued, datetime.date):
                issued_already = (issued <= today)
            if issued_already:
                issued_tag = AtomFeed.makeelement("{%s}created" % AtomFeed.DCTERMS_NS)
                # Use datetime.isoformat instead of datetime.strftime because
                # strftime only works on dates after 1890, and we have works
                # that were issued much earlier than that.
                # TODO: convert to local timezone, not that it matters much.
                issued_tag.text = issued.isoformat().split('T')[0]
                entry.extend([issued_tag])

        return entry

    def add_breadcrumbs(self, lane, annotator, include_lane=False):
        """Add list of ancestor links in a breadcrumbs element."""
        # Ensure that lane isn't top-level before proceeding
        if annotator.lane_url(lane) != annotator.default_lane_url():
            breadcrumbs = AtomFeed.makeelement("{%s}breadcrumbs" % AtomFeed.SIMPLIFIED_NS)

            # Add root link
            root_url = annotator.default_lane_url()
            breadcrumbs.append(
                AtomFeed.link(title=annotator.top_level_title(), href=root_url)
            )
            
            # Add links for all visible ancestors that aren't root
            for ancestor in reversed(list(lane.parentage)):
                lane_url = annotator.lane_url(ancestor)
                if lane_url != root_url:
                    breadcrumbs.append(
                        AtomFeed.link(title=ancestor.display_name, href=lane_url)
                    )

            # Include link to lane
            # For search, breadcrumbs include the searched lane
            if include_lane:
                breadcrumbs.append(
                    AtomFeed.link(title=lane.display_name, href=annotator.lane_url(lane))
                )

            self.feed.append(breadcrumbs)

    @classmethod
    def minimal_opds_entry(cls, identifier, cover, description, quality,
        most_recent_update=None
    ):
        elements = []
        representations = []
        most_recent_update = None
        if cover:
            cover_representation = cover.representation
            representations.append(cover.representation)
            cover_link = AtomFeed.makeelement(
                "link", href=cover_representation.mirror_url,
                type=cover_representation.media_type, rel=Hyperlink.IMAGE)
            elements.append(cover_link)
            if cover_representation.thumbnails:
                thumbnail = cover_representation.thumbnails[0]
                representations.append(thumbnail)
                thumbnail_link = AtomFeed.makeelement(
                    "link", href=thumbnail.mirror_url,
                    type=thumbnail.media_type,
                    rel=Hyperlink.THUMBNAIL_IMAGE
                )
                elements.append(thumbnail_link)
        if description:
            content = description.representation.content
            if isinstance(content, str):
                content = content.decode("utf8")
            description_e = AtomFeed.summary(content, type='html')
            elements.append(description_e)
            representations.append(description.representation)

        if quality:
            elements.append(
                Annotator.rating_tag(Measurement.QUALITY, quality))

        # The update date is the most recent date any of these
        # resources were mirrored/fetched.
        potential_update_dates = [
            r.mirrored_at or r.fetched_at for r in representations
            if r.mirrored_at or r.fetched_at
        ]
        if most_recent_update:
            potential_update_dates.append(most_recent_update)

        if potential_update_dates:
            update_date = max(potential_update_dates)
            elements.append(AtomFeed.updated(AtomFeed._strftime(update_date)))
        entry = AtomFeed.entry(
            AtomFeed.id(identifier.urn),
            AtomFeed.title(OPDSFeed.NO_TITLE),
            *elements
        )
        return entry

    @classmethod
    def link(cls, rel, href, type):
        return AtomFeed.makeelement("link", type=type, rel=rel, href=href)

    @classmethod
    def acquisition_link(cls, rel, href, types):
        if types:            
            initial_type = types[0]
            indirect_types = types[1:]
        else:
            initial_type = None
            indirect_types = []
        link = cls.link(rel, href, initial_type)
        indirect = cls.indirect_acquisition(indirect_types)
        if indirect is not None:
            link.append(indirect)
        return link

    @classmethod
    def indirect_acquisition(cls, indirect_types):
        top_level_parent = None
        parent = None
        for t in indirect_types:
            indirect_link = AtomFeed.makeelement(
                "{%s}indirectAcquisition" % AtomFeed.OPDS_NS, type=t)
            if parent is not None:
                parent.extend([indirect_link])
            parent = indirect_link
            if top_level_parent is None:
                top_level_parent = indirect_link
        return top_level_parent

    @classmethod
    def license_tags(cls, license_pool, loan, hold):
        # Generate a list of licensing tags. These should be inserted
        # into a <link> tag.
        tags = []
        availability_tag_name = None
        suppress_since = False
        status = None
        since = None
        until = None

        if not license_pool:
            return
        default_loan_period = default_reservation_period = None
        collection = license_pool.collection
        if (loan or hold) and not license_pool.open_access:
            if loan:
                obj = loan
            elif hold:
                obj = hold
            library = obj.patron.library
            default_loan_period = datetime.timedelta(
                collection.default_loan_period(library)
            )
        if loan:
            status = 'available'
            since = loan.start
            until = loan.until(default_loan_period)
        elif hold:
            if not license_pool.open_access:
                default_reservation_period = datetime.timedelta(
                    collection.default_reservation_period
                )
            until = hold.until(default_loan_period, default_reservation_period)
            if hold.position == 0:
                status = 'ready'
                since = None
            else:
                status = 'reserved'
                since = hold.start
        elif (license_pool.open_access or (
                license_pool.licenses_available > 0 and
                license_pool.licenses_owned > 0)
          ):
            status = 'available'
        else:
            status='unavailable'

        kw = dict(status=status)
        if since:
            kw['since'] = AtomFeed._strftime(since)
        if until:
            kw['until'] = AtomFeed._strftime(until)
        tag_name = "{%s}availability" % AtomFeed.OPDS_NS
        availability_tag = AtomFeed.makeelement(tag_name, **kw)
        tags.append(availability_tag)

        # Open-access pools do not need to display <opds:holds> or <opds:copies>.
        if license_pool.open_access:
            return tags


        holds_kw = dict(total=str(license_pool.patrons_in_hold_queue or 0))
        if hold and hold.position:
            holds_kw['position'] = str(hold.position)
        holds = AtomFeed.makeelement("{%s}holds" % AtomFeed.OPDS_NS, **holds_kw)
        tags.append(holds)

        copies_kw = dict(
            total=str(license_pool.licenses_owned or 0),
            available=str(license_pool.licenses_available or 0),
        )
        copies = AtomFeed.makeelement("{%s}copies" % AtomFeed.OPDS_NS, **copies_kw)
        tags.append(copies)

        return tags

    @classmethod
    def format_types(cls, delivery_mechanism):
        """Generate a set of types suitable for passing into
        acquisition_link().
        """
        types = []
        # If this is a streaming book, you have to get an OPDS entry, then
        # get a direct link to the streaming reader from that.
        if delivery_mechanism.is_streaming:
            types.append(OPDSFeed.ENTRY_TYPE)

        # If this is a DRM-encrypted book, you have to get through the DRM
        # to get the goodies inside.
        drm = delivery_mechanism.drm_scheme_media_type
        if drm:
            types.append(drm)

        # Finally, you get the goodies.
        media = delivery_mechanism.content_type_media_type
        if media:
            types.append(media)
        return types


class LookupAcquisitionFeed(AcquisitionFeed):
    """Used when the user has requested a lookup of a specific identifier,
    which may be different from the identifier used by the Work's
    default LicensePool.
    """

    def create_entry(self, work):
        """Turn an Identifier and a Work into an entry for an acquisition
        feed.
        """
        identifier, work = work

        # Most of the time we can use the cached OPDS entry for the
        # Work. However, that cached OPDS feed is designed around one
        # specific LicensePool, and it's possible that the client is
        # asking for a lookup centered around a different edition of the
        # same book.
        default_licensepool = self.annotator.active_licensepool_for(work)
        if identifier.licensed_through:
            active_licensepool = identifier.licensed_through[0]
        else:
            active_licensepool = default_licensepool

        # In that case, we can't use the cached OPDS entry. We need to
        # create a new one (and not store it in the cache).
        use_cache = (active_licensepool == default_licensepool)

        error_status = error_message = None
        if not active_licensepool:
            error_status = 404
            error_message = "Identifier not found in collection"
        elif identifier.work != work:
            error_status = 500
            error_message = 'I tried to generate an OPDS entry for the identifier "%s" using a Work not associated with that identifier.' % identifier.urn
           
        if error_status:
            return self.error_message(identifier, error_status, error_message)

        if active_licensepool:
            edition = active_licensepool.presentation_edition
        else:
            edition = work.presentation_edition
        try:
            return self._create_entry(
                work, active_licensepool, edition, identifier,
                use_cache=use_cache
            )
        except UnfulfillableWork, e:
            logging.info(
                "Work %r is not fulfillable, refusing to create an <entry>.",
                work
            )
            return self.error_message(
                identifier,
                403,
                "I know about this work but can offer no way of fulfilling it."
            )

# Mock annotators for use in unit tests.

class TestAnnotator(Annotator):

    def __init__(self):
        self.lanes_by_work = defaultdict(list)

    @classmethod
    def lane_url(cls, lane):
        if lane and lane.has_visible_children:
            return cls.groups_url(lane)
        elif lane:
            return cls.feed_url(lane)
        else:
            return ""

    @classmethod
    def feed_url(cls, lane, facets=None, pagination=None):
        if isinstance(lane, Lane):
            base = "http://%s/" % lane.url_name
        else:
            base = "http://%s/" % lane.display_name
        sep = '?'
        if facets:
            base += sep + facets.query_string
            sep = '&'
        if pagination:
            base += sep + pagination.query_string
        return base

    @classmethod
    def search_url(cls, lane, query, pagination):
        if isinstance(lane, Lane):
            base = "http://%s/" % lane.url_name
        else:
            base = "http://%s/" % lane.display_name
        sep = '?'
        if pagination:
            base += sep + pagination.query_string
        return base

    @classmethod
    def groups_url(cls, lane):
        if lane and isinstance(lane, Lane):
            identifier = lane.id
        else:
            identifier = ""
        return "http://groups/%s" % identifier

    @classmethod
    def default_lane_url(cls):
        return cls.groups_url(None)

    @classmethod
    def facet_url(cls, facets):
        return "http://facet/" + "&".join(
            ["%s=%s" % (k, v) for k, v in sorted(facets.items())]
        )

    @classmethod
    def top_level_title(cls):
        return "Test Top Level Title"


class TestAnnotatorWithGroup(TestAnnotator):

    def group_uri(self, work, license_pool, identifier):
        lanes = self.lanes_by_work.get(work, None)

        if lanes:
            lane_name = lanes[0]['lane'].display_name
            additional_lanes = lanes[1:]
            if additional_lanes:
                self.lanes_by_work[work] = additional_lanes
        else:
            lane_name = str(work.id)
        return ("http://group/%s" % lane_name,
                "Group Title for %s!" % lane_name)

    def group_uri_for_lane(self, lane):
        if lane:
            return ("http://groups/%s" % lane.display_name, 
                    "Groups of %s" % lane.display_name)
        else:
            return "http://groups/", "Top-level groups"

    def top_level_title(self):
        return "Test Top Level Title"


class TestUnfulfillableAnnotator(TestAnnotator):
    """Raise an UnfulfillableWork exception when asked to annotate an entry."""

    @classmethod
    def annotate_work_entry(self, *args, **kwargs):
        raise UnfulfillableWork()

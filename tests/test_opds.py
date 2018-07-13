from collections import defaultdict
import feedparser
import datetime
from lxml import etree
from StringIO import StringIO
from nose.tools import (
    eq_,
    set_trace,
    assert_raises,
)

from . import (
    DatabaseTest,
)

from psycopg2.extras import NumericRange
from config import (
    Configuration,
    temp_config,
)
from entrypoint import (
    AudiobooksEntryPoint,
    EbooksEntryPoint,
    EntryPoint,
    EverythingEntryPoint,
)
from facets import FacetConstants
import model
from model import (
    CachedFeed,
    ConfigurationSetting,
    Contributor,
    DataSource,
    DeliveryMechanism,
    ExternalIntegration,
    Genre,
    Measurement,
    Representation,
    SessionManager,
    Subject,
    Work,
    get_one,
)

from facets import FacetConstants

from lane import (
    Facets,
    FeaturedFacets,
    Lane,
    Pagination,
    SearchFacets,
    WorkList,
)

from opds import (
    AcquisitionFeed,
    Annotator,
    LookupAcquisitionFeed,
    OPDSFeed,
    UnfulfillableWork,
    VerboseAnnotator,
    TestAnnotator,
    TestAnnotatorWithGroup,
    TestUnfulfillableAnnotator
)

from util.opds_writer import (
    AtomFeed,
    OPDSFeed,
    OPDSMessage,
)
from opds_import import OPDSXMLParser

from classifier import (
    Classifier,
    Contemporary_Romance,
    Epic_Fantasy,
    Fantasy,
    Urban_Fantasy,
    History,
    Mystery,
)

from external_search import DummyExternalSearchIndex
import xml.etree.ElementTree as ET
from flask_babel import lazy_gettext as _

class TestBaseAnnotator(DatabaseTest):

    def test_active_licensepool_for_ignores_superceded_licensepools(self):
        work = self._work(with_license_pool=True,
                          with_open_access_download=True)
        [pool1] = work.license_pools
        edition, pool2 = self._edition(with_license_pool=True)
        work.license_pools.append(pool2)

        # Start off with neither LicensePool being open-access. pool1
        # will become open-access later on, which is why we created an
        # open-access download for it.
        pool1.open_access = False
        pool1.licenses_owned = 1

        pool2.open_access = False
        pool2.licenses_owned = 1

        # If there are multiple non-superceded non-open-access license
        # pools for a work, the active license pool is one of them,
        # though we don't really know or care which one.
        assert Annotator.active_licensepool_for(work) is not None

        # Neither license pool is open-access, and pool1 is superceded.
        # The active license pool is pool2.
        pool1.superceded = True
        eq_(pool2, Annotator.active_licensepool_for(work))

        # pool2 is superceded and pool1 is not. The active licensepool
        # is pool1.
        pool1.superceded = False
        pool2.superceded = True
        eq_(pool1, Annotator.active_licensepool_for(work))

        # If both license pools are superceded, there is no active license
        # pool for the book.
        pool1.superceded = True
        eq_(None, Annotator.active_licensepool_for(work))
        pool1.superceded = False
        pool2.superceded = False

        # If one license pool is open-access and the other is not, the
        # open-access pool wins.
        pool1.open_access = True
        eq_(pool1, Annotator.active_licensepool_for(work))
        pool1.open_access = False

        # pool2 is open-access but has no usable download. The other
        # pool wins.
        pool2.open_access = True
        eq_(pool1, Annotator.active_licensepool_for(work))
        pool2.open_access = False

        # If one license pool has no owned licenses and the other has
        # owned licenses, the one with licenses wins.
        pool1.licenses_owned = 0
        pool2.licenses_owned = 1
        eq_(pool2, Annotator.active_licensepool_for(work))
        pool1.licenses_owned = 1

        # If one license pool has a presentation edition that's missing
        # a title, and the other pool has a presentation edition with a title,
        # the one with a title wins.
        pool2.presentation_edition.title = None
        eq_(pool1, Annotator.active_licensepool_for(work))

    def test_authors(self):
        # Create an Edition with an author and a narrator.
        edition = self._edition(authors=["Steven King"])
        edition.add_contributor(
            "Jonathan Frakes", Contributor.NARRATOR_ROLE
        )
        author, contributor = Annotator.authors(None, edition)

        # The <author> tag indicates a role of 'author', so there's no
        # need for an explicitly specified role property.
        eq_('author', author.tag)
        [name] = author.getchildren()
        eq_("name", name.tag)
        eq_("King, Steven", name.text)
        eq_({}, author.attrib)

        # The <contributor> tag includes an explicitly specified role
        # property to explain the nature of the contribution.
        eq_('contributor', contributor.tag)
        [name] = contributor.getchildren()
        eq_("name", name.tag)
        eq_("Frakes, Jonathan", name.text)
        role_attrib = '{%s}role' % AtomFeed.OPF_NS
        eq_(Contributor.MARC_ROLE_CODES[Contributor.NARRATOR_ROLE],
            contributor.attrib[role_attrib])

    def test_annotate_work_entry_adds_tags(self):
        work = self._work(with_license_pool=True,
                          with_open_access_download=True)
        work.last_update_time = datetime.datetime(2018, 2, 5, 7, 39, 49, 580651)
        [pool] = work.license_pools
        pool.availability_time = datetime.datetime(2015, 1, 1)

        entry = []
        # This will create four extra tags which could not be
        # generated in the cached entry because they depend on the
        # active LicensePool or identifier: the Atom ID, the distributor,
        # the date published and the date updated.
        annotator = Annotator()
        annotator.annotate_work_entry(work, pool, None, None, None, entry)
        [id, distributor, published, updated] = entry

        id_tag = etree.tostring(id)
        assert 'id' in id_tag
        assert pool.identifier.urn in id_tag

        assert 'ProviderName="Gutenberg"' in etree.tostring(distributor)

        published_tag = etree.tostring(published)
        assert 'published' in published_tag
        assert '2015-01-01' in published_tag

        updated_tag = etree.tostring(updated)
        assert 'updated' in updated_tag
        assert '2018-02-05' in updated_tag

        entry = []
        # We can pass in a specific update time to override the one
        # found in work.last_update_time.
        annotator.annotate_work_entry(
            work, pool, None, None, None, entry,
            updated=datetime.datetime(2017, 1, 2, 3, 39, 49, 580651)
        )
        [id, distributor, published, updated] = entry
        assert 'updated' in etree.tostring(updated)
        assert '2017-01-02' in etree.tostring(updated)

class TestAnnotators(DatabaseTest):

    def test_all_subjects(self):
        work = self._work(genre="Fiction", with_open_access_download=True)
        edition = work.presentation_edition
        identifier = edition.primary_identifier
        source1 = DataSource.lookup(self._db, DataSource.GUTENBERG)
        source2 = DataSource.lookup(self._db, DataSource.OCLC)

        subjects = [
            (source1, Subject.FAST, "fast1", "name1", 1),
            (source1, Subject.LCSH, "lcsh1", "name2", 1),
            (source2, Subject.LCSH, "lcsh1", "name2", 1),
            (source1, Subject.LCSH, "lcsh2", "name3", 3),
            (source1, Subject.DDC, "300", "Social sciences, sociology & anthropology", 1),
        ]

        for source, subject_type, subject, name, weight in subjects:
            identifier.classify(source, subject_type, subject, name, weight=weight)

        old_ids = model.Identifier.recursively_equivalent_identifier_ids

        class MockIdentifier(model.Identifier):
            called_with_cutoff = None
            @classmethod
            def recursively_equivalent_identifier_ids(
                    cls, _db, identifier_ids, levels=5, threshold=0.50,
                    cutoff=None):
                cls.called_with_cutoff = cutoff
                return old_ids(_db, identifier_ids, levels, threshold)
        old_identifier = model.Identifier
        model.Identifier = MockIdentifier

        category_tags = VerboseAnnotator.categories(work)
        model.Identifier = old_identifier

        # Although the default 'cutoff' for
        # recursively_equivalent_identifier_ids is null, when we are
        # generating subjects as part of an OPDS feed, the cutoff is
        # set to 100. This gives us reasonable worst-case performance
        # at the cost of not showing every single random subject under
        # which an extremely popular book is filed.
        eq_(100, MockIdentifier.called_with_cutoff)

        ddc_uri = Subject.uri_lookup[Subject.DDC]
        rating_value = '{http://schema.org/}ratingValue'
        eq_([{'term': u'300',
              rating_value: 1,
              'label': u'Social sciences, sociology & anthropology'}],
            category_tags[ddc_uri])

        fast_uri = Subject.uri_lookup[Subject.FAST]
        eq_([{'term': u'fast1', 'label': u'name1', rating_value: 1}],
            category_tags[fast_uri])

        lcsh_uri = Subject.uri_lookup[Subject.LCSH]
        eq_([{'term': u'lcsh1', 'label': u'name2', rating_value: 2},
             {'term': u'lcsh2', 'label': u'name3', rating_value: 3}],
            sorted(category_tags[lcsh_uri]))

        genre_uri = Subject.uri_lookup[Subject.SIMPLIFIED_GENRE]
        eq_([dict(label='Fiction', term=Subject.SIMPLIFIED_GENRE+"Fiction")], category_tags[genre_uri])

    def test_appeals(self):
        work = self._work(with_open_access_download=True)
        work.appeal_language = 0.1
        work.appeal_character = 0.2
        work.appeal_story = 0.3
        work.appeal_setting = 0.4
        work.calculate_opds_entries(verbose=True)

        category_tags = VerboseAnnotator.categories(work)
        appeal_tags = category_tags[Work.APPEALS_URI]
        expect = [
            (Work.APPEALS_URI + Work.LANGUAGE_APPEAL, Work.LANGUAGE_APPEAL, 0.1),
            (Work.APPEALS_URI + Work.CHARACTER_APPEAL, Work.CHARACTER_APPEAL, 0.2),
            (Work.APPEALS_URI + Work.STORY_APPEAL, Work.STORY_APPEAL, 0.3),
            (Work.APPEALS_URI + Work.SETTING_APPEAL, Work.SETTING_APPEAL, 0.4)
        ]
        actual = [
            (x['term'], x['label'], x['{http://schema.org/}ratingValue'])
            for x in appeal_tags
        ]
        eq_(set(expect), set(actual))

    def test_detailed_author(self):
        c, ignore = self._contributor("Familyname, Givenname")
        c.display_name = "Givenname Familyname"
        c.family_name = "Familyname"
        c.wikipedia_name = "Givenname Familyname (Author)"
        c.viaf = "100"
        c.lc = "n100"

        author_tag = VerboseAnnotator.detailed_author(c)

        tag_string = etree.tostring(author_tag)
        assert "<name>Givenname Familyname</" in tag_string
        assert "<simplified:sort_name>Familyname, Givenname</" in tag_string
        assert "<simplified:wikipedia_name>Givenname Familyname (Author)</" in tag_string
        assert "<schema:sameas>http://viaf.org/viaf/100</" in tag_string
        assert "<schema:sameas>http://id.loc.gov/authorities/names/n100</"

        work = self._work(authors=[], with_license_pool=True)
        work.presentation_edition.add_contributor(c, Contributor.PRIMARY_AUTHOR_ROLE)

        [same_tag] = VerboseAnnotator.authors(work, work.presentation_edition)
        eq_(tag_string, etree.tostring(same_tag))

    def test_duplicate_author_names_are_ignored(self):
        """Ignores duplicate author names"""
        work = self._work(with_license_pool=True)
        duplicate = self._contributor()[0]
        duplicate.sort_name = work.author

        edition = work.presentation_edition
        edition.add_contributor(duplicate, Contributor.AUTHOR_ROLE)

        eq_(1, len(Annotator.authors(work, edition)))

    def test_all_annotators_mention_every_relevant_author(self):
        work = self._work(authors=[], with_license_pool=True)
        edition = work.presentation_edition

        primary_author, ignore = self._contributor()
        author, ignore = self._contributor()
        illustrator, ignore = self._contributor()
        barrel_washer, ignore = self._contributor()

        edition.add_contributor(
            primary_author, Contributor.PRIMARY_AUTHOR_ROLE
        )
        edition.add_contributor(author, Contributor.AUTHOR_ROLE)

        # This contributor is relevant because we have a MARC Role Code
        # for the role.
        edition.add_contributor(illustrator, Contributor.ILLUSTRATOR_ROLE)

        # This contributor is not relevant because we have no MARC
        # Role Code for the role.
        edition.add_contributor(barrel_washer, "Barrel Washer")

        role_attrib = '{%s}role' % AtomFeed.OPF_NS
        illustrator_code = Contributor.MARC_ROLE_CODES[
            Contributor.ILLUSTRATOR_ROLE
        ]

        for annotator in Annotator, VerboseAnnotator:
            tags = Annotator.authors(work, edition)
            # We made two <author> tags and one <contributor>
            # tag, for the illustrator.
            eq_(['author', 'author', 'contributor'],
                [x.tag for x in tags])
            eq_([None, None, illustrator_code],
                [x.attrib.get(role_attrib) for x in tags]
            )

    def test_ratings(self):
        work = self._work(
            with_license_pool=True, with_open_access_download=True)
        work.quality = 1.0/3
        work.popularity = 0.25
        work.rating = 0.6
        work.calculate_opds_entries(verbose=True)
        feed = AcquisitionFeed(
            self._db, self._str, self._url, [work], VerboseAnnotator
        )
        url = self._url
        tag = feed.create_entry(work, None)

        nsmap = dict(schema='http://schema.org/')
        ratings = [(rating.get('{http://schema.org/}ratingValue'),
                    rating.get('{http://schema.org/}additionalType'))
                   for rating in tag.xpath("schema:Rating", namespaces=nsmap)]
        expected = [
            ('0.3333', Measurement.QUALITY),
            ('0.2500', Measurement.POPULARITY),
            ('0.6000', None)
        ]
        eq_(set(expected), set(ratings))

    def test_subtitle(self):
        work = self._work(with_license_pool=True, with_open_access_download=True)
        work.presentation_edition.subtitle = "Return of the Jedi"
        work.calculate_opds_entries()

        raw_feed = unicode(AcquisitionFeed(
            self._db, self._str, self._url, [work], Annotator
        ))
        assert "schema:alternativeHeadline" in raw_feed
        assert work.presentation_edition.subtitle in raw_feed

        feed = feedparser.parse(unicode(raw_feed))
        alternative_headline = feed['entries'][0]['schema_alternativeheadline']
        eq_(work.presentation_edition.subtitle, alternative_headline)

        # If there's no subtitle, the subtitle tag isn't included.
        work.presentation_edition.subtitle = None
        work.calculate_opds_entries()
        raw_feed = unicode(AcquisitionFeed(
            self._db, self._str, self._url, [work], Annotator
        ))

        assert "schema:alternativeHeadline" not in raw_feed
        assert "Return of the Jedi" not in raw_feed
        [entry] = feedparser.parse(unicode(raw_feed))['entries']
        assert 'schema_alternativeheadline' not in entry.items()

    def test_series(self):
        work = self._work(with_license_pool=True, with_open_access_download=True)
        work.presentation_edition.series = "Harry Otter and the Lifetime of Despair"
        work.presentation_edition.series_position = 4
        work.calculate_opds_entries()

        raw_feed = unicode(AcquisitionFeed(
            self._db, self._str, self._url, [work], Annotator
        ))
        assert "schema:Series" in raw_feed
        assert work.presentation_edition.series in raw_feed

        feed = feedparser.parse(unicode(raw_feed))
        schema_entry = feed['entries'][0]['schema_series']
        eq_(work.presentation_edition.series, schema_entry['name'])
        eq_(str(work.presentation_edition.series_position), schema_entry['schema:position'])

        # The series position can be 0, for a prequel for example.
        work.presentation_edition.series_position = 0
        work.calculate_opds_entries()

        raw_feed = unicode(AcquisitionFeed(
            self._db, self._str, self._url, [work], Annotator
        ))
        assert "schema:Series" in raw_feed
        assert work.presentation_edition.series in raw_feed

        feed = feedparser.parse(unicode(raw_feed))
        schema_entry = feed['entries'][0]['schema_series']
        eq_(work.presentation_edition.series, schema_entry['name'])
        eq_(str(work.presentation_edition.series_position), schema_entry['schema:position'])

        # If there's no series title, the series tag isn't included.
        work.presentation_edition.series = None
        work.calculate_opds_entries()
        raw_feed = unicode(AcquisitionFeed(
            self._db, self._str, self._url, [work], Annotator
        ))

        assert "schema:Series" not in raw_feed
        assert "Lifetime of Despair" not in raw_feed
        [entry] = feedparser.parse(unicode(raw_feed))['entries']
        assert 'schema_series' not in entry.items()


class TestOPDS(DatabaseTest):

    def links(self, entry, rel=None):
        if 'feed' in entry:
            entry = entry['feed']
        links = sorted(entry['links'], key=lambda x: (x['rel'], x.get('title')))
        r = []
        for l in links:
            if (not rel or l['rel'] == rel or
                (isinstance(rel, list) and l['rel'] in rel)):
                r.append(l)
        return r

    def setup(self):
        super(TestOPDS, self).setup()

        self.fiction = self._lane("Fiction")
        self.fiction.fiction = True
        self.fiction.audiences = [Classifier.AUDIENCE_ADULT]

        self.fantasy = self._lane(
            "Fantasy", parent=self.fiction, genres="Fantasy"
        )
        self.history = self._lane(
            "History", genres="History"
        )
        self.ya = self._lane("Young Adult")
        self.ya.history = None
        self.ya.audiences = [Classifier.AUDIENCE_YOUNG_ADULT]
        self.romance = self._lane("Romance", genres="Romance")
        self.romance.fiction = True
        self.contemporary_romance = self._lane(
            "Contemporary Romance", parent=self.romance,
            genres="Contemporary Romance"
        )

        self.conf = WorkList()
        self.conf.initialize(
            self._default_library,
            children=[self.fiction, self.fantasy, self.history, self.ya,
                      self.romance]
        )

    def test_acquisition_link(self):
        m = AcquisitionFeed.acquisition_link
        rel = AcquisitionFeed.BORROW_REL
        href = self._url

        # A doubly-indirect acquisition link.
        a = m(rel, href, ["text/html", "text/plain", "application/pdf"])
        eq_(etree.tostring(a), '<link href="%s" rel="http://opds-spec.org/acquisition/borrow" type="text/html"><ns0:indirectAcquisition xmlns:ns0="http://opds-spec.org/2010/catalog" type="text/plain"><ns0:indirectAcquisition type="application/pdf"/></ns0:indirectAcquisition></link>' % href)

        # A direct acquisition link.
        b = m(rel, href, ["application/epub"])
        eq_(etree.tostring(b), '<link href="%s" rel="http://opds-spec.org/acquisition/borrow" type="application/epub"/>' % href)

    def test_group_uri(self):
        work = self._work(with_open_access_download=True, authors="Alice")
        [lp] = work.license_pools

        annotator = TestAnnotatorWithGroup()
        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               [work], annotator)
        u = unicode(feed)
        parsed = feedparser.parse(u)
        [group_link] = parsed.entries[0]['links']
        expect_uri, expect_title = annotator.group_uri(
            work, lp, lp.identifier)
        eq_(OPDSFeed.GROUP_REL, group_link['rel'])
        eq_(expect_uri, group_link['href'])
        eq_(expect_title, group_link['title'])

        # Verify that the same group_uri is created whether a Work or
        # a MaterializedWorkWithGenre is passed in.
        self.add_to_materialized_view([work])
        from model import MaterializedWorkWithGenre
        [mw] = self._db.query(MaterializedWorkWithGenre).all()

        mw_uri, mw_title = annotator.group_uri(mw, lp, lp.identifier)
        eq_(mw_uri, expect_uri)
        assert str(mw.works_id) in mw_uri

    def test_acquisition_feed(self):
        work = self._work(with_open_access_download=True, authors="Alice")

        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               [work])
        u = unicode(feed)
        assert '<entry schema:additionalType="http://schema.org/EBook">' in u
        parsed = feedparser.parse(u)
        [with_author] = parsed['entries']
        eq_("Alice", with_author['authors'][0]['name'])

    def test_acquisition_feed_includes_license_source(self):
        work = self._work(with_open_access_download=True)
        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               [work])
        gutenberg = DataSource.lookup(self._db, DataSource.GUTENBERG)

        # The <bibframe:distribution> tag containing the license
        # source should show up once and only once. (At one point a
        # bug caused it to be added to the generated OPDS twice.)
        expect = '<bibframe:distribution bibframe:ProviderName="%s"/>' % (
            gutenberg.name
        )
        assert (1, unicode(feed).count(expect))

        # If the LicensePool is a stand-in produced for internal
        # processing purposes, it does not represent an actual license for
        # the book, and the <bibframe:distribution> tag is not
        # included.
        internal = DataSource.lookup(self._db, DataSource.INTERNAL_PROCESSING)
        work.license_pools[0].data_source = internal
        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               [work])
        assert '<bibframe:distribution' not in unicode(feed)


    def test_acquisition_feed_includes_author_tag_even_when_no_author(self):
        work = self._work(with_open_access_download=True)
        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               [work])
        u = unicode(feed)
        assert "<author>" in u

    def test_acquisition_feed_includes_permanent_work_id(self):
        work = self._work(with_open_access_download=True)
        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               [work])
        u = unicode(feed)
        parsed = feedparser.parse(u)
        entry = parsed['entries'][0]
        eq_(work.presentation_edition.permanent_work_id,
            entry['simplified_pwid'])

    def test_lane_feed_contains_facet_links(self):
        work = self._work(with_open_access_download=True)

        lane = self._lane()
        facets = Facets.default(self._default_library)

        cached_feed = AcquisitionFeed.page(
            self._db, "title", "http://the-url.com/",
            lane, TestAnnotator, facets=facets
        )

        u = unicode(cached_feed)
        parsed = feedparser.parse(u)
        by_title = parsed['feed']

        [self_link] = self.links(by_title, 'self')
        eq_("http://the-url.com/", self_link['href'])
        facet_links = self.links(by_title, AcquisitionFeed.FACET_REL)

        library = self._default_library
        order_facets = library.enabled_facets(
            Facets.ORDER_FACET_GROUP_NAME
        )
        availability_facets = library.enabled_facets(
            Facets.AVAILABILITY_FACET_GROUP_NAME
        )
        collection_facets = library.enabled_facets(
            Facets.COLLECTION_FACET_GROUP_NAME
        )

        def link_for_facets(facets):
            return [x for x in facet_links if facets.query_string in x['href']]

        facets = Facets(library, None, None, None)
        for i1, i2, new_facets, selected in facets.facet_groups:
            links = link_for_facets(new_facets)
            if selected:
                # This facet set is already selected, so it should
                # show up three times--once for every facet group.
                eq_(3, len(links))
            else:
                # This facet set is not selected, so it should have one
                # transition link.
                eq_(1, len(links))

        # As we'll see below, the feed parser parses facetGroup as
        # facetgroup and activeFacet as activefacet. As we see here,
        # that's not a problem with the generator code.
        assert 'opds:facetgroup' not in u
        assert 'opds:facetGroup' in u
        assert 'opds:activefacet' not in u
        assert 'opds:activeFacet' in u

    def test_acquisition_feed_includes_available_and_issued_tag(self):
        today = datetime.date.today()
        today_s = today.strftime("%Y-%m-%d")
        the_past = today - datetime.timedelta(days=2)
        the_past_s = the_past.strftime("%Y-%m-%d")
        the_past_time = the_past.strftime(AtomFeed.TIME_FORMAT)
        the_distant_past = today - datetime.timedelta(days=100)
        the_distant_past_s = the_distant_past.strftime('%Y-%m-%dT%H:%M:%SZ')
        the_future = today + datetime.timedelta(days=2)

        # This work has both issued and published. issued will be used
        # for the dc:issued tag.
        work1 = self._work(with_open_access_download=True)
        work1.presentation_edition.issued = today
        work1.presentation_edition.published = the_past
        work1.license_pools[0].availability_time = the_distant_past

        # This work only has published. published will be used for the
        # dc:issued tag.
        work2 = self._work(with_open_access_download=True)
        work2.presentation_edition.published = the_past
        work2.license_pools[0].availability_time = the_distant_past

        # This work has neither published nor issued. There will be no
        # dc:issued tag.
        work3 = self._work(with_open_access_download=True)
        work3.license_pools[0].availability_time = None

        # This work is issued in the future. Since this makes no
        # sense, there will be no dc:issued tag.
        work4 = self._work(with_open_access_download=True)
        work4.presentation_edition.issued = the_future
        work4.presentation_edition.published = the_future
        work4.license_pools[0].availability_time = None

        for w in work1, work2, work3, work4:
            w.calculate_opds_entries(verbose=False)

        self._db.commit()
        works = self._db.query(Work)
        with_times = AcquisitionFeed(
            self._db, "test", "url", works, TestAnnotator)
        u = unicode(with_times)
        assert 'dcterms:issued' in u

        with_times = etree.parse(StringIO(u))
        entries = OPDSXMLParser._xpath(with_times, '/atom:feed/atom:entry')
        parsed = []
        for entry in entries:
            title = OPDSXMLParser._xpath1(entry, 'atom:title').text
            issued = OPDSXMLParser._xpath1(entry, 'dcterms:issued')
            if issued != None:
                issued = issued.text
            published = OPDSXMLParser._xpath1(entry, 'atom:published')
            if published != None:
                published = published.text
            parsed.append(
                dict(
                    title=title,
                    issued=issued,
                    published=published,
                )
            )
        e1, e2, e3, e4 = sorted(
            parsed, key = lambda x: x['title']
        )
        eq_(today_s, e1['issued'])
        eq_(the_distant_past_s, e1['published'])

        eq_(the_past_s, e2['issued'])
        eq_(the_distant_past_s, e2['published'])

        eq_(None, e3['issued'])
        eq_(None, e3['published'])

        eq_(None, e4['issued'])
        eq_(None, e4['published'])

    def test_acquisition_feed_includes_publisher_and_imprint_tag(self):
        work = self._work(with_open_access_download=True)
        work.presentation_edition.publisher = "The Publisher"
        work.presentation_edition.imprint = "The Imprint"
        work2 = self._work(with_open_access_download=True)
        work2.presentation_edition.publisher = None

        self._db.commit()
        for w in work, work2:
            w.calculate_opds_entries(verbose=False)

        works = self._db.query(Work)
        with_publisher = AcquisitionFeed(
            self._db, "test", "url", works, TestAnnotator)
        with_publisher = feedparser.parse(unicode(with_publisher))
        entries = sorted(with_publisher['entries'], key = lambda x: x['title'])
        eq_('The Publisher', entries[0]['dcterms_publisher'])
        eq_('The Imprint', entries[0]['bib_publisherimprint'])
        assert 'publisher' not in entries[1]

    def test_acquisition_feed_includes_audience_as_category(self):
        work = self._work(with_open_access_download=True)
        work.audience = "Young Adult"
        work2 = self._work(with_open_access_download=True)
        work2.audience = "Children"
        work2.target_age = NumericRange(7,9)
        work3 = self._work(with_open_access_download=True)
        work3.audience = None
        work4 = self._work(with_open_access_download=True)
        work4.audience = "Adult"
        work4.target_age = NumericRange(18)

        self._db.commit()

        for w in work, work2, work3, work4:
            w.calculate_opds_entries(verbose=False)

        works = self._db.query(Work)
        with_audience = AcquisitionFeed(self._db, "test", "url", works)
        u = unicode(with_audience)
        with_audience = feedparser.parse(u)
        ya, children, no_audience, adult = sorted(with_audience['entries'], key = lambda x: int(x['title']))
        scheme = "http://schema.org/audience"
        eq_(
            [('Young Adult', 'Young Adult')],
            [(x['term'], x['label']) for x in ya['tags']
             if x['scheme'] == scheme]
        )

        eq_(
            [('Children', 'Children')],
            [(x['term'], x['label']) for x in children['tags']
             if x['scheme'] == scheme]
        )

        age_scheme = Subject.uri_lookup[Subject.AGE_RANGE]
        eq_(
            [('7-9', '7-9')],
            [(x['term'], x['label']) for x in children['tags']
             if x['scheme'] == age_scheme]
        )

        eq_([],
            [(x['term'], x['label']) for x in no_audience['tags']
             if x['scheme'] == scheme])

        # Even though the 'Adult' book has a target age, the target
        # age is not shown, because target age is only a relevant
        # concept for children's and YA books.
        eq_(
            [],
            [(x['term'], x['label']) for x in adult['tags']
             if x['scheme'] == age_scheme]
        )

    def test_acquisition_feed_includes_category_tags_for_appeals(self):
        work = self._work(with_open_access_download=True)
        work.appeal_language = 0.1
        work.appeal_character = 0.2
        work.appeal_story = 0.3
        work.appeal_setting = 0.4

        work2 = self._work(with_open_access_download=True)

        for w in work, work2:
            w.calculate_opds_entries(verbose=False)

        self._db.commit()
        works = self._db.query(Work)
        feed = AcquisitionFeed(self._db, "test", "url", works)
        feed = feedparser.parse(unicode(feed))
        entries = sorted(feed['entries'], key = lambda x: int(x['title']))

        tags = entries[0]['tags']
        matches = [(x['term'], x['label']) for x in tags if x['scheme'] == Work.APPEALS_URI]
        eq_([
            (Work.APPEALS_URI + 'Character', 'Character'),
            (Work.APPEALS_URI + 'Language', 'Language'),
            (Work.APPEALS_URI + 'Setting', 'Setting'),
            (Work.APPEALS_URI + 'Story', 'Story'),
        ],
            sorted(matches)
        )

        tags = entries[1]['tags']
        matches = [(x['term'], x['label']) for x in tags if x['scheme'] == Work.APPEALS_URI]
        eq_([], matches)

    def test_acquisition_feed_includes_category_tags_for_fiction_status(self):
        work = self._work(with_open_access_download=True)
        work.fiction = False

        work2 = self._work(with_open_access_download=True)
        work2.fiction = True

        for w in work, work2:
            w.calculate_opds_entries(verbose=False)

        self._db.commit()
        works = self._db.query(Work)
        feed = AcquisitionFeed(self._db, "test", "url", works)
        feed = feedparser.parse(unicode(feed))
        entries = sorted(feed['entries'], key = lambda x: int(x['title']))

        scheme = "http://librarysimplified.org/terms/fiction/"

        eq_([(scheme+'Nonfiction', 'Nonfiction')],
            [(x['term'], x['label']) for x in entries[0]['tags']
             if x['scheme'] == scheme]
        )
        eq_([(scheme+'Fiction', 'Fiction')],
            [(x['term'], x['label']) for x in entries[1]['tags']
             if x['scheme'] == scheme]
        )


    def test_acquisition_feed_includes_category_tags_for_genres(self):
        work = self._work(with_open_access_download=True)
        g1, ignore = Genre.lookup(self._db, "Science Fiction")
        g2, ignore = Genre.lookup(self._db, "Romance")
        work.genres = [g1, g2]

        work.calculate_opds_entries(verbose=False)

        self._db.commit()
        works = self._db.query(Work)
        feed = AcquisitionFeed(self._db, "test", "url", works)
        feed = feedparser.parse(unicode(feed))
        entries = sorted(feed['entries'], key = lambda x: int(x['title']))

        scheme = Subject.SIMPLIFIED_GENRE
        eq_(
            [(scheme+'Romance', 'Romance'),
             (scheme+'Science%20Fiction', 'Science Fiction')],
            sorted(
                [(x['term'], x['label']) for x in entries[0]['tags']
                 if x['scheme'] == scheme]
            )
        )

    def test_acquisition_feed_omits_works_with_no_active_license_pool(self):
        work = self._work(title="open access", with_open_access_download=True)
        no_license_pool = self._work(title="no license pool", with_license_pool=False)
        no_download = self._work(title="no download", with_license_pool=True)
        no_download.license_pools[0].open_access = True
        not_open_access = self._work("not open access", with_license_pool=True)
        not_open_access.license_pools[0].open_access = False
        self._db.commit()

        # We get a feed with two entries--the open-access book and
        # the non-open-access book--and two error messages--the book with
        # no license pool and the book but with no download.
        works = self._db.query(Work)
        by_title_feed = AcquisitionFeed(self._db, "test", "url", works)
        by_title_raw = unicode(by_title_feed)
        by_title = feedparser.parse(by_title_raw)

        # We have two entries...
        eq_(2, len(by_title['entries']))
        eq_(["not open access", "open access"], sorted(
            [x['title'] for x in by_title['entries']]))

        # ...and two messages.
        eq_(2,
            by_title_raw.count("I've heard about this work but have no active licenses for it.")
        )

    def test_acquisition_feed_includes_image_links(self):
        work = self._work(genre=Fantasy, with_open_access_download=True)
        work.presentation_edition.cover_thumbnail_url = "http://thumbnail/b"
        work.presentation_edition.cover_full_url = "http://full/a"
        work.calculate_opds_entries(verbose=False)

        feed = feedparser.parse(unicode(work.simple_opds_entry))
        links = sorted([x['href'] for x in feed['entries'][0]['links'] if
                        'image' in x['rel']])
        eq_(['http://full/a', 'http://thumbnail/b'], links)

    def test_acquisition_feed_image_links_respect_cdn(self):
        work = self._work(genre=Fantasy, with_open_access_download=True)
        work.presentation_edition.cover_thumbnail_url = "http://thumbnail.com/b"
        work.presentation_edition.cover_full_url = "http://full.com/a"

        # Create some CDNS.
        with temp_config() as config:
            config[Configuration.INTEGRATIONS][ExternalIntegration.CDN] = {
                'thumbnail.com' : 'http://foo/',
                'full.com' : 'http://bar/'
            }
            work.calculate_opds_entries(verbose=False)

        feed = feedparser.parse(work.simple_opds_entry)
        links = sorted([x['href'] for x in feed['entries'][0]['links'] if
                        'image' in x['rel']])
        eq_(['http://bar/a', 'http://foo/b'], links)

    def test_messages(self):
        """Test the ability to include OPDSMessage objects for a given URN in
        lieu of a proper ODPS entry.
        """
        messages = [
            OPDSMessage("urn:foo", 400, _("msg1")),
            OPDSMessage("urn:bar", 500, _("msg2")),
        ]
        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               [], precomposed_entries=messages)
        feed = unicode(feed)
        for m in messages:
            assert m.urn in feed
            assert str(m.status_code) in feed
            assert str(m.message) in feed

    def test_precomposed_entries(self):
        """Test the ability to include precomposed OPDS entries
        in a feed.
        """
        entry = AcquisitionFeed.E.entry()
        entry.text='foo'
        feed = AcquisitionFeed(self._db, "test", "http://the-url.com/",
                               works=[], precomposed_entries=[entry])
        feed = unicode(feed)
        assert '<entry>foo</entry>' in feed

    def test_page_feed(self):
        """Test the ability to create a paginated feed of works for a given
        lane.
        """
        lane = self.contemporary_romance
        work1 = self._work(genre=Contemporary_Romance, with_open_access_download=True)
        work2 = self._work(genre=Contemporary_Romance, with_open_access_download=True)

        self.add_to_materialized_view([work1, work2], True)
        facets = Facets.default(self._default_library)
        pagination = Pagination(size=1)

        def make_page(pagination):
            return AcquisitionFeed.page(
                self._db, "test", self._url, lane, TestAnnotator,
                pagination=pagination
            )
        cached_works = make_page(pagination)
        parsed = feedparser.parse(unicode(cached_works))
        eq_(work1.title, parsed['entries'][0]['title'])

        # Make sure the links are in place.
        [up_link] = self.links(parsed, 'up')
        eq_(TestAnnotator.groups_url(lane.parent), up_link['href'])
        eq_(lane.parent.display_name, up_link['title'])

        [start] = self.links(parsed, 'start')
        eq_(TestAnnotator.groups_url(None), start['href'])
        eq_(TestAnnotator.top_level_title(), start['title'])

        [next_link] = self.links(parsed, 'next')
        eq_(TestAnnotator.feed_url(lane, facets, pagination.next_page), next_link['href'])

        # This was the first page, so no previous link.
        eq_([], self.links(parsed, 'previous'))

        # Now get the second page and make sure it has a 'previous' link.
        cached_works = make_page(pagination.next_page)
        parsed = feedparser.parse(cached_works)
        [previous] = self.links(parsed, 'previous')
        eq_(TestAnnotator.feed_url(lane, facets, pagination), previous['href'])
        eq_(work2.title, parsed['entries'][0]['title'])

        # The feed has breadcrumb links
        parentage = list(lane.parentage)
        root = ET.fromstring(cached_works)
        breadcrumbs = root.find("{%s}breadcrumbs" % AtomFeed.SIMPLIFIED_NS)
        links = breadcrumbs.getchildren()

        # There's one breadcrumb link for each parent Lane, plus one for
        # the top-level.
        eq_(len(parentage) + 1, len(links))
        eq_(TestAnnotator.top_level_title(), links[0].get("title"))
        eq_(TestAnnotator.default_lane_url(), links[0].get("href"))
        for i, lane in enumerate(parentage):
            eq_(lane.display_name, links[i+1].get("title"))
            eq_(TestAnnotator.lane_url(lane), links[i+1].get("href"))

        # When a feed is created without a cache_type of NO_CACHE,
        # CachedFeeds aren't used.
        old_cache_count = self._db.query(CachedFeed).count()
        raw_page = AcquisitionFeed.page(
            self._db, "test", self._url, lane, TestAnnotator,
            pagination=pagination.next_page, cache_type=AcquisitionFeed.NO_CACHE
        )

        # Unicode is returned instead of a CachedFeed object.
        eq_(True, isinstance(raw_page, unicode))
        # No new CachedFeeds have been created.
        eq_(old_cache_count, self._db.query(CachedFeed).count())
        # The entries in the feed are the same as they were when
        # they were cached before.
        eq_(sorted(parsed.entries), sorted(feedparser.parse(raw_page).entries))

    def test_page_feed_for_worklist(self):
        """Test the ability to create a paginated feed of works for a
        WorkList instead of a Lane.
        """
        lane = self.conf
        work1 = self._work(genre=Contemporary_Romance, with_open_access_download=True)
        work2 = self._work(genre=Contemporary_Romance, with_open_access_download=True)

        self.add_to_materialized_view([work1, work2], True)
        facets = Facets.default(self._default_library)
        pagination = Pagination(size=1)

        def make_page(pagination):
            return AcquisitionFeed.page(
                self._db, "test", self._url, lane, TestAnnotator,
                pagination=pagination
            )
        cached_works = make_page(pagination)
        parsed = feedparser.parse(unicode(cached_works))
        eq_(work1.title, parsed['entries'][0]['title'])

        # Make sure the links are in place.
        # This is the top-level, so no up link.
        eq_([], self.links(parsed, 'up'))

        [start] = self.links(parsed, 'start')
        eq_(TestAnnotator.groups_url(None), start['href'])
        eq_(TestAnnotator.top_level_title(), start['title'])

        [next_link] = self.links(parsed, 'next')
        eq_(TestAnnotator.feed_url(lane, facets, pagination.next_page), next_link['href'])

        # This was the first page, so no previous link.
        eq_([], self.links(parsed, 'previous'))

        # Now get the second page and make sure it has a 'previous' link.
        cached_works = make_page(pagination.next_page)
        parsed = feedparser.parse(cached_works)
        [previous] = self.links(parsed, 'previous')
        eq_(TestAnnotator.feed_url(lane, facets, pagination), previous['href'])
        eq_(work2.title, parsed['entries'][0]['title'])

        # The feed has no parents, so no breadcrumbs.
        root = ET.fromstring(cached_works)
        breadcrumbs = root.find("{%s}breadcrumbs" % AtomFeed.SIMPLIFIED_NS)
        eq_(None, breadcrumbs)

        # When a feed is created without a cache_type of NO_CACHE,
        # CachedFeeds aren't used.
        old_cache_count = self._db.query(CachedFeed).count()
        raw_page = AcquisitionFeed.page(
            self._db, "test", self._url, lane, TestAnnotator,
            pagination=pagination.next_page, cache_type=AcquisitionFeed.NO_CACHE
        )

        # Unicode is returned instead of a CachedFeed object.
        eq_(True, isinstance(raw_page, unicode))
        # No new CachedFeeds have been created.
        eq_(old_cache_count, self._db.query(CachedFeed).count())
        # The entries in the feed are the same as they were when
        # they were cached before.
        eq_(sorted(parsed.entries), sorted(feedparser.parse(raw_page).entries))

    def test_groups_feed(self):
        """Test the ability to create a grouped feed of recommended works for
        a given lane.
        """
        epic_fantasy = self._lane(
            "Epic Fantasy", parent=self.fantasy, genres=["Epic Fantasy"]
        )
        urban_fantasy = self._lane(
            "Urban Fantasy", parent=self.fantasy, genres=["Urban Fantasy"]
        )
        work1 = self._work(genre=Epic_Fantasy, with_open_access_download=True)
        work1.quality = 0.75
        work2 = self._work(genre=Urban_Fantasy, with_open_access_download=True)
        work2.quality = 0.75
        self.add_to_materialized_view([work1, work2])

        library = self._default_library
        library.setting(library.FEATURED_LANE_SIZE).value = 2

        annotator = TestAnnotatorWithGroup()

        cached_groups = AcquisitionFeed.groups(
            self._db, "test", self._url, self.fantasy, annotator,
            force_refresh=True
        )
        parsed = feedparser.parse(cached_groups)

        # There are four entries in three lanes.
        e1, e2, e3, e4 = parsed['entries']

        # Each entry has one and only one link.
        [l1], [l2], [l3], [l4] = [x['links'] for x in parsed['entries']]

        # Those links are 'collection' links that classify the
        # works under their subgenres.
        assert all([l['rel'] == 'collection' for l in (l1, l2)])

        eq_(l1['href'], 'http://group/Epic Fantasy')
        eq_(l1['title'], 'Group Title for Epic Fantasy!')
        eq_(l2['href'], 'http://group/Urban Fantasy')
        eq_(l2['title'], 'Group Title for Urban Fantasy!')
        eq_(l3['href'], 'http://group/Fantasy')
        eq_(l3['title'], 'Group Title for Fantasy!')
        eq_(l4['href'], 'http://group/Fantasy')
        eq_(l4['title'], 'Group Title for Fantasy!')

        # The feed itself has an 'up' link which points to the
        # groups for Fiction, and a 'start' link which points to
        # the top-level groups feed.
        [up_link] = self.links(parsed['feed'], 'up')
        eq_("http://groups/%s" % self.fiction.id, up_link['href'])
        eq_("Fiction", up_link['title'])

        [start_link] = self.links(parsed['feed'], 'start')
        eq_("http://groups/", start_link['href'])
        eq_(annotator.top_level_title(), start_link['title'])

        # The feed has breadcrumb links
        ancestors = list(self.fantasy.parentage)
        root = ET.fromstring(cached_groups)
        breadcrumbs = root.find("{%s}breadcrumbs" % AtomFeed.SIMPLIFIED_NS)
        links = breadcrumbs.getchildren()
        eq_(len(ancestors) + 1, len(links))
        eq_(annotator.top_level_title(), links[0].get("title"))
        eq_(annotator.default_lane_url(), links[0].get("href"))
        for i, lane in enumerate(reversed(ancestors)):
            eq_(lane.display_name, links[i+1].get("title"))
            eq_(annotator.lane_url(lane), links[i+1].get("href"))

        # When a feed is created without a cache_type of NO_CACHE,
        # CachedFeeds aren't used.
        old_cache_count = self._db.query(CachedFeed).count()
        raw_groups = AcquisitionFeed.groups(
            self._db, "test", self._url, self.fantasy, annotator,
            cache_type=AcquisitionFeed.NO_CACHE
        )

        # Unicode is returned instead of a CachedFeed object.
        eq_(True, isinstance(raw_groups, unicode))
        # No new CachedFeeds have been created.
        eq_(old_cache_count, self._db.query(CachedFeed).count())
        # The entries in the feed are the same as they were when
        # they were cached before.
        eq_(sorted(parsed.entries), sorted(feedparser.parse(raw_groups).entries))

    def test_groups_feed_with_empty_sublanes_is_page_feed(self):
        """Test that a page feed is returned when the requested groups
        feed has no books in the groups.
        """
        library = self._default_library

        test_lane = self._lane("Test Lane", genres=['Mystery'])

        # If groups()
        class MockGroups(object):
            called_with = None
            def groups(self, *args, **kwargs):
                self.called_with = (args, kwargs)
                return []
        mock = MockGroups()
        test_lane.groups = mock.groups

        work1 = self._work(genre=Mystery, with_open_access_download=True)
        work1.quality = 0.75
        work2 = self._work(genre=Mystery, with_open_access_download=True)
        work2.quality = 0.75
        self.add_to_materialized_view([work1, work2], True)

        library.setting(library.FEATURED_LANE_SIZE).value = 2
        annotator = TestAnnotator()

        feed = AcquisitionFeed.groups(
            self._db, "test", self._url, test_lane, annotator,
            force_refresh=True
        )

        # The lane has no sublanes, so a page feed was created for it
        # and filed as a groups feed.
        cached = get_one(self._db, CachedFeed, lane=test_lane)
        eq_(CachedFeed.GROUPS_TYPE, cached.type)

        parsed = feedparser.parse(feed)

        # There are two entries, one for each work.
        e1, e2 = parsed['entries']

        # The entries have no links (no collection links).
        assert all('links' not in entry for entry in [e1, e2])

        # groups() was never called.
        eq_(None, mock.called_with)

        # Now the lane has a sublane, but Lane.groups(), once called,
        # returns nothing.
        self._db.delete(cached)
        sublane = self._lane(parent=test_lane)
        feed = AcquisitionFeed.groups(
            self._db, "test", self._url, test_lane, annotator,
            force_refresh=True
        )
        assert mock.called_with is not None

        # So again, we get a page-type feed filed as a groups-type
        # feed, containing two entries with no collection links.
        eq_(CachedFeed.GROUPS_TYPE, cached.type)
        parsed = feedparser.parse(feed)
        e1, e2 = parsed['entries']
        assert all('links' not in entry for entry in [e1, e2])

    def test_search_feed(self):
        """Test the ability to create a paginated feed of works for a given
        search query.
        """
        fantasy_lane = self.fantasy
        work1 = self._work(genre=Epic_Fantasy, with_open_access_download=True)
        work2 = self._work(genre=Epic_Fantasy, with_open_access_download=True)
        self.add_to_materialized_view([work1, work2], True)

        pagination = Pagination(size=1)
        search_client = DummyExternalSearchIndex()
        search_client.bulk_update([work1, work2])

        def make_page(pagination):
            return AcquisitionFeed.search(
                self._db, "test", self._url, fantasy_lane, search_client,
                "fantasy",
                pagination=pagination,
                annotator=TestAnnotator,
            )
        feed = make_page(pagination)
        parsed = feedparser.parse(feed)
        eq_(work1.title, parsed['entries'][0]['title'])

        # Make sure the links are in place.
        [start] = self.links(parsed, 'start')
        eq_(TestAnnotator.groups_url(None), start['href'])
        eq_(TestAnnotator.top_level_title(), start['title'])

        [next_link] = self.links(parsed, 'next')
        eq_(TestAnnotator.search_url(fantasy_lane, "test", pagination.next_page), next_link['href'])

        # This was the first page, so no previous link.
        eq_([], self.links(parsed, 'previous'))

        # Make sure there's an "up" link to the lane that was searched
        [up_link] = self.links(parsed, 'up')
        uplink_url = TestAnnotator.lane_url(fantasy_lane)
        eq_(uplink_url, up_link['href'])
        eq_(fantasy_lane.display_name, up_link['title'])

        # Now get the second page and make sure it has a 'previous' link.
        feed = make_page(pagination.next_page)
        parsed = feedparser.parse(feed)
        [previous] = self.links(parsed, 'previous')
        eq_(TestAnnotator.search_url(fantasy_lane, "test", pagination), previous['href'])
        eq_(work2.title, parsed['entries'][0]['title'])

        # The feed has breadcrumb links
        parentage = list(fantasy_lane.parentage)
        root = ET.fromstring(feed)
        breadcrumbs = root.find("{%s}breadcrumbs" % AtomFeed.SIMPLIFIED_NS)
        links = breadcrumbs.getchildren()
        eq_(len(parentage) + 2, len(links))
        eq_(TestAnnotator.top_level_title(), links[0].get("title"))
        eq_(TestAnnotator.default_lane_url(), links[0].get("href"))
        for i, lane in enumerate(parentage):
            eq_(lane.display_name, links[i+1].get("title"))
            eq_(TestAnnotator.lane_url(lane), links[i+1].get("href"))
        eq_(fantasy_lane.display_name, links[-1].get("title"))
        eq_(TestAnnotator.lane_url(fantasy_lane), links[-1].get("href"))

    def test_cache(self):
        work1 = self._work(title="The Original Title",
                           genre=Epic_Fantasy, with_open_access_download=True)
        fantasy_lane = self.fantasy
        self.add_to_materialized_view([work1], True)

        def make_page():
            return AcquisitionFeed.page(
                self._db, "test", self._url, fantasy_lane, TestAnnotator,
                pagination=Pagination.default()
            )

        af = AcquisitionFeed
        policy = ConfigurationSetting.sitewide(
            self._db, af.NONGROUPED_MAX_AGE_POLICY)
        policy.value = "10"

        feed1 = make_page()
        assert work1.title in feed1
        cached = get_one(self._db, CachedFeed, lane=fantasy_lane)
        old_timestamp = cached.timestamp

        work2 = self._work(
            title="A Brand New Title",
            genre=Epic_Fantasy, with_open_access_download=True
        )
        self.add_to_materialized_view([work2], True)

        # The new work does not show up in the feed because
        # we get the old cached version.
        feed2 = make_page()
        assert work2.title not in feed2
        assert cached.timestamp == old_timestamp

        # Change the policy to disable caching, and we get
        # a brand new page with the new work.
        policy.value = "0"

        feed3 = make_page()
        assert cached.timestamp > old_timestamp
        assert work2.title in feed3


class TestAcquisitionFeed(DatabaseTest):

    def test_add_entrypoint_links(self):
        """Verify that add_entrypoint_links calls _entrypoint_link
        on every EntryPoint passed in.
        """
        m = AcquisitionFeed.add_entrypoint_links

        old_entrypoint_link = AcquisitionFeed._entrypoint_link
        class Mock(object):
            attrs = dict(href="the response")

            def __init__(self):
                self.calls = []

            def __call__(self, *args):
                self.calls.append(args)
                return self.attrs

        mock = Mock()
        old_entrypoint_link = AcquisitionFeed._entrypoint_link
        AcquisitionFeed._entrypoint_link = mock

        xml = etree.fromstring("<feed/>")
        feed = OPDSFeed("title", "url")
        feed.feed = xml
        entrypoints = [AudiobooksEntryPoint, EbooksEntryPoint]
        url_generator = object()
        AcquisitionFeed.add_entrypoint_links(
            feed, url_generator, entrypoints, EbooksEntryPoint,
            "Some entry points"
        )

        # Two different calls were made to the mock method.
        c1, c2 = mock.calls

        # The first entry point is not selected.
        eq_(c1,
            (url_generator, AudiobooksEntryPoint, EbooksEntryPoint, True, "Some entry points")
        )
        # The second one is selected.
        eq_(c2,
            (url_generator, EbooksEntryPoint, EbooksEntryPoint, False, "Some entry points")
        )

        # Two identical <link> tags were added to the <feed> tag, one
        # for each call to the mock method.
        l1, l2 = list(xml.iterchildren())
        for l in l1, l2:
            eq_("link", l.tag)
            eq_(mock.attrs, l.attrib)
        AcquisitionFeed._entrypoint_link = old_entrypoint_link

        # If there is only one facet in the facet group, no links are
        # added.
        xml = etree.fromstring("<feed/>")
        feed.feed = xml
        mock.calls = []
        entrypoints = [EbooksEntryPoint]
        AcquisitionFeed.add_entrypoint_links(
            feed, url_generator, entrypoints, EbooksEntryPoint,
            "Some entry points"
        )
        eq_([], mock.calls)

    def test_entrypoint_link(self):
        """Test the _entrypoint_link method's ability to create
        attributes for <link> tags.
        """
        m = AcquisitionFeed._entrypoint_link
        def g(entrypoint, is_default):
            """A mock URL generator."""
            return "%s - %s" % (entrypoint.INTERNAL_NAME, is_default)

        # If the entry point is not registered, None is returned.
        eq_(None, m(g, object(), object(), True, "group"))

        # Now make a real set of link attributes.
        l = m(g, AudiobooksEntryPoint, AudiobooksEntryPoint, False, "Grupe")

        # The link is identified as belonging to an entry point-type
        # facet group.
        eq_(l['rel'], AcquisitionFeed.FACET_REL)
        eq_(l['{http://librarysimplified.org/terms/}facetGroupType'],
            FacetConstants.ENTRY_POINT_REL)
        eq_('Grupe', l['{http://opds-spec.org/2010/catalog}facetGroup'])

        # This facet is the active one in the group.
        eq_('true', l['{http://opds-spec.org/2010/catalog}activeFacet'])

        # The URL generator was invoked to create the href.
        eq_(l['href'], g(AudiobooksEntryPoint, False))

        # The facet title identifies it as a way to look at audiobooks.
        eq_(EntryPoint.DISPLAY_TITLES[AudiobooksEntryPoint], l['title'])

        # Now try some variants.

        # Here, the entry point is the default one.
        l = m(g, AudiobooksEntryPoint, AudiobooksEntryPoint, True, "Grupe")

        # This may affect the URL generated for the facet link.
        eq_(l['href'], g(AudiobooksEntryPoint, True))

        # Here, the entry point for which we're generating the link is
        # not the selected one -- EbooksEntryPoint is.
        l = m(g, AudiobooksEntryPoint, EbooksEntryPoint, True, "Grupe")

        # This means the 'activeFacet' attribute is not present.
        assert '{http://opds-spec.org/2010/catalog}activeFacet' not in l

    def test_groups_propagates_facets(self):
        """AcquisitionFeed.groups() might call several different
        methods that each need a facet object.
        """
        class Mock(object):
            """Contains all the mock methods used by this test."""
            def fetch(self, *args, **kwargs):
                self.fetch_called_with = kwargs['facets']
                return None, False

            def groups(self, _db, facets):
                self.groups_called_with = facets
                return []

            def page(self, *args, **kwargs):
                self.page_called_with = facets
                return []

        mock = Mock()
        old_cachedfeed_fetch = CachedFeed.fetch
        CachedFeed.fetch = mock.fetch

        lane = self._lane()
        sublane = self._lane(parent=lane)
        lane.groups = mock.groups

        old_acquisitionfeed_page = AcquisitionFeed.page
        AcquisitionFeed.page = mock.page

        # Here's the MacGuffin -- watch it!
        facets = object()

        AcquisitionFeed.groups(
            self._db, "title", "url", lane, TestAnnotator, facets=facets
        )
        # We called CachedFeed.fetch with the given facets object.
        eq_(facets, mock.fetch_called_with)

        # That didn't return anything usable, so we passed the
        # facets into lane.groups().
        eq_(facets, mock.groups_called_with)

        # That didn't return anything either, so as a last ditch
        # effort we passed the facets into AcquisitionFeed.page().
        eq_(facets, mock.page_called_with)

        # Un-mock the methods that we mocked.
        CachedFeed.fetch = old_cachedfeed_fetch
        AcquisitionFeed.page = old_acquisitionfeed_page

    def test_license_tags_no_loan_or_hold(self):
        edition, pool = self._edition(with_license_pool=True)
        availability, holds, copies = AcquisitionFeed.license_tags(
            pool, None, None
        )
        eq_(dict(status='available'), availability.attrib)
        eq_(dict(total='0'), holds.attrib)
        eq_(dict(total='1', available='1'), copies.attrib)

    def test_license_tags_hold_position(self):
        # When a book is placed on hold, it typically takes a while
        # for the LicensePool to be updated with the new number of
        # holds. This test verifies the normal and exceptional
        # behavior used to generate the opds:holds tag in different
        # scenarios.
        edition, pool = self._edition(with_license_pool=True)
        patron = self._patron()

        # If the patron's hold position is less than the total number
        # of holds+reserves, that total is used as opds:total.
        pool.patrons_in_hold_queue = 3
        hold, is_new = pool.on_hold_to(patron, position=1)

        availability, holds, copies = AcquisitionFeed.license_tags(
            pool, None, hold
        )
        eq_('1', holds.attrib['position'])
        eq_('3', holds.attrib['total'])

        # If the patron's hold position is missing, we assume they
        # are last in the list.
        hold.position = None
        availability, holds, copies = AcquisitionFeed.license_tags(
            pool, None, hold
        )
        eq_('3', holds.attrib['position'])
        eq_('3', holds.attrib['total'])

        # If the patron's current hold position is greater than the
        # total recorded number of holds+reserves, their position will
        # be used as the value of opds:total.
        hold.position = 5
        availability, holds, copies = AcquisitionFeed.license_tags(
            pool, None, hold
        )
        eq_('5', holds.attrib['position'])
        eq_('5', holds.attrib['total'])

        # A patron earlier in the holds queue may see a different
        # total number of holds, but that's fine -- it doesn't matter
        # very much to that person the precise number of people behind
        # them in the queue.
        hold.position = 4
        availability, holds, copies = AcquisitionFeed.license_tags(
            pool, None, hold
        )
        eq_('4', holds.attrib['position'])
        eq_('4', holds.attrib['total'])

        # If the patron's hold position is zero (because the book is
        # reserved to them), we do not represent them as having a hold
        # position (so no opds:position), but they still count towards
        # opds:total in the case where the LicensePool's information
        # is out of date.
        hold.position = 0
        pool.patrons_in_hold_queue = 0
        availability, holds, copies = AcquisitionFeed.license_tags(
            pool, None, hold
        )
        assert 'position' not in holds.attrib
        eq_('1', holds.attrib['total'])

    def test_single_entry(self):

        # Here's a Work with two LicensePools.
        work = self._work(with_open_access_download=True)
        original_pool = work.license_pools[0]
        edition, new_pool = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        work.license_pools.append(new_pool)

        # The presentation edition of the Work is associated with
        # the first LicensePool added to it.
        eq_(work.presentation_edition, original_pool.presentation_edition)

        # This is the edition used when we create an <entry> tag for
        # this Work.
        entry = AcquisitionFeed.single_entry(
            self._db, work, TestAnnotator
        )
        entry = etree.tostring(entry)
        assert original_pool.presentation_edition.title in entry
        assert new_pool.presentation_edition.title not in entry

        # If the edition was issued before 1980, no datetime formatting error
        # is raised.
        work.simple_opds_entry = work.verbose_opds_entry = None
        five_hundred_years = datetime.timedelta(days=(500*365))
        work.presentation_edition.issued = (
            datetime.datetime.utcnow() - five_hundred_years
        )

        entry = AcquisitionFeed.single_entry(self._db, work, TestAnnotator)

        expected = str(work.presentation_edition.issued.date())
        assert expected in etree.tostring(entry)

    def test_entry_cache_adds_missing_drm_namespace(self):

        work = self._work(with_open_access_download=True)

        # This work's OPDS entry was created with a namespace map
        # that did not include the drm: namespace.
        work.simple_opds_entry = "<entry><foo>bar</foo></entry>"
        pool = work.license_pools[0]

        # But now the annotator is set up to insert a tag with that
        # namespace.
        class AddDRMTagAnnotator(TestAnnotator):
            @classmethod
            def annotate_work_entry(
                    cls, work, license_pool, edition, identifier, feed,
                    entry):
                drm_link = OPDSFeed.makeelement("{%s}licensor" % OPDSFeed.DRM_NS)
                entry.extend([drm_link])

        # The entry is retrieved from cache and the appropriate
        # namespace inserted.
        entry = AcquisitionFeed.single_entry(
            self._db, work, AddDRMTagAnnotator
        )
        eq_('<entry xmlns:drm="http://librarysimplified.org/terms/drm"><foo>bar</foo><drm:licensor/></entry>',
            etree.tostring(entry)
        )

    def test_error_when_work_has_no_identifier(self):
        """We cannot create an OPDS entry for a Work that cannot be associated
        with an Identifier.
        """
        work = self._work(title=u"Hello, World!", with_license_pool=True)
        work.license_pools[0].identifier = None
        work.presentation_edition.primary_identifier = None
        entry = AcquisitionFeed.single_entry(
            self._db, work, TestAnnotator
        )
        eq_(entry, None)

    def test_error_when_work_has_no_licensepool(self):
        work = self._work()
        feed = AcquisitionFeed(
            self._db, self._str, self._url, [], annotator=Annotator
        )
        entry = feed.create_entry(work)
        expect = AcquisitionFeed.error_message(
            work.presentation_edition.primary_identifier,
            403,
            "I've heard about this work but have no active licenses for it.",
        )
        eq_(expect, entry)

    def test_error_when_work_has_no_presentation_edition(self):
        """We cannot create an OPDS entry (or even an error message) for a
        Work that is disconnected from any Identifiers.
        """
        work = self._work(title=u"Hello, World!", with_license_pool=True)
        work.license_pools[0].presentation_edition = None
        work.presentation_edition = None
        feed = AcquisitionFeed(
            self._db, self._str, self._url, [], annotator=Annotator
        )
        entry = feed.create_entry(work)
        eq_(None, entry)

    def test_cache_usage(self):
        work = self._work(with_open_access_download=True)
        feed = AcquisitionFeed(
            self._db, self._str, self._url, [], annotator=Annotator
        )

        # Set the Work's cached OPDS entry to something that's clearly wrong.
        tiny_entry = '<feed>cached entry</feed>'
        work.simple_opds_entry = tiny_entry

        # If we pass in use_cache=True, the cached value is used as a basis
        # for the annotated entry.
        entry = feed.create_entry(work, use_cache=True)
        eq_(tiny_entry, work.simple_opds_entry)

        # We know what the final value looks like -- it's the cached entry
        # run through `Annotator.annotate_work_entry`.
        [pool] = work.license_pools
        xml = etree.fromstring(work.simple_opds_entry)
        annotator = Annotator()
        annotator.annotate_work_entry(
            work, pool, pool.presentation_edition, pool.identifier, feed,
            xml
        )
        eq_(etree.tostring(xml), etree.tostring(entry))

        # If we pass in use_cache=False, a new OPDS entry is created
        # from scratch, but the cache is not updated.
        entry = feed.create_entry(work, use_cache=False)
        assert etree.tostring(entry) != tiny_entry
        eq_(tiny_entry, work.simple_opds_entry)

        # If we pass in force_create, a new OPDS entry is created
        # and the cache is updated.
        entry = feed.create_entry(work, force_create=True)
        entry_string = etree.tostring(entry)
        assert entry_string != tiny_entry
        assert work.simple_opds_entry != tiny_entry

        # Again, we got entry_string by running the (new) cached value
        # through `Annotator.annotate_work_entry`.
        full_entry = etree.fromstring(work.simple_opds_entry)
        annotator.annotate_work_entry(
            work, pool, pool.presentation_edition, pool.identifier, feed,
            full_entry
        )
        eq_(entry_string, etree.tostring(full_entry))

    def test_exception_during_entry_creation_is_not_reraised(self):
        # This feed will raise an exception whenever it's asked
        # to create an entry.
        class DoomedFeed(AcquisitionFeed):
            def _create_entry(self, *args, **kwargs):
                raise Exception("I'm doomed!")
        feed = DoomedFeed(
            self._db, self._str, self._url, [], annotator=Annotator
        )
        work = self._work(with_open_access_download=True)

        # But calling create_entry() doesn't raise an exception, it
        # just returns None.
        entry = feed.create_entry(work)
        eq_(entry, None)

    def test_unfilfullable_work(self):
        work = self._work(with_open_access_download=True)
        [pool] = work.license_pools
        entry = AcquisitionFeed.single_entry(
            self._db, work, TestUnfulfillableAnnotator
        )
        expect = AcquisitionFeed.error_message(
            pool.identifier, 403,
            "I know about this work but can offer no way of fulfilling it."
        )
        eq_(expect, entry)

    def test_format_types(self):
        epub_no_drm, ignore = DeliveryMechanism.lookup(
            self._db, Representation.EPUB_MEDIA_TYPE, DeliveryMechanism.NO_DRM)
        epub_adobe_drm, ignore = DeliveryMechanism.lookup(
            self._db, Representation.EPUB_MEDIA_TYPE, DeliveryMechanism.ADOBE_DRM)
        overdrive_streaming_text, ignore = DeliveryMechanism.lookup(
            self._db, DeliveryMechanism.STREAMING_TEXT_CONTENT_TYPE, DeliveryMechanism.OVERDRIVE_DRM)

        eq_([Representation.EPUB_MEDIA_TYPE],
            AcquisitionFeed.format_types(epub_no_drm))
        eq_([DeliveryMechanism.ADOBE_DRM, Representation.EPUB_MEDIA_TYPE],
            AcquisitionFeed.format_types(epub_adobe_drm))
        eq_([OPDSFeed.ENTRY_TYPE, Representation.TEXT_HTML_MEDIA_TYPE + DeliveryMechanism.STREAMING_PROFILE],
            AcquisitionFeed.format_types(overdrive_streaming_text))

    def test_add_breadcrumbs(self):
        _db = self._db

        def getElementChildren(feed):
            f = feed.feed[0]
            children = f.getchildren()
            return children

        class MockFeed(AcquisitionFeed):
            def __init__(self):
                super(MockFeed, self).__init__(
                    _db, "", "", [], annotator=TestAnnotator()
                )
                self.feed = []

        lane = self._lane()
        sublane = self._lane(parent=lane)
        subsublane = self._lane(parent=sublane)
        ep = AudiobooksEntryPoint

        # The top level with no entrypoint
        # Top Level Title >
        feed = MockFeed()
        feed.add_breadcrumbs(lane)
        children = getElementChildren(feed)

        eq_(len(children), 1)
        eq_(children[0].attrib.get("href"), TestAnnotator.default_lane_url())
        eq_(children[0].attrib.get("title"), TestAnnotator.top_level_title())

        # The top level with an entrypoint
        # Top Level Title > Audio
        feed = MockFeed()
        feed.add_breadcrumbs(lane, entrypoint=ep)
        children = getElementChildren(feed)

        eq_(len(children), 2)
        eq_(children[0].attrib.get("href"), TestAnnotator.default_lane_url())
        eq_(children[0].attrib.get("title"), TestAnnotator.top_level_title())
        eq_(children[1].attrib.get("href"), TestAnnotator.default_lane_url() + "?entrypoint=" + ep.INTERNAL_NAME)
        eq_(children[1].attrib.get("title"), ep.INTERNAL_NAME)

        # One lane level down but with no entrypoint
        # Top Level Title > 2001
        feed = MockFeed()
        feed.add_breadcrumbs(sublane)
        children = getElementChildren(feed)

        eq_(len(children), 2)
        eq_(children[0].attrib.get("href"), TestAnnotator.default_lane_url())
        eq_(children[0].attrib.get("title"), TestAnnotator.top_level_title())
        assert(("?entrypoint=" + ep.INTERNAL_NAME) not in children[1].attrib.get("href"))
        eq_(children[1].attrib.get("title"), lane.display_name)

        # One lane level down and with an entrypoint
        # Each sublane will have the entrypoint propagated down to its link
        # Top Level Title > Audio > 2001
        feed = MockFeed()
        feed.add_breadcrumbs(sublane, entrypoint=ep)
        children = getElementChildren(feed)

        eq_(len(children), 3)
        eq_(children[0].attrib.get("href"), TestAnnotator.default_lane_url())
        eq_(children[0].attrib.get("title"), TestAnnotator.top_level_title())
        eq_(children[1].attrib.get("href"), TestAnnotator.default_lane_url() + "?entrypoint=" + ep.INTERNAL_NAME)
        eq_(children[1].attrib.get("title"), ep.INTERNAL_NAME)
        assert(("?entrypoint=" + ep.INTERNAL_NAME) in children[2].attrib.get("href"))
        eq_(children[2].attrib.get("title"), lane.display_name)

        # Two lane levels down but no entrypoint
        # Top Level Title > 2001 > 2002
        feed = MockFeed()
        feed.add_breadcrumbs(subsublane)
        children = getElementChildren(feed)

        eq_(len(children), 3)
        eq_(children[0].attrib.get("href"), TestAnnotator.default_lane_url())
        eq_(children[0].attrib.get("title"), TestAnnotator.top_level_title())
        assert(("?entrypoint=" + ep.INTERNAL_NAME) not in children[1].attrib.get("href"))
        eq_(children[1].attrib.get("title"), lane.display_name)
        assert(("?entrypoint=" + ep.INTERNAL_NAME) not in children[1].attrib.get("href"))
        eq_(children[2].attrib.get("title"), sublane.display_name)

        # Two lane levels down after the entrypoint
        # Each sublane will have the entrypoint propagated down to its link
        # Top Level Title > Audio > 2001 > 2002
        feed = MockFeed()
        feed.add_breadcrumbs(subsublane, entrypoint=ep)
        children = getElementChildren(feed)

        eq_(len(children), 4)
        eq_(children[0].attrib.get("href"), TestAnnotator.default_lane_url())
        eq_(children[0].attrib.get("title"), TestAnnotator.top_level_title())
        eq_(children[1].attrib.get("href"), TestAnnotator.default_lane_url() + "?entrypoint=" + ep.INTERNAL_NAME)
        eq_(children[1].attrib.get("title"), ep.INTERNAL_NAME)
        assert(("?entrypoint=" + ep.INTERNAL_NAME) in children[2].attrib.get("href"))
        eq_(children[2].attrib.get("title"), lane.display_name)
        assert(("?entrypoint=" + ep.INTERNAL_NAME) in children[3].attrib.get("href"))
        eq_(children[3].attrib.get("title"), sublane.display_name)


    def test_add_breadcrumb_links(self):

        class MockFeed(AcquisitionFeed):
            add_link_calls = []
            add_breadcrumbs_call = None
            current_entrypoint = None
            def add_link_to_feed(self, **kwargs):
                self.add_link_calls.append(kwargs)

            def add_breadcrumbs(self, lane, entrypoint):
                self.add_breadcrumbs_call = (lane, entrypoint)

            def show_current_entrypoint(self, entrypoint):
                self.current_entrypoint = entrypoint

        annotator = TestAnnotator
        feed = MockFeed(self._db, "title", "url", [], annotator=annotator)

        lane = self._lane()
        sublane = self._lane(parent=lane)
        ep = AudiobooksEntryPoint
        feed.add_breadcrumb_links(sublane, ep)

        # add_link_to_feed was called twice, to create the 'start' and
        # 'up' links.
        start, up = feed.add_link_calls
        eq_('start', start['rel'])
        eq_(annotator.top_level_title(), start['title'])

        eq_('up', up['rel'])
        eq_(lane.display_name, up['title'])

        # The Lane and EntryPoint were passed into add_breadcrumbs.
        eq_((sublane, ep), feed.add_breadcrumbs_call)

        # The EntryPoint was passed into show_current_entrypoint.
        eq_(ep, feed.current_entrypoint)

    def test_show_current_entrypoint(self):
        """Calling AcquisitionFeed.show_current_entrypoint annotates
        the top-level <feed> tag with information about the currently
        selected entrypoint, if any.
        """
        feed = AcquisitionFeed(self._db, "title", "url", [], annotator=None)
        assert feed.CURRENT_ENTRYPOINT_ATTRIBUTE not in feed.feed.attrib

        # No entry point, no annotation.
        feed.show_current_entrypoint(None)

        ep = AudiobooksEntryPoint
        feed.show_current_entrypoint(ep)
        eq_(ep.URI, feed.feed.attrib[feed.CURRENT_ENTRYPOINT_ATTRIBUTE])


class TestLookupAcquisitionFeed(DatabaseTest):

    def feed(self, annotator=VerboseAnnotator, **kwargs):
        """Helper method to create a LookupAcquisitionFeed."""
        return LookupAcquisitionFeed(
            self._db, u"Feed Title", "http://whatever.io", [],
            annotator=annotator, **kwargs
        )

    def entry(self, identifier, work, annotator=VerboseAnnotator, **kwargs):
        """Helper method to create an entry."""
        feed = self.feed(annotator, **kwargs)
        entry = feed.create_entry((identifier, work))
        if isinstance(entry, OPDSMessage):
            return feed, entry
        if entry:
            entry = etree.tostring(entry)
        return feed, entry

    def test_create_entry_uses_specified_identifier(self):

        # Here's a Work with two LicensePools.
        work = self._work(with_open_access_download=True)
        original_pool = work.license_pools[0]
        edition, new_pool = self._edition(
            with_license_pool=True, with_open_access_download=True
        )
        work.license_pools.append(new_pool)

        # We can generate two different OPDS entries for a single work
        # depending on which identifier we look up.
        ignore, e1 = self.entry(original_pool.identifier, work)
        assert original_pool.identifier.urn in e1
        assert original_pool.presentation_edition.title in e1
        assert new_pool.identifier.urn not in e1
        assert new_pool.presentation_edition.title not in e1

        # Passing in the other identifier gives an OPDS entry with the
        # same bibliographic data (taken from the original pool's
        # presentation edition) but with different identifier
        # information.
        i = new_pool.identifier
        ignore, e2 = self.entry(i, work)
        assert new_pool.identifier.urn in e2
        assert new_pool.presentation_edition.title not in e2
        assert original_pool.presentation_edition.title in e2
        assert original_pool.identifier.urn not in e2

    def test_error_on_mismatched_identifier(self):
        """We get an error if we try to make it look like an Identifier lookup
        retrieved a Work that's not actually associated with that Identifier.
        """
        work = self._work(with_open_access_download=True)

        # Here's an identifier not associated with any LicensePool or
        # Work.
        identifier = self._identifier()

        # It doesn't make sense to make an OPDS feed out of that
        # Identifier and a totally random Work.
        expect_error = 'I tried to generate an OPDS entry for the identifier "%s" using a Work not associated with that identifier.'
        feed, entry = self.entry(identifier, work)
        eq_(
            entry,OPDSMessage(
                identifier.urn, 500, expect_error  % identifier.urn
            )
        )

        # Even if the Identifier does have a Work, if the Works don't
        # match, we get the same error.
        edition, lp = self._edition(with_license_pool=True)
        work2 = lp.calculate_work()
        feed, entry = self.entry(lp.identifier, work)
        eq_(entry,
            OPDSMessage(
                lp.identifier.urn, 500, expect_error % lp.identifier.urn
            )
        )

    def test_error_when_work_has_no_licensepool(self):
        """Under most circumstances, a Work must have at least one
        LicensePool for a lookup to succeed.
        """

        # Here's a work with no LicensePools.
        work = self._work(title=u"Hello, World!", with_license_pool=False)
        identifier = work.presentation_edition.primary_identifier
        feed, entry = self.entry(identifier, work)
        # By default, a work is treated as 'not in the collection' if
        # there is no LicensePool for it.
        isinstance(entry, OPDSMessage)
        eq_(404, entry.status_code)
        eq_("Identifier not found in collection", entry.message)

    def test_unfilfullable_work(self):
        work = self._work(with_open_access_download=True)
        [pool] = work.license_pools
        feed, entry = self.entry(pool.identifier, work,
                                 TestUnfulfillableAnnotator)
        expect = AcquisitionFeed.error_message(
            pool.identifier, 403,
            "I know about this work but can offer no way of fulfilling it."
        )
        eq_(expect, entry)

    def test_create_entry_uses_cache_for_all_licensepools_for_work(self):
        """A Work's cached OPDS entries can be reused by all LicensePools for
        that Work, even LicensePools associated with different
        identifiers.
        """
        class InstrumentableActiveLicensePool(VerboseAnnotator):
            """A mock class that lets us control the output of
            active_license_pool.
            """

            ACTIVE = None

            @classmethod
            def active_licensepool_for(cls, work):
                return cls.ACTIVE
        feed = self.feed(annotator=InstrumentableActiveLicensePool())

        # Here are two completely different LicensePools for the same work.
        work = self._work(with_license_pool=True)
        work.verbose_opds_entry = "<entry>Cached</entry>"
        [pool1] = work.license_pools
        identifier1 = pool1.identifier

        collection2 = self._collection()
        edition2 = self._edition()
        pool2 = self._licensepool(edition=edition2, collection=collection2)
        identifier2 = pool2.identifier
        work.license_pools.append(pool2)

        # Regardless of which LicensePool the annotator thinks is
        # 'active', passing in (identifier, work) will use the cache.
        m = feed.create_entry
        annotator = feed.annotator

        annotator.ACTIVE = pool1
        eq_("Cached", m((pool1.identifier, work)).text)

        annotator.ACTIVE = pool2
        eq_("Cached", m((pool2.identifier, work)).text)

        # If for some reason we pass in an identifier that is not
        # associated with the active license pool, we don't get
        # anything.
        work.license_pools = [pool1]
        result = m((identifier2, work))
        assert isinstance(result, OPDSMessage)
        assert (
            'using a Work not associated with that identifier.'
            in result.message
        )


class TestEntrypointLinkInsertion(DatabaseTest):
    """Verify that the three main types of OPDS feeds -- grouped,
    paginated, and search results -- will all include links to the same
    feed but through a different entry point.
    """

    def setup(self):
        super(TestEntrypointLinkInsertion, self).setup()

        # Mock for AcquisitionFeed.add_entrypoint_links
        class Mock(object):
            def add_entrypoint_links(self, *args):
                self.called_with = args
        self.mock = Mock()

        # A WorkList with no EntryPoints -- should not call the mock method.
        self.no_eps = WorkList()
        self.no_eps.initialize(
            library=self._default_library, display_name="no_eps"
        )

        # A WorkList with two EntryPoints -- may call the mock method
        # depending on circumstances.
        self.entrypoints = [AudiobooksEntryPoint, EbooksEntryPoint]
        self.wl = WorkList()
        # The WorkList must have at least one child, or we won't generate
        # a real groups feed for it.
        self.lane = self._lane()
        self.wl.initialize(library=self._default_library, display_name="wl",
        entrypoints=self.entrypoints, children=[self.lane])

        def works(_db, facets=None, pagination=None):
            """Mock WorkList.works so we don't need any actual works
            to run the test.
            """
            return []
        self.no_eps.works = works
        self.wl.works = works

        self.annotator = TestAnnotator
        self.old_add_entrypoint_links = AcquisitionFeed.add_entrypoint_links
        AcquisitionFeed.add_entrypoint_links = self.mock.add_entrypoint_links

    def teardown(self):
        super(TestEntrypointLinkInsertion, self).teardown()
        AcquisitionFeed.add_entrypoint_links = self.old_add_entrypoint_links

    def test_groups(self):
        """When AcquisitionFeed.groups() generates a grouped
        feed, it will link to different entry points into the feed,
        assuming the WorkList has different entry points.
        """
        def run(wl=None, facets=None):
            """Call groups() and see what add_entrypoint_links
            was called with.
            """
            self.mock.called_with = None
            AcquisitionFeed.groups(
                self._db, "title", "url", wl, self.annotator,
                cache_type=AcquisitionFeed.NO_CACHE, facets=facets,
            )
            return self.mock.called_with

        # This WorkList has no entry points, so the mock method is not
        # even called.
        eq_(None, run(self.no_eps))

        # A WorkList with entry points does cause the mock method
        # to be called.
        facets = FeaturedFacets(
            minimum_featured_quality=self._default_library.minimum_featured_quality,
            entrypoint=EbooksEntryPoint
        )
        feed, make_link, entrypoints, selected = run(self.wl, facets)

        # add_entrypoint_links was passed both possible entry points
        # and the selected entry point.
        eq_(self.wl.entrypoints, entrypoints)
        eq_(selected, EbooksEntryPoint)

        # The make_link function that was passed in calls
        # TestAnnotator.groups_url() when passed an EntryPoint.
        eq_("http://groups/?entrypoint=Book", make_link(EbooksEntryPoint, False))

    def test_page(self):
        """When AcquisitionFeed.page() generates the first page of a paginated
        list, it will link to different entry points into the list,
        assuming the WorkList has different entry points.
        """
        def run(wl=None, facets=None, pagination=None):
            """Call page() and see what add_entrypoint_links
            was called with.
            """
            self.mock.called_with = None
            AcquisitionFeed.page(
                self._db, "title", "url", wl, self.annotator,
                cache_type=AcquisitionFeed.NO_CACHE, facets=facets,
                pagination=pagination
            )
            return self.mock.called_with

        # The WorkList has no entry points, so the mock method is not
        # even called.
        eq_(None, run(self.no_eps))

        # Let's give the WorkList two possible entry points, and choose one.
        facets = Facets.default(self._default_library).navigate(
            entrypoint=EbooksEntryPoint
        )
        feed, make_link, entrypoints, selected = run(self.wl, facets)

        # This time, add_entrypoint_links was called, and passed both
        # possible entry points and the selected entry point.
        eq_(self.wl.entrypoints, entrypoints)
        eq_(selected, EbooksEntryPoint)

        # The make_link function that was passed in calls
        # TestAnnotator.feed_url() when passed an EntryPoint. The
        # Facets object's other facet groups are propagated in this URL.
        first_page_url = "http://wl/?available=all&collection=main&entrypoint=Book&order=author"
        eq_(first_page_url, make_link(EbooksEntryPoint, False))

        # Pagination information is not propagated through entry point links
        # -- you always start at the beginning of the list.
        pagination = Pagination(offset=100)
        feed, make_link, entrypoints, selected = run(
            self.wl, facets, pagination
        )
        eq_(first_page_url, make_link(EbooksEntryPoint, False))

    def test_search(self):
        """When AcquisitionFeed.search() generates the first page of
        search results, it will link to related searches for different
        entry points, assuming the WorkList has different entry points.
        """
        def run(wl=None, facets=None, pagination=None):
            """Call search() and see what add_entrypoint_links
            was called with.
            """
            self.mock.called_with = None
            AcquisitionFeed.search(
                self._db, "title", "url", wl, None, None,
                annotator=self.annotator, facets=facets,
                pagination=pagination
            )
            return self.mock.called_with

        # Mock search() so it never tries to return anything.
        def mock_search(self, *args, **kwargs):
            return []
        self.no_eps.search = mock_search
        self.wl.search = mock_search

        # This WorkList has no entry points, so the mock method is not
        # even called.
        eq_(None, run(self.no_eps))

        # The mock method is called for a WorkList that does have
        # entry points.
        facets = SearchFacets().navigate(entrypoint=EbooksEntryPoint)
        assert isinstance(facets, SearchFacets)
        feed, make_link, entrypoints, selected = run(self.wl, facets)

        # Since the SearchFacets has more than one entry point,
        # the EverythingEntryPoint is prepended to the list of possible
        # entry points.
        eq_(
            [EverythingEntryPoint, AudiobooksEntryPoint, EbooksEntryPoint],
            entrypoints
        )

        # add_entrypoint_links was passed the three possible entry points
        # and the selected entry point.
        eq_(selected, EbooksEntryPoint)

        # The make_link function that was passed in calls
        # TestAnnotator.search_url() when passed an EntryPoint.
        first_page_url = 'http://wl/?entrypoint=Book'
        eq_(first_page_url, make_link(EbooksEntryPoint, False))

        # Pagination information is not propagated through entry point links
        # -- you always start at the beginning of the list.
        pagination = Pagination(offset=100)
        feed, make_link, entrypoints, selected = run(
            self.wl, facets, pagination
        )
        eq_(first_page_url, make_link(EbooksEntryPoint, False))

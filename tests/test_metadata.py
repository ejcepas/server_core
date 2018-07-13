from StringIO import StringIO
from nose.tools import (
    eq_,
    set_trace,
)
import datetime
import pkgutil
import csv
from copy import deepcopy

from classifier import Classifier
from metadata_layer import (
    CSVFormatError,
    CSVMetadataImporter,
    CirculationData,
    ContributorData,
    MeasurementData,
    FormatData,
    LinkData,
    MARCExtractor,
    Metadata,
    IdentifierData,
    ReplacementPolicy,
    SubjectData,
    ContributorData,
)

import os
from model import (
    Contributor,
    CoverageRecord,
    DataSource,
    Edition,
    Identifier,
    Measurement,
    DeliveryMechanism,
    Hyperlink, 
    Representation,
    RightsStatus,
    Subject,
)

from . import (
    DatabaseTest,
    DummyHTTPClient,
    DummyMetadataClient,
)

from s3 import MockS3Uploader
from classifier import NO_VALUE, NO_NUMBER

class TestIdentifierData(object):

    def test_constructor(self):
        data = IdentifierData(Identifier.ISBN, "foo", 0.5)
        eq_(Identifier.ISBN, data.type)
        eq_("foo", data.identifier)
        eq_(0.5, data.weight)

class TestMetadataImporter(DatabaseTest):

    def test_parse(self):
        base_path = os.path.split(__file__)[0]
        path = os.path.join(
            base_path, "files/csv/staff_picks_small.csv")
        reader = csv.DictReader(open(path))
        importer = CSVMetadataImporter(
            DataSource.LIBRARY_STAFF,
        )
        generator = importer.to_metadata(reader)
        m1, m2, m3 = list(generator)

        eq_(u"Horrorst\xf6r", m1.title)
        eq_("Grady Hendrix", m1.contributors[0].display_name)
        eq_("Martin Jensen", m2.contributors[0].display_name)

        # Let's check out the identifiers we found.

        # The first book has an Overdrive ID
        [overdrive] = m1.identifiers
        eq_(Identifier.OVERDRIVE_ID, overdrive.type)
        eq_('504BA8F6-FF4E-4B57-896E-F1A50CFFCA0C', overdrive.identifier)
        eq_(0.75, overdrive.weight)

        # The second book has no ID at all.
        eq_([], m2.identifiers)

        # The third book has both a 3M ID and an Overdrive ID.
        overdrive, threem = sorted(m3.identifiers, key=lambda x: x.identifier)

        eq_(Identifier.OVERDRIVE_ID, overdrive.type)
        eq_('eae60d41-e0b8-4f9d-90b5-cbc43d433c2f', overdrive.identifier)
        eq_(0.75, overdrive.weight)

        eq_(Identifier.THREEM_ID, threem.type)
        eq_('eswhyz9', threem.identifier)
        eq_(0.75, threem.weight)

        # Now let's check out subjects.
        eq_(
            [
                ('schema:typicalAgeRange', u'Adult', 100),
                ('tag', u'Character Driven', 100),
                ('tag', u'Historical', 100), 
                ('tag', u'Nail-Biters', 100),
                ('tag', u'Setting Driven', 100)
            ],
            [(x.type, x.identifier, x.weight) 
             for x in sorted(m2.subjects, key=lambda x: x.identifier)]
        )

    def test_classifications_from_another_source_not_updated(self):

        # Set up an edition whose primary identifier has two
        # classifications.
        source1 = DataSource.lookup(self._db, DataSource.AXIS_360)
        source2 = DataSource.lookup(self._db, DataSource.METADATA_WRANGLER)
        edition = self._edition()
        identifier = edition.primary_identifier
        c1 = identifier.classify(source1, Subject.TAG, "i will persist")
        c2 = identifier.classify(source2, Subject.TAG, "i will perish")

        # Now we get some new metadata from source #2.
        subjects = [SubjectData(type=Subject.TAG, identifier="i will conquer")]
        metadata = Metadata(subjects=subjects, data_source=source2)
        replace = ReplacementPolicy(subjects=True)
        metadata.apply(edition, None, replace=replace)

        # The old classification from source #2 has been destroyed.
        # The old classification from source #1 is still there.
        eq_(
            ['i will conquer', 'i will persist'],
            sorted([x.subject.identifier for x in identifier.classifications])
        )

    def test_links(self):
        edition = self._edition()
        l1 = LinkData(rel=Hyperlink.IMAGE, href="http://example.com/")
        l2 = LinkData(rel=Hyperlink.DESCRIPTION, content="foo")
        metadata = Metadata(links=[l1, l2], 
                            data_source=edition.data_source)
        metadata.apply(edition, None)
        [image, description] = sorted(
            edition.primary_identifier.links, key=lambda x:x.rel
        )
        eq_(Hyperlink.IMAGE, image.rel)
        eq_("http://example.com/", image.resource.url)

        eq_(Hyperlink.DESCRIPTION, description.rel)
        eq_("foo", description.resource.representation.content)

    def test_image_with_original_and_rights(self):
        edition = self._edition()
        data_source = DataSource.lookup(self._db, DataSource.LIBRARY_STAFF)
        original = LinkData(rel=Hyperlink.IMAGE,
                            href="http://example.com/",
                            media_type=Representation.PNG_MEDIA_TYPE,
                            rights_uri=RightsStatus.PUBLIC_DOMAIN_USA,
                            rights_explanation="This image is from 1922",
                            )
        image_data = '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x03\x00\x00\x00%\xdbV\xca\x00\x00\x00\x06PLTE\xffM\x00\x01\x01\x01\x8e\x1e\xe5\x1b\x00\x00\x00\x01tRNS\xcc\xd24V\xfd\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        derivative = LinkData(rel=Hyperlink.IMAGE,
                              href="generic uri",
                              content=image_data,
                              media_type=Representation.PNG_MEDIA_TYPE,
                              rights_uri=RightsStatus.PUBLIC_DOMAIN_USA,
                              rights_explanation="This image is from 1922",
                              original=original,
                              transformation_settings=dict(position='top')
                              )

        metadata = Metadata(links=[derivative], data_source=data_source)
        metadata.apply(edition, None)
        [image] = edition.primary_identifier.links
        eq_(Hyperlink.IMAGE, image.rel)
        eq_("generic uri", image.resource.url)
        eq_(image_data, image.resource.representation.content)
        eq_(RightsStatus.PUBLIC_DOMAIN_USA, image.resource.rights_status.uri)
        eq_("This image is from 1922", image.resource.rights_explanation)

        eq_([], image.resource.transformations)
        transformation = image.resource.derived_through
        eq_(image.resource, transformation.derivative)

        eq_("http://example.com/", transformation.original.url)
        eq_(RightsStatus.PUBLIC_DOMAIN_USA, transformation.original.rights_status.uri)
        eq_("This image is from 1922", transformation.original.rights_explanation)
        eq_("top", transformation.settings.get("position"))

    def test_image_and_thumbnail(self):
        edition = self._edition()
        l2 = LinkData(
            rel=Hyperlink.THUMBNAIL_IMAGE, href="http://thumbnail.com/",
            media_type=Representation.JPEG_MEDIA_TYPE,
        )
        l1 = LinkData(
            rel=Hyperlink.IMAGE, href="http://example.com/", thumbnail=l2,
            media_type=Representation.JPEG_MEDIA_TYPE,
        )

        # Even though we're only passing in the primary image link...
        metadata = Metadata(links=[l1], 
                            data_source=edition.data_source)
        metadata.apply(edition, None)

        # ...a Hyperlink is also created for the thumbnail.
        [image, thumbnail] = sorted(
            edition.primary_identifier.links, key=lambda x:x.rel
        )
        eq_(Hyperlink.IMAGE, image.rel)
        eq_([thumbnail.resource.representation],
            image.resource.representation.thumbnails
        )

    def test_thumbnail_isnt_a_thumbnail(self):
        edition = self._edition()
        not_a_thumbnail = LinkData(
            rel=Hyperlink.DESCRIPTION, content="A great book",
            media_type=Representation.TEXT_PLAIN,
        )
        image = LinkData(
            rel=Hyperlink.IMAGE, href="http://example.com/", 
            thumbnail=not_a_thumbnail,
            media_type=Representation.JPEG_MEDIA_TYPE,
        )

        metadata = Metadata(links=[image], 
                            data_source=edition.data_source)
        metadata.apply(edition, None)

        # Only one Hyperlink was created for the image, because
        # the alleged 'thumbnail' wasn't actually a thumbnail.
        [image_obj] = edition.primary_identifier.links
        eq_(Hyperlink.IMAGE, image_obj.rel)
        eq_([], image_obj.resource.representation.thumbnails)

        # If we pass in the 'thumbnail' separately, a Hyperlink is
        # created for it, but it's still not a thumbnail of anything.
        metadata = Metadata(links=[image, not_a_thumbnail], 
                            data_source=edition.data_source)
        metadata.apply(edition, None)
        [image, description] = sorted(
            edition.primary_identifier.links, key=lambda x:x.rel
        )
        eq_(Hyperlink.DESCRIPTION, description.rel)
        eq_("A great book", description.resource.representation.content)
        eq_([], image.resource.representation.thumbnails)
        eq_(None, description.resource.representation.thumbnail_of)

    def test_image_and_thumbnail_are_the_same(self):
        edition = self._edition()
        url = "http://tinyimage.com/image.jpg"
        l2 = LinkData(
            rel=Hyperlink.THUMBNAIL_IMAGE, href=url,
        )
        l1 = LinkData(
            rel=Hyperlink.IMAGE, href=url, thumbnail=l2,
        )
        metadata = Metadata(links=[l1, l2], 
                            data_source=edition.data_source)
        metadata.apply(edition, None)
        [image, thumbnail] = sorted(
            edition.primary_identifier.links, key=lambda x:x.rel
        )

        # The image and its thumbnail point to the same resource.
        eq_(image.resource, thumbnail.resource)

        eq_(Hyperlink.IMAGE, image.rel)
        eq_(Hyperlink.THUMBNAIL_IMAGE, thumbnail.rel)

        # The thumbnail is marked as a thumbnail of the main image.
        eq_([thumbnail.resource.representation],
            image.resource.representation.thumbnails
        )
        eq_(url, edition.cover_full_url)
        eq_(url, edition.cover_thumbnail_url)

    def test_image_becomes_representation_but_thumbnail_does_not(self):
        edition = self._edition()

        # The thumbnail link has no media type, and none can be
        # derived from the URL.
        l2 = LinkData(
            rel=Hyperlink.THUMBNAIL_IMAGE, href="http://tinyimage.com/",
        )

        # The full-sized image link does not have this problem.
        l1 = LinkData(
            rel=Hyperlink.IMAGE, href="http://largeimage.com/", thumbnail=l2,
            media_type=Representation.JPEG_MEDIA_TYPE,
        )
        metadata = Metadata(links=[l1], 
                            data_source=edition.data_source)
        metadata.apply(edition, None)

        # Both LinkData objects have been imported as Hyperlinks.
        [image, thumbnail] = sorted(
            edition.primary_identifier.links, key=lambda x:x.rel
        )

        # However, since no Representation was created for the thumbnail,
        # the relationship between the main image and the thumbnail could
        # not be imported.
        eq_(None, thumbnail.resource.representation)
        eq_([], image.resource.representation.thumbnails)

        # The edition ends up with a full-sized image but no
        # thumbnail. This could potentially be improved, since we know
        # the two Resources are associated with the same Identifier.
        # But we lose track of the fact that the two Resources are
        # _the same image_ at different resolutions.
        eq_("http://largeimage.com/", edition.cover_full_url)
        eq_(None, edition.cover_thumbnail_url)

    def sample_cover_path(self, name):
        base_path = os.path.split(__file__)[0]
        resource_path = os.path.join(base_path, "files", "covers")
        sample_cover_path = os.path.join(resource_path, name)
        return sample_cover_path


    def test_image_scale_and_mirror(self):
        # Make sure that open access material links are translated to our S3 buckets, and that 
        # commercial material links are left as is.
        # Note: mirroring links is now also CirculationData's job.  So the unit tests 
        # that test for that have been changed to call to mirror cover images.
        # However, updated tests passing does not guarantee that all code now 
        # correctly calls on CirculationData, too.  This is a risk.

        mirror = MockS3Uploader()
        edition, pool = self._edition(with_license_pool=True)
        content = open(self.sample_cover_path("test-book-cover.png")).read()
        l1 = LinkData(
            rel=Hyperlink.IMAGE, href="http://example.com/",
            media_type=Representation.JPEG_MEDIA_TYPE,
            content=content
        )
        thumbnail_content = open(self.sample_cover_path("tiny-image-cover.png")).read()
        l2 = LinkData(
            rel=Hyperlink.THUMBNAIL_IMAGE, href="http://example.com/thumb.jpg",
            media_type=Representation.JPEG_MEDIA_TYPE,
            content=content
        )

        # When we call metadata.apply, all image links will be scaled and
        # 'mirrored'.
        policy = ReplacementPolicy(mirror=mirror)
        metadata = Metadata(links=[l1, l2], data_source=edition.data_source)
        metadata.apply(edition, pool.collection, replace=policy)

        # Two Representations were 'mirrored'.
        image, thumbnail = mirror.uploaded

        # The image...
        [image_link] = image.resource.links
        eq_(Hyperlink.IMAGE, image_link.rel)

        # And its thumbnail.
        eq_(image, thumbnail.thumbnail_of)

        # The original image is too big to be a thumbnail.
        eq_(600, image.image_height)
        eq_(400, image.image_width)

        # The thumbnail is the right height.
        eq_(Edition.MAX_THUMBNAIL_HEIGHT, thumbnail.image_height)
        eq_(Edition.MAX_THUMBNAIL_WIDTH, thumbnail.image_width)

        # The thumbnail is newly generated from the full-size
        # image--the thumbnail that came in from the OPDS feed was
        # ignored.
        assert thumbnail.url != l2.href
        assert thumbnail.content != l2.content

        # Both images have been 'mirrored' to Amazon S3.
        assert image.mirror_url.startswith('https://s3.amazonaws.com/test.cover.bucket/')
        assert image.mirror_url.endswith('cover.jpg')

        # The thumbnail image has been converted to PNG.
        assert thumbnail.mirror_url.startswith('https://s3.amazonaws.com/test.cover.bucket/scaled/300/')
        assert thumbnail.mirror_url.endswith('cover.png')

    def test_mirror_thumbnail_only(self):
        # Make sure a thumbnail image is mirrored when there's no cover image.
        mirror = MockS3Uploader()
        edition, pool = self._edition(with_license_pool=True)
        thumbnail_content = open(self.sample_cover_path("tiny-image-cover.png")).read()
        l = LinkData(
            rel=Hyperlink.THUMBNAIL_IMAGE, href="http://example.com/thumb.png",
            media_type=Representation.PNG_MEDIA_TYPE,
            content=thumbnail_content
        )

        policy = ReplacementPolicy(mirror=mirror)
        metadata = Metadata(links=[l], data_source=edition.data_source)
        metadata.apply(edition, pool.collection, replace=policy)

        # One Representation was 'mirrored'.
        [thumbnail] = mirror.uploaded

        # The image has been 'mirrored' to Amazon S3.
        assert thumbnail.mirror_url.startswith('https://s3.amazonaws.com/test.cover.bucket/')
        assert thumbnail.mirror_url.endswith('thumb.png')

    def test_mirror_open_access_link_fetch_failure(self):
        edition, pool = self._edition(with_license_pool=True)

        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        m = Metadata(data_source=data_source)

        mirror = MockS3Uploader()
        h = DummyHTTPClient()

        policy = ReplacementPolicy(mirror=mirror, http_get=h.do_get)

        link = LinkData(
            rel=Hyperlink.IMAGE,
            media_type=Representation.JPEG_MEDIA_TYPE,
            href="http://example.com/",
        )

        link_obj, ignore = edition.primary_identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
            media_type=link.media_type, content=link.content
        )
        h.queue_response(403)
        
        m.mirror_link(edition, data_source, link, link_obj, policy)

        representation = link_obj.resource.representation

        # Fetch failed, so we should have a fetch exception but no mirror url.
        assert representation.fetch_exception != None
        eq_(None, representation.mirror_exception)
        eq_(None, representation.mirror_url)
        eq_(link.href, representation.url)
        assert representation.fetched_at != None
        eq_(None, representation.mirrored_at)

        # the edition's identifier-associated license pool should not be 
        # suppressed just because fetch failed on getting image.
        eq_(False, pool.suppressed)

        # the license pool only gets its license_exception column filled in
        # if fetch failed on getting an Hyperlink.OPEN_ACCESS_DOWNLOAD-type epub.
        eq_(None, pool.license_exception)

    def test_mirror_404_error(self):
        mirror = MockS3Uploader()
        h = DummyHTTPClient()
        h.queue_response(404)
        policy = ReplacementPolicy(mirror=mirror, http_get=h.do_get)

        edition, pool = self._edition(with_license_pool=True)

        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)

        link = LinkData(
            rel=Hyperlink.IMAGE,
            media_type=Representation.JPEG_MEDIA_TYPE,
            href="http://example.com/",
        )

        link_obj, ignore = edition.primary_identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
            media_type=link.media_type, content=link.content
        )

        m = Metadata(data_source=data_source)
        
        m.mirror_link(edition, data_source, link, link_obj, policy)

        # Since we got a 404 error, the cover image was not mirrored.
        eq_(404, link_obj.resource.representation.status_code)
        eq_(None, link_obj.resource.representation.mirror_url)
        eq_([], mirror.uploaded)

    def test_mirror_open_access_link_mirror_failure(self):
        edition, pool = self._edition(with_license_pool=True)

        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        m = Metadata(data_source=data_source)

        mirror = MockS3Uploader(fail=True)
        h = DummyHTTPClient()

        policy = ReplacementPolicy(mirror=mirror, http_get=h.do_get)

        content = open(self.sample_cover_path("test-book-cover.png")).read()
        link = LinkData(
            rel=Hyperlink.IMAGE,
            media_type=Representation.JPEG_MEDIA_TYPE,
            href="http://example.com/",
            content=content
        )

        link_obj, ignore = edition.primary_identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
            media_type=link.media_type, content=link.content
        )

        h.queue_response(200, media_type=Representation.JPEG_MEDIA_TYPE)
        
        m.mirror_link(edition, data_source, link, link_obj, policy)

        representation = link_obj.resource.representation

        # The representation was fetched successfully.
        eq_(None, representation.fetch_exception)
        assert representation.fetched_at != None

        # But mirroring failed.
        assert representation.mirror_exception != None
        eq_(None, representation.mirrored_at)
        eq_(link.media_type, representation.media_type)
        eq_(link.href, representation.url)

        # The mirror url is not set.
        eq_(None, representation.mirror_url)

        # Book content is still there since it wasn't mirrored.
        assert representation.content != None

        # the edition's identifier-associated license pool should not be 
        # suppressed just because fetch failed on getting image.
        eq_(False, pool.suppressed)

        # the license pool only gets its license_exception column filled in
        # if fetch failed on getting an Hyperlink.OPEN_ACCESS_DOWNLOAD-type epub.
        eq_(None, pool.license_exception)

    def test_mirror_link_bad_media_type(self):
        edition, pool = self._edition(with_license_pool=True)

        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        m = Metadata(data_source=data_source)

        mirror = MockS3Uploader()
        h = DummyHTTPClient()

        policy = ReplacementPolicy(mirror=mirror, http_get=h.do_get)

        content = open(self.sample_cover_path("test-book-cover.png")).read()

        # We thought this link was for an image file.
        link = LinkData(
            rel=Hyperlink.IMAGE,
            media_type=Representation.JPEG_MEDIA_TYPE,
            href="http://example.com/",
            content=content
        )
        link_obj, ignore = edition.primary_identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
        )

        # The remote server told us a generic media type.
        h.queue_response(200, media_type=Representation.OCTET_STREAM_MEDIA_TYPE, content=content)
        
        m.mirror_link(edition, data_source, link, link_obj, policy)
        representation = link_obj.resource.representation

        # The representation was fetched and mirrored successfully.
        # We assumed the original image media type was correct.
        eq_(None, representation.fetch_exception)
        assert representation.fetched_at != None
        eq_(None, representation.mirror_exception)
        assert representation.mirrored_at != None
        eq_(Representation.JPEG_MEDIA_TYPE, representation.media_type)
        eq_(link.href, representation.url)
        assert "Gutenberg" in representation.mirror_url
        assert representation.mirror_url.endswith("%s/cover.jpg" % edition.primary_identifier.identifier)

        # We don't know the media type for this link, but it has a file extension.
        link = LinkData(
            rel=Hyperlink.IMAGE,
            href="http://example.com/image.png",
            content=content
        )
        link_obj, ignore = edition.primary_identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
        )
        h.queue_response(200, media_type=Representation.OCTET_STREAM_MEDIA_TYPE, content=content)
        m.mirror_link(edition, data_source, link, link_obj, policy)
        representation = link_obj.resource.representation

        # The representation is still fetched and mirrored successfully.
        # We used the media type from the file extension.
        eq_(None, representation.fetch_exception)
        assert representation.fetched_at != None
        eq_(None, representation.mirror_exception)
        assert representation.mirrored_at != None
        eq_(Representation.PNG_MEDIA_TYPE, representation.media_type)
        eq_(link.href, representation.url)
        assert "Gutenberg" in representation.mirror_url
        assert representation.mirror_url.endswith("%s/image.png" % edition.primary_identifier.identifier)

        # We don't know the media type of this link, and there's no extension.
        link = LinkData(
            rel=Hyperlink.IMAGE,
            href="http://example.com/unknown",
            content=content
        )
        link_obj, ignore = edition.primary_identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
        )
        h.queue_response(200, media_type=Representation.OCTET_STREAM_MEDIA_TYPE, content=content)
        m.mirror_link(edition, data_source, link, link_obj, policy)
        representation = link_obj.resource.representation

        # The representation is fetched, but we don't try to mirror it
        # since it doesn't have a mirrorable media type.
        eq_(None, representation.fetch_exception)
        assert representation.fetched_at != None
        eq_(None, representation.mirror_exception)
        eq_(None, representation.mirrored_at)
        eq_(Representation.OCTET_STREAM_MEDIA_TYPE, representation.media_type)
        eq_(link.href, representation.url)
        eq_(None, representation.mirror_url)

    def test_non_open_access_book_not_mirrored(self):        
        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        m = Metadata(data_source=data_source)

        mirror = MockS3Uploader(fail=True)
        h = DummyHTTPClient()

        policy = ReplacementPolicy(mirror=mirror, http_get=h.do_get)

        content = "foo"
        link = LinkData(
            rel=Hyperlink.OPEN_ACCESS_DOWNLOAD,
            media_type=Representation.EPUB_MEDIA_TYPE,
            href="http://example.com/",
            content=content,
            rights_uri=RightsStatus.IN_COPYRIGHT
        )

        identifier = self._identifier()
        link_obj, is_new = identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
            media_type=link.media_type, content=link.content,
        )

        # The Hyperlink object makes it look like an open-access book,
        # but the context we have from the OPDS feed says that it's
        # not.
        m.mirror_link(None, data_source, link, link_obj, policy)

        # No HTTP requests were made.
        eq_([], h.requests)
        
        # Nothing was uploaded.
        eq_([], mirror.uploaded)
        
    def test_mirror_with_content_modifier(self):
        edition, pool = self._edition(with_license_pool=True)

        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        m = Metadata(data_source=data_source)

        mirror = MockS3Uploader()
        def dummy_content_modifier(representation):
            representation.content = "Replaced Content"
        h = DummyHTTPClient()

        policy = ReplacementPolicy(mirror=mirror, content_modifier=dummy_content_modifier, http_get=h.do_get)

        link = LinkData(
            rel=Hyperlink.OPEN_ACCESS_DOWNLOAD,
            media_type=Representation.EPUB_MEDIA_TYPE,
            href="http://example.com/test.epub",
            content="I'm an epub",
        )

        link_obj, ignore = edition.primary_identifier.add_link(
            rel=link.rel, href=link.href, data_source=data_source,
            media_type=link.media_type, content=link.content
        )

        h.queue_response(200, media_type=Representation.EPUB_MEDIA_TYPE)
        
        m.mirror_link(edition, data_source, link, link_obj, policy)

        representation = link_obj.resource.representation

        # The representation was fetched successfully.
        eq_(None, representation.fetch_exception)
        assert representation.fetched_at != None

        # The mirror url is set.
        assert "Gutenberg" in representation.mirror_url
        assert representation.mirror_url.endswith("%s/%s.epub" % (edition.primary_identifier.identifier, edition.title))

        # Content isn't there since it was mirrored.
        eq_(None, representation.content)

        # The representation was mirrored, with the modified content.
        eq_([representation], mirror.uploaded)
        eq_(["Replaced Content"], mirror.content)

    def test_measurements(self):
        edition = self._edition()
        measurement = MeasurementData(quantity_measured=Measurement.POPULARITY,
                                      value=100)
        metadata = Metadata(measurements=[measurement],
                            data_source=edition.data_source)
        metadata.apply(edition, None)
        [m] = edition.primary_identifier.measurements
        eq_(Measurement.POPULARITY, m.quantity_measured)
        eq_(100, m.value)


    def test_coverage_record(self):
        edition, pool = self._edition(with_license_pool=True)
        data_source = edition.data_source

        # No preexisting coverage record
        coverage = CoverageRecord.lookup(edition, data_source)
        eq_(coverage, None)
        
        last_update = datetime.datetime(2015, 1, 1)

        m = Metadata(data_source=data_source,
                     title=u"New title", data_source_last_updated=last_update)
        m.apply(edition, None)
        
        coverage = CoverageRecord.lookup(edition, data_source)
        eq_(last_update, coverage.timestamp)
        eq_(u"New title", edition.title)

        older_last_update = datetime.datetime(2014, 1, 1)
        m = Metadata(data_source=data_source,
                     title=u"Another new title", 
                     data_source_last_updated=older_last_update
        )
        m.apply(edition, None)
        eq_(u"New title", edition.title)

        coverage = CoverageRecord.lookup(edition, data_source)
        eq_(last_update, coverage.timestamp)

        m.apply(edition, None, force=True)
        eq_(u"Another new title", edition.title)
        coverage = CoverageRecord.lookup(edition, data_source)
        eq_(older_last_update, coverage.timestamp)



class TestContributorData(DatabaseTest):
    def test_from_contribution(self):
        # Makes sure ContributorData.from_contribution copies all the fields over.
        
        # make author with that name, add author to list and pass to edition
        contributors = ["PrimaryAuthor"]
        edition, pool = self._edition(with_license_pool=True, authors=contributors)
        
        contribution = edition.contributions[0]
        contributor = contribution.contributor
        contributor.lc = "1234567"
        contributor.viaf = "ABC123"
        contributor.aliases = ["Primo"]
        contributor.display_name = "Test Author For The Win"
        contributor.family_name = "TestAuttie"
        contributor.wikipedia_name = "TestWikiAuth"
        contributor.biography = "He was born on Main Street."

        contributor_data = ContributorData.from_contribution(contribution)

        # make sure contributor fields are still what I expect
        eq_(contributor_data.lc, contributor.lc)
        eq_(contributor_data.viaf, contributor.viaf)
        eq_(contributor_data.aliases, contributor.aliases)
        eq_(contributor_data.display_name, contributor.display_name)
        eq_(contributor_data.family_name, contributor.family_name)
        eq_(contributor_data.wikipedia_name, contributor.wikipedia_name)
        eq_(contributor_data.biography, contributor.biography)


    def test_apply(self):
        # Makes sure ContributorData.apply copies all the fields over when there's changes to be made.


        contributor_old, made_new = self._contributor(sort_name="Doe, John", viaf="viaf12345")

        kwargs = dict()
        kwargs[Contributor.BIRTH_DATE] = '2001-01-01'

        contributor_data = ContributorData(
            sort_name = "Doerr, John",
            lc = "1234567", 
            viaf = "ABC123", 
            aliases = ["Primo"], 
            display_name = "Test Author For The Win", 
            family_name = "TestAuttie", 
            wikipedia_name = "TestWikiAuth", 
            biography = "He was born on Main Street.", 
            extra = kwargs, 
        )

        contributor_new, changed = contributor_data.apply(contributor_old)

        eq_(changed, True)
        eq_(contributor_new.sort_name, u"Doerr, John")
        eq_(contributor_new.lc, u"1234567")
        eq_(contributor_new.viaf, u"ABC123")
        eq_(contributor_new.aliases, [u"Primo"])
        eq_(contributor_new.display_name, u"Test Author For The Win")
        eq_(contributor_new.family_name, u"TestAuttie")
        eq_(contributor_new.wikipedia_name, u"TestWikiAuth")
        eq_(contributor_new.biography, u"He was born on Main Street.")

        eq_(contributor_new.extra[Contributor.BIRTH_DATE], u"2001-01-01")
        #eq_(contributor_new.contributions, u"Audio")
        #eq_(contributor_new.work_contributions, u"Audio")

        contributor_new, changed = contributor_data.apply(contributor_new)
        eq_(changed, False)

    def test_display_name_to_sort_name_from_existing_contributor(self):
        # If there's an existing contributor with a matching display name,
        # we'll use their sort name.
        existing_contributor, ignore = self._contributor(sort_name="Sort, Name", display_name="John Doe")
        eq_("Sort, Name", ContributorData.display_name_to_sort_name_from_existing_contributor(self._db, "John Doe"))

        # Otherwise, we don't know.
        eq_(None, ContributorData.display_name_to_sort_name_from_existing_contributor(self._db, "Jane Doe"))

    def test_find_sort_name(self):
        metadata_client = DummyMetadataClient()
        metadata_client.lookups["Metadata Client Author"] = "Author, M. C."
        existing_contributor, ignore = self._contributor(sort_name="Author, E.", display_name="Existing Author")
        contributor_data = ContributorData()

        # If there's already a sort name, keep it.
        contributor_data.sort_name = "Sort Name"
        eq_(True, contributor_data.find_sort_name(self._db, [], metadata_client))
        eq_("Sort Name", contributor_data.sort_name)
        
        contributor_data.sort_name = "Sort Name"
        contributor_data.display_name = "Existing Author"
        eq_(True, contributor_data.find_sort_name(self._db, [], metadata_client))
        eq_("Sort Name", contributor_data.sort_name)

        contributor_data.sort_name = "Sort Name"
        contributor_data.display_name = "Metadata Client Author"
        eq_(True, contributor_data.find_sort_name(self._db, [], metadata_client))
        eq_("Sort Name", contributor_data.sort_name)

        # If there's no sort name but there's already an author with the same display name,
        # use that author's sort name.
        contributor_data.sort_name = None
        contributor_data.display_name = "Existing Author"
        eq_(True, contributor_data.find_sort_name(self._db, [], metadata_client))
        eq_("Author, E.", contributor_data.sort_name)

        # If there's no sort name and no existing author, check the metadata wrangler
        # for a sort name.
        contributor_data.sort_name = None
        contributor_data.display_name = "Metadata Client Author"
        eq_(True, contributor_data.find_sort_name(self._db, [], metadata_client))
        eq_("Author, M. C.", contributor_data.sort_name)

        # If there's no sort name, no existing author, and nothing from the metadata
        # wrangler, guess the sort name based on the display name.
        contributor_data.sort_name = None
        contributor_data.display_name = "New Author"
        eq_(True, contributor_data.find_sort_name(self._db, [], metadata_client))
        eq_("Author, New", contributor_data.sort_name)

class TestLinkData(DatabaseTest):

    def test_guess_media_type(self):
        rel = Hyperlink.IMAGE

        # Sometimes we have no idea what media type is at the other
        # end of a link.
        unknown = LinkData(rel, href="http://foo/bar.unknown")
        eq_(None, unknown.guessed_media_type)

        # Sometimes we can guess based on the file extension.
        jpeg = LinkData(rel, href="http://foo/bar.jpeg")
        eq_(Representation.JPEG_MEDIA_TYPE, jpeg.guessed_media_type)

        # An explicitly known media type takes precedence over 
        # something we guess from the file extension.
        png = LinkData(rel, href="http://foo/bar.jpeg", 
                       media_type=Representation.PNG_MEDIA_TYPE)
        eq_(Representation.PNG_MEDIA_TYPE, png.guessed_media_type)

        description = LinkData(Hyperlink.DESCRIPTION, content="Some content")
        eq_(None, description.guessed_media_type)

class TestMetadata(DatabaseTest):
    def test_from_edition(self):
        # Makes sure Metadata.from_edition copies all the fields over.

        edition, pool = self._edition(with_license_pool=True)
        edition.series = "Harry Otter and the Mollusk of Infamy"
        edition.series_position = "14"
        edition.primary_identifier.add_link(Hyperlink.IMAGE, "image", edition.data_source)
        metadata = Metadata.from_edition(edition)

        # make sure the metadata and the originating edition match 
        for field in Metadata.BASIC_EDITION_FIELDS:
            eq_(getattr(edition, field), getattr(metadata, field))

        e_contribution = edition.contributions[0]
        m_contributor_data = metadata.contributors[0]
        eq_(e_contribution.contributor.sort_name, m_contributor_data.sort_name)
        eq_(e_contribution.role, m_contributor_data.roles[0])

        eq_(edition.data_source, metadata.data_source(self._db))
        eq_(edition.primary_identifier.identifier, metadata.primary_identifier.identifier)

        e_link = edition.primary_identifier.links[0]
        m_link = metadata.links[0]
        eq_(e_link.rel, m_link.rel)
        eq_(e_link.resource.url, m_link.href)

        # The series position can also be 0.
        edition.series_position = 0
        metadata = Metadata.from_edition(edition)
        eq_(edition.series_position, metadata.series_position)

    def test_update(self):
        # Tests that Metadata.update correctly prefers new fields to old, unless 
        # new fields aren't defined.

        edition_old, pool = self._edition(with_license_pool=True)
        edition_old.publisher = "test_old_publisher"
        edition_old.subtitle = "old_subtitile"
        edition_old.series = "old_series"
        edition_old.series_position = 5
        metadata_old = Metadata.from_edition(edition_old)

        edition_new, pool = self._edition(with_license_pool=True)
        # set more fields on metadatas
        edition_new.publisher = None
        edition_new.subtitle = "new_updated_subtitile"
        edition_new.series = "new_series"
        edition_new.series_position = 0
        metadata_new = Metadata.from_edition(edition_new)

        metadata_old.update(metadata_new)

        eq_(metadata_old.publisher, "test_old_publisher")
        eq_(metadata_old.subtitle, metadata_new.subtitle)
        eq_(metadata_old.series, edition_new.series)
        eq_(metadata_old.series_position, edition_new.series_position)

    def test_apply(self):
        edition_old, pool = self._edition(with_license_pool=True)

        metadata = Metadata(
            data_source=DataSource.OVERDRIVE,
            title=u"The Harry Otter and the Seaweed of Ages",
            sort_title=u"Harry Otter and the Seaweed of Ages, The",
            subtitle=u"Kelp At It",
            series=u"The Harry Otter Sagas",
            series_position=u"4",
            language=u"eng",
            medium=u"Audio",
            publisher=u"Scholastic Inc",
            imprint=u"Follywood",
            published=datetime.date(1987, 5, 4),
            issued=datetime.date(1989, 4, 5)
        )

        edition_new, changed = metadata.apply(edition_old, pool.collection)

        eq_(changed, True)
        eq_(edition_new.title, u"The Harry Otter and the Seaweed of Ages")
        eq_(edition_new.sort_title, u"Harry Otter and the Seaweed of Ages, The")
        eq_(edition_new.subtitle, u"Kelp At It")
        eq_(edition_new.series, u"The Harry Otter Sagas")
        eq_(edition_new.series_position, u"4")
        eq_(edition_new.language, u"eng")
        eq_(edition_new.medium, u"Audio")
        eq_(edition_new.publisher, u"Scholastic Inc")
        eq_(edition_new.imprint, u"Follywood")
        eq_(edition_new.published, datetime.date(1987, 5, 4))
        eq_(edition_new.issued, datetime.date(1989, 4, 5))

        edition_new, changed = metadata.apply(edition_new, pool.collection)
        eq_(changed, False)

        # The series position can also be 0.
        metadata.series_position = 0
        edition_new, changed = metadata.apply(edition_new, pool.collection)
        eq_(changed, True)
        eq_(edition_new.series_position, 0)

    def test_apply_identifier_equivalency(self):

        # Set up primary identifier with matching & new IdentifierData objects
        edition, pool = self._edition(with_license_pool=True)
        primary = edition.primary_identifier
        primary_as_data = IdentifierData(
            type=primary.type, identifier=primary.identifier
        )
        other_data = IdentifierData(type=u"abc", identifier=u"def")

        # Prep Metadata object.
        metadata = Metadata(
            data_source=DataSource.OVERDRIVE,
            primary_identifier=primary,
            identifiers=[primary_as_data, other_data]
        )

        # The primary identifier is put into the identifiers array after init
        eq_(3, len(metadata.identifiers))
        assert primary in metadata.identifiers

        metadata.apply(edition, pool.collection)
        # Neither the primary edition nor the identifier data that represents
        # it have become equivalencies.
        eq_(1, len(primary.equivalencies))
        [equivalency] = primary.equivalencies
        eq_(equivalency.output.type, u"abc")
        eq_(equivalency.output.identifier, u"def")

    def test_apply_no_value(self):
        edition_old, pool = self._edition(with_license_pool=True)

        metadata = Metadata(
            data_source=DataSource.PRESENTATION_EDITION,
            subtitle=NO_VALUE,
            series=NO_VALUE,
            series_position=NO_NUMBER
        )

        edition_new, changed = metadata.apply(edition_old, pool.collection)

        eq_(changed, True)
        eq_(edition_new.title, edition_old.title)
        eq_(edition_new.sort_title, edition_old.sort_title)
        eq_(edition_new.subtitle, None)
        eq_(edition_new.series, None)
        eq_(edition_new.series_position, None)
        eq_(edition_new.language, edition_old.language)
        eq_(edition_new.medium, edition_old.medium)
        eq_(edition_new.publisher, edition_old.publisher)
        eq_(edition_new.imprint, edition_old.imprint)
        eq_(edition_new.published, edition_old.published)
        eq_(edition_new.issued, edition_old.issued)

    def test_apply_creates_coverage_records(self):
        edition, pool = self._edition(with_license_pool=True)

        metadata = Metadata(
            data_source=DataSource.OVERDRIVE,
            title=self._str
        )

        edition, changed = metadata.apply(edition, pool.collection)

        # One success was recorded.
        records = self._db.query(
            CoverageRecord
        ).filter(
            CoverageRecord.identifier_id==edition.primary_identifier.id
        ).filter(
            CoverageRecord.operation==None
        )
        eq_(1, records.count())
        eq_(CoverageRecord.SUCCESS, records.all()[0].status)

        # No metadata upload failure was recorded, because this metadata
        # came from Overdrive.
        records = self._db.query(
            CoverageRecord
        ).filter(
            CoverageRecord.identifier_id==edition.primary_identifier.id
        ).filter(
            CoverageRecord.operation==CoverageRecord.METADATA_UPLOAD_OPERATION
        )
        eq_(0, records.count())

        # Apply metadata from a different source.
        metadata = Metadata(
            data_source=DataSource.GUTENBERG,
            title=self._str
        )

        edition, changed = metadata.apply(edition, pool.collection)

        # Another success record was created.
        records = self._db.query(
            CoverageRecord
        ).filter(
            CoverageRecord.identifier_id==edition.primary_identifier.id
        ).filter(
            CoverageRecord.operation==None
        )
        eq_(2, records.count())
        for record in records.all():
            eq_(CoverageRecord.SUCCESS, record.status)

        # But now there's also a metadata upload failure.
        records = self._db.query(
            CoverageRecord
        ).filter(
            CoverageRecord.identifier_id==edition.primary_identifier.id
        ).filter(
            CoverageRecord.operation==CoverageRecord.METADATA_UPLOAD_OPERATION
        )
        eq_(1, records.count())
        eq_(CoverageRecord.TRANSIENT_FAILURE, records.all()[0].status)



    def test_update_contributions(self):
        edition = self._edition()

        # A test edition is created with a test contributor. This
        # particular contributor is about to be destroyed and replaced by
        # new data.
        [old_contributor] = edition.contributors

        contributor = ContributorData(
            display_name="Robert Jordan", 
            sort_name="Jordan, Robert",
            wikipedia_name="Robert_Jordan",
            viaf="79096089",
            lc="123",
            roles=[Contributor.PRIMARY_AUTHOR_ROLE]
        )

        metadata = Metadata(DataSource.OVERDRIVE, contributors=[contributor])
        metadata.update_contributions(self._db, edition, replace=True)

        # The old contributor has been removed and replaced with the new
        # one.
        [contributor] = edition.contributors
        assert contributor != old_contributor

        # And the new one has all the information provided by 
        # the Metadata object.
        eq_("Jordan, Robert", contributor.sort_name)
        eq_("Robert Jordan", contributor.display_name)
        eq_("79096089", contributor.viaf)
        eq_("123", contributor.lc)
        eq_("Robert_Jordan", contributor.wikipedia_name)

    def test_filter_recommendations(self):
        metadata = Metadata(DataSource.OVERDRIVE)
        known_identifier = self._identifier()
        unknown_identifier = IdentifierData(Identifier.ISBN, "hey there")

        # Unknown identifiers are filtered out of the recommendations.
        metadata.recommendations += [known_identifier, unknown_identifier]
        metadata.filter_recommendations(self._db)
        eq_([known_identifier], metadata.recommendations)

        # It works with IdentifierData as well.
        known_identifier_data = IdentifierData(
            known_identifier.type, known_identifier.identifier
        )
        metadata.recommendations = [known_identifier_data, unknown_identifier]
        metadata.filter_recommendations(self._db)
        [result] = metadata.recommendations
        # The IdentifierData has been replaced by a bonafide Identifier.
        eq_(True, isinstance(result, Identifier))
        # The genuwine article.
        eq_(known_identifier, result)


    def test_metadata_can_be_deepcopied(self):
        # Check that we didn't put something in the metadata that
        # will prevent it from being copied. (e.g., self.log)

        subject = SubjectData(Subject.TAG, "subject")
        contributor = ContributorData()
        identifier = IdentifierData(Identifier.GUTENBERG_ID, "1")
        link = LinkData(Hyperlink.OPEN_ACCESS_DOWNLOAD, "example.epub")
        measurement = MeasurementData(Measurement.RATING, 5)
        circulation = CirculationData(data_source=DataSource.GUTENBERG,
            primary_identifier=identifier, 
            licenses_owned=0, 
            licenses_available=0, 
            licenses_reserved=0, 
            patrons_in_hold_queue=0)
        primary_as_data = IdentifierData(
            type=identifier.type, identifier=identifier.identifier
        )
        other_data = IdentifierData(type=u"abc", identifier=u"def")

        m = Metadata(
            DataSource.GUTENBERG,
            subjects=[subject],
            contributors=[contributor],
            primary_identifier=identifier,
            links=[link],
            measurements=[measurement],
            circulation=circulation,

            title="Hello Title",
            subtitle="Subtle Hello",
            sort_title="Sorting Howdy",
            language="US English",
            medium=Edition.BOOK_MEDIUM,
            series="1",
            series_position=1,
            publisher="Hello World Publishing House",
            imprint=u"Follywood",
            issued=datetime.datetime.utcnow(),
            published=datetime.datetime.utcnow(),
            identifiers=[primary_as_data, other_data],
            data_source_last_updated=datetime.datetime.utcnow(),
        )

        m_copy = deepcopy(m)

        # If deepcopy didn't throw an exception we're ok.
        assert m_copy is not None


    def test_links_filtered(self):
        # test that filter links to only metadata-relevant ones
        link1 = LinkData(Hyperlink.OPEN_ACCESS_DOWNLOAD, "example.epub")
        link2 = LinkData(rel=Hyperlink.IMAGE, href="http://example.com/")
        link3 = LinkData(rel=Hyperlink.DESCRIPTION, content="foo")
        link4 = LinkData(
            rel=Hyperlink.THUMBNAIL_IMAGE, href="http://thumbnail.com/",
            media_type=Representation.JPEG_MEDIA_TYPE,
        )
        link5 = LinkData(
            rel=Hyperlink.IMAGE, href="http://example.com/", thumbnail=link4,
            media_type=Representation.JPEG_MEDIA_TYPE,
        )
        links = [link1, link2, link3, link4, link5]

        identifier = IdentifierData(Identifier.GUTENBERG_ID, "1")
        metadata = Metadata(
            data_source=DataSource.GUTENBERG,
            primary_identifier=identifier,
            links=links,
        )

        filtered_links = sorted(metadata.links, key=lambda x:x.rel)

        eq_([link2, link5, link4, link3], filtered_links)


class TestAssociateWithIdentifiersBasedOnPermanentWorkID(DatabaseTest):

    def test_success(self):
        pwid = 'pwid1'

        # Here's a print book.
        book = self._edition()
        book.medium = Edition.BOOK_MEDIUM
        book.permanent_work_id = pwid

        # Here's an audio book with the same PWID.
        audio = self._edition()
        audio.medium = Edition.AUDIO_MEDIUM
        audio.permanent_work_id=pwid

        # Here's an Metadata object for a second print book with the
        # same PWID.
        identifier = self._identifier()
        identifierdata = IdentifierData(
            type=identifier.type, identifier=identifier.identifier
        )
        metadata = Metadata(
            DataSource.GUTENBERG,
            primary_identifier=identifierdata, medium=Edition.BOOK_MEDIUM
        )
        metadata.permanent_work_id=pwid

        # Call the method we're testing.
        metadata.associate_with_identifiers_based_on_permanent_work_id(
            self._db
        )

        # The identifier of the second print book has been associated
        # with the identifier of the first print book, but not
        # with the identifier of the audiobook
        equivalent_identifiers = [x.output for x in identifier.equivalencies]
        eq_([book.primary_identifier], equivalent_identifiers)
    

class TestMARCExtractor(DatabaseTest):

    def setup(self):
        super(TestMARCExtractor, self).setup()
        base_path = os.path.split(__file__)[0]
        self.resource_path = os.path.join(base_path, "files", "marc")

    def sample_data(self, filename):
        with open(os.path.join(self.resource_path, filename)) as fh:
            return fh.read()

    def test_parse_year(self):
        m = MARCExtractor.parse_year
        nineteen_hundred = datetime.datetime.strptime("1900", "%Y")
        eq_(nineteen_hundred, m("1900"))
        eq_(nineteen_hundred, m("1900."))
        eq_(None, m("not a year"))

    def test_parser(self):
        """Parse a MARC file into Metadata objects."""

        file = self.sample_data("ils_plympton_01.mrc")
        metadata_records = MARCExtractor.parse(file, "Plympton")

        eq_(36, len(metadata_records))

        record = metadata_records[1]
        eq_("Strange Case of Dr Jekyll and Mr Hyde", record.title)
        eq_("Stevenson, Robert Louis", record.contributors[0].sort_name)
        assert "Recovering the Classics" in record.publisher
        eq_("9781682280041", record.primary_identifier.identifier)
        eq_(Identifier.ISBN, record.primary_identifier.type)
        subjects = record.subjects
        eq_(2, len(subjects))
        for s in subjects:
            eq_(Classifier.FAST, s.type)
        assert "Canon" in subjects[0].identifier
        eq_(Edition.BOOK_MEDIUM, record.medium)
        eq_(2015, record.issued.year)
        eq_('eng', record.language)

        eq_(1, len(record.links))
        assert "Utterson and Enfield are worried about their friend" in record.links[0].content

    def test_name_cleanup(self):
        """Test basic name cleanup techniques."""
        m = MARCExtractor.name_cleanup
        eq_("Dante Alighieri", m("Dante Alighieri,   1265-1321, author."))
        eq_("Stevenson, Robert Louis", m("Stevenson, Robert Louis."))
        eq_("Wells, H.G.", m("Wells,     H.G."))

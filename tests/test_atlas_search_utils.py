from datetime import date, datetime, time
from unittest import mock

from django.db.utils import OperationalError
from django.test import TestCase
from pymongo.errors import PyMongoError

from django_atlas_search.exceptions import BatchUpdateError, UnorderedQuerySetError
from django_atlas_search.utils import (
    bulk_delete_atlas_records,
    bulk_update_atlas_records,
    get_unix_timestamp,
    atlas_search,
    update_batch,
)

from tests.collections import SongCollection
from tests.factories import ArtistFactory, SongFactory
from tests.models import Artist, Song
from tests.utils import get_document


class TestUpdateBatch(TestCase):
    def setUp(self):
        self.song_count = 10
        SongFactory.create_batch(size=self.song_count)

    def test_update_batch(self):
        songa = Song.objects.all()
        self.assertEqual(songa.count(), self.song_count)

        with self.assertLogs(level="DEBUG") as logs:
            batch_number = 1
            update_batch(songa, SongCollection, batch_number)
            # Check that batch update was logged (format may vary)
            self.assertTrue(any(f"Batch {batch_number}" in log for log in logs.output))

    @mock.patch(
        "tests.collections.SongCollection.update", return_value=None
    )
    def test_update_batch_with_none(self, _):
        songs = Song.objects.all()
        self.assertEqual(songs.count(), self.song_count)
        # MongoDB bulk_write returns BulkWriteResult, not a list
        # This test verifies None handling
        update_batch(songs, SongCollection, 1)


class TestBulkUpdateAtlasRecords(TestCase):
    def setUp(self):
        self.unordered_queryset_message = (
            "Pagination may yield inconsistent results with an unordered object_list. "
            "Please provide an ordered objects"
        )
        self.song_count = 10
        SongFactory.create_batch(size=self.song_count)

    def test_bulk_update_atlas_records(self):
        songs = Song.objects.all().order_by("pk")

        # This exception is thrown because the tests are running
        # against SQLite which doesn't support a high level of concurrency.
        # https://docs.djangoproject.com/en/4.2/ref/databases/#database-is-locked-errors
        with self.assertRaises(OperationalError):
            bulk_update_atlas_records(songs, batch_size=200, num_threads=2)

    def test_bulk_update_atlas_records_invalid_type(self):
        ArtistFactory.create_batch(size=20)
        artists = Artist.objects.all().order_by("pk")

        with self.assertLogs(level="ERROR") as logs:
            bulk_update_atlas_records(artists, batch_size=200, num_threads=2)
            expected_log = (
                f"The objects for {artists.model.__name__} does not use AtlasSearchQuerySet "
                "as it's manager. Please update the model manager for the class to use AtlasSearch."
            )
            last_log_message = logs[-1][0].replace("ERROR:django_atlas_search.utils:", "")
            self.assertEqual(last_log_message, expected_log)

    @mock.patch("django.db.models.QuerySet.order_by", side_effect=TypeError)
    def test_bulk_update_atlas_records_unordered_queryset(self, _):
        songs = Song.objects.all()

        with self.assertRaises(UnorderedQuerySetError):
            bulk_update_atlas_records(songs, batch_size=200, num_threads=2)


class TestBulkDeleteAtlasRecords(TestCase):
    def setUp(self):
        self.search_index_class = Song.search_index_class
        self.database_name = self.search_index_class.database_name
        self.collection_name = self.search_index_class.collection_name
        SongFactory.create_batch(size=20)

    def test_bulk_delete_atlas_records(self):
        songs = Song.objects.all().order_by("pk")
        first_ten_songs = songs[:10]

        song_ids = []
        for song in first_ten_songs:
            song_document = get_document(self.database_name, self.collection_name, song.pk)
            self.assertIsNotNone(song_document)
            self.assertEqual(song_document["title"], song.title)
            song_ids.append(song.pk)

        bulk_delete_atlas_records(song_ids, self.database_name, self.collection_name)

        for song in songs:
            song_document = get_document(self.database_name, self.collection_name, song.pk)
            if song.pk in song_ids:
                self.assertIsNone(song_document)
            else:
                self.assertIsNotNone(song_document)
                self.assertEqual(song_document["title"], song.title)

    @mock.patch(
        "django_atlas_search.atlas_client.client", side_effect=PyMongoError("Test error")
    )
    def test_bulk_delete_atlas_records_exception_raised(self, _):
        songs = Song.objects.all().order_by("pk")
        first_ten_songs = songs[:10]

        song_ids = []
        for song in first_ten_songs:
            song_document = get_document(self.database_name, self.collection_name, song.pk)
            self.assertIsNotNone(song_document)
            self.assertEqual(song_document["title"], song.title)
            song_ids.append(song.pk)

        with self.assertLogs(level="ERROR") as logs:
            bulk_delete_atlas_records(song_ids, self.database_name, self.collection_name)
            last_log = logs[-1][0].replace("ERROR:django_atlas_search.utils:", "")
            expected_log_message = (
                f"Could not delete the documents IDs {song_ids}\nError: "
            )
            self.assertIn(expected_log_message, last_log)


class TestAtlasSearch(TestCase):
    def setUp(self):
        self.search_index_class = Song.search_index_class
        self.database_name = self.search_index_class.database_name
        self.collection_name = self.search_index_class.collection_name
        self.index_name = self.search_index_class.index_name
        self.query_fields = self.search_index_class.query_by_fields
        SongFactory.create_batch(size=20)

    def test_atlas_search(self):
        results = atlas_search(
            database_name=self.database_name,
            collection_name=self.collection_name,
            index_name=self.index_name,
            q="song",
            query_by=self.query_fields
        )
        self.assertIsNotNone(results)
        self.assertIn("found", results)
        self.assertIn("hits", results)


class TestGetUnixTimestamp(TestCase):
    def test_get_unix_timestamp_datetime(self):
        now = datetime(year=2023, month=11, day=23, hour=16, minute=20, second=00)
        now_timestamp = get_unix_timestamp(now)
        self.assertTrue(isinstance(now_timestamp, int))
        self.assertEqual(now_timestamp, now.timestamp())

    def test_get_unix_timestamp_date(self):
        today = date(year=2023, month=11, day=23)
        today_timestamp = get_unix_timestamp(today)
        self.assertTrue(isinstance(today_timestamp, int))
        self.assertEqual(
            today_timestamp, datetime.combine(today, datetime.min.time()).timestamp()
        )

    def test_get_unix_timestamp_time(self):
        now = time(hour=16, minute=20, second=00)
        now_timestamp = get_unix_timestamp(now)
        self.assertTrue(isinstance(now_timestamp, int))
        self.assertEqual(
            now_timestamp, datetime.combine(datetime.today(), now).timestamp()
        )

    def test_get_unix_timestamp_invalid_type(self):
        invalid_datetime = "2023-11-23 16:20:00"
        with self.assertRaises(TypeError):
            get_unix_timestamp(invalid_datetime)

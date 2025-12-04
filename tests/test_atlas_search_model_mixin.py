from django.test import TestCase

from django_atlas_search.mixins import AtlasSearchManager

from tests.factories import ArtistFactory, GenreFactory, SongFactory
from tests.models import Song
from tests.utils import get_document


class TestAtlasSearchMixin(TestCase):
    def setUp(self):
        self.genre = GenreFactory()
        self.artist = ArtistFactory()
        self.song = SongFactory(genre=self.genre, artists=[self.artist])

    def test_get_search_index_class(self):
        search_index_class = Song.get_search_index_class()
        self.assertEqual(search_index_class.__name__, "SongCollection")

    def test_atlas_search_manager(self):
        self.assertIsInstance(Song.objects, AtlasSearchManager)

    def test_update_collection(self):
        search_index_class = self.song.search_index_class
        database_name = search_index_class.database_name
        collection_name = search_index_class.collection_name
        song_document = get_document(database_name, collection_name, self.song.pk)
        self.assertEqual(song_document["genre_name"], self.song.genre.name)

        genre_name = "Dancehall"
        self.genre.name = genre_name
        self.genre.save(update_fields=["name"])

        song_document = get_document(database_name, collection_name, self.song.pk)
        self.assertNotEqual(song_document["genre_name"], self.song.genre.name)
        self.assertEqual(self.song.genre.name, genre_name)

        Song.objects.get_queryset().update()
        song_document = get_document(database_name, collection_name, self.song.pk)
        self.assertEqual(song_document["genre_name"], genre_name)
        self.assertEqual(song_document["genre_name"], self.song.genre.name)

    def test_delete_collection(self):
        search_index_class = self.song.search_index_class
        database_name = search_index_class.database_name
        collection_name = search_index_class.collection_name
        song_document = get_document(database_name, collection_name, self.song.pk)
        self.assertEqual(song_document["title"], self.song.title)

        Song.objects.get_queryset().delete()

        song_document = get_document(database_name, collection_name, self.song.pk)
        self.assertIsNone(song_document)

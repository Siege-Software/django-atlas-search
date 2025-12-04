from django_atlas_search import fields
from django_atlas_search.collections import AtlasSearchIndex


class SongCollection(AtlasSearchIndex):
    database_name = "test_db"
    collection_name = "songs"
    index_name = "song_search_index"
    query_by_fields = "title,artist_names,genre_name"

    title = fields.AtlasSearchCharField()
    genre_name = fields.AtlasSearchCharField(value="genre.name")
    genre_id = fields.AtlasSearchIntegerField()
    release_date = fields.AtlasSearchDateField(optional=True)
    artist_names = fields.AtlasSearchArrayField(
        base_field=fields.AtlasSearchCharField(), value="artist_names"
    )
    number_of_comments = fields.AtlasSearchIntegerField(searchable=False, optional=True)
    number_of_views = fields.AtlasSearchIntegerField(searchable=False, optional=True)
    library_ids = fields.AtlasSearchArrayField(
        base_field=fields.AtlasSearchIntegerField(), value="library_ids"
    )

# django-atlas-search

[![Build](https://github.com/Siege-Software/django-atlas-search/workflows/build/badge.svg?branch=main)](https://github.com/Siege-Software/django-atlas-search/actions)
[![codecov](https://codecov.io/gh/Siege-Software/django-atlas-search/branch/main/graph/badge.svg?token=S4W0E84821)](https://codecov.io/gh/Siege-Software/django-atlas-search)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
![PyPI download month](https://img.shields.io/pypi/dm/django-atlas-search.svg)
[![PyPI version](https://badge.fury.io/py/django-atlas-search.svg)](https://pypi.python.org/pypi/django-atlas-search/)
![Python versions](https://img.shields.io/badge/python-%3E%3D3.8-brightgreen)
![Django Versions](https://img.shields.io/badge/django-%3E%3D3.2-brightgreen)
[![PyPI License](https://img.shields.io/pypi/l/django-atlas-search.svg)](https://pypi.org/project/django-atlas-search/)


## What is it?
Faster Django Admin & Search powered by [Atlas Search](https://www.mongodb.com/products/platform/atlas-search)

## Quick Start Guide

### Installation

```sh
pip install django-atlas-search
```

or install directly from github to test the most recent version

```sh
pip install git+https://github.com/Siege-Software/django-atlas-search.git
```

### Configuration

Update your settings to include the following

- Add `django_atlas_search` to the list of installed apps.

```py
...
INSTALLED_APPS = [
    ...
    "django_atlas_search"
]
```

- Add `ATLAS_CONNECTION_SECRET_STRING` connection details. Read more about [Connection Strings](https://www.mongodb.com/docs/manual/reference/connection-string/)

```py
...
ATLAS_CONNECTION_SECRET_STRING="mongodb://127.0.0.1:32768/?directConnection=true"
```

Follow this [guide](https://www.mongodb.com/docs/atlas/getting-started/) to setup atlas search

### Create Collections
Throughout this guide, we’ll refer to the following models, which comprise a song catalogue application:

```
from django.db import models


class Genre(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Artist(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Song(models.Model):
    title = models.CharField(max_length=100)
    genre = models.ForeignKey(Genre, on_delete=models.CASCADE)
    release_date = models.DateField(blank=True, null=True)
    artists = models.ManyToManyField(Artist)
    number_of_comments = models.IntegerField(default=0)
    number_of_views = models.IntegerField(default=0)
    duration = models.DurationField()
    description = models.TextField()

    def __str__(self):
        return self.title
     
    def artist_names(self):
        return list(self.artists.all().values_list('name', flat=True))
        
```

For such an application, you might be interested in improving the search and load times on the song records list view.

```
from django_atlas_search.collections import AtlasSearchIndex
from django_atlas_search import fields


class SongCollection(AtlasSearchIndex):
    # Required: database and collection configuration
    database_name = "my_database"
    collection_name = "songs"
    index_name = "song_search_index"
    
    # At least one of the indexed fields has to be provided as one of the `query_by_fields`. Must be a CharField
    query_by_fields = 'title,artist_names,genre_name'
    
    title = fields.AtlasSearchCharField()
    genre_name = fields.AtlasSearchCharField(value='genre.name')
    genre_id = fields.AtlasSearchIntegerField()
    release_date = fields.AtlasSearchDateField(optional=True)
    artist_names = fields.AtlasSearchArrayField(base_field=fields.AtlasSearchCharField(), value='artist_names')
    number_of_comments = fields.AtlasSearchIntegerField(searchable=False, optional=True)
    number_of_views = fields.AtlasSearchIntegerField(searchable=False, optional=True)
    duration = fields.AtlasSearchIntegerField()  # Duration stored as seconds
```

It's okay to store fields that you don't intend to search but to display on the admin. Such fields should be marked as non-searchable e.g:

    number_of_views = fields.AtlasSearchIntegerField(searchable=False, optional=True)

Update the song model as follows:
```
from django_atlas_search.mixins import AtlasSearchModelMixin

class Song(AtlasSearchModelMixin):
    ...
    search_index_class = SongCollection
    ...
```

The `AtlasSearchModelMixin` provides a Manager that overrides the `update` and `delete` methods of the QuerySet.
If your model has a custom manager, make sure the custom manager inherits `django_atlas_search.mixins.AtlasSearchManager`

How the value of a field is retrieved from a model instance:
1. The collection field name is called as a property of the model instance
2. If `value` is provided, it will be called as a property or method of the model instance

Where the collections live is totally dependent on you but we recommend having a `collections.py` file
in the django app where the model you are creating a collection for is.

> [!NOTE]  
> We recommend displaying data from ForeignKey or OneToOne fields as string attributes using the display decorator to
> avoid triggering database queries that will negatively affect performance.

Instead of this in the admin:
```
@admin.display('Genre')
def genre_name(self, obj):
    return obj.genre.name
```

Do this:

```
@admin.display('Genre')
def genre_name(self, obj):
    # genre_name is field in the Collection. You can also store the object url as html
    return obj.genre_name
```

### Search Collections

Using Atlas Search for search

```py
from django_atlas_search.utils import atlas_search

from .models import Song


def search_songs(request):
    search_term = request.GET.get("q", None)
    songs = Song.objects.all()

    if search_term:
        search_index_class = Song.search_index_class
        results = atlas_search(
            database_name=search_index_class.database_name,
            collection_name=search_index_class.collection_name,
            index_name=search_index_class.index_name,
            q=search_term,
            query_by=search_index_class.query_by_fields,
            page=1,
            per_page=20
        )
        ids = [result["document"]["id"] for result in results["hits"]]
        songs = songs.filter(id__in=ids)

    ...
```

### Update Collection Schema
To add or remove fields to a collection's schema, update your collection then run:
    `python manage.py updatecollections`. Consider adding this to your CI/CD pipeline.

This also updates the [synonyms](#synonyms)

> [!NOTE]
> Atlas Search doesn't support in-place index updates. The `updatecollections` command will drop and recreate the index when changes are detected.

### How updates are made to Atlas Search
1. Signals -
`django-atlas-search` listens to signal events (`post_save`, `pre_delete`, `m2m_changed`) to update Atlas Search records. 
If [`update_fields`](https://docs.djangoproject.com/en/4.2/ref/models/instances/#specifying-which-fields-to-save)
were provided in the save method, only these fields will be updated in Atlas Search.

2. Update query -
`django-atlas-search` overrides Django's `QuerySet.update` to make updates to Atlas Search on the specified fields

3. Manual -
You can also update Atlas Search records manually e.g after doing a `bulk_create`
```
objs = Song.objects.bulk_create(
    [
      Song(title="Watch What I Do"),
      Song(title="Midnight City"),
   ]
)
search_index = Song.search_index_class(objs, many=True)
search_index.update()
```

### Admin Integration
To make a model admin display and search from the model's Atlas Search index, the admin class should
inherit `AtlasSearchAdminMixin`. This also adds Live Search to your admin changelist view.

```
from django_atlas_search.admin import AtlasSearchAdminMixin

@admin.register(Song)
class SongAdmin(AtlasSearchAdminMixin):
    ...
    list_display = ['title', 'genre_name', 'release_date', 'number_of_views', 'duration']
    
    @admin.display(description='Genre')
    def genre_name(self, obj):
        # genre_name is stored in the Atlas Search index
        return obj.genre_name
    ...

```

### Indexing
For the initial setup, you will need to index in bulk. Bulk updating is multi-threaded. Depending on your system specs, you should set the `batch_size` keyword argument.

```
from django_atlas_search.utils import bulk_delete_atlas_records, bulk_update_atlas_records

model_qs = Song.objects.all().order_by('id')  # querysets should be ordered
bulk_update_atlas_records(model_qs, batch_size=1024)
```

> [!NOTE]
> Before bulk indexing, make sure you've created the Atlas Search index by running `python manage.py updatecollections`.

### Custom Admin Filters
To make use of custom admin filters, define a `filter_by` property in the filter definition.
Define boolean Atlas Search field `has_views` that gets its value from a model property. This example is not necessarily practical but for demo purposes.

```
# models.py
class Song(models.Model):
    ...
    @property
    def has_views(self):
        return self.number_of_views > 0
    ...

# collections.py
class SongCollection(AtlasSearchIndex):
    ...
    has_views = fields.AtlasSearchBooleanField()
    ...
```

```
class HasViewsFilter(admin.SimpleListFilter):
    title = _('Has Views')
    parameter_name = 'has_views'

    def lookups(self, request, model_admin):
        return (
            ('all', 'All'),
            ('True', 'Yes'),
            ('False', 'No')
        )

    def queryset(self, request, queryset):
        # This is used by the default django admin
        if self.value() == 'True':
            return queryset.filter(number_of_views__gt=0)
        elif self.value() == 'False':
            return queryset.filter(number_of_views=0)
            
        return queryset

    @property
    def filter_by(self):
        # This is used by Atlas Search
        if self.value() == 'True':
            return {"has_views": "=true"}
        elif self.value() == 'False':
            return {"has_views": "=false"}

        return {}
```

Note that simple lookups like the one above are done by default (hence no need to define `filter_by`) if 
the `parameter_name` is a field in the collection

### Synonyms
The [synonyms](https://www.mongodb.com/docs/atlas/atlas-search/synonyms/) feature allows you to define search terms that 
should be considered equivalent. Synonyms should be defined with classes that inherit from `Synonym`

```
from django_atlas_search.collections import Synonym

# say you need users searching the genre hip-hop to get results if they use the search term rap

class HipHopSynonym(Synonym):
    name = 'hip-hop-synonyms'
    synonyms = ['hip-hop', 'rap']
    collection = 'synonymous_terms'  # MongoDB collection for synonym data
 
# Update the collection to include the synonym
class SongCollection(AtlasSearchIndex):
    ...
    synonyms = [HipHopSynonym]
    ...
    
```
To update the collection with any changes made to synonyms run `python manage.py updatecollections`

## Field Types

django-atlas-search supports the following field types:

- `AtlasSearchCharField` - String field for text search
- `AtlasSearchTextField` - Text field optimized for full-text search
- `AtlasSearchKeywordField` - Keyword field for exact matching
- `AtlasSearchIntegerField` - Integer field (32-bit)
- `AtlasSearchLongField` - Long integer field (64-bit)
- `AtlasSearchFloatField` - Floating point number field
- `AtlasSearchDecimalField` - Decimal field (stored as string for precision)
- `AtlasSearchBooleanField` - Boolean field
- `AtlasSearchDateField` - Date field
- `AtlasSearchDateTimeField` - DateTime field
- `AtlasSearchTimeField` - Time field
- `AtlasSearchArrayField` - Array field containing elements of a specific type
- `AtlasSearchJSONField` - JSON field (stored as string)
- `AtlasSearchGeoPointField` - Geospatial point field for location-based search
- `AtlasSearchFacetField` - Field specifically for faceted search

## Requirements

- Python >= 3.8
- Django >= 3.2
- PyMongo >= 4.0
- MongoDB with Atlas Search enabled

## License

MIT License - see LICENSE file for details



from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from enum import StrEnum
from operator import methodcaller
from typing import Dict, Iterable, List, Union

from django.conf import settings
from django.db.models import QuerySet
from django.utils.functional import cached_property

from django_atlas_search.fields import AtlasSearchField, AtlasSearchObjectIdField

try:
    from django.utils.functional import classproperty
except ImportError:
    from django.utils.decorators import classproperty


from django_atlas_search.atlas_client import client

logger = logging.getLogger(__name__)

_COLLECTION_META_OPTIONS = {
    "schema_name",
    "default_sorting_field",
    "token_separators",
    "symbols_to_index",
    "query_by_fields",
}
_SYNONYM_PARAMETERS = {"synonyms", "name", "input", "mapping_type", "analyzer"}


class MappingType(StrEnum):
    EQUIVALENT = 'equivalent'
    EXPLICIT = 'explicit'


@dataclass
class Synonym:
    name: str = ""
    synonyms: list[str] = None
    input: list[str] = None
    mapping_type: MappingType = MappingType.EQUIVALENT
    analyzer: str = "lucene.standard"
    collection: str = "synonymous_terms"

    def data(self):
        if not self.name:
            raise ValueError("the name attribute must be set")

        if not self.synonyms:
            raise ValueError("the synonyms attribute must be set")

        if self.mapping_type == MappingType.EXPLICIT and self.input is None:
            raise ValueError("the input attribute must be set for explicit mappings")

        _data = {
            "name": self.name,
            "mappingType": self.mapping_type,
            "synonyms": self.synonyms
        }

        if self.input:
            _data["input"] = self.input
            if self.mapping_type == MappingType.EQUIVALENT:
                warnings.warn(
                    "Using equivalent mappingType with input. Changing to explicit.",
                    RuntimeWarning,
                    stacklevel=2
                )
                _data["mappingType"] = MappingType.EXPLICIT

        return _data

    def index_definition(self):
        return {
            "name": self.name,
            "source": {
                "collection": self.collection,
            },
            "analyzer": self.analyzer
        }


class AtlasSearchIndex:
    database_name: str = None
    collection_name: str = None
    index_name: str = None
    synonyms: List[Synonym] = []

    def __init__(
            self,
            database_name: str,
            collection_name: str,
            index_name: str,
            obj: Union[object, QuerySet, Iterable] = None,
            many: bool = False,
            data: list = None,
            update_fields: list = None
    ):
        assert not all([obj, data]), "`obj` and `data` cannot be provided together"
        if data and obj:
            raise Exception("'data' and 'obj' are mutually exclusive")

        self._data = data
        self.many = many
        self.obj = obj

        self.update_fields = update_fields
        self.fields = self.get_fields()
        self.index_name = index_name
        self.database_name = database_name
        self.collection_name = collection_name
        db = client[self.database_name]
        self.collection = db[self.collection_name]

    @cached_property
    def data(self):
        return self.get_data()

    def get_data(self):
        if self._data:
            return self._data

        if not self.obj:
            return []

        data = []
        if self.many:
            for _obj in self.obj:
                if obj_data := self._get_object_data(_obj):
                    data.append(obj_data)
        else:
            if obj_data := self._get_object_data(self.obj):
                data.append(obj_data)

        return data

    @classmethod
    def get_fields(cls) -> Dict[str, AtlasSearchField]:
        """
        Returns:
            A dictionary of the field names to the field definition for this collection
        """
        fields = {}
        # Avoid Recursion Errors
        exclude_attributes = {"searchable_fields", "facetable_fields"}

        for attr in dir(cls):
            if attr in exclude_attributes:
                continue
            attr_value = getattr(cls, attr, None)
            if not isinstance(attr_value, AtlasSearchField):
                continue

            attr_value._name = attr
            attr_value._value = attr_value._value or attr
            fields[attr] = attr_value

        # Auto adds _id if absent (MongoDB's default primary key)
        if not fields.get("_id"):
            _id = AtlasSearchObjectIdField(searchable=False, value="_id")
            _id._name = "_id"
            fields["_id"] = _id

        return fields

    @cached_property
    def validated_data(self) -> list:
        """
        Returns a list of the collection data with values converted into the correct Python objects
        """
        _validated_data = []

        for obj in self.data:
            data = {}
            for key, value in obj.items():
                try:
                    field = self.get_field(key)
                except KeyError:
                    continue
                data[key] = field.to_python(value)

            _validated_data.append(data)

        return _validated_data

    def __str__(self):
        return self.index_name

    @classproperty
    def searchable_fields(cls) -> list:
        """
        Returns:
            The names of searchable fields
        """
        fields = cls.get_fields()
        return [field.name for field in fields.values() if field.searchable]

    @classproperty
    def facetable_fields(cls) -> list:
        """
        Returns:
            The names of facetable fields
        """
        fields = cls.get_fields()
        return [field.name for field in fields.values() if field.facetable]

    @classmethod
    def get_field(cls, name) -> AtlasSearchField:
        """
        Get the field with the provided name from the collection

        Args:
            name: the field name

        Returns:
            An AtlasSearchField
        """
        fields = cls.get_fields()
        return fields[name]

    @classmethod
    def get_django_lookup(cls, field, value, exception: Exception) -> dict:
        """
        Get the lookup that would have been used for this field in django. Expects to find a method on
        the collection called `get_FIELD_lookup` otherwise a NotImplementedError is raised

        Args:
            field: the name of the field in the collection
            value: the value to look for
            exception: the django exception that led us here

        Returns:
            A dictionary of the fields to the value.
        """
        if "get_%s_lookup" % field not in cls.__dict__:
            raise Exception([
                exception,
                NotImplementedError("get_%s_lookup is not implemented" % field)
            ])

        method = methodcaller("get_%s_lookup" % field, value)
        return method(cls)

    @cached_property
    def search_index_definition(self) -> dict:
        """
        Returns:
            The Atlas Search index definition
        """
        mappings = {
            "dynamic": False,
            "fields": {}
        }

        for field in self.fields.values():
            if field.searchable:
                field_mapping = field.get_atlas_mapping()
                if field_mapping:
                    mappings["fields"][field.name] = field_mapping

        if self.synonyms:
            synonym_list = []
            for synonym in self.synonyms:
                synonym_list.append(synonym.index_definition)

            if synonym_list:
                mappings["synonyms"] = synonym_list

        return {
            "name": self.index_name,
            "definition": {
                "mappings": mappings
            }
        }

    def _get_object_data(self, obj):
        if self.update_fields:
            # we need the _id for updates and a user can leave it out
            update_fields = set(self.fields.keys()).intersection(
                set(self.update_fields)
            )
            if update_fields:
                update_fields.add("_id")
                fields = []
                for field_name in update_fields:
                    try:
                        field = self.get_field(field_name)
                    except KeyError:
                        continue
                    fields.append(field)
            else:
                fields = []
        else:
            fields = self.fields.values()

        return {field.name: field.value(obj) for field in fields}

    def create_search_index(self):
        """
        Create a new Atlas Search index on the MongoDB collection
        """
        try:
            if not self.collection:
                raise ValueError("MongoDB collection not initialized")

            # Create the search index
            index_definition = self.search_index_definition
            result = self.collection.create_search_index(index_definition["definition"], name=self.index_name)
            logger.info(f"Created search index '{self.index_name}' for collection '{self.collection_name}'")
            return result
        except Exception as e:
            logger.error(f"Failed to create search index: {e}")
            raise

    def update_search_index(self):
        """
        Update an existing Atlas Search index
        Note: Atlas Search doesn't support direct index updates like Typesense.
        You typically need to drop and recreate the index.
        """
        try:
            # List existing indexes
            existing_indexes = list(self.collection.list_search_indexes())
            index_exists = any(idx.get("name") == self.index_name for idx in existing_indexes)

            if index_exists:
                logger.info(
                    f"Search index '{self.index_name}' already exists. Consider dropping and recreating for updates.")
            else:
                self.create_search_index()

        except Exception as e:
            logger.error(f"Failed to update search index: {e}")
            raise

    def drop_search_index(self):
        """
        Drops an Atlas Search index from the MongoDB collection
        """
        try:
            if not self.collection:
                raise ValueError("MongoDB collection not initialized")

            self.collection.drop_search_index(self.index_name)
            logger.info(f"Dropped search index '{self.index_name}' from collection '{self.collection_name}'")
        except Exception as e:
            logger.error(f"Failed to drop search index: {e}")
            raise

    def get_search_indexes(self):
        """
        Retrieve all search indexes for this collection
        """
        if not self.collection:
            raise ValueError("MongoDB collection not initialized")

        return list(self.collection.list_search_indexes())

    def delete(self):
        """
        Delete documents from the MongoDB collection
        """
        if not self.data:
            return

        try:
            if not self.collection:
                raise ValueError("MongoDB collection not initialized")

            document_ids = [obj.get('_id') for obj in self.data if obj.get('_id')]
            if document_ids:
                result = self.collection.delete_many({"_id": {"$in": document_ids}})
                return result
        except Exception as e:
            logger.error(f"Failed to delete documents: {e}")
            raise

    def update(self, upsert: bool = True):
        """
        Update or insert documents in the MongoDB collection
        """
        if not self.data:
            return

        try:
            if not self.collection:
                raise ValueError("MongoDB collection not initialized")

            if len(self.data) == 1:
                return self._update_single_document(self.data[0], upsert)
            else:
                return self._update_multiple_documents(upsert)
        except Exception as e:
            logger.error(f"Failed to update documents: {e}")
            raise

    def _update_single_document(self, document, upsert=True):
        """
        Update a single document
        """
        document_id = document.get("_id")

        if document_id:
            # Update existing document
            update_doc = {k: v for k, v in document.items() if k != "_id"}
            result = self.collection.update_one(
                {"_id": document_id},
                {"$set": update_doc},
                upsert=upsert
            )
        else:
            # Insert new document
            result = self.collection.insert_one(document)

        return result

    def _update_multiple_documents(self, upsert=True):
        """
        Update multiple documents using bulk operations
        """
        from pymongo import UpdateOne, InsertOne

        operations = []

        for document in self.data:
            document_id = document.get("_id")

            if document_id:
                # Update existing document
                update_doc = {k: v for k, v in document.items() if k != "_id"}
                operations.append(
                    UpdateOne(
                        {"_id": document_id},
                        {"$set": update_doc},
                        upsert=upsert
                    )
                )
            else:
                # Insert new document
                operations.append(InsertOne(document))

        if operations:
            result = self.collection.bulk_write(operations)
            return result

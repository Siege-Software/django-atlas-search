from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum


    class StrEnum(str, Enum):
        pass

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

from pymongo.errors import OperationFailure

from django_atlas_search.atlas_client import client

logger = logging.getLogger(__name__)

_SYNONYM_PARAMETERS = {"synonyms", "name", "input", "mapping_type", "analyzer"}


class MappingType(StrEnum):
    EQUIVALENT = 'equivalent'
    EXPLICIT = 'explicit'


@dataclass
class Synonym:
    """
    Dataclass for defining Atlas Search synonyms.
    
    Note: Default values (including empty strings) are allowed in the class definition.
    Validation only occurs when instance methods are called (e.g., index_definition()).
    """
    name: str = ""  # Default empty string is fine for class definition; validated on instance use
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
        """
        Returns the index definition for this synonym.
        
        Validates that required fields are set on this instance.
        Note: This validation only runs when called on an instance, not on the class definition.
        The dataclass can have empty default values; validation occurs here when the instance is used.
        
        Raises:
            ValueError: If name or collection is empty on this instance
            TypeError: If called on the class instead of an instance
        """
        # Ensure this is called on an instance, not the class itself
        if isinstance(self, type):
            raise TypeError(
                "index_definition() can only be called on Synonym instances, not on the Synonym class. "
                "Instantiate the Synonym class first (e.g., Synonym(name='...'))."
            )
        
        # Validation only occurs on instances, not on the dataclass definition
        # Check instance attributes, not class attributes
        instance_name = getattr(self, 'name', None)
        instance_collection = getattr(self, 'collection', None)
        
        if not instance_name:
            raise ValueError(
                "Synonym instance 'name' attribute cannot be empty. "
                "The name is required for Atlas Search index definitions. "
                "Set the name when creating the Synonym instance."
            )
        
        if not instance_collection:
            raise ValueError(
                "Synonym instance 'collection' attribute cannot be empty. "
                "The collection name is required for Atlas Search synonym sources. "
                "Set the collection when creating the Synonym instance."
            )
        
        return {
            "name": instance_name,
            "source": {
                "collection": instance_collection,
            },
            "analyzer": getattr(self, 'analyzer', 'lucene.standard')
        }


class AtlasSearchIndex:
    database_name: str = None
    collection_name: str = None
    index_name: str = None
    synonyms: List[Synonym] = []
    query_by_fields: str = ""  # Comma-separated list of fields to search by

    def __init__(
            self,
            database_name: str = None,
            collection_name: str = None,
            index_name: str = None,
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
        # Use provided values or fall back to class attributes
        self.index_name = index_name or self.__class__.index_name
        self.database_name = database_name or self.__class__.database_name
        self.collection_name = collection_name or self.__class__.collection_name
        # Ensure synonyms is an instance attribute (copy from class if needed)
        self.synonyms = self.__class__.synonyms.copy() if self.__class__.synonyms else []

        self.db = client[self.database_name]
        self.collection = self.db[self.collection_name]

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
        # Avoid Recursion Errors - exclude classproperties that call get_fields()
        exclude_attributes = {"searchable_fields", "facetable_fields", "sortable_fields", "schema_name"}

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
        # Map Django model 'id' field to MongoDB '_id'
        if not fields.get("_id"):
            _id = AtlasSearchObjectIdField(searchable=False, value="id")  # Django models use 'id'
            _id._name = "_id"  # But MongoDB uses '_id'
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

    @classproperty
    def schema_name(cls) -> str:
        """
        Returns:
            The schema/collection name for this index (alias for collection_name)
        """
        return cls.collection_name or cls.index_name or ""

    @classproperty
    def sortable_fields(cls) -> list:
        """
        Returns:
            The names of sortable fields (all searchable fields are sortable by default)
        """
        fields = cls.get_fields()
        return [field.name for field in fields.values() if field.searchable]

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

        # Build the definition structure according to MongoDB Atlas Search API
        # Structure: { "name": "...", "definition": { "mappings": {...}, "synonyms": [...] } }
        definition = {
            "mappings": mappings
        }

        # Synonyms should be at the definition level, not inside mappings
        # According to MongoDB Atlas Search API documentation
        if self.synonyms:
            synonym_list = []
            for i, synonym in enumerate(self.synonyms):
                # Ensure it's an instance of Synonym, not the class itself
                # isinstance() can be True for the class in some edge cases, so we also check it's not a type
                if isinstance(synonym, Synonym) and not isinstance(synonym, type):
                    try:
                        # Only validate when calling on an instance, not on the class definition
                        synonym_def = synonym.index_definition()
                        synonym_list.append(synonym_def)
                    except ValueError as e:
                        raise ValueError(
                            f"Invalid synonym instance at index {i} in collection '{self.collection_name}': {e}"
                        ) from e
                elif synonym is Synonym or (isinstance(synonym, type) and issubclass(synonym, Synonym)):
                    # This is the class itself, not an instance - skip validation
                    raise TypeError(
                        f"Expected Synonym instance at index {i}, got the Synonym class itself. "
                        f"You must instantiate the Synonym class (e.g., Synonym(name='...')) before using it."
                    )
                else:
                    # If it's not a Synonym instance, raise an error
                    raise TypeError(
                        f"Expected Synonym instance at index {i}, got {type(synonym)}"
                    )

            if synonym_list:
                definition["synonyms"] = synonym_list

        return {
            "name": self.index_name,
            "definition": definition
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

        data = {}
        for field in fields:
            # Map Django model 'id' to MongoDB '_id' if needed
            if field.name == "_id" and hasattr(obj, 'id') and not hasattr(obj, '_id'):
                # Django model uses 'id', MongoDB uses '_id'
                data["_id"] = field.value(obj) if field._value == "_id" else obj.id
            else:
                data[field.name] = field.value(obj)

        return data

    def ensure_collection_exists(self):
        """
        Ensure the MongoDB collection exists. Creates it if it doesn't exist.
        If the database doesn't exist, operations will fail naturally.
        """
        if self.collection is None:
            raise ValueError("MongoDB collection not initialized")

        # Check if collection exists
        try:
            existing_collections = self.db.list_collection_names()
            if self.collection_name not in existing_collections:
                # Collection doesn't exist, create it by inserting and immediately deleting a dummy document
                # This is the standard way to create an empty collection in MongoDB
                logger.info(
                    f"Collection '{self.collection_name}' does not exist in database '{self.database_name}'. "
                    f"Creating collection..."
                )
                self.collection.insert_one({"_temp": True})
                self.collection.delete_one({"_temp": True})
                logger.info(
                    f"Successfully created collection '{self.collection_name}' in database '{self.database_name}'."
                )
        except Exception as e:
            # For database errors (like database not existing), let them propagate naturally
            logger.error(
                f"Failed to ensure collection '{self.collection_name}' exists in database '{self.database_name}': {e}"
            )
            raise

    def create_search_index(self):
        """
        Create a new Atlas Search index on the MongoDB collection
        
        WARNING: PyMongo's create_search_index() is asynchronous and returns immediately
        after submitting the index creation request to Atlas. It does NOT wait for:
        - Index definition validation
        - Index creation completion
        - Error detection from invalid definitions
        
        Atlas validates the index definition asynchronously. If the definition is invalid
        (e.g., incorrect structure, invalid field mappings), Atlas will reject it AFTER
        this method has already returned successfully. The code currently assumes success
        if no immediate exception is raised, but the index may fail to create on Atlas.
        
        To verify the index was actually created, check the index status in Atlas UI or
        use get_search_indexes() after a delay to confirm the index exists.
        """
        try:
            if self.collection is None:
                raise ValueError("MongoDB collection not initialized")

            # Ensure collection exists before creating search index
            self.ensure_collection_exists()

            # Create the search index
            # PyMongo create_search_index requires 'model' as a positional argument
            # The model can be a dict with 'definition' and 'name' keys
            # 
            # WARNING: This call returns immediately after submission. It does NOT wait
            # for Atlas to validate the definition or complete index creation. Invalid
            # definitions will be rejected asynchronously by Atlas, but this method will
            # have already returned successfully.
            index_model = self.search_index_definition
            result = self.collection.create_search_index(index_model)
            
            # WARNING: This log message indicates the request was submitted, not that
            # the index was successfully created. Atlas validates and creates the index
            # asynchronously. Check Atlas UI or use get_search_indexes() to verify.
            logger.warning(
                f"Search index creation request submitted for '{self.index_name}' "
                f"on collection '{self.collection_name}'. "
                f"Note: Index creation is asynchronous. Verify in Atlas UI that the index "
                f"was created successfully. Invalid definitions will be rejected by Atlas "
                f"after this method returns."
            )
            logger.info(f"Submitted search index creation request for '{self.index_name}' for collection '{self.collection_name}'")
            return result
        except Exception as e:
            logger.error(f"Failed to create search index: {e}")
            raise

    def update_search_index(self):
        """
        Update an existing Atlas Search index
        Note: Atlas Search doesn't support direct index updates.
        You typically need to drop and recreate the index.
        """
        try:
            # List existing indexes
            existing_indexes = self.get_search_indexes()
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
        Drops an Atlas Search index from the MongoDB collection.
        Handles the case where the index is already being deleted.
        """
        try:
            if self.collection is None:
                raise ValueError("MongoDB collection not initialized")

            self.collection.drop_search_index(self.index_name)
            logger.info(f"Dropped search index '{self.index_name}' from collection '{self.collection_name}'")
        except OperationFailure as e:
            # Error code 20 (IllegalOperation) means index is already being deleted
            if e.code == 20 and "already requested to be deleted" in str(e):
                logger.info(
                    f"Search index '{self.index_name}' is already being deleted. "
                    f"Waiting for deletion to complete before recreating."
                )
                # Index is already being deleted, we can proceed to create a new one
                # The old one will be fully removed by Atlas
                return
            raise
        except Exception as e:
            logger.error(f"Failed to drop search index: {e}")
            raise

    def get_search_indexes(self):
        """
        Retrieve all search indexes for this collection
        
        Raises:
            ValueError: If collection is not initialized
            OperationFailure: If not connected to MongoDB Atlas (code 6047401)
        """
        if self.collection is None:
            raise ValueError("MongoDB collection not initialized")

        try:
            return list(self.collection.list_search_indexes())
        except OperationFailure as e:
            # Error code 6047401 indicates $listSearchIndexes is only available on MongoDB Atlas
            if e.code == 6047401:
                raise OperationFailure(
                    "Atlas Search operations require a MongoDB Atlas connection. "
                    "Please ensure your ATLAS_CONNECTION_SECRET_STRING points to a MongoDB Atlas cluster, "
                    "not a local MongoDB instance. "
                    "See: https://www.mongodb.com/docs/atlas/getting-started/",
                    code=6047401
                ) from e
            raise

    def _normalize_index_definition(self, definition: dict) -> dict:
        """
        Normalize an index definition for comparison by sorting keys and removing metadata.
        This ensures we can compare definitions even if keys are in different order.
        """
        if not definition:
            return {}
        
        # Create a deep copy to avoid modifying the original
        normalized = json.loads(json.dumps(definition))
        
        # Recursively sort dictionary keys
        def sort_dict(d):
            if isinstance(d, dict):
                return {k: sort_dict(v) for k, v in sorted(d.items())}
            elif isinstance(d, list):
                return [sort_dict(item) for item in d]
            else:
                return d
        
        return sort_dict(normalized)

    def _index_definitions_equal(self, def1: dict, def2: dict) -> bool:
        """
        Compare two index definitions to see if they are equivalent.
        Returns True if definitions are the same, False otherwise.
        """
        normalized1 = self._normalize_index_definition(def1)
        normalized2 = self._normalize_index_definition(def2)
        return normalized1 == normalized2

    def delete(self):
        """
        Delete documents from the MongoDB collection
        """
        if not self.data:
            return

        try:
            if self.collection is None:
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
            if self.collection is None:
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

        # Convert Django model ID (int/str) to ObjectId if needed
        from bson import ObjectId
        if document_id and not isinstance(document_id, ObjectId):
            if isinstance(document_id, (int, str)):
                try:
                    document_id = ObjectId(str(document_id))
                except Exception:
                    # If conversion fails, use as-is (might be a string ID)
                    pass

        if document_id:
            # Update existing document
            update_doc = {k: v for k, v in document.items() if k != "_id"}
            result = self.collection.update_one(
                {"_id": document_id},
                {"$set": update_doc},
                upsert=upsert
            )
        else:
            # Insert new document (MongoDB will auto-generate _id)
            result = self.collection.insert_one(document)

        return result

    def _update_multiple_documents(self, upsert=True):
        """
        Update multiple documents using bulk operations
        """
        from pymongo import UpdateOne, InsertOne
        from bson import ObjectId

        operations = []

        for document in self.data:
            document_id = document.get("_id")

            # Convert Django model ID (int/str) to ObjectId if needed
            if document_id and not isinstance(document_id, ObjectId):
                if isinstance(document_id, (int, str)):
                    try:
                        document_id = ObjectId(str(document_id))
                    except Exception:
                        # If conversion fails, use as-is (might be a string ID)
                        pass

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
                # Insert new document (MongoDB will auto-generate _id)
                operations.append(InsertOne(document))

        if operations:
            result = self.collection.bulk_write(operations)
            return result

    def update_atlas_collection(self):
        """
        Create or update the Atlas Search index for this collection.
        This method is called by the management command to sync index definitions.
        Only updates if the index definition has actually changed.
        
        WARNING: This method returns "created" or "updated" based on the API call submission,
        NOT on actual index creation/validation. PyMongo's create_search_index() is asynchronous
        and returns immediately. Atlas validates the index definition asynchronously, so invalid
        definitions will be rejected AFTER this method returns. Always verify index creation
        in the Atlas UI or by checking get_search_indexes() after a delay.
        
        Returns:
            str: "created" if a new index creation was submitted, "updated" if an existing index
                 update was submitted, "unchanged" if the index exists and is already up to date,
                 None if nothing was done
                 
        Note: Return values indicate request submission, not successful creation/validation.
        """
        try:
            existing_indexes = self.get_search_indexes()
            existing_index = None
            for idx in existing_indexes:
                if idx.get("name") == self.index_name:
                    existing_index = idx
                    break

            new_definition = self.search_index_definition

            if existing_index:
                # Index exists, check if definition has changed
                existing_def = existing_index.get("definition", {})
                new_def = new_definition.get("definition", {})
                
                if self._index_definitions_equal(existing_def, new_def):
                    # Definitions are the same, no update needed
                    logger.info(
                        f"Search index '{self.index_name}' for collection '{self.collection_name}' "
                        f"is already up to date. No changes needed."
                    )
                    return "unchanged"
                else:
                    # Definition has changed, need to update
                    logger.info(
                        f"Search index definition for '{self.index_name}' has changed. "
                        f"Updating index for collection '{self.collection_name}'"
                    )
                    # For updates, we need to drop and recreate since Atlas Search
                    # doesn't support in-place index updates
                    self.drop_search_index()
                    self.create_search_index()
                    return "updated"
            else:
                # Index doesn't exist, create it
                logger.info(
                    f"Creating new search index '{self.index_name}' for collection '{self.collection_name}'"
                )
                self.create_search_index()
                return "created"

        except Exception as e:
            logger.error(f"Failed to update Atlas collection: {e}")
            raise

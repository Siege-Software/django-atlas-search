import concurrent.futures
import json
import logging
import os
import time as t_time
from datetime import date, datetime, time
from typing import List, Dict, Any, Optional

from bson import ObjectId
from django.core.exceptions import FieldError
from django.core.paginator import Paginator
from django.db.models import QuerySet
from pymongo.errors import PyMongoError

from django_atlas_search.exceptions import BatchUpdateError, UnorderedQuerySetError
from django_atlas_search.atlas_client import client

logger = logging.getLogger(__name__)


def update_batch(documents_queryset: QuerySet, collection_class, batch_no: int) -> None:
    """Updates a batch of documents using the Atlas Search/MongoDB API.

    Parameters
    ----------
    documents_queryset : QuerySet
        The Django objects QuerySet to update. It must be an `AtlasSearchModelMixin` subclass.
    collection_class : AtlasSearchIndex
        The Django Atlas Search index to update.
    batch_no : int
        The batch identifier number.

    Returns
    -------
    None

    Raises
    ------
    BatchUpdateError
        Raised when an error occurs during updating Atlas Search collection.
    """
    collection = collection_class(obj=documents_queryset, many=True)
    responses = collection.update()
    if responses is None:
        return

    # MongoDB bulk_write returns a BulkWriteResult, not a list
    # Check if it's a BulkWriteResult and verify success
    if hasattr(responses, 'inserted_count') or hasattr(responses, 'modified_count'):
        # MongoDB bulk operation succeeded
        logger.debug(f"Batch {batch_no} Updated with {len(collection.data)} records ✓")
    else:
        # Unexpected response format
        logger.warning(f"Batch {batch_no} update returned unexpected response format: {type(responses)}")

    logger.debug(f"Batch {batch_no} Updated with {len(collection.data)} records ✓")


def bulk_update_atlas_records(
        records_queryset: QuerySet,
        batch_size: int = 1024,
        num_threads: int = os.cpu_count(),
) -> None:
    """This method updates Atlas Search records for both objects .update() calls from
    AtlasSearchModelMixin subclasses.
    This function should be called on every model update statement for data consistency.

    Parameters
    ----------
    records_queryset : QuerySet
        The Django objects QuerySet to update. It must be an `AtlasSearchModelMixin` subclass.
    batch_size : int
        The number of objects to be indexed in a single run. Defaults to 1024.
    num_threads : int
        The number of threads that will be used. Defaults to `os.cpu_count()`

    Returns
    -------
    None

    Raises
    ------
    UnorderedQuerySetError
        Raised when unordered queryset is ordered by `primary_key` and
        throws a `FieldError` or `TypeError`.
    """

    from django_atlas_search.mixins import AtlasSearchQuerySet

    if not isinstance(records_queryset, AtlasSearchQuerySet):
        logger.error(
            f"The objects for {records_queryset.model.__name__} does not use AtlasSearchQuerySet "
            f"as it's manager. Please update the model manager for the class to use AtlasSearch."
        )
        return

    if not records_queryset.ordered:
        try:
            records_queryset = records_queryset.order_by("pk")
        except (FieldError, TypeError):
            raise UnorderedQuerySetError(
                "Pagination may yield inconsistent results with an unordered object_list. "
                "Please provide an ordered objects."
            )

    collection_class = records_queryset.model.search_index_class
    paginator = Paginator(records_queryset, batch_size)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = []
        for page_no in paginator.page_range:
            documents_queryset = paginator.page(page_no).object_list
            logger.debug(f"Updating batch {page_no} of {paginator.num_pages}")
            future = executor.submit(
                update_batch, documents_queryset, collection_class, page_no
            )
            futures.append(future)

        for future in concurrent.futures.as_completed(futures):
            future.result()


def bulk_delete_atlas_records(document_ids: list, database_name: str, collection_name: str) -> None:
    """This method deletes Atlas Search records for objects .delete() calls
    from AtlasSearchModelMixin subclasses.

    Parameters
    ----------
    document_ids : list
        The list of document IDs to be deleted.
    database_name : str
        The database name containing the collection.
    collection_name : str
        The collection name to delete the documents from.

    Returns
    -------
    None
    """

    try:
        db = client[database_name]
        collection = db[collection_name]

        # Convert string IDs to ObjectId if needed
        object_ids = []
        for doc_id in document_ids:
            if isinstance(doc_id, str):
                try:
                    object_ids.append(ObjectId(doc_id))
                except Exception:
                    object_ids.append(doc_id)
            else:
                object_ids.append(doc_id)

        result = collection.delete_many({"_id": {"$in": object_ids}})
        logger.info(f"Deleted {result.deleted_count} documents from {collection_name}")
    except PyMongoError as error:
        logger.error(
            f"Could not delete the documents IDs {document_ids}\nError: {error}"
        )


def get_unix_timestamp(datetime_object) -> int:
    """Get the unix timestamp from a datetime object with the time part set to midnight

    Parameters
    ----------
    datetime_object: date, datetime, time

    Returns
    -------
    timestamp : int
        Returns the datetime object timestamp.

    Raises
    ------
    TypeError
        Raised when a non datetime parameter is passed.
    """

    # isinstance can take a union type but we call it multiple times for clarity
    if isinstance(datetime_object, datetime):
        timestamp = int(datetime_object.timestamp())

    elif isinstance(datetime_object, date):
        timestamp = int(
            datetime.combine(datetime_object, datetime.min.time()).timestamp()
        )

    elif isinstance(datetime_object, time):
        timestamp = int(datetime.combine(datetime.today(), datetime_object).timestamp())

    else:
        raise TypeError(
            f"Expected a date/datetime/time objects but got {datetime_object} of type {type(datetime_object)}"
        )

    return timestamp


def export_documents(
        database_name: str,
        collection_name: str,
        filter_query: dict = None,
        projection: dict = None,
) -> List[dict]:
    """Export documents from MongoDB collection.

    Parameters
    ----------
    database_name : str
        The database name containing the collection.
    collection_name : str
        The collection name to export documents from.
    filter_query : dict, optional
        MongoDB filter query (e.g., {"field": "value"}).
    projection : dict, optional
        MongoDB projection to include/exclude fields (e.g., {"field1": 1, "field2": 0}).

    Returns
    -------
    List[dict]
        List of documents as dictionaries.
    """
    try:
        db = client[database_name]
        collection = db[collection_name]

        find_params = {}
        if filter_query:
            find_params["filter"] = filter_query
        if projection:
            find_params["projection"] = projection

        cursor = collection.find(**find_params)
        return list(cursor)
    except PyMongoError as error:
        logger.error(f"Could not export documents from {collection_name}\nError: {error}")
        raise


def atlas_search(
        database_name: str,
        collection_name: str,
        index_name: str,
        q: str = "*",
        query_by: str = "",
        page: int = 1,
        per_page: int = 10,
        filter_by: str = "",
        sort_by: str = "",
        **kwargs
) -> Dict[str, Any]:
    """
    Perform an Atlas Search query using MongoDB aggregation pipeline.

    Parameters
    ----------
    database_name : str
        The database name containing the collection.
    collection_name : str
        The collection name to search.
    index_name : str
        The Atlas Search index name to use.
    q : str
        The search query string. Use "*" for match all.
    query_by : str
        Comma-separated list of fields to search by.
    page : int
        Page number (1-indexed).
    per_page : int
        Number of results per page.
    filter_by : str
        Filter string in format "field:value && field2:value2" (will be converted to MongoDB query).
    sort_by : str
        Sort string in format "field:asc,field2:desc" (will be converted to MongoDB sort).
    **kwargs
        Additional search parameters.

    Returns
    -------
    dict
        Search results in the following format:
        {
            "hits": [{"document": {...}, "highlights": [...]}, ...],
            "found": int,
            "page": int,
            "search_time_ms": float
        }
    """
    start_time = t_time.perf_counter()

    try:
        db = client[database_name]
        collection = db[collection_name]

        # Build the aggregation pipeline
        pipeline = []

        # $search stage - Atlas Search query (only if we have a search query)
        if q and q != "*":
            search_stage = {
                "$search": {
                    "index": index_name,
                }
            }

            query_by_fields = [f.strip() for f in query_by.split(",") if f.strip()]
            if query_by_fields:
                # Multi-field search - path should be an array for multiple fields
                if len(query_by_fields) == 1:
                    search_stage["$search"]["text"] = {
                        "query": q,
                        "path": query_by_fields[0]
                    }
                else:
                    # For multiple fields, use path as array
                    search_stage["$search"]["text"] = {
                        "query": q,
                        "path": query_by_fields
                    }
            else:
                # Default: search all text fields using wildcard
                search_stage["$search"]["text"] = {
                    "query": q,
                    "path": {"wildcard": "*"}
                }

            pipeline.append(search_stage)
        else:
            # Match all - start with empty $match
            pipeline.append({"$match": {}})

        # Parse and add filters
        if filter_by:
            filter_dict = _parse_filter_string(filter_by)
            if filter_dict:
                pipeline.append({"$match": filter_dict})

        # Add facet stage if requested (for future use)
        # Facets would go here

        # Add sort stage
        if sort_by:
            sort_dict = _parse_sort_string(sort_by)
            if sort_dict:
                pipeline.append({"$sort": sort_dict})
        elif q and q != "*":
            # Default sort by relevance score (descending) only if we have a search query
            # Atlas Search $search automatically provides a score field
            pipeline.append({"$sort": {"score": -1}})

        # Add pagination
        skip = (page - 1) * per_page
        pipeline.append({"$skip": skip})
        pipeline.append({"$limit": per_page})

        # Add project stage to include score (only if we have a search query)
        # Atlas Search $search automatically provides a score field, so we just preserve it
        if q and q != "*":
            pipeline.append({
                "$project": {
                    "score": 1,  # Preserve the score field from $search
                    "document": "$$ROOT"
                }
            })
        else:
            pipeline.append({
                "$project": {
                    "score": {"$literal": 0},  # No search query, so no score
                    "document": "$$ROOT"
                }
            })

        # Execute aggregation
        results = list(collection.aggregate(pipeline))

        # Get total count (separate aggregation for performance)
        count_pipeline = pipeline[:-3]  # Remove skip, limit, and project stages
        count_pipeline.append({"$count": "total"})
        count_result = list(collection.aggregate(count_pipeline))
        total_found = count_result[0]["total"] if count_result else 0

        # Format results to match expected structure
        hits = []
        for result in results:
            doc = result.get("document", result)
            # Remove MongoDB _id and convert to string if needed
            if "_id" in doc and isinstance(doc["_id"], ObjectId):
                doc["_id"] = str(doc["_id"])
            # Also ensure "id" field exists (for Django model compatibility)
            if "_id" in doc and "id" not in doc:
                doc["id"] = str(doc["_id"])

            hits.append({
                "document": doc,
                "highlights": [],  # TODO: Implement highlighting
                "score": result.get("score", 0)
            })

        search_time_ms = (t_time.perf_counter() - start_time)

        return {
            "hits": hits,
            "found": total_found,
            "page": page,
            "search_time_ms": search_time_ms
        }

    except PyMongoError as error:
        logger.error(f"Atlas Search query failed: {error}")
        raise
    except Exception as error:
        logger.error(f"Unexpected error during Atlas Search: {error}")
        raise


def _parse_filter_string(filter_by: str) -> dict:
    """Parse filter string like 'field:value && field2:value2' into MongoDB query dict."""
    if not filter_by:
        return {}

    query = {}
    # Split by && for AND conditions
    conditions = [c.strip() for c in filter_by.split("&&")]

    for condition in conditions:
        if ":" not in condition:
            continue

        parts = condition.split(":", 1)
        field = parts[0].strip()
        value_str = parts[1].strip()

        # Parse operators and values
        if value_str.startswith(">="):
            value = value_str[2:].strip()
            query[field] = {"$gte": _parse_value(value)}
        elif value_str.startswith("<="):
            value = value_str[2:].strip()
            query[field] = {"$lte": _parse_value(value)}
        elif value_str.startswith(">"):
            value = value_str[1:].strip()
            query[field] = {"$gt": _parse_value(value)}
        elif value_str.startswith("<"):
            value = value_str[1:].strip()
            query[field] = {"$lt": _parse_value(value)}
        elif value_str.startswith("!="):
            value = value_str[2:].strip()
            query[field] = {"$ne": _parse_value(value)}
        elif value_str.startswith("="):
            value = value_str[1:].strip()
            query[field] = _parse_value(value)
        else:
            # Default: exact match
            query[field] = _parse_value(value_str)

    return query


def _parse_sort_string(sort_by: str) -> dict:
    """Parse sort string like 'field:asc,field2:desc' into MongoDB sort dict."""
    if not sort_by:
        return {}

    sort_dict = {}
    sorts = [s.strip() for s in sort_by.split(",")]

    for sort_expr in sorts:
        if ":" not in sort_expr:
            continue

        parts = sort_expr.split(":", 1)
        field = parts[0].strip()
        direction = parts[1].strip().lower()

        sort_dict[field] = 1 if direction == "asc" else -1

    return sort_dict


def _parse_value(value_str: str):
    """Parse a string value into appropriate Python type."""
    value_str = value_str.strip()

    # Boolean
    if value_str.lower() in ("true", "false"):
        return value_str.lower() == "true"

    # Number
    try:
        if "." in value_str:
            return float(value_str)
        return int(value_str)
    except ValueError:
        pass

    # String (remove quotes if present)
    if (value_str.startswith('"') and value_str.endswith('"')) or \
            (value_str.startswith("'") and value_str.endswith("'")):
        return value_str[1:-1]

    return value_str

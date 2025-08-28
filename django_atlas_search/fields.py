import json
from decimal import Decimal
from datetime import datetime, date, time
from typing import Optional
from operator import attrgetter

from bson import ObjectId

from django_atlas_search.utils import get_unix_timestamp


class AtlasSearchField:
    """
    Base field class for Atlas Search collections
    """
    _field_type = "string"
    _searchable = True

    def __init__(
            self,
            value: Optional[str] = None,
            searchable: bool = None,
            facetable: bool = False,
            analyzer: str = "lucene.standard",
            index_analyzer: str = None,
            search_analyzer: str = None,
            multi: bool = False,
            store: bool = False,
            highlight: bool = False,
            optional: bool = False,
            **kwargs
    ):
        self._value = value
        self._name = None
        self.searchable = self._searchable if searchable is None else searchable
        self.facetable = facetable
        self.analyzer = analyzer
        self.index_analyzer = index_analyzer
        self.search_analyzer = search_analyzer
        self.multi = multi
        self.store = store
        self.highlight = highlight
        self.optional = optional
        self.extra_options = kwargs

    def __str__(self):
        return f"{self.name}"

    @property
    def field_type(self):
        return self._field_type

    @property
    def name(self):
        return self._name

    @property
    def attrs(self):
        """Return field attributes for Atlas Search mapping"""
        return self.get_atlas_mapping()

    def value(self, obj):
        """Extract field value from an object"""
        try:
            __value = attrgetter(self._value)(obj)
        except AttributeError as er:
            if self.optional:
                __value = None
            else:
                raise er

        if callable(__value):
            return __value()

        return __value

    def to_python(self, value):
        """Convert value to Python object"""
        return value

    def get_atlas_mapping(self) -> dict:
        """
        Get the Atlas Search field mapping configuration
        """
        if not self.searchable:
            return None

        mapping = {"type": self.field_type}

        # Add analyzer configuration for text fields
        if self.field_type == "string":
            mapping["analyzer"] = self.analyzer
            if self.index_analyzer:
                mapping["indexAnalyzer"] = self.index_analyzer
            if self.search_analyzer:
                mapping["searchAnalyzer"] = self.search_analyzer

        # Handle facetable fields
        if self.facetable:
            if self.field_type == "string":
                mapping["type"] = "stringFacet"
            elif self.field_type in ["number", "date"]:
                mapping["type"] = f"{self.field_type}Facet"

        # Add multi-field support
        if self.multi:
            mapping["multi"] = {"keyword": {"type": "keyword"}}

        # Add highlight support
        if self.highlight:
            mapping["highlight"] = {"enabled": True}

        # Store field values
        if self.store:
            mapping["store"] = True

        # Add any extra options
        mapping.update(self.extra_options)

        return mapping


class AtlasSearchCharField(AtlasSearchField):
    """String field for Atlas Search"""
    _field_type = "string"

    def value(self, obj):
        __value = super().value(obj)
        if isinstance(__value, str):
            return __value
        if __value is None:
            return "" if not self.optional else None
        return str(__value)


class AtlasSearchTextField(AtlasSearchCharField):
    """Text field optimized for full-text search"""

    def __init__(self, analyzer: str = "lucene.standard", **kwargs):
        super().__init__(analyzer=analyzer, **kwargs)


class AtlasSearchKeywordField(AtlasSearchCharField):
    """Keyword field for exact matching"""

    def __init__(self, **kwargs):
        super().__init__(analyzer="lucene.keyword", **kwargs)


class AtlasSearchIntegerMixin(AtlasSearchField):
    """Mixin for integer fields"""

    def value(self, obj):
        _value = super().value(obj)

        if _value is None:
            return None
        try:
            return int(_value)
        except (TypeError, ValueError) as e:
            raise e.__class__(
                f"Field '{self.name}' expected a number but got {_value}.",
            ) from e

    def to_python(self, value):
        return int(value) if value is not None else None


class AtlasSearchIntegerField(AtlasSearchIntegerMixin):
    """32-bit integer field"""
    _field_type = "number"


class AtlasSearchLongField(AtlasSearchIntegerMixin):
    """64-bit integer field"""
    _field_type = "number"


class AtlasSearchFloatField(AtlasSearchField):
    """Floating point number field"""
    _field_type = "number"

    def value(self, obj):
        _value = super().value(obj)
        if _value is None:
            return None
        try:
            return float(_value)
        except (TypeError, ValueError) as e:
            raise e.__class__(
                f"Field '{self.name}' expected a float but got {_value}.",
            ) from e

    def to_python(self, value):
        return float(value) if value is not None else None


class AtlasSearchDecimalField(AtlasSearchField):
    """
    Decimal field stored as string for precision
    """
    _field_type = "string"

    def value(self, obj):
        __value = super().value(obj)
        return str(__value) if __value is not None else None

    def to_python(self, value):
        return Decimal(value) if value is not None else None


class AtlasSearchBooleanField(AtlasSearchField):
    """Boolean field"""
    _field_type = "boolean"

    def value(self, obj):
        _value = super().value(obj)
        return bool(_value) if _value is not None else None

    def to_python(self, value):
        return bool(value) if value is not None else None


class AtlasSearchDateTimeFieldBase(AtlasSearchField):
    """Base class for date/time fields"""
    _field_type = "date"

    def value(self, obj):
        _value = super().value(obj)

        if _value is None:
            return None

        # MongoDB/BSON handles datetime objects natively
        if isinstance(_value, (datetime, date)):
            return _value

        # If it's a Unix timestamp in seconds, convert to datetime
        if isinstance(_value, (int, float)):
            # Check if it's likely milliseconds (BSON format) or seconds
            if _value > 1e10:  # Likely milliseconds (after year 2001)
                return datetime.fromtimestamp(_value / 1000.0)
            else:  # Likely seconds
                return datetime.fromtimestamp(_value)

        return _value

    def to_python(self, value):
        """Convert BSON date back to Python datetime"""
        if isinstance(value, datetime):
            return value
        elif isinstance(value, (int, float)):
            # BSON dates are stored as milliseconds since epoch
            if value > 1e10:  # Likely milliseconds
                return datetime.fromtimestamp(value / 1000.0)
            else:  # Likely seconds
                return datetime.fromtimestamp(value)
        return value


class AtlasSearchDateField(AtlasSearchDateTimeFieldBase):
    """Date field"""

    def to_python(self, value):
        dt = super().to_python(value)
        return dt.date() if isinstance(dt, datetime) else value


class AtlasSearchDateTimeField(AtlasSearchDateTimeFieldBase):
    """DateTime field"""
    pass


class AtlasSearchTimeField(AtlasSearchDateTimeFieldBase):
    """Time field"""

    def to_python(self, value):
        dt = super().to_python(value)
        return dt.time() if isinstance(dt, datetime) else value


class AtlasSearchJSONField(AtlasSearchField):
    """
    JSON field stored as string
    """
    _field_type = "string"

    def value(self, obj):
        __value = super().value(obj)
        if __value is None:
            return None
        return json.dumps(__value, default=str)

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value


class AtlasSearchObjectIdField(AtlasSearchField):
    """MongoDB ObjectId field"""
    _field_type = "objectId"
    _searchable = False

    def value(self, obj):
        _value = super().value(obj)
        if isinstance(_value, ObjectId):
            return _value
        elif isinstance(_value, str):
            return ObjectId(_value)
        return _value

    def to_python(self, value):
        if isinstance(value, ObjectId):
            return value
        elif isinstance(value, str):
            return ObjectId(value)
        return value


class AtlasSearchArrayField(AtlasSearchField):
    """Array field containing elements of a specific type"""

    def __init__(self, base_field: AtlasSearchField, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_field = base_field
        # Atlas Search handles arrays automatically for most field types
        self._field_type = self.base_field._field_type

    def value(self, obj):
        __value = super().value(obj)
        if __value is None:
            return None
        if not isinstance(__value, (list, tuple)):
            return [__value]
        return list(__value)

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [self.base_field.to_python(item) for item in value]
        return [self.base_field.to_python(value)]

    def get_atlas_mapping(self) -> dict:
        """Array fields use the base field mapping in Atlas Search"""
        if not self.searchable:
            return None
        return self.base_field.get_atlas_mapping()


class AtlasSearchFacetField(AtlasSearchField):
    """Field specifically for faceted search"""

    def __init__(self, field_type: str = "string", **kwargs):
        kwargs['facetable'] = True
        super().__init__(**kwargs)
        if field_type == "string":
            self._field_type = "stringFacet"
        elif field_type in ["number", "date"]:
            self._field_type = f"{field_type}Facet"
        else:
            self._field_type = field_type


class AtlasSearchGeoPointField(AtlasSearchField):
    """Geospatial point field for location-based search"""
    _field_type = "geo"

    def value(self, obj):
        _value = super().value(obj)
        if _value is None:
            return None

        # Expected format: {"type": "Point", "coordinates": [longitude, latitude]}
        if isinstance(_value, dict) and _value.get("type") == "Point":
            return _value
        elif isinstance(_value, (list, tuple)) and len(_value) == 2:
            return {
                "type": "Point",
                "coordinates": [float(_value[0]), float(_value[1])]
            }
        return _value

    def get_atlas_mapping(self) -> dict:
        if not self.searchable:
            return None
        return {"type": "geo"}


# Field type mappings for easy reference
ATLAS_SEARCH_DATETIME_FIELDS = [
    AtlasSearchDateTimeField,
    AtlasSearchDateField,
    AtlasSearchTimeField,
]

ATLAS_SEARCH_NUMBER_FIELDS = [
    AtlasSearchIntegerField,
    AtlasSearchLongField,
    AtlasSearchFloatField,
]

ATLAS_SEARCH_TEXT_FIELDS = [
    AtlasSearchCharField,
    AtlasSearchTextField,
    AtlasSearchKeywordField,
]
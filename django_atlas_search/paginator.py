import copy

from django.core.paginator import Paginator
from django.utils.functional import cached_property


class AtlasSearchPaginator(Paginator):
    def __init__(
        self, object_list, per_page, orphans=0, allow_empty_first_page=True, model=None
    ):
        super().__init__(object_list, per_page, orphans, allow_empty_first_page)
        self.model = model
        self.search_index_class = self.model.get_search_index_class()
        self.results = self.prepare_results()

    def prepare_results(self):
        """
        Do whatever is required to present the values correctly in the admin.
        """
        # Handle both old format (list of dicts) and new format (dict with 'hits')
        if isinstance(self.object_list, dict) and "hits" in self.object_list:
            documents = [hit.get('document', hit) for hit in self.object_list["hits"]]
        else:
            # Fallback for direct list of documents
            documents = self.object_list if isinstance(self.object_list, list) else []
        
        if not documents:
            return []
        
        search_index = self.model.get_search_index(data=documents, many=True)
        model_field_names = set((local_field.name for local_field in self.model._meta.local_fields))
        results = []

        for _data in search_index.validated_data:
            data = copy.deepcopy(_data)
            properties = {}

            for field_name in search_index.fields.keys():
                if field_name not in model_field_names:
                    try:
                        field_val = data.pop(field_name)
                    except KeyError:
                        pass
                    else:
                        properties[field_name] = field_val

            result_instance = self.model(**data)
            for key, value in properties.items():
                try:
                    setattr(result_instance, key, value)
                except AttributeError:
                    # non-data descriptors
                    result_instance.__dict__[key] = value

            results.append(result_instance)

        return results

    def page(self, number):
        """Return a Page object for the given 1-based page number."""
        number = self.validate_number(number)
        bottom = (number - 1) * self.per_page
        top = bottom + self.per_page
        if top + self.orphans >= self.count:
            top = self.count
        return self._get_page(self.results[bottom:top], number, self)

    @cached_property
    def count(self):
        """Return the total number of objects, across all pages."""
        if isinstance(self.object_list, dict) and "found" in self.object_list:
            return self.object_list["found"]
        elif isinstance(self.object_list, list):
            return len(self.object_list)
        else:
            return 0

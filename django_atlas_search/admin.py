import logging

from django.contrib import admin
from django.contrib.auth.admin import csrf_protect_m
from django.db.models import QuerySet
from django.forms import forms
from django.http import JsonResponse

from django_atlas_search.changelist import AtlasSearchChangeList
from django_atlas_search.mixins import AtlasSearchModelMixin
from django_atlas_search.paginator import AtlasSearchPaginator
from django_atlas_search.utils import atlas_search

logger = logging.getLogger(__name__)


class AtlasSearchAdminMixin(admin.ModelAdmin):
    atlas_search_fields = []

    def get_atlas_search_fields(self, request):
        """
        Return a sequence containing the fields to be searched whenever
        somebody submits a search query.
        """
        return self.atlas_search_fields

    @property
    def media(self):
        super_media = super().media
        return forms.Media(
            js=super_media._js + ["admin/js/search-atlas.js"],
            css=super_media._css,
        )

    @csrf_protect_m
    def changelist_view(self, request, extra_context=None):
        """
        The 'change list' admin view for this model.
        """
        template_response = super().changelist_view(request, extra_context)

        is_ajax = request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        if is_ajax:
            html = template_response.render().rendered_content
            return JsonResponse(data={"html": html}, safe=False)

        return template_response

    def get_sortable_by(self, request):
        """
        Get sortable fields; these are fields that sort is defaulted or set to True.

        Args:
            request: the HttpRequest

        Returns:
            A list of field names
        """

        sortable_fields = super().get_sortable_by(request)
        return set(sortable_fields).intersection(
            self.model.search_index_class.sortable_fields
        )

    def get_results(self, request):
        """
        Get all indexed data without any filtering or specific search terms. Works like `ModelAdmin.get_queryset()`

        Args:
            request: the HttpRequest

        Returns:
            A dictionary with 'hits' and 'found' keys
        """
        search_index_class = self.model.search_index_class
        # Get all documents (match all query)
        results = atlas_search(
            database_name=search_index_class.database_name,
            collection_name=search_index_class.collection_name,
            index_name=search_index_class.index_name,
            q="*",
            query_by="",
            page=1,
            per_page=1  # We only need the count
        )
        return results

    def get_changelist(self, request, **kwargs):
        """
        Return the ChangeList class for use on the changelist page.
        """
        return AtlasSearchChangeList

    def get_paginator(
        self, request, results, per_page, orphans=0, allow_empty_first_page=True
    ):
        # fallback incase we receive a queryset.
        if isinstance(results, QuerySet):
            return super().get_paginator(
                request, results, per_page, orphans, allow_empty_first_page
            )

        return AtlasSearchPaginator(
            results, per_page, orphans, allow_empty_first_page, self.model
        )

    def get_atlas_search_results(
            self,
            request,
            search_term: str,
            page_num: int = 1,
            filter_by: str = "",
            sort_by: str = "",
            list_per_page: int = None
    ):
        """
        Get the results from Atlas Search with the provided filtering, sorting, pagination and search parameters applied

        Args:
            search_term: The search term provided in the search form
            request: the current request object
            page_num: The requested page number
            filter_by: The filtering parameters
            sort_by: The sort parameters
            list_per_page: The number of results to return per page

        Returns:
            A dictionary with 'hits' and 'found' keys
        """
        if list_per_page is None:
            list_per_page = self.list_per_page

        search_index_class = self.model.search_index_class
        results = atlas_search(
            database_name=search_index_class.database_name,
            collection_name=search_index_class.collection_name,
            index_name=search_index_class.index_name,
            q=search_term or "*",
            query_by=search_index_class.query_by_fields or "",
            page=page_num,
            per_page=list_per_page,
            filter_by=filter_by,
            sort_by=sort_by,
        )
        return results

    def get_search_results(self, request, queryset, search_term):
        may_have_duplicates = False

        if search_term:
            # Always filter by Atlas Search results when a search term is present.
            # This applies to both normal display and action submissions, ensuring
            # admin actions receive a queryset scoped to the current search results
            # rather than the full table.
            results = self.get_atlas_search_results(request, search_term)
            ids = [result["document"]["id"] for result in results["hits"]]
            queryset = queryset.filter(id__in=ids)

        return queryset, may_have_duplicates

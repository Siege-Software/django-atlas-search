from django.db import models


class AtlasSearchQuerySet(models.QuerySet):
    def delete(self):
        assert issubclass(self.model, AtlasSearchModelMixin), (
            f"Model `{self.model}` must inherit `AtlasSearchMixin` to use the AtlasSearchQueryset Manager"
        )
        search_index = self.model.get_search_index(self, many=True)
        search_index.delete()
        return super().delete()

    def update(self, **kwargs):
        assert issubclass(self.model, AtlasSearchModelMixin), (
            f"Model `{self.model}` must inherit `AtlasSearchMixin` to use the AtlasSearchQueryset Manager"
        )
        obj_ids = list(self.values_list('id', flat=True))
        update_result = super().update(**kwargs)
        queryset = self.model.objects.filter(id__in=obj_ids)
        search_index = self.model.get_search_index(queryset, many=True, update_fields=kwargs.keys())
        search_index.update()
        return update_result


class AtlasSearchManager(models.Manager):
    def get_queryset(self):
        return AtlasSearchQuerySet(self.model, using=self._db)


class AtlasSearchModelMixin(models.Model):
    search_index_class = None
    objects = AtlasSearchQuerySet.as_manager()

    class Meta:
        abstract = True

    @classmethod
    def get_search_index_class(cls):
        """
        Return the class to use for the search index.
        Defaults to using `self.search_index_class`.
        """
        assert cls.search_index_class is not None, (
            "'%s' should either include a `search_index_class` attribute, "
            "or override the `get_search_index_class()` method."
            % cls.__name__
        )

        return cls.search_index_class

    @classmethod
    def get_search_index(cls, *args, **kwargs):
        """
        Return the search index obj.
        """
        search_index_class = cls.get_search_index_class()
        return search_index_class(*args, **kwargs)


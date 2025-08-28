from django.db import transaction
from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.dispatch import receiver

from django_atlas_search.mixins import AtlasSearchModelMixin


@receiver(post_save)
def post_save_atlas_search_models(sender, instance, **kwargs):
    if not issubclass(sender, AtlasSearchModelMixin):
        return

    transaction.on_commit(
        sender.get_search_index(instance, update_fields=kwargs.get('update_fields', [])).update
    )


@receiver(pre_delete)
def pre_delete_atlas_search_models(sender, instance, **kwargs):
    if not issubclass(sender, AtlasSearchModelMixin):
        return

    sender.get_search_index(instance).delete()


@receiver(m2m_changed)
def m2m_changed_atlas_search_models(instance, model, action, **kwargs):
    if action in ["post_add", "post_remove", "post_clear"]:
        if isinstance(instance, AtlasSearchModelMixin):
            instance_class = instance.__class__
            instance_class.get_search_index(instance).update()

        if issubclass(model, AtlasSearchModelMixin):
            pk_set = list(kwargs.get("pk_set"))
            obj = model.objects.filter(pk__in=pk_set)
            model.get_search_index(obj=obj, many=True).update()

import sys

from django.core.management import BaseCommand
from django.apps import apps


class Command(BaseCommand):
    help = "Create and/or Update Atlas Collections"

    def add_arguments(self, parser):
        parser.add_argument(
            "args",
            metavar="collection_name",
            nargs="*",
            help="Specify the collection schema name(s) to create or update.",
        )

    def handle(self, *collection_names, **options):
        collections = {}
        for model_data in apps.all_models.values():
            for model in model_data.values():
                if hasattr(model, 'search_index_class'):
                    search_index_class = model.search_index_class
                    schema_name = search_index_class.schema_name or search_index_class.collection_name
                    if schema_name:
                        collections[schema_name] = search_index_class

        collections_for_action = []
        # Make sure the collection name(s) they asked for exists
        if collection_names := set(collection_names):
            has_bad_names = False

            for collection_name in collection_names:
                try:
                    collection = collections[collection_name]
                except KeyError:
                    self.stderr.write(f"No collection exists with schema name '{collection_name}'")
                    has_bad_names = True
                else:
                    collections_for_action.append(collection)

            if has_bad_names:
                sys.exit(2)
        else:
            collections_for_action = collections.values()
        
        # Track results
        created_count = 0
        updated_count = 0
        unchanged_count = 0
        
        for collection_class in collections_for_action:
            index_name = collection_class.index_name
            collection_name = collection_class.collection_name or collection_class.schema_name
            # Instantiate with required parameters from class attributes
            instance = collection_class(
                database_name=collection_class.database_name,
                collection_name=collection_name,
                index_name=index_name
            )
            result = instance.update_atlas_collection()
            
            # Print success message for each collection
            if result == "created":
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Successfully created search index '{index_name}' "
                        f"for collection '{collection_name}'"
                    )
                )
                created_count += 1
            elif result == "updated":
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Successfully updated search index '{index_name}' "
                        f"for collection '{collection_name}'"
                    )
                )
                updated_count += 1
            elif result == "unchanged":
                self.stdout.write(
                    f"○ Search index '{index_name}' "
                    f"for collection '{collection_name}' is already up to date"
                )
                unchanged_count += 1
        
        # Print summary
        if created_count == 0 and updated_count == 0:
            if unchanged_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\nSummary: All {unchanged_count} collection(s) are already up to date. "
                        f"No changes were needed."
                    )
                )
            else:
                self.stdout.write(self.style.WARNING("No collections were added or updated."))
        else:
            total = created_count + updated_count
            summary_parts = []
            if created_count > 0:
                summary_parts.append(f"{created_count} created")
            if updated_count > 0:
                summary_parts.append(f"{updated_count} updated")
            if unchanged_count > 0:
                summary_parts.append(f"{unchanged_count} unchanged")
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nSummary: {total + unchanged_count} collection(s) processed ({', '.join(summary_parts)})"
                )
            )

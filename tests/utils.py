from bson import ObjectId
from pymongo.errors import PyMongoError

from django_atlas_search.atlas_client import client


def get_document(database_name: str, collection_name: str, document_id):
    """
    Retrieve a document from MongoDB collection by ID.
    
    Args:
        database_name: The database name
        collection_name: The collection name
        document_id: The document ID (can be int, str, or ObjectId)
    
    Returns:
        The document dict or None if not found
    """
    try:
        db = client[database_name]
        collection = db[collection_name]
        
        # Convert document_id to ObjectId if needed
        if isinstance(document_id, (int, str)):
            try:
                document_id = ObjectId(str(document_id))
            except Exception:
                # If conversion fails, try as string
                pass
        
        document = collection.find_one({"_id": document_id})
        return document
    except PyMongoError:
        return None

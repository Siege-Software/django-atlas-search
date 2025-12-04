import pymongo
from django.conf import settings

client = pymongo.MongoClient(settings.ATLAS_CONNECTION_SECRET_STRING)

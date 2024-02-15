import os
from dotenv import load_dotenv
from pymongo import MongoClient, errors
load_dotenv('config.env', override=True)

DATABASE_URL = os.getenv('DATABASE_URL')
if len(DATABASE_URL) == 0:
    DATABASE_URL = ''

usersettings_collection = None

if DATABASE_URL:
    conndb = MongoClient(DATABASE_URL)
    dbmongo = conndb.pachax001
    try:
        # The ismaster command is cheap and does not require auth.
        conndb.admin.command('ismaster')
        print("MongoDB connection successful")
        usersettings_collection = dbmongo.usersettings
        filter = {}  # replace this with the filter that matches the document
        update = {"$setOnInsert": {"caption": None, "thumbnail": None}}
        usersettings_collection.update_one(filter, update, upsert=True)
    except errors.ConnectionFailure:
        print("MongoDB connection unsuccessful")

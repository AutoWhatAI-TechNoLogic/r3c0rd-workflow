from contextlib import contextmanager
from pymongo import MongoClient
from app.config import Config
import logging

@contextmanager
def get_db_collection(collection_name=None):
    """
    Provides a MongoDB collection within a context manager.
    Handles connection opening and closing for each use.
    This is designed to work with free/shared tiers of
    MongoDB Atlas that have strict connection limits.
    """
    client = None
    try:
        # Establish a new connection
        client = MongoClient(
            Config.MONGODB_URI,
            serverSelectionTimeoutMS=Config.MONGODB_TIMEOUT,
            connectTimeoutMS=10000,  # Give 10s to connect
            socketTimeoutMS=30000
        )
        
        # Verify connection
        client.admin.command('ping')
        
        db = client[Config.DB_NAME]
        collection = db[collection_name or Config.COLLECTION_NAME]
        
        logging.info(f"MongoDB connection opened to {Config.DB_NAME}.{collection.name}")
        yield collection
    
    except Exception as e:
        logging.error(f"MongoDB connection failed: {e}")
        yield None  # Propagate failure
    
    finally:
        if client:
            client.close()
            logging.info("MongoDB connection closed.")
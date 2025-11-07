import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    PINECONE_API_KEY = os.getenv('PINECONE_API_KEY')
    PINECONE_ENVIRONMENT = os.getenv('PINECONE_ENVIRONMENT', 'us-east-1')
    PINECONE_INDEX_NAME = os.getenv('PINECONE_INDEX_NAME', 'workflow-embeddings')
    MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
    DB_NAME = os.getenv('MONGODB_DB_NAME', 'workflow_db')
    COLLECTION_NAME = os.getenv('MONGODB_COLLECTION', 'enhanced_workflows')
    MAX_WORKERS = 10
    MONGODB_TIMEOUT = 5000  # Server selection timeout
    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSION = 1536
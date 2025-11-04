from pinecone import Pinecone, ServerlessSpec
from app.config import Config
import logging

class PineconeManager:
    _instance = None
    _pc = None
    _index = None
    _connected = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize Pinecone connection"""
        if self._pc is not None:
            return
        
        try:
            logging.info("🔄 Initializing Pinecone connection...")
            self._pc = Pinecone(api_key=Config.PINECONE_API_KEY)
            
            existing_indexes = [index.name for index in self._pc.list_indexes()]
            
            if Config.PINECONE_INDEX_NAME not in existing_indexes:
                logging.info(f"📝 Creating Pinecone index: {Config.PINECONE_INDEX_NAME}")
                self._pc.create_index(
                    name=Config.PINECONE_INDEX_NAME,
                    dimension=Config.EMBEDDING_DIMENSION,
                    metric='cosine',
                    spec=ServerlessSpec(
                        cloud='aws',
                        region=Config.PINECONE_ENVIRONMENT
                    )
                )
            
            self._index = self._pc.Index(Config.PINECONE_INDEX_NAME)
            self._connected = True
            logging.info("✅ Pinecone connection successful")
        except Exception as e:
            logging.warning(f"⚠️ Pinecone connection failed: {e}")
            self._connected = False
            self._index = None
    
    def get_index(self):
        """Get Pinecone index"""
        if not self._connected or self._index is None:
            self.initialize()
        return self._index
    
    @property
    def is_connected(self):
        return self._connected

# Initialize the singleton instance
pinecone_manager = PineconeManager()
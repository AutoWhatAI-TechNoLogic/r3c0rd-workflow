from flask import Flask
from flask_cors import CORS
from app.config import Config
from app.vector_db import pinecone_manager
from app.utils import executor
import logging

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Enable CORS
    CORS(app)
    
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Register blueprints
    from app.routes import main_bp
    app.register_blueprint(main_bp)

    # Initialize global singletons (like Pinecone)
    # We use before_request to do this on the first request
    # in a non-blocking way.
    
    @app.before_request
    def initialize_connections():
        """Initialize connections on first request"""
        if not pinecone_manager.is_connected:
            # Submit initialization to the thread pool
            executor.submit(pinecone_manager.initialize)
        # Note: We DO NOT initialize Mongo here. It's done on-demand.
    
    @app.teardown_appcontext
    def cleanup(error=None):
        """Cleanup resources"""
        # No DB cleanup needed, context manager in app/db.py handles it.
        pass

    return app
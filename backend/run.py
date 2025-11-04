import os
import logging
from app import create_app
from app.config import Config

# Create the Flask app using the factory
app = create_app()

if __name__ == '__main__':
    logging.info("🚀 Starting Flask server...")
    logging.info(f"📁 Database: {Config.DB_NAME}")
    logging.info(f"📊 Collection: {Config.COLLECTION_NAME}")
    logging.info(f"✅ OpenAI: {'Configured' if Config.OPENAI_API_KEY else 'Missing'}")
    logging.info(f"🌲 Pinecone: {'Configured' if Config.PINECONE_API_KEY else 'Missing'}")
    
    # --- CHANGED PORT TO 5001 ---
    app.run(host='127.0.0.1', debug=True, port=5000, threaded=True)


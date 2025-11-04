from concurrent.futures import ThreadPoolExecutor
from app.config import Config

# Global thread pool executor
executor = ThreadPoolExecutor(max_workers=Config.MAX_WORKERS)
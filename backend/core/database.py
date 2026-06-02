from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

# Get Database URL from environment variable. The fallback is a local-only
# placeholder for development and must not be used for deployment secrets.
DEFAULT_DB_URL = "postgresql://postgres:postgres@localhost:5432/aicareer_bridge"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

# The Engine establishes the physical connection to the database
engine = create_engine(DATABASE_URL, echo=False)

# The Session is what FastAPI will use to execute queries
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Modern SQLAlchemy 2.0 Base class
class Base(DeclarativeBase):
    pass
 
# Dependency function for FastAPI routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

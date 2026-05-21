from fastapi import FastAPI
from core.database import engine
from models import models
from routers import sync, auth, ai

# Ensure tables exist
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Academic Planner API",
    description="Backend for the Local-First AI Academic Planner",
    version="2.0.0",
)

# Mount routers
app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(ai.router)


@app.get("/health")
def health_check():
    return {"status": "online", "service": "ai-academic-planner-backend"}

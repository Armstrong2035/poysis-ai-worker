import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.blocks.retrieval.router import router as retrieval_router
from app.blocks.classifier.router import router as classifier_router
from app.blocks.recommendation.router import router as recommendation_router
from app.blocks.clustering.router import router as clustering_router
from app.api.tracking import router as tracking_router
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from dotenv import load_dotenv

load_dotenv(override=True)

app = FastAPI(title="Poysis Worker API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/ping")
async def ping():
    """Lightweight health check to keep the server warm."""
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Poysis Worker API is online", "mode": "multi-tenant"}

# Golden Quad Blocks
app.include_router(retrieval_router, prefix="/retrieval")
app.include_router(classifier_router, prefix="/classify")
app.include_router(recommendation_router, prefix="/recommend")
app.include_router(clustering_router, prefix="/cluster")
# Analytics & Tracking
app.include_router(tracking_router)
app.include_router(analytics_router)
# Auth
app.include_router(auth_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

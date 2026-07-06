from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from google.genai import types
from google.adk.runners import Runner
import inspect
from app.agent import app as adk_app
from app.app_utils import services
from dotenv import load_dotenv
import os
import json

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

runner = Runner(
    app=adk_app,
    session_service=services.get_session_service(),
    artifact_service=services.get_artifact_service(),
    auto_create_session=True,
)

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    user_id: str = "default_user"

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    async def event_generator():
        new_msg = types.Content(role="user", parts=[types.Part.from_text(text=request.message)])
        
        gen = runner.run(
            user_id=request.user_id,
            session_id=request.session_id or "default_session",
            new_message=new_msg
        )
        if inspect.isasyncgen(gen):
            async for event in gen:
                # Add tool_calls and tool_responses to event dump manually if needed,
                # but model_dump_json() usually handles it.
                yield f"data: {event.model_dump_json()}\n\n"
        else:
            for event in gen:
                yield f"data: {event.model_dump_json()}\n\n"
                
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/last_update")
def get_last_update():
    from sqlalchemy import create_engine, text
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ.get("DB_PASSWORD", "postgres")
    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "utci-tracker-db")
    engine = create_engine(f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}")
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(observation_timestamp) FROM utci_grid")).scalar()
            if result:
                return {"last_update": result.isoformat() + "Z"}
    except Exception as e:
        return {"last_update": None}
    return {"last_update": None}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

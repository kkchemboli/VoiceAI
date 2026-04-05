import os
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Optional

# Load environment variables
load_dotenv()

app = FastAPI(title="Voice Agent API")

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase Setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("Connected to Supabase")
    except Exception as e:
        print(f"Failed to connect to Supabase: {e}")

class ConfigUpdate(BaseModel):
    system_prompt: str

class KnowledgeUpdate(BaseModel):
    content: str

@app.get("/")
async def root():
    return {"message": "Voice Agent API is running"}

@app.get("/api/config")
async def get_config():
    """Fetch agent configuration (system prompt)"""
    if not supabase:
        return {"system_prompt": "Expert Institute of Advance Technologies Pvt. Ltd. (Local Fallback Prompt)"}
    
    try:
        # Assuming a table 'agent_config' with columns 'key' and 'value'
        response = supabase.table("agent_config").select("value").eq("key", "system_prompt").execute()
        if response.data and len(response.data) > 0:
            return {"system_prompt": response.data[0]["value"]}
        return {"system_prompt": ""}
    except Exception as e:
        print(f"Error fetching config: {e}")
        return {"system_prompt": "", "error": str(e)}

@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    """Update agent configuration"""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    
    try:
        supabase.table("agent_config").upsert({"key": "system_prompt", "value": config.system_prompt}).execute()
        return {"status": "success", "message": "Configuration updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
async def get_logs():
    """Fetch call logs"""
    if not supabase:
        # Return dummy logs for UI testing if no supabase
        return [
            {"id": 1, "date": "2024-03-20", "time": "10:30 AM", "duration": "5m 20s", "status": "Completed", "customer": "Rajesh Kumar", "summary": "Interested in Mobile Repairing course."},
            {"id": 2, "date": "2024-03-20", "time": "11:15 AM", "duration": "3m 45s", "status": "Completed", "customer": "Anjali Singh", "summary": "Asked about fee structure for iPhone course."}
        ]
    
    try:
        response = supabase.table("call_logs").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        print(f"Error fetching logs: {e}")
        return []

@app.get("/api/knowledge")
async def get_knowledge(lang: str = "en"):
    """Fetch knowledge base content"""
    if not supabase:
        # Fallback to local files if supabase is missing
        file_map = {"en": "knowledge.txt", "hi": "knowledge_hi.txt"}
        path = os.path.join(os.path.dirname(__file__), file_map.get(lang, "knowledge.txt"))
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return {"content": f.read()}
        return {"content": "Knowledge base file not found locally."}
    
    try:
        response = supabase.table("knowledge_base").select("content").eq("language", lang).execute()
        if response.data and len(response.data) > 0:
            return {"content": response.data[0]["content"]}
        return {"content": ""}
    except Exception as e:
        print(f"Error fetching knowledge: {e}")
        return {"content": "", "error": str(e)}

@app.post("/api/knowledge/{lang}")
async def update_knowledge(lang: str, data: KnowledgeUpdate):
    """Update knowledge base content"""
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    
    try:
        # Upsert based on language column
        supabase.table("knowledge_base").upsert(
            {"language": lang, "content": data.content}
        ).execute()
        return {"status": "success", "message": f"Knowledge base ({lang}) updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

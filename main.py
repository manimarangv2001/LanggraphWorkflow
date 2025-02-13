import json
import logging
from DataModel.ServiceNowAPI import APIResponse
from fastapi import FastAPI, HTTPException
import uvicorn
 
# Import our flow logic
from flow_logic import init_graph
 
app = FastAPI()
graph = None  # We'll initialize this on startup
 
@app.on_event("startup")
async def startup_event():
    """
    On application startup, initialize our StateGraph by calling init_graph().
    """
    global graph
    graph = await init_graph()  # This ensures the graph is compiled once.
 
@app.get("/")
async def read_root():
    return {"message": "LangGraph Assistant is Running (Async)!"}

@app.post("/api/task")
async def execute_flow(task_data: APIResponse):
    """
    Endpoint to handle the flow for a given "number" (e.g. the ServiceNow Task Number).
    We will parse the JSON, create a thread_id, and invoke the graph.
    """
    try:
        # Build the dict in the same format as the original code expects:
        task_response = task_data.model_dump()
        
        # Construct a unique thread_id. For example:
        thread_id = "task_" + task_response["result"][0]["number"]
 
        # Now invoke the graph asynchronously
        output = graph.ainvoke(
            {"task_response": task_response},
            config={"configurable": {"thread_id": thread_id}}
        )
 
        # Since ainvoke returns an Awaitable, we either await it in an async route
        # or wrap it in a sync call. Because this route is sync, let's do:
        return await output
 
    except Exception as e:
        logging.error(f"Error executing flow: {e}")
        raise HTTPException(status_code=500, detail=str(e))
 
if __name__ == "__main__":
    # Run the app using uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
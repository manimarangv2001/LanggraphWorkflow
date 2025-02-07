import os
import json
import logging
import subprocess
import yaml
from enum import IntEnum
from typing import Literal
from typing_extensions import TypedDict
 
# Third-party libs
from dotenv import load_dotenv
 
# LangGraph imports
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
 
# -----------------------------------------------------------------------
# Configure Logging
# -----------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
 
# -----------------------------------------------------------------------
# Load Environment Variables
# -----------------------------------------------------------------------
load_dotenv()
user = os.getenv('SERVICENOW_USER')
pwd = os.getenv('SERVICENOW_PWD')
endpoint = "https://hexawaretechnologiesincdemo8.service-now.com"
db_path = os.getenv('DATABASE_PATH')
 
# -----------------------------------------------------------------------
# Define the FlowState
# -----------------------------------------------------------------------
class FlowState(TypedDict):
    task_response: dict
    flow_name: str
    actions_list: list
    current_action: str
    additional_variables: dict
    worknote_content: str
    execution_log: list  # We'll store logs & updates here
    action_index: int
    next_action: bool
    error_occurred: bool
 
# -----------------------------------------------------------------------
# Define TicketState
# -----------------------------------------------------------------------
class TicketState(IntEnum):
    PENDING = 0
    OPEN = 1
    WORK_IN_PROGRESS = 2
    CLOSED_COMPLETE = 3
    CLOSED_INCOMPLETE = 4
    CLOSED_SKIPPED = 5
    RESOLVED = 6
 
# -----------------------------------------------------------------------
# Asynchronous Helper Functions
# -----------------------------------------------------------------------
async def run_powershell_command(command: str):
    """Execute a PowerShell command and return status and output."""
    try:
        logging.debug(f"Executing PowerShell command: {command}")
        result = subprocess.run(
            ["powershell", "-Command", command],
            capture_output=True,
            text=True
        )
        return {
            "Status": "Success" if result.returncode == 0 else "Error",
            "OutputMessage": result.stdout.strip(),
            "ErrorMessage": result.stderr.strip(),
        }
    except Exception as e:
        return {
            "Status": "Error",
            "OutputMessage": "",
            "ErrorMessage": str(e)
        }
 
def parse_powershell_output(powershell_response: dict, additional_variables: dict):
    """
    Parse JSON output from the PowerShell script and update additional_variables.
    Returns (updated_vars, worknote_content, error_occurred).
    """
    try:
        error_occured = False
        if powershell_response["Status"] == "Success":
            # Try to load the output as JSON.
            try:
                powershell_output = json.loads(powershell_response["OutputMessage"] or "{}")
            except Exception as json_e:
                logging.error(f"JSON decode error: {json_e}")
                raise RuntimeError("Output is not valid JSON.")
 
            if not isinstance(powershell_output, dict):
                powershell_output = json.loads(powershell_output)
 
            if powershell_output.get("Status") == "Success":
                additional_variables.update(powershell_output)
                worknote_content = powershell_output.get("OutputMessage", "Execution Successful")
            else:
                OutputMessage = powershell_output.get("OutputMessage", "")
                ErrorMessage = powershell_output.get("ErrorMessage", "")
                worknote_content = f"{OutputMessage}\n{ErrorMessage}"
                error_occured = True
        else:
            worknote_content = powershell_response["ErrorMessage"]
        return additional_variables, worknote_content, error_occured
    except Exception as e:
        raise RuntimeError(f"Error parsing PowerShell execution: {e}")
 
# -----------------------------------------------------------------------
# Flow Node Functions (Async)
# -----------------------------------------------------------------------
import httpx
 
async def initialize_flow_state(state: FlowState) -> FlowState:
    """
    Determine the flow name from the short_description and initialize the state.
    Now reading from a YAML file instead of CSV.
    """
    logging.debug("Checking flow name.")
    task_response = state["task_response"]
    if "result" not in task_response or not task_response["result"]:
        raise ValueError("Task response is missing 'result' data.")
 
    short_description = task_response["result"][0].get("short_description")
    if not short_description:
        raise ValueError("Short description is missing in the task response.")
 
    # --- Load from YAML ---
    try:
        with open("flow_details.yml", "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f)  # e.g., {"flows": [ {...}, {...} ]}
            # Create a dict that maps a short_description -> { flow_name, reassignment_group, ... }
            flow_map = {
                item["short_description"]: {
                    "flow_name": item["flow_name"],
                    "reassignment_group": item["reassignment_group"],
                }
                for item in yaml_data.get("flows", [])
            }
    except FileNotFoundError:
        raise ValueError("flow_details.yml file not found.")
    except KeyError as e:
        raise ValueError(f"Missing key in flow_details.yml: {e}")
 
    # Lookup the short_description
    if short_description not in flow_map:
        logging.error(f"No flow found for: {short_description}")
        raise ValueError(f"No flow found for short description: {short_description}")
 
    mapping_data = flow_map[short_description]
    state["flow_name"] = mapping_data["flow_name"]
    logging.debug(f"Flow name determined: {state['flow_name']}")
 
    # Optionally store the reassignment_group in state["additional_variables"]
    state["additional_variables"]["reassignment_group"] = mapping_data["reassignment_group"]
 
    # Initialize state fields
    state["actions_list"] = []
    state["current_action"] = ""
    state["worknote_content"] = ""
    state["execution_log"] = []
    state["action_index"] = 0
    state["next_action"] = False
    state["error_occurred"] = False
 
    # Mark ticket as WORK_IN_PROGRESS
    updated_state = await update_ticket_state(state, TicketState.WORK_IN_PROGRESS)
    return updated_state
 
async def update_ticket_state(state: FlowState, task_state: TicketState) -> FlowState:
    """
    Update the ticket state in ServiceNow using the given task_state and log the change.
    """
    try:
        task_response = state["task_response"]
        state_request = {"state": str(task_state.value)}
        updated_state_json = json.dumps(state_request)
 
        table_name = task_response["result"][0]["sys_class_name"]
        sys_id = task_response["result"][0]["sys_id"]
 
        url = f"{endpoint}/api/now/table/{table_name}/{sys_id}"
        async with httpx.AsyncClient() as client:
            auth = (user, pwd)
            resp = await client.put(
                url,
                auth=auth,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                content=updated_state_json
            )
            if resp.status_code != 200:
                raise Exception(f"Failed to update state: {resp.json()}")
 
        state["worknote_content"] = "Worknotes updated successfully"
        # Log the updated ticket state in execution_log
        state["execution_log"].append({
            "action": "update_ticket_state",
            "ticket_state_value": task_state.value,
            "ticket_state_name": task_state.name,
            "description": f"Ticket state successfully updated to {task_state.name} ({task_state.value})."
        })
    except Exception as e:
        raise RuntimeError(f"Error updating state: {e}")
    return state
 
async def retrieve_flow_scripts(state: FlowState) -> FlowState:
    """Fetch PowerShell scripts from UseCases/<flow_name>."""
    logging.debug("Fetching actions for the flow.")
    flow_dir = "UseCases"
    try:
        actions_dir = os.path.join(flow_dir, state["flow_name"])
        actions_list = os.listdir(actions_dir)
    except Exception as e:
        logging.error(f"Error fetching actions: {e}")
        raise RuntimeError(f"Error fetching actions: {e}")
 
    state["actions_list"] = actions_list
    logging.debug(f"Actions found: {actions_list}")
    return state
 
async def evaluate_flow_decision(state: FlowState) -> FlowState:
    """Decide whether to continue or end the flow."""
    logging.debug("Assistant node: deciding next step.")
    if state["error_occurred"]:
        state["next_action"] = False
        updated_state = await update_servicenow_assignment_group(state)
        logging.debug("Assistant: error_occured=True, will end flow.")
        return updated_state
 
    if state["action_index"] < len(state["actions_list"]):
        state["next_action"] = True
        state["current_action"] = state["actions_list"][state["action_index"]]
        logging.debug(f"Assistant: next_action=True. Next script: {state['current_action']}")
    else:
        state["next_action"] = False
        state["current_action"] = ""
        state = await update_ticket_state(state, TicketState.CLOSED_COMPLETE)
        logging.debug("Assistant: no more actions, ending flow.")
 
    return state

async def execute_flow_script(state: FlowState) -> FlowState:
    """Execute the current PowerShell script asynchronously."""
    logging.debug("Executing current action.")
    idx = state["action_index"]
    actions = state["actions_list"]
    additional_vars = state["additional_variables"]
    task_response = state["task_response"]
 
    if idx < len(actions):
        action_name = actions[idx]
        action_path = os.path.join("UseCases", state["flow_name"], action_name)
        logging.debug(f"Running action script: {action_path}")
 
        try:
            # Build the header part of the script as a single string.
            header = (
                f"$jsonObject = '{json.dumps(task_response)}' | ConvertFrom-Json; "
                f"$SCTASK_RESPONSE = $jsonObject.result; "
                f"$ADDITIONAL_VARIABLES = '{json.dumps(additional_vars)}' | ConvertFrom-Json; "
            )
            with open(action_path, 'r') as script_file:
                file_content = script_file.read()
            powershell_script = header + file_content
 
            ps_result = await run_powershell_command(powershell_script)
            state["execution_log"].append({
                "script": action_name,
                "Status": ps_result["Status"],
                "OutputMessage": ps_result["OutputMessage"],
                "ErrorMessage": ps_result["ErrorMessage"]
            })
 
            if ps_result["Status"] == "Error":
                logging.error(f"Error executing {action_name}: {ps_result['ErrorMessage']}")
                state["worknote_content"] = f"Error in {action_name}: {ps_result['ErrorMessage']}"
                state["error_occurred"] = True
            else:
                updated_vars, note_content, error_occured = parse_powershell_output(ps_result, additional_vars)
                state["additional_variables"] = updated_vars
                state["worknote_content"] = note_content
                state["error_occurred"] = error_occured
 
        except Exception as e:
            logging.error(f"Execution failed for {action_name}: {e}")
            state["worknote_content"] = f"Execution failed for {action_name}: {e}"
            state["error_occurred"] = True
 
        state["action_index"] = idx + 1
    else:
        state["worknote_content"] = "All actions executed."
        state["error_occurred"] = False
 
    logging.debug(f"State after executing action: {state}")
    return state
 
async def update_servicenow_worknotes(state: FlowState) -> FlowState:
    """Update the ServiceNow record's worknotes with the result of the action."""
    logging.debug("Updating worknotes on ServiceNow.")
    try:
        task_response = state["task_response"]
        content = state["worknote_content"]
 
        table_name = task_response["result"][0]["sys_class_name"]
        sys_id = task_response["result"][0]["sys_id"]
        url = f"{endpoint}/api/now/table/{table_name}/{sys_id}"
 
        body = {"work_notes": content}
 
        async with httpx.AsyncClient() as client:
            auth = (user, pwd)
            resp = await client.put(
                url,
                auth=auth,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json=body
            )
            if resp.status_code != 200:
                logging.error(f"Failed to update worknotes: {resp.json()}")
                raise Exception(f"Failed to update worknotes: {resp.json()}")
 
        state["worknote_content"] = "Worknotes updated successfully"
    except Exception as e:
        logging.error(f"Error updating worknotes: {e}")
        raise RuntimeError(f"Error updating worknotes: {e}")
 
    logging.debug(f"State after updating worknotes: {state}")
    return state
 
async def retrieve_reassignment_group(state: FlowState):
    return state["additional_variables"].get("reassignment_group", "")
 
async def update_servicenow_assignment_group(state: FlowState):
    try:
        task_response = state["task_response"]
        reassignment_group_sys_id = await retrieve_reassignment_group(state)
        data = {"assignment_group": reassignment_group_sys_id}
 
        table_name = task_response["result"][0]["sys_class_name"]
        sys_id = task_response["result"][0]["sys_id"]
        url = f"{endpoint}/api/now/table/{table_name}/{sys_id}"
 
        async with httpx.AsyncClient() as client:
            auth = (user, pwd)
            resp = await client.put(
                url,
                auth=auth,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                data=json.dumps(data)
            )
            if resp.status_code != 200:
                raise Exception(f"Failed to update assignment group: {resp.json()}")
 
        state["worknote_content"] = "Worknotes updated successfully"
        # Log the updated ticket state in execution_log
        state["execution_log"].append({
            "action": "update_servicenow_assignment_group",
        })
    except Exception as e:
        raise RuntimeError(f"Error updating assignment group: {e}")
    return state
 
# -----------------------------------------------------------------------
# Decide Whether to Continue or End
# -----------------------------------------------------------------------
def determine_flow_outcome(state: FlowState) -> Literal["execute_flow_script", END]:  # type: ignore
    return "execute_flow_script" if state["next_action"] else END
 
# -----------------------------------------------------------------------
# Build and Compile the StateGraph
# -----------------------------------------------------------------------
builder = StateGraph(FlowState)
 
builder.add_node("initialize_flow_state", initialize_flow_state)
builder.add_node("retrieve_flow_scripts", retrieve_flow_scripts)
builder.add_node("evaluate_flow_decision", evaluate_flow_decision)
builder.add_node("execute_flow_script", execute_flow_script)
builder.add_node("update_servicenow_worknotes", update_servicenow_worknotes)
 
builder.add_edge(START, "initialize_flow_state")
builder.add_edge("initialize_flow_state", "retrieve_flow_scripts")
builder.add_edge("retrieve_flow_scripts", "evaluate_flow_decision")
builder.add_conditional_edges("evaluate_flow_decision", determine_flow_outcome)
builder.add_edge("execute_flow_script", "update_servicenow_worknotes")
builder.add_edge("update_servicenow_worknotes", "evaluate_flow_decision")
 
# We will keep a reference to a compiled graph, but we initialize it via `init_graph()`.
_graph = None
 
async def init_graph():
    """
    Initialize and return the compiled StateGraph with the AsyncSqliteSaver.
    This will be called once in the FastAPI startup event.
    """
    global _graph
    if _graph is None:
        import aiosqlite
        conn = await aiosqlite.connect(db_path, check_same_thread=False)
        memory = AsyncSqliteSaver(conn)
        _graph = builder.compile(checkpointer=memory)
    return _graph
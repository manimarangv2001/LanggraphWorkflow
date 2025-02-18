import os
import json
import logging
import asyncio
from enum import IntEnum
from typing import Literal
from typing_extensions import TypedDict
import httpx
import yaml
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# Configure Logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load Environment Variables
load_dotenv()
user = os.getenv('SERVICENOW_USER')
pwd = os.getenv('SERVICENOW_PWD')
endpoint = "https://hexawaretechnologiesincdemo8.service-now.com"
db_path = os.getenv('DATABASE_PATH')

# Define the FlowState
class FlowState(TypedDict):
    task_response: dict
    flow_name: str
    actions_list: list
    current_action: str
    additional_variables: dict
    worknote_content: str
    execution_log: list
    action_index: int
    next_action: bool
    error_occurred: bool

# Define TicketState
class TicketState(IntEnum):
    PENDING = 0
    OPEN = 1
    WORK_IN_PROGRESS = 2
    CLOSED_COMPLETE = 3
    CLOSED_INCOMPLETE = 4
    CLOSED_SKIPPED = 5
    RESOLVED = 6

# Asynchronous Helper Functions



async def run_script(script_path: str, inputs: dict) -> dict:
    """
    Execute a script based on its file extension asynchronously.
    Supports:
      - Python (.py): Runs with 'python' interpreter; inputs passed as a JSON string.
      - Node.js (.js): Runs with 'node' interpreter; inputs passed as a JSON string.
      - PowerShell (.ps1): Runs with 'powershell'; inputs passed as individual key-value arguments.

    Args:
        script_path (str): Path to the script file.
        inputs (dict): Input data for the script.

    Returns:
        dict: Execution result containing:
            - Status: "Success" or "Error"
            - Outputs: Parsed outputs from the script (if available)
            - ErrorMessage: Any error message encountered
    """
    if not os.path.exists(script_path):
        error_msg = f"Script file not found: {script_path}"
        logging.error(error_msg)
        return {"Status": "Error", "Outputs": None, "ErrorMessage": error_msg}

    ext = os.path.splitext(script_path)[1].lower()

    try:
        if ext in [".py", ".js"]:
            interpreter = "python" if ext == ".py" else "node"
            inputs_json = json.dumps(inputs)
            command = [interpreter, script_path, inputs_json]
        elif ext == ".ps1":
            command = ["powershell", "-File", script_path]
            for key, value in inputs.items():
                command.extend([f"-{key}", str(value)])
        else:
            error_msg = f"Unsupported script file type: {ext}"
            logging.error(error_msg)
            return {"Status": "Error", "Outputs": None, "ErrorMessage": error_msg}

        logging.info(f"Executing command: {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        stdout_decoded = stdout.decode().strip().replace('\r\n', ' ')
        stderr_decoded = stderr.decode().strip().replace('\r\n', ' ')

        if process.returncode == 0:
            try:
                outputs = json.loads(stdout_decoded)
            except json.JSONDecodeError:
                outputs = stdout_decoded
            return {"Status": "Success", "Outputs": outputs, "ErrorMessage": ""}
        else:
            logging.error(f"Script execution error: {stderr_decoded}")
            return {"Status": "Error", "Outputs": None, "ErrorMessage": stderr_decoded}

    except Exception as e:
        logging.error(f"Exception occurred during script execution: {e}")
        return {"Status": "Error", "Outputs": None, "ErrorMessage": str(e)}
    



async def initialize_flow_state(state: FlowState) -> FlowState:
    logging.debug("Checking flow name.")
    task_response = state["task_response"]
    if "result" not in task_response or not task_response["result"]:
        raise ValueError("Task response is missing 'result' data.")

    short_description = task_response["result"][0].get("short_description")
    if not short_description:
        raise ValueError("Short description is missing in the task response.")

    try:
        with open("flow_details.yml", "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f)
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

    if short_description not in flow_map:
        logging.error(f"No flow found for: {short_description}")
        raise ValueError(f"No flow found for short description: {short_description}")

    mapping_data = flow_map[short_description]
    state["flow_name"] = mapping_data["flow_name"]
    logging.debug(f"Flow name determined: {state['flow_name']}")

    state["additional_variables"] = {"reassignment_group": mapping_data["reassignment_group"]}
    state["actions_list"] = []
    state["current_action"] = ""
    state["worknote_content"] = ""
    state["execution_log"] = []
    state["action_index"] = 0
    state["next_action"] = False
    state["error_occurred"] = False

    logging.debug(f"Initial additional_variables: {state['additional_variables']}")

    updated_state = await update_ticket_state(state, TicketState.WORK_IN_PROGRESS)
    return updated_state

def parse_powershell_output(powershell_response: dict, additional_variables: dict):
    """
    Parse output from the PowerShell script and update additional_variables.
    Returns (updated_vars, worknote_content, error_occurred).
    """
    try:
        error_occurred = False
        worknote_content = ""

        if powershell_response["Status"] == "Success":
            powershell_output = powershell_response.get("Outputs")

            # Check if the output is None or empty
            if powershell_output is None:
                worknote_content = "PowerShell script returned null output."
                error_occurred = True
                return additional_variables, worknote_content, error_occurred

            # Check if the output is already a dictionary
            if isinstance(powershell_output, dict):
                parsed_output = powershell_output
            elif isinstance(powershell_output, str):
                # Attempt to parse the output as JSON
                try:
                    parsed_output = json.loads(powershell_output)
                except json.JSONDecodeError as json_e:
                    logging.error(f"JSON decode error: {json_e}")
                    worknote_content = f"Output is not valid JSON: {powershell_output}"
                    error_occurred = True
                    return additional_variables, worknote_content, error_occurred
            else:
                worknote_content = f"Unexpected output type: {type(powershell_output)}"
                error_occurred = True
                return additional_variables, worknote_content, error_occurred

            if isinstance(parsed_output, dict):
                if parsed_output.get("Status") == "Success":
                    additional_variables.update({
                        "Userstobeadded": parsed_output.get("Userstobeadded", ""),
                        "uniquegroupname": parsed_output.get("uniquegroupname", ""),
                        "OwnerEmail": parsed_output.get("OwnerEmail", "")
                    })
                    worknote_content = parsed_output.get("OutputMessage", "Execution Successful")
                else:
                    OutputMessage = parsed_output.get("OutputMessage", "")
                    ErrorMessage = parsed_output.get("ErrorMessage", "")
                    worknote_content = f"{OutputMessage}\n{ErrorMessage}"
                    error_occurred = True
            else:
                worknote_content = f"Unexpected output format: {parsed_output}"
                error_occurred = True
        else:
            worknote_content = powershell_response["ErrorMessage"]
            error_occurred = True

        logging.debug(f"Updated additional_variables: {additional_variables}")
        return additional_variables, worknote_content, error_occurred
    except Exception as e:
        raise RuntimeError(f"Error parsing PowerShell execution: {e}")



      
async def update_ticket_state(state: FlowState, task_state: TicketState) -> FlowState:
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
    logging.debug("Assistant node: deciding next step.")
    if state["error_occurred"]:
        state["next_action"] = False
        updated_state = await update_servicenow_assignment_group(state)
        logging.debug("Assistant: error_occurred=True, will end flow.")
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
            header = (
                f"$jsonObject = '{json.dumps(task_response)}' | ConvertFrom-Json; "
                f"$SCTASK_RESPONSE = $jsonObject.result; "
                f"$ADDITIONAL_VARIABLES = '{json.dumps(additional_vars)}' | ConvertFrom-Json; "
            )
            with open(action_path, 'r') as script_file:
                file_content = script_file.read()
            powershell_script = header + file_content

            ps_result = await run_script(action_path, {"script_content": powershell_script})
            state["execution_log"].append({
                "script": action_name,
                "Status": ps_result["Status"],
                "OutputMessage": ps_result["Outputs"],
                "ErrorMessage": ps_result["ErrorMessage"]
            })

            if ps_result["Status"] == "Error":
                logging.error(f"Error executing {action_name}: {ps_result['ErrorMessage']}")
                state["worknote_content"] = f"Error in {action_name}: {ps_result['ErrorMessage']}"
                state["error_occurred"] = True
            else:
                updated_vars, note_content, error_occurred = parse_powershell_output(ps_result, additional_vars)
                state["additional_variables"] = updated_vars
                state["worknote_content"] = note_content
                state["error_occurred"] = error_occurred

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
        state["execution_log"].append({
            "action": "update_servicenow_assignment_group",
        })
    except Exception as e:
        raise RuntimeError(f"Error updating assignment group: {e}")
    return state

def determine_flow_outcome(state: FlowState) -> Literal["execute_flow_script", END]:
    return "execute_flow_script" if state["next_action"] else END

# Build and Compile the StateGraph
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

_graph = None

async def init_graph():
    global _graph
    if _graph is None:
        import aiosqlite
        conn = await aiosqlite.connect(db_path, check_same_thread=False)
        memory = AsyncSqliteSaver(conn)
        _graph = builder.compile(checkpointer=memory)
    return _graph

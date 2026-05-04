"""
FastAPI Backend for Temporal Research UI
=========================================
Production-ready backend connecting to Temporal workflows.

Environment Variables:
- TEMPORAL_PROFILE: name of the env config profile to use (optional).
- TEMPORAL_ADDRESS: Temporal server address (default: 127.0.0.1:7233)
- TEMPORAL_NAMESPACE: Temporal namespace (default: default)
- TEMPORAL_API_KEY: API key for Temporal Cloud (disabled by default)
- TEMPORAL_TASK_QUEUE: Task queue name (default: research-queue)
- NEO4J_URI: Neo4j connection URI (default: bolt://localhost:7687)
- NEO4J_USER: Neo4j username (default: neo4j)
- NEO4J_PASSWORD: Neo4j password (required for memory features)
"""

import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from auth0_ai_utils import get_google_drive_token
from auth0_fastapi.auth import AuthClient
from auth0_fastapi.config import Auth0Config
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from auth0_fastapi.server.routes import router, register_auth_routes
from auth0_fastapi.errors import AccessTokenForConnectionError
#from auth0 import auth0_config, auth0_client
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.envconfig import ClientConfig

from openai_agents.memory.neo4j_memory import get_neo4j_memory
from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchResult,
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.research_models import (
    SingleClarificationInput,
    UserQueryInput,
)

from personalization_models import (
    UpdateFromGoogleDocRequest,
    PersonalizationState,
)
from personalization_store import (
    get_personalization_state,
    upsert_personalization_state,
    close_driver as close_personalization_driver,
)
from personalization_merge import merge_state
from personalization_worker_client import extract_phrases
from google_drive_tools import drive_file_metadata, export_google_doc_text, GOOGLE_DOC_MIME


# Load environment variables
load_dotenv()

TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "research-queue")

AUTH0_CLIENT_ID=os.getenv("AUTH0_CLIENT_ID")
AUTH0_CLIENT_SECRET=os.getenv("AUTH0_CLIENT_SECRET")
AUTH0_DOMAIN=os.getenv("AUTH0_DOMAIN")
APP_SECRET_KEY=os.getenv("APP_SECRET_KEY")
BASE_URL=os.getenv("BASE_URL")

auth0_config = Auth0Config(
    domain=AUTH0_DOMAIN,
    clientId=AUTH0_CLIENT_ID,
    clientSecret=AUTH0_CLIENT_SECRET,
    authorization_params={
        "scope": "openid profile email offline_access",
        "audience": f"https://{AUTH0_DOMAIN}/me/",
        "prompt": "consent"
    },
    mount_connected_account_routes = True,
    appBaseUrl=BASE_URL,
    secret=APP_SECRET_KEY,
    routes={
        "login": "/login",
        "callback": "/callback",
        "logout": "/logout",
    }
)

auth0_client = AuthClient(auth0_config)

def extract_h1_from_markdown(markdown: str) -> Optional[str]:
    """
    Extract the first H1 heading (# Title) from markdown text.

    Args:
        markdown: Markdown text to parse

    Returns:
        The H1 title text (without the #), or None if no H1 found
    """
    if not markdown:
        return None

    lines = markdown.strip().split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("##"):
            # Found H1 (starts with "# " but not "##")
            title = stripped[2:].strip()  # Remove "# " prefix
            return title if title else None

    return None


# ============================================
# FastAPI App Setup
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup (optional)
    yield
    # shutdown
    try:
        close_personalization_driver()
    except Exception:
        pass

app = FastAPI(
    title="Temporal Research API",
    description="Backend API for the Temporal Research Demo UI",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for frontend
_allowed_origins = [o for o in [BASE_URL, "http://localhost:8234"]]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionMiddleware, secret_key=os.getenv("APP_SECRET_KEY"))
app.state.auth_config = auth0_config
app.state.auth_client = auth0_client

register_auth_routes(router, auth0_config)
app.include_router(router)


# ============================================
# Temporal Client Setup
# ============================================


temporal_client: Optional[Client] = None

temporal_config = ClientConfig.load_client_connect_config()
temporal_config.setdefault("target_host", "localhost:7233")
temporal_config.setdefault("namespace", "default")


async def get_temporal_client() -> Client:
    global temporal_client
    if temporal_client:
        return temporal_client

    print(
        f"Connecting to Temporal at {temporal_config.get('target_host')} in namespace {temporal_config.get('namespace')}"
    )

    temporal_client = await Client.connect(
        **temporal_config,
        data_converter=pydantic_data_converter,
    )
    return temporal_client


# ============================================
# Request/Response Models
# ============================================
class StartResearchRequest(BaseModel):
    query: str


class AnswerRequest(BaseModel):
    answer: str


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    status: str  # "pending", "awaiting_clarifications", "researching", "complete"
    original_query: Optional[str] = None
    current_question: Optional[str] = None
    current_question_index: int = 0
    total_questions: int = 0
    clarification_responses: Dict[str, str] = {}


class ResearchResultResponse(BaseModel):
    workflow_id: str
    markdown_report: str
    short_summary: str
    follow_up_questions: List[str]


class ConversationResponse(BaseModel):
    conversation_id: str
    workflow_id: str
    original_query: str
    status: str
    created_at: str


# ============================================
# Static File Serving
# ============================================
@app.get("/")
async def serve_index(request: Request, response: Response):
    """Serve the main chat interface"""
    try:
        await auth0_client.require_session(request, response)
    except Exception as e:
        return RedirectResponse(url="/auth/login")

    index_path = Path(__file__).parent.parent / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    raise HTTPException(status_code=404, detail="Index page not found")


@app.get("/success")
async def serve_success(request: Request, response: Response):
    """Serve the success/results page"""
    try:
        await auth0_client.require_session(request, response)
    except Exception as e:
        return RedirectResponse(url="/auth/login")

    success_path = Path(__file__).parent.parent / "success.html"
    if success_path.exists():
        return HTMLResponse(content=success_path.read_text())
    raise HTTPException(status_code=404, detail="Success page not found")


@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()

    return RedirectResponse(
        url=(
            f"https://{AUTH0_DOMAIN}/v2/logout"
            f"?client_id={AUTH0_CLIENT_ID}"
            f"&returnTo={BASE_URL}"
        )
    )

# Serve static assets (JS, CSS, fonts, images)
static_path = Path(__file__).parent.parent
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Serve temporary images
temp_images_path = Path(__file__).resolve().parent.parent.parent / "temp_images"
temp_images_path.mkdir(exist_ok=True)
app.mount(
    "/temp_images", StaticFiles(directory=str(temp_images_path)), name="temp_images"
)

# ============================================
# API Endpoints
# ============================================


@app.post("/api/start-research")
async def start_research(
    request: StartResearchRequest,
    fastapi_request: Request,
    response: Response,
    _auth_session=Depends(auth0_client.require_session),
):
    client = await get_temporal_client()
    workflow_id = f"interactive-research-{uuid.uuid4().hex[:8]}"

    # Identify user (same pattern as /api/personalization)
    user = await get_userinfo_or_401(fastapi_request, response)
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing sub in userinfo")

    # Load personalization (may be None)
    p = get_personalization_state(user_id)

    base_query = request.query.strip()
    enriched_query = base_query

    if p:
        topics = [t for t in (p.topic_preferences or []) if isinstance(t, str) and t.strip()]
        interests = [i for i in (p.research_interests or []) if isinstance(i, str) and i.strip()]

        if topics or interests:
            enriched_query = (
                f"{base_query}\n\n"
                "Personalization context (use to tailor research relevance; do not mention unless asked):\n"
                f"- Topic preferences: {', '.join(topics) if topics else '(none)'}\n"
                f"- Research interests: {', '.join(interests) if interests else '(none)'}\n"
            )

    handle = await client.start_workflow(
        InteractiveResearchWorkflow.run,
        args=[None, False],
        id=workflow_id,
        task_queue=TEMPORAL_TASK_QUEUE,
    )

    status = await handle.execute_update(
        InteractiveResearchWorkflow.start_research,
        UserQueryInput(query=enriched_query),
    )

    # Persist conversation: keep original query as the user-facing query
    memory = await get_neo4j_memory()
    if memory:
        await memory.create_conversation(
            workflow_id=workflow_id,
            original_query=base_query,
            status=status.status,
        )
        await memory.add_message(
            workflow_id=workflow_id,
            message_type="human",
            content=base_query,
            message_category="initial_query",
        )
        # Optional: store personalization as a system/internal message for debugging/audit
        if enriched_query != base_query:
            await memory.add_message(
                workflow_id=workflow_id,
                message_type="system",
                content=enriched_query,
                message_category="personalization_context",
            )

    return {"workflow_id": workflow_id, "status": "started"}


@app.get("/api/status/{workflow_id}")
async def get_status(workflow_id: str, _auth_session = Depends(auth0_client.require_session)):
    """
    Get current workflow status.

    Returns:
        workflow_id: Workflow identifier
        status: Current status (awaiting_clarifications, researching, completed)
        current_question: The clarification question to display (if awaiting)
        current_question_index: Index of current question
        total_questions: Total number of clarification questions
    """
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    status = await handle.query(InteractiveResearchWorkflow.get_status)

    # Update conversation status in Neo4j
    memory = await get_neo4j_memory()
    if memory:
        try:
            await memory.update_conversation_status(
                workflow_id=workflow_id, status=status.status
            )
            # Only save the current question when it's first displayed (sequence matches chat order)
            # This ensures messages are saved in the order they appear in the chat window
            if status.status == "awaiting_clarifications" and status.current_question:
                existing_messages = await memory.get_messages(workflow_id)
                # Check if we've already saved this specific question
                current_question_saved = any(
                    m.content == status.current_question
                    and m.message_category == "clarification_question"
                    for m in existing_messages
                )
                # Only save if this is the first question (index 0) and it hasn't been saved yet
                if status.current_question_index == 0 and not current_question_saved:
                    await memory.add_message(
                        workflow_id=workflow_id,
                        message_type="ai",
                        content=status.current_question,
                        message_category="clarification_question",
                    )
            # If research is completed, save the result if it hasn't been saved yet
            elif status.status == "completed":
                print(
                    f"INFO: Detected completed status for workflow {workflow_id}, checking for result..."
                )
                # Check if result already exists
                existing_results = await memory.get_results(workflow_id)
                print(f"INFO: Existing results count: {len(existing_results)}")
                if not existing_results:
                    # Try to get the result from the workflow
                    try:
                        print(
                            f"INFO: Attempting to fetch result from workflow {workflow_id}..."
                        )
                        desc = await handle.describe()
                        print(f"INFO: Workflow description status: {desc.status}")
                        if desc.status and desc.status.name == "COMPLETED":
                            print(f"INFO: Workflow is COMPLETED, fetching result...")
                            result = await handle.result()
                            # Handle both dict and InteractiveResearchResult objects
                            if isinstance(result, dict):
                                short_summary = result.get("short_summary", "")
                                markdown_report = result.get("markdown_report", "")
                                image_file_path = result.get("image_file_path")
                                follow_up_questions = result.get(
                                    "follow_up_questions", []
                                )
                                existing_result_id = result.get("existing_result_id")
                            else:
                                short_summary = result.short_summary
                                markdown_report = result.markdown_report
                                image_file_path = result.image_file_path
                                follow_up_questions = result.follow_up_questions or []
                                existing_result_id = getattr(
                                    result, "existing_result_id", None
                                )

                            # Normalize image_file_path to ensure it starts with / for web serving
                            if image_file_path and not image_file_path.startswith("/"):
                                image_file_path = f"/{image_file_path}"
                            print(
                                f"INFO: Got result, short_summary length: {len(short_summary)}, markdown length: {len(markdown_report)}"
                            )

                            # Check if this is a reused result from knowledge graph
                            if existing_result_id:
                                print(
                                    f"INFO: Reusing existing Result node {existing_result_id} from knowledge graph"
                                )
                                result_node = await memory.link_existing_result(
                                    workflow_id=workflow_id,
                                    existing_result_id=existing_result_id,
                                )
                                print(
                                    f"INFO: ✅ Linked existing Result node {existing_result_id} to conversation {workflow_id}"
                                )
                                # No need to re-index - it's already indexed
                            else:
                                # Extract title from H1 in markdown
                                title = extract_h1_from_markdown(markdown_report)
                                print(f"INFO: Extracted title from markdown: {title}")
                                result_node = await memory.add_result(
                                    workflow_id=workflow_id,
                                    short_summary=short_summary,
                                    markdown_report=markdown_report,
                                    title=title,
                                    image_file_path=image_file_path,
                                )
                                print(
                                    f"INFO: ✅ Research result saved as Result node for conversation {workflow_id} (from status check)"
                                )

                                # Index the result in Neo4j for RAG
                                try:
                                    from openai_agents.memory.neo4j_rag import (
                                        get_neo4j_rag,
                                    )

                                    rag = await get_neo4j_rag()
                                    if rag:
                                        await rag.index_result_node(
                                            result_node.result_id, markdown_report
                                        )
                                        print(
                                            f"INFO: ✅ Indexed result {result_node.result_id} for RAG"
                                        )
                                except Exception as rag_error:
                                    print(
                                        f"WARNING: Failed to index result for RAG: {rag_error}"
                                    )
                        else:
                            print(
                                f"WARNING: Workflow status is {desc.status}, not COMPLETED yet"
                            )
                    except Exception as result_error:
                        print(
                            f"ERROR: Could not save result when status became completed: {result_error}"
                        )
                        import traceback

                        traceback.print_exc()
                else:
                    print(
                        f"INFO: Result already exists for workflow {workflow_id}, skipping save"
                    )
        except Exception as e:
            print(f"Warning: Failed to update conversation status in Neo4j: {e}")
            import traceback

            traceback.print_exc()

    response = {
        "workflow_id": workflow_id,
        "status": status.status,
        "original_query": status.original_query,
        "current_question": status.current_question,
        "current_question_index": status.current_question_index,
        "total_questions": len(status.clarification_questions or []),
        "clarification_responses": status.clarification_responses or {},
    }

    if status.status == "awaiting_clarifications":
        response["current_question"] = status.get_current_question()

    return response


@app.post("/api/answer/{workflow_id}/{current_question_index}")
async def submit_answer(
    workflow_id: str, current_question_index: int,
    request: AnswerRequest,
    _auth_session = Depends(auth0_client.require_session)
):
    """
    Submit an answer to a clarification question.

    Returns:
        status: "accepted" if answer was recorded
        workflow_status: Current workflow status after answer
        questions_remaining: Number of questions left
    """
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    await handle.execute_update(
        InteractiveResearchWorkflow.provide_single_clarification,
        SingleClarificationInput(
            question_index=current_question_index, answer=request.answer.strip()
        ),
    )

    status = await handle.query(InteractiveResearchWorkflow.get_status)

    # Save user's answer as a human message
    memory = await get_neo4j_memory()
    if memory:
        try:
            await memory.add_message(
                workflow_id=workflow_id,
                message_type="human",
                content=request.answer.strip(),
                message_category="clarification_answer",
            )
            # After saving the answer, save the next question if it exists
            # This ensures questions are saved one at a time, matching chat order
            if status.status in ["awaiting_clarifications", "collecting_answers"]:
                next_question = status.get_current_question()
                if next_question:
                    # Check if we've already saved this question
                    existing_messages = await memory.get_messages(workflow_id)
                    question_already_saved = any(
                        m.content == next_question
                        and m.message_category == "clarification_question"
                        for m in existing_messages
                    )
                    if not question_already_saved:
                        await memory.add_message(
                            workflow_id=workflow_id,
                            message_type="ai",
                            content=next_question,
                            message_category="clarification_question",
                        )
        except Exception as e:
            print(f"Warning: Failed to save answer message to Neo4j: {e}")

    return {
        "status": "accepted",
        "workflow_status": status.status,
        "questions_remaining": len(status.clarification_questions or [])
        - status.current_question_index,
    }

    raise HTTPException(
        status_code=501,
        detail="Temporal integration not configured. See backend/main.py for setup instructions.",
    )


@app.get("/api/result/{workflow_id}")
async def get_result(workflow_id: str, _auth_session = Depends(auth0_client.require_session)):
    """
    Get final research result.

    Returns:
        workflow_id: Workflow identifier
        markdown_report: Full markdown research report
        short_summary: Brief summary of findings
        follow_up_questions: Suggested follow-up questions
    """
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    # Check if workflow is complete
    desc = await handle.describe()
    if not desc.status or desc.status.name != "COMPLETED":
        raise HTTPException(status_code=400, detail="Research not complete yet")

    result = await handle.result()

    # Handle both dict and InteractiveResearchResult objects
    if isinstance(result, dict):
        short_summary = result.get("short_summary", "")
        markdown_report = result.get("markdown_report", "")
        image_file_path = result.get("image_file_path")
        follow_up_questions = result.get("follow_up_questions", [])
        existing_result_id = result.get("existing_result_id")
    else:
        short_summary = result.short_summary
        markdown_report = result.markdown_report
        image_file_path = result.image_file_path
        follow_up_questions = result.follow_up_questions or []
        existing_result_id = getattr(result, "existing_result_id", None)

    # Normalize image_file_path to ensure it starts with / for web serving
    if image_file_path and not image_file_path.startswith("/"):
        image_file_path = f"/{image_file_path}"

    # Save final research result as a Result node (check if already saved to avoid duplicates)
    memory = await get_neo4j_memory()
    if memory:
        try:
            print(f"INFO: get_result() called for workflow {workflow_id}")
            # Check if result already exists
            existing_results = await memory.get_results(workflow_id)
            print(
                f"INFO: Existing results count in get_result(): {len(existing_results)}"
            )
            if not existing_results:
                # Check if this is a reused result from knowledge graph
                if existing_result_id:
                    print(
                        f"INFO: Reusing existing Result node {existing_result_id} from knowledge graph"
                    )
                    result_node = await memory.link_existing_result(
                        workflow_id=workflow_id,
                        existing_result_id=existing_result_id,
                    )
                    print(
                        f"INFO: ✅ Linked existing Result node {existing_result_id} to conversation {workflow_id} (from get_result endpoint)"
                    )
                    # No need to re-index - it's already indexed
                else:
                    # Save as a Result node (not a Message node)
                    print(f"INFO: Saving result to Neo4j for workflow {workflow_id}...")
                    # Extract title from H1 in markdown
                    title = extract_h1_from_markdown(markdown_report)
                    print(f"INFO: Extracted title from markdown: {title}")
                    result_node = await memory.add_result(
                        workflow_id=workflow_id,
                        short_summary=short_summary,
                        markdown_report=markdown_report,
                        title=title,
                        image_file_path=image_file_path,
                    )
                    print(
                        f"INFO: ✅ Research result saved as Result node for conversation {workflow_id} (from get_result endpoint)"
                    )

                    # Index the result in Neo4j for RAG
                    try:
                        from openai_agents.memory.neo4j_rag import get_neo4j_rag

                        rag = await get_neo4j_rag()
                        if rag:
                            await rag.index_result_node(
                                result_node.result_id, markdown_report
                            )
                            print(
                                f"INFO: ✅ Indexed result {result_node.result_id} for RAG"
                            )
                    except Exception as rag_error:
                        print(f"WARNING: Failed to index result for RAG: {rag_error}")
            else:
                print(
                    f"INFO: Result already exists for conversation {workflow_id}, skipping save"
                )
        except Exception as e:
            print(f"ERROR: Failed to save research result to Neo4j: {e}")
            import traceback

            traceback.print_exc()
    else:
        print(
            f"WARNING: Neo4j memory not available, cannot save result for {workflow_id}"
        )

    # Normalize image_file_path in the result object before returning
    if isinstance(result, dict):
        img_path = result.get("image_file_path")
        if img_path and not img_path.startswith("/"):
            result["image_file_path"] = f"/{img_path}"
    else:
        # Convert dataclass to dict for JSON serialization
        if hasattr(result, "image_file_path") and result.image_file_path:
            if not result.image_file_path.startswith("/"):
                result.image_file_path = f"/{result.image_file_path}"
        # Convert to dict for proper JSON serialization
        result = {
            "workflow_id": workflow_id,
            "short_summary": result.short_summary,
            "markdown_report": result.markdown_report,
            "follow_up_questions": result.follow_up_questions or [],
            "image_file_path": result.image_file_path,
            "existing_result_id": getattr(result, "existing_result_id", None),
        }

    print(
        f"INFO: Returning result with image_file_path: {result.get('image_file_path') if isinstance(result, dict) else getattr(result, 'image_file_path', None)}"
    )
    return result


@app.get("/api/stream/{workflow_id}")
async def stream_status(workflow_id: str, _auth_session = Depends(auth0_client.require_session)):
    """
    Server-Sent Events endpoint for live status updates.

    Streams status updates every second until workflow completes.
    """
    # TODO: Implement SSE streaming with Temporal
    #
    # async def event_generator():
    #     client = await get_temporal_client()
    #     handle = client.get_workflow_handle(workflow_id)
    #
    #     while True:
    #         status = await handle.query(InteractiveResearchWorkflow.get_status)
    #
    #         data = {
    #             "status": status.status,
    #             "current_question_index": status.current_question_index,
    #             "total_questions": len(status.clarification_questions or []),
    #         }
    #
    #         yield f"data: {json.dumps(data)}\n\n"
    #
    #         if status.status == "complete":
    #             break
    #
    #         await asyncio.sleep(1)
    #
    # return StreamingResponse(
    #     event_generator(),
    #     media_type="text/event-stream",
    #     headers={
    #         "Cache-Control": "no-cache",
    #         "Connection": "keep-alive",
    #     }
    # )

    raise HTTPException(
        status_code=501,
        detail="Temporal integration not configured. See backend/main.py for setup instructions.",
    )


@app.get("/api/conversations")
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    _auth_session = Depends(auth0_client.require_session)):
    """
    List all conversations from Neo4j memory.

    Returns:
        List of conversation objects with workflow_id, original_query, status, etc.
    """
    memory = await get_neo4j_memory()
    if not memory:
        return {"conversations": [], "error": "Neo4j memory not available"}

    try:
        conversations = await memory.list_conversations(limit=limit, offset=offset)
        return {
            "conversations": [conv.to_dict() for conv in conversations],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list conversations: {str(e)}"
        )


@app.get("/api/conversations/{workflow_id}")
async def get_conversation(workflow_id: str, _auth_session = Depends(auth0_client.require_session)):
    """
    Get a specific conversation by workflow ID.

    Returns:
        Conversation object with workflow_id, original_query, status, etc.
    """
    memory = await get_neo4j_memory()
    if not memory:
        raise HTTPException(status_code=503, detail="Neo4j memory not available")

    try:
        conversation = await memory.get_conversation(workflow_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get conversation: {str(e)}"
        )


@app.get("/api/conversations/{workflow_id}/messages")
async def get_conversation_messages(workflow_id: str, _auth_session = Depends(auth0_client.require_session)):
    """
    Get all messages for a conversation, ordered by sequence.

    Returns:
        List of message objects with type, content, timestamp, sequence, etc.
    """
    memory = await get_neo4j_memory()
    if not memory:
        raise HTTPException(status_code=503, detail="Neo4j memory not available")

    try:
        messages = await memory.get_messages(workflow_id)
        return {"messages": [msg.to_dict() for msg in messages]}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get conversation messages: {str(e)}"
        )

# ============================================
# Personalization Endpoints
# ============================================
async def get_userinfo_or_401(request: Request, response: Response) -> dict:
    """
    Fetch user identity via Auth0 /userinfo using the session token set.
    """
    store_options = {"request": request, "response": response}
    user = await auth0_client.client.get_user(store_options=store_options)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/api/google/status")
async def google_status(
    request: Request,
    response: Response,
    _auth_session=Depends(auth0_client.require_session),
):
    """
    Returns whether the user's Google account is connected in Token Vault.
    """
    token = get_google_drive_token()
    return {"connected": bool(token)}


@app.get("/api/personalization")
async def get_personalization(
    request: Request,
    response: Response,
    _auth_session = Depends(auth0_client.require_session),
):
    user = await get_userinfo_or_401(request, response)
    user_id = user.get("sub")
    email = user.get("email")

    if not user_id:
        raise HTTPException(status_code=400, detail="Missing sub in userinfo")

    existing = get_personalization_state(user_id)
    if not existing:
        return PersonalizationState(user_id=user_id, email=email).model_dump()
    return existing.model_dump()


@app.post("/api/personalization/update-from-google-doc")
async def update_from_google_doc(
    body: UpdateFromGoogleDocRequest,
    request: Request,
    response: Response,
    _auth_session = Depends(auth0_client.require_session),
):
    """
    Event-driven: user shares a Google Doc → gateway reads doc text using Token Vault token
    → worker extracts phrases → merge with existing → persist → return updated state.
    """
    user = await get_userinfo_or_401(request, response)
    user_id = user.get("sub")
    email = user.get("email")

    if not user_id:
        raise HTTPException(status_code=400, detail="Missing sub in userinfo")

    # 1) Get Google access token via Token Vault (connected accounts)
    try:
        store_options = {"request": request, "response": response}
        google_access_token = await auth0_client.client.get_access_token_for_connection(
            {"connection": "google-oauth2"},
            store_options=store_options
        )
    except AccessTokenForConnectionError:
        raise HTTPException(status_code=403, detail="Google not connected")

    # 2) Fetch doc metadata & text
    meta = await drive_file_metadata(google_access_token, body.drive_file_id)
    if meta.get("mimeType") != GOOGLE_DOC_MIME:
        raise HTTPException(status_code=400, detail=f"Only Google Docs supported. mimeType={meta.get('mimeType')}")

    doc_text = await export_google_doc_text(google_access_token, body.drive_file_id)

    # 3) Load existing personalization state (Neo4j)
    existing = get_personalization_state(user_id) or PersonalizationState(user_id=user_id, email=email)

    # 4) Extraction worker (internal service; receives text only)
    extracted = await extract_phrases(doc_text)

    # 5) Merge + cap deterministically
    merged_topics, merged_interests = merge_state(
        existing.topic_preferences,
        existing.research_interests,
        extracted.topic_preferences,
        extracted.research_interests,
    )

    # 6) Persist
    source_doc_ids = ([body.drive_file_id] + (existing.source_doc_ids or []))[:10]
    source_doc_titles = ([meta.get("name", "")] + (existing.source_doc_titles or []))[:10]

    updated = PersonalizationState(
        user_id=user_id,
        email=email or existing.email,
        topic_preferences=merged_topics,
        research_interests=merged_interests,
        source_doc_ids=source_doc_ids,
        source_doc_titles=source_doc_titles,
    )
    saved = upsert_personalization_state(updated)

    return saved.model_dump()


# ============================================
# Health Endpoint
# ============================================
@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    memory = await get_neo4j_memory()
    neo4j_status = "connected" if memory else "not_configured"

    return {
        "status": "healthy",
        # "temporal_profile": TEMPORAL_PROFILE,
        "temporal_address": temporal_config.get("target_host"),
        "temporal_namespace": temporal_config.get("namespace"),
        "task_queue": TEMPORAL_TASK_QUEUE,
        "neo4j_memory": neo4j_status,
    }


# ============================================
# Main Entry Point
# ============================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8234)

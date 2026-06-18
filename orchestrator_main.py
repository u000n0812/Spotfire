from fastapi import FastAPI, HTTPException, Query, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated
import uvicorn
import os
from pydantic import BaseModel, Field
from typing import Optional
import logging


from langsmith import Client


import authentication
import prompts
from orchestrator import Orchestrator
from config import OrchestratorConfiguration

logging.basicConfig()
logging.root.setLevel(logging.NOTSET)
logging.basicConfig(level=logging.NOTSET)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
FASTAPI_APP_VERSION = os.environ.get("FASTAPI_APP_VERSION", "0.0.0")

logger = logging.getLogger("orchestrator")
logger.setLevel(LOG_LEVEL)


class HistoryMsg(BaseModel):
    role: str
    content: str

class ClientData(BaseModel):
    data_name: str = Field(description="Name of the data")
    value: str = Field(description="Data value")

class OrchestratorRequest(BaseModel):
    """
    Object to send requests to Orchestrator
    """

    request_tag: Optional[str] = Field(
        default="OrchRequest",
        description="Used to assign a label to individual requests made to a REST service. This tag helps in tracking and managing requests, allowing for easy identification and categorization. It may contain information such as the purpose of the request, the source of the request, or any other relevant metadata. By utilizing requestTag, developers can efficiently monitor, analyze, and troubleshoot requests")
    temperature: Optional[float] = Field(
        default=None,
        description="Model temperature"
    )
    index_name: Optional[str] = Field(
        default=None,
        description="Name of the collection of documents to be used as context",
    )
    index_score_threshold: Optional[float] = Field(
        default=None,
        description="Value to be used to compare against confidence scores returned when searching indexers",
    )
    index_topk: Optional[int] = Field(
        default=None, description="Number of top candidates to consider from indexer search"
    )
    retriever_type: Optional[str] = Field(
        default=None, description="Type of retriever to be used"
    )
    use_secondary_model_plugin: Optional[bool] = Field(default=None, description="Indicates whether to use the secondary model plugin.")
    llm_name: Optional[str] = Field(default=None, description="Name of model")
    llm_mode: Optional[str] = Field(default=None, description="LLM operation type. For example, chat, completions, etc.")
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt.  If provided, it overwrites orchestrator generated system prompt",
        example="Your are an AI assistant. Answer the question based on the context below. Keep the answer short and concise.",
    )
    user_intent: Optional[str] = Field(
        default=None,
        description="Explicit user intent.  If provided, it overwrites orchestrator generated intent.",
        example="QueryIntent",
    )
    history: Optional[str] = Field(
        default=None,
        description="Refers to previous interactions that have occurred between the user and the model",
        example="[{'role': 'user', 'content': 'question1'}, {'role': 'assistant', 'content': 'response1'},{'role': 'user', 'content': 'question2'}, {'role': 'assistant', 'content': 'response2'}]",
    )
    image: Optional[str] = Field(default=None, description="Base64 encoded image")
    image_url: Optional[str] = Field(default=None, description="URL of the image")
    client_data: list[ClientData] | None = Field(
        default=None, description="Array of name/value pairs providing client data"
    )
    prompt: str = Field(
        description="User question", example="How to create a bar chart?"
    )

class SourceObj(BaseModel):
    """
    Object describing the source of contextual data
    """

    fileName: str = Field(description="Name of the data source")
    pageNumber: int = Field(description="Page number of the source data")
    refId: int = Field(description="Reference to the citation used in the response")


class PromptInfo(BaseModel):
    """
    Class used to store system prompt information.
    """

    system_prompt: str = Field(
        description="Specifies the prompt to be registered and associated with a specific user intent",
        example="You are an AI assistant helping to answer Spotfire questions",
    )
    system_prompt_parameters: list[str] | None = Field(
        default=None,
        description="Name of template parameters to be replaced in the system prompt",
    )
    retriever_type: Optional[str] = Field(default=None, description="Type of retriever to be used")
    use_secondary_model_plugin: Optional[bool] = Field(default=None, description="Indicates whether to use the secondary model plugin.")
    llm_name: str = Field(default="gpt-35-turbo", description="Name of model")
    llm_mode: str = Field(default="chat", description="Model operation")
    temperature: float = Field(default=0.0, description="Model temperature")
    index_name: Optional[str] = Field(default=None, description="Name of the index if context is used")
    index_score_threshold: Optional[float] = Field(
        default=None,
        description="Value to be used to compare against confidence scores returned when searching indexers",
    )
    index_topk: Optional[int] = Field(
        default=None,
        description="Number of top candidates to consider from indexer search",
    )

class OrchestratorResponse(BaseModel):
    """
    Object returned by orchestrator
    """

    result: str = Field(description="Result from LLM to submitted request")
    gpt_prompt: str = Field(description="Prompts passed to LLM")
    sources: list[SourceObj] = []

class RegisterPromptRequest(BaseModel):
    """
    Class utilized as an argument within the operation for registering a system prompt designed for a particular user intent.
    """

    user_intent: str = Field(
        description="Specifies the user intent for which a system prompt is being registered when calling a function to register system prompts. This argument helps define the context in which the system prompt will be used, ensuring that it aligns accurately with the user's intended actions or queries.",
        example="SpotfireHelp",
    )
    prompt_info: PromptInfo = Field(description="Prompt information")

class RegisterPromptSetRequest(BaseModel):
    """
    Class utilized as an argument within the operation for registering system prompts designed for a set of user intents.
    """

    prompts: list[RegisterPromptRequest] = Field(
        description="List of prompts to be registered"
    )


class RegisterPromptResponse(BaseModel):
    """
    Used to represent the response data returned by an operation for registering system prompts tailored to specific user intents.
    """

    status: str = Field(
        description="Indicates the status of the system prompt registration operation. It can convey information such as whether the registration was successful, if there were any errors encountered during the process, or other relevant status messages."
    )


class GetPromptResponse(BaseModel):
    """
    Used to represent the response data returned by an operation for getting a system prompt associated to specific user intents.
    """

    status: str = Field(
        description="Indicates the status of the system prompt get operation. It can convey information such as whether the removal was successful, if there were any errors encountered during the process, or other relevant status messages."
    )
    prompt_info: PromptInfo | None = Field(
        description="The system prompt information associated with the user itent"
    )


class GetUserIntentsResponse(BaseModel):
    """
    Used to represent the response data returned by an operation for getting registered user intents
    """

    user_intents: list[str] = []


class RemovePromptResponse(BaseModel):
    """
    Used to represent the response data returned by an operation for removing a system prompt tailored to specific user intents.
    """

    status: str = Field(
        description="Indicates the status of the system prompt remove operation. It can convey information such as whether the removal was successful, if there were any errors encountered during the process, or other relevant status messages."
    )

# Load the Langsmith client
client = Client()

# Set the secret key
# random secret key that will be used to sign the JWT tokens
# This key can be obtained using the command: openssl rand -hex 32
authentication.set_secret_key(os.getenv("SECRET_KEY"))

# Set the hashed password for administrator
authentication.set_admin_hashed_password(os.getenv("HASHED_ADMIN_PASSWORD"))

description = """
Orchestrator API for LLMs

The Orchestrator component is at the core of facilitating rich interactions with Large Language Models (LLM) like GPT.
It acts as a dynamic bridge, fostering smooth and insightful exchanges between users and these powerful models.

"""

app = FastAPI(
    title="Spotfire LLM Orchestrator",
    description=description,
    version=FASTAPI_APP_VERSION,
    contact={
        "name": "Spotfire team",
        "url": "http://www.spotfire.com/contact-us/",
        "email": "copilot-dev@spotfire.com",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
)


@app.post("/token", response_model=authentication.Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
):
    user = authentication.authenticate_user(
        authentication.users_db, form_data.username, form_data.password
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = authentication.timedelta(
        days=int(os.getenv("ACCESS_TOKEN_EXPIRE_DAYS"))
    )
    access_token = authentication.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/")
async def root():
    return {
        "message": "LLM Orchestrator - REST API"
    }

@app.post(
    "/orchestrator",
    summary="Analyze and process the received question",
    response_model=OrchestratorResponse,
)
def handle_request(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
    orchestrator_request: OrchestratorRequest,
) -> any:
    """
    Orchestrator engages in a multi-step process to provide a meaningful response to a question.
    It analyzes and processes the received question by understanding its context and intent,
    and then calls the LLM to generate a coherent and contextually relevant response in natural language.
    """

    logger.info("Processing request.")
    sources = []

    print("******************")
    print("Orchestrator request: ")
    print(orchestrator_request)



    # Validate input params
    orch_config = OrchestratorConfiguration(orchestrator_request)

    print("Orchestrator config: ")
    print(orch_config)

    # Get system prompt information for user_intent
    system_prompt_info = prompts.prompt_dict[orch_config.user_intent]

    # Update config with system prompt information
    orch_config.update(system_prompt_info)

    # Display the configuration
    orch_config.display()

    # Execute the chain
    # result = chains.executeChain(orch_config)

    # Process the request
    orchestrator = Orchestrator()
    result = orchestrator.processRequest(orch_config)

    # --- PATCH: populate "sources" with the documents used for this question. ---
    # The bundled chat chain answers but does not expose its source documents,
    # so the response "sources" was always empty. We run the same retrieval here
    # and report each unique (file, page) so answers can be verified.
    try:
        import orch_utils
        _embeddings = orch_utils.getEmbeddings()
        _retriever = orch_utils.getRetriever(None, _embeddings, orch_config)
        _docs = _retriever.invoke(orch_config.user_prompt)
        _seen = set()
        _ref = 1
        for _d in _docs:
            _meta = getattr(_d, "metadata", {}) or {}
            _fname = _meta.get("source") or _meta.get("filename") or ""
            _page_raw = _meta.get("page") or _meta.get("pageNumber") or 0
            try:
                _page = int(float(_page_raw))
            except (ValueError, TypeError):
                _page = 0
            _key = (_fname, _page)
            if _key in _seen:
                continue
            _seen.add(_key)
            sources.append(SourceObj(fileName=_fname, pageNumber=_page, refId=_ref))
            _ref += 1
    except Exception as e:
        logger.warning("Failed to build sources: %s", e)
    # --- END PATCH ---

    # return {"result": result, "gpt_prompt":chat_prompt_value.to_string(), "sources":sources}
    return {"result": result, "gpt_prompt": "", "sources": sources}


@app.put(
    "/system-prompt/register",
    summary="Register system prompt to be used for specific user itent",
    response_model=RegisterPromptResponse,
)
def handle_request(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
    register_request: RegisterPromptRequest,
) -> any:
    """
    Register a system prompt specifically tailored to a user's intent.
    This operation is a key component in enhancing conversational interactions, as it allows
    the customization of system responses based on the user's expressed intent.
    By registering system prompts for specific user intents, developers can fine-tune
    the conversational experience, ensuring that the responses provided by the system align
    seamlessly with the user's expectations and the context of the conversation.
    This level of customization and context-awareness enhances the overall user experience
    and ensures more meaningful and relevant interactions with the system.
    """

    prompt_info = {
        "system_prompt": register_request.prompt_info.system_prompt,
        "system_prompt_parameters": register_request.prompt_info.system_prompt_parameters,
        "use_secondary_model_plugin": register_request.prompt_info.use_secondary_model_plugin,
        "llm_name": register_request.prompt_info.llm_name,
        "llm_mode": register_request.prompt_info.llm_mode,
        "temperature": register_request.prompt_info.temperature,
        "index_name": register_request.prompt_info.index_name,
        "index_score_threshold": register_request.prompt_info.index_score_threshold,
        "index_topk": register_request.prompt_info.index_topk,
        "retriever_type": register_request.prompt_info.retriever_type,
    }

    prompts.updatePromptDict(register_request.user_intent, prompt_info)

    # logger.info("Updated prompt dict: %s", prompts.prompt_dict)

    return {"status": "Success"}

@app.put(
    "/system-prompt/register-set",
    summary="Register a set of system prompt to be used for specific user itents",
    response_model=RegisterPromptResponse,
)
def handle_request(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
    register_request: RegisterPromptSetRequest,
) -> any:
    """
    Register a set of system prompts specifically tailored for specific user intents.
    This operation is a key component in enhancing conversational interactions, as it allows
    the customization of system responses based on the user's expressed intent.
    By registering system prompts for specific user intents, developers can fine-tune
    the conversational experience, ensuring that the responses provided by the system align
    seamlessly with the user's expectations and the context of the conversation.
    This level of customization and context-awareness enhances the overall user experience
    and ensures more meaningful and relevant interactions with the system.
    """

    for prompt in register_request.prompts:

        prompt_info = {
            "system_prompt": prompt.prompt_info.system_prompt,
            "system_prompt_parameters": prompt.prompt_info.system_prompt_parameters,
            "use_secondary_model_plugin": prompt.prompt_info.use_secondary_model_plugin,
            "llm_name": prompt.prompt_info.llm_name,
            "llm_mode": prompt.prompt_info.llm_mode,
            "temperature": prompt.prompt_info.temperature,
            "index_name": prompt.prompt_info.index_name,
            "index_score_threshold": prompt.prompt_info.index_score_threshold,
            "index_topk": prompt.prompt_info.index_topk,
            "retriever_type": prompt.prompt_info.retriever_type,
        }

        prompts.updatePromptDict(prompt.user_intent, prompt_info)

    return {"status": "Success"}


@app.get(
    "/system-prompt/get",
    summary="Get system prompt used for specific user itent",
    response_model=GetPromptResponse,
)
def handle_request(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
    # user_intent: Annotated[str, Query(
    #     description="Specifies the user intent associated with the requested system prompt"
    # )] = "SpotfireHelp",
    user_intent: str = Query(
        default="SpotfireHelp",
        description="Specifies the user intent associated with the requested system prompt",
    ),
) -> any:
    """
    Get a system prompt specifically registered to a user's intent.
    """

    result = prompts.getPromptDict(user_intent)

    return result


@app.get(
    "/system-prompt/user-intents",
    summary="Get all registered user itents",
    response_model=GetUserIntentsResponse,
)
def handle_request(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
) -> any:
    """
    Get all registered user itents.
    """

    user_intents = prompts.keysPromptDic()

    logger.info("Keys array: %s", user_intents)

    return {"user_intents": user_intents}

@app.patch(
    "/system-prompt/remove",
    summary="Remove system prompt assoicated with specific user itent",
    response_model=RemovePromptResponse,
)
def handle_request(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
    userIntent: str = Query(
        default="SpotfireHelp",
        description="Specifies the user intent associated with the system prompt to be removed",
    ),
) -> any:
    """
    Enables the removal of a system prompt specifically tailored to a user's intent.
    """

    status = prompts.popPromptDict(userIntent)

    return {"status": status}


@app.patch(
    "/system-prompt/clear",
    summary="Resets the system by removing all existing prompts and intents.",
    response_model=RemovePromptResponse,
)
def handle_request(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
) -> any:
    """
    Initiates a system-wide reset by removing all currently stored prompts and intents.
    This action effectively clears the system's memory of any predefined prompts or recognized intentions,
    allowing for a fresh start or reconfiguration of the system's behavior and response patterns.
    It's a fundamental step in system maintenance and can be performed to update the system's behavior,
    adapt to new requirements, or troubleshoot any issues related to existing prompts and intents
    """

    status = prompts.clearPromptDict()

    return {"status": status}


if __name__ == "__main__":
    uvicorn.run(app, port=8080, host="0.0.0.0")

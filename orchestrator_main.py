from fastapi import FastAPI, HTTPException, Query, Depends, status, Form
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


# --- PATCH: Spotfire 문서 Q&A(User_Docs)를 우리 RAG 체인에 맞게 보정 ---
# 원본 User_Docs 프롬프트는 ${context}(string.Template 문법)을 쓰는데, 체인은
# {context}(LangChain PromptTemplate)을 기대 → 문맥이 주입되지 않음.
# Spotfire 가 system_prompt 없이 User_Docs 로 질의할 때도 검색 문맥이 들어가도록
# {context} 기반 한국어 프롬프트로 교체.
try:
    if "User_Docs" in prompts.prompt_dict:
        prompts.prompt_dict["User_Docs"]["system_prompt"] = (
            "당신은 문서 기반 한국어 비서입니다. 아래 문맥(context)에 있는 내용만 "
            "근거로 한국어로만 답하세요. 한자나 중국어를 쓰지 마세요. 문맥에 답이 "
            "없으면 '문서에서 찾을 수 없습니다'라고만 답하세요.\n\n문맥:\n{context}"
        )
        prompts.prompt_dict["User_Docs"]["system_prompt_parameters"] = None
        prompts.prompt_dict["User_Docs"]["retriever_type"] = "similarity"
        prompts.prompt_dict["Agent_User_Docs"] = prompts.prompt_dict["User_Docs"]
except Exception as _e:
    logger.warning("Failed to adjust User_Docs prompt: %s", _e)
# --- END PATCH ---


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


# --- PATCH: OAuth2 client-credentials token endpoint ---
# Spotfire Copilot 프론트엔드는 /client/token 으로 client_id/client_secret 을 보내
# 토큰을 받는데, 이 orchestrator 이미지엔 해당 엔드포인트가 없어 404 가 났음.
# (Spotfire 패널의 "Error communicating with orchestrator / 404" 원인)
# client_id/client_secret 을 검증하고 admin 권한 토큰을 발급해 보완함.
# 유효 값은 환경변수 COPILOT_CLIENT_ID / COPILOT_CLIENT_SECRET (기본 spotfire/spotfire).
@app.post("/client/token", response_model=authentication.Token)
async def client_for_access_token(
    grant_type: str = Form(default=None),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    scope: str = Form(default=None),
):
    expected_id = os.getenv("COPILOT_CLIENT_ID", "spotfire")
    expected_secret = os.getenv("COPILOT_CLIENT_SECRET", "spotfire")
    if client_id != expected_id or client_secret != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = authentication.timedelta(
        days=int(os.getenv("ACCESS_TOKEN_EXPIRE_DAYS"))
    )
    # admin 을 subject 로 발급해야 이후 보호된 엔드포인트의 사용자 조회를 통과함
    access_token = authentication.create_access_token(
        data={"sub": "admin"}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


# Spotfire 가 client 등록을 호출하는 경우를 대비한 보조 엔드포인트.
# 설정된 자격증명을 그대로 돌려줌(Spotfire 는 보통 Preferences 의 값을 직접 사용).
@app.post("/register-client")
async def register_client(
    current_user: Annotated[
        authentication.User, Depends(authentication.get_current_active_user)
    ],
):
    return {
        "client_id": os.getenv("COPILOT_CLIENT_ID", "spotfire"),
        "client_secret": os.getenv("COPILOT_CLIENT_SECRET", "spotfire"),
        "token_endpoint": "/client/token",
    }


# Spotfire 프론트엔드가 시작할 때마다 조회하는데 이 orchestrator
# (FASTAPI_APP_VERSION=2.0.0) 에는 없어 404 가 반복 기록됨.
# 에이전트 기능은 쓰지 않으므로 빈 목록을 돌려줘 프론트엔드가 기본 모드로 진행하게 함.
@app.get("/agents/available")
async def agents_available():
    return []
# --- END PATCH ---


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

    # --- PATCH: Spotfire 의 인텐트 분류 요청에 프론트엔드가 받는 라벨로 응답 ---
    # Spotfire 는 "Classify this question: ..." 를 보내고, result 가 자기 쪽 인텐트
    # 목록에 있는 이름이길 기대함. 아니면 "Error determining intent" 로 거부함.
    #
    # 이 목록은 백엔드의 prompts.prompt_dict 와 다름 — "SpecificDataQuestion" 도
    # "Specific_Data_Question" 도 둘 다 거부당했음. 반면 이미지에 내장된 분류기
    # (orchestrator-classifier)는 prompt_dict 에 없는 'GeneralHelp' 같은 라벨을
    # 내놓는데, 이게 이 orchestrator 가 상정하는 정식 어휘임.
    # → 자체 목록으로 찍지 말고 내장 분류기가 이미 결정한 라벨을 그대로 돌려줌.
    #   (내장 분류기는 OrchestratorConfiguration() 생성 시 이미 실행됨)
    #
    # 프론트엔드가 쓰는 정확한 이름을 알아내면 코드 수정 없이 환경변수로 지정 가능:
    #   COPILOT_INTENT_LABELS  = 쉼표로 구분한 유효 인텐트 목록 (지정 시 LLM 분류 사용)
    #   COPILOT_INTENT_DEFAULT = 분류 실패 시 사용할 기본 라벨
    _cls_prompt = (orch_config.user_prompt or "").strip()
    if _cls_prompt.lower().startswith("classify this question"):
        _question = _cls_prompt.split(":", 1)[-1].strip() if ":" in _cls_prompt else _cls_prompt

        _builtin_intent = (orch_config.user_intent or "").strip()
        _valid_intents = [
            s.strip()
            for s in os.getenv("COPILOT_INTENT_LABELS", "").split(",")
            if s.strip()
        ]
        _default_intent = os.getenv("COPILOT_INTENT_DEFAULT", "").strip()

        if not _valid_intents:
            # 목록 미지정(기본): 내장 분류기 결과를 그대로 사용
            _chosen = _builtin_intent or _default_intent or "GeneralHelp"
        else:
            _default_intent = _default_intent or _valid_intents[0]
            try:
                from langchain_community.chat_models import ChatOllama
                _cls_model = ChatOllama(
                    base_url=os.environ.get("OLLAMA_BASE_URL"),
                    model=os.getenv("CHAT_SIMPLE_MODEL_NAME") or "qwen2.5",
                    temperature=0,
                    num_ctx=int(os.getenv("CHAT_NUM_CTX", "8192")),
                )
                _instr = (
                    "You are an intent classifier for Spotfire Copilot. Read the user's "
                    "question and reply with EXACTLY ONE of the following intent names and "
                    "nothing else (no explanation, no punctuation, no quotes):\n"
                    + ", ".join(_valid_intents)
                    + "\n\nQuestion: " + _question + "\nIntent:"
                )
                _resp = _cls_model.invoke(_instr)
                _txt = getattr(_resp, "content", str(_resp))
                # 긴 이름을 먼저 맞춰서 짧은 이름이 그 변형을 가로채지 않게 함
                _chosen = next(
                    (
                        v
                        for v in sorted(_valid_intents, key=len, reverse=True)
                        if v.lower() in _txt.lower()
                    ),
                    _default_intent,
                )
            except Exception as _e:
                logger.warning("Classification patch failed: %s", _e)
                _chosen = _default_intent

        logger.info(
            "Classified question '%s' as intent: %s (builtin classifier said: %s)",
            _question, _chosen, _builtin_intent or "<none>",
        )
        return {"result": _chosen, "gpt_prompt": "", "sources": []}
    # --- END PATCH ---

    # --- PATCH: 분류기/Spotfire 가 prompt_dict 에 없는 intent 를 보내면 원본은
    # KeyError 로 500 이 났음 → 기본값을 등록해 크래시 방지(buildSystemPrompt 도 통과). ---
    if orch_config.user_intent not in prompts.prompt_dict:
        logger.warning(
            "Unregistered user_intent '%s' — registering a default entry",
            orch_config.user_intent,
        )
        prompts.prompt_dict[orch_config.user_intent] = {
            "system_prompt": orch_config.system_prompt
            or "당신은 Spotfire 도우미입니다. 한국어로 간결하게 답하세요.",
            "system_prompt_parameters": None,
            "use_secondary_model_plugin": False,
            "llm_name": orch_config.llm_name
            or os.getenv("CHAT_COMPLEX_MODEL_NAME")
            or "qwen2.5",
            "llm_mode": orch_config.llm_mode or "chat",
            "temperature": orch_config.temperature
            if orch_config.temperature is not None
            else 0.2,
            "index_name": orch_config.index_name,
            "index_score_threshold": orch_config.index_score_threshold,
            "index_topk": orch_config.index_topk,
            "retriever_type": orch_config.retriever_type,
        }
    # --- END PATCH ---

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
    #
    # 인덱스가 없는 인텐트(GeneralHelp 등)는 검색할 대상 자체가 없으므로 건너뜀.
    # 그냥 두면 요청마다 "Redis failed to connect: Index None does not exist" 경고가 쌓임.
    if not getattr(orch_config, "index_name", None):
        logger.debug(
            "Intent %s has no index configured - skipping sources",
            orch_config.user_intent,
        )
    else:
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

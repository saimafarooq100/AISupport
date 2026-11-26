"""FastAPI application entry point."""

# campus-performance-v1
from ai_support.performance import run_ai

import os
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mlflow.exceptions import MlflowException
from psycopg import Error as PsycopgError

from ai_support import __version__
from ai_support.agents.actions import ActionAuthorizationError, FeedbackAuthorizationError
from ai_support.agents.schema import AgentReply, EmailReceipt
from ai_support.agents.service import EmailDeliveryError
from ai_support.api.models import (
    ChatRequest,
    DepartmentProbability,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    RAGSearchRequest,
    RAGSearchResponse,
    RAGSearchResult,
    RouterPredictionRequest,
    RouterPredictionResponse,
)
from ai_support.api.runtime import create_phase4_services
from ai_support.logging import configure_logging
from ai_support.monitoring.metrics import HTTP_DURATION, HTTP_REQUESTS, metrics_payload
from ai_support.rag.service import RAGService
from ai_support.router.service import RouterService
from ai_support.security.auth import AuthenticationError, Principal

SERVICE_NAME = "uowd-ai-support-api"


def create_app() -> FastAPI:
    """Create the API application without starting external services."""

    configure_logging()
    application = FastAPI(
        title="UOWD AI Support Assistant API",
        description="Department routing and permission-filtered retrieval for UOWD support.",
        version=__version__,
    )
    application.state.router_service = RouterService(
        Path.cwd(), os.getenv("AI_SUPPORT_ROUTER_MODEL_REFERENCE", "champion")
    )
    database_url = os.getenv("AI_SUPPORT_DATABASE_URL")
    application.state.rag_service = RAGService(Path.cwd(), database_url) if database_url else None
    auth_service, chat_service = create_phase4_services(Path.cwd())
    application.state.auth_service = auth_service
    application.state.chat_service = chat_service
    bearer = HTTPBearer(auto_error=False)

    @application.middleware("http")
    async def observe_http(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - started)

    def current_principal(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Principal:
        if request.app.state.auth_service is None:
            raise HTTPException(status_code=503, detail="authentication is not configured")
        if credentials is None:
            raise HTTPException(status_code=401, detail="bearer session required")
        try:
            return request.app.state.auth_service.verify(credentials.credentials)
        except AuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        """Report that the API process is ready to accept requests."""

        return HealthResponse(service=SERVICE_NAME, version=__version__)

    @application.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        payload, content_type = metrics_payload()
        return Response(content=payload, headers={"Content-Type": content_type})

    @application.post("/v1/auth/login", response_model=LoginResponse, tags=["authentication"])
    async def login(payload: LoginRequest, request: Request) -> LoginResponse:
        """Exchange verified synthetic assessment credentials for a signed session."""

        service = request.app.state.auth_service
        if service is None:
            raise HTTPException(status_code=503, detail="authentication is not configured")
        try:
            token, principal, ttl = service.login(payload.user_id, payload.login_code)
        except AuthenticationError as error:
            raise HTTPException(status_code=401, detail="invalid synthetic credentials") from error
        return LoginResponse(
            access_token=token,
            expires_in=ttl,
            audience=principal.audience,
            user_id=principal.user_id,
        )

    @application.post(
        "/v1/router/{audience}/predict",
        response_model=RouterPredictionResponse,
        tags=["router"],
    )
    async def predict_department(
        audience: Literal["student", "staff"],
        payload: RouterPredictionRequest,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> RouterPredictionResponse:
        """Predict a department using only the selected audience's registered model."""

        if principal.audience != audience:
            raise HTTPException(status_code=403, detail="audience access denied")
        try:
            prediction = await run_ai(
                request.app.state.router_service.predict,
                audience,
                payload.ticket_subject,
                payload.ticket_description,
            )
        except (MlflowException, OSError) as error:
            raise HTTPException(
                status_code=503, detail="approved router model unavailable"
            ) from error
        return RouterPredictionResponse(
            audience=prediction.audience,
            department=prediction.department,
            confidence=prediction.confidence,
            top_departments=[
                DepartmentProbability(department=score.department, probability=score.probability)
                for score in prediction.top_departments
            ],
            model_name=prediction.model_name,
            model_reference=prediction.model_reference,
        )

    @application.post(
        "/v1/rag/{audience}/search",
        response_model=RAGSearchResponse,
        tags=["rag"],
    )
    async def search_knowledge(
        audience: Literal["student", "staff"],
        payload: RAGSearchRequest,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> RAGSearchResponse:
        """Search only the selected audience's approved immutable release."""

        if principal.audience != audience:
            raise HTTPException(status_code=403, detail="audience access denied")
        service = request.app.state.rag_service
        if service is None:
            raise HTTPException(status_code=503, detail="RAG database is not configured")
        try:
            results = await run_ai(service.search, audience, payload.query, payload.department)
        except (FileNotFoundError, PsycopgError) as error:
            raise HTTPException(
                status_code=503, detail="approved RAG release unavailable"
            ) from error
        return RAGSearchResponse(
            audience=audience,
            results=[
                RAGSearchResult(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    department=result.department,
                    source=result.source,
                    content=result.content,
                    score=result.score,
                    citation=result.citation,
                )
                for result in results
            ],
        )

    @application.post("/v1/chat", response_model=AgentReply, tags=["chat"])
    async def chat(
        payload: ChatRequest,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AgentReply:
        """Run the controlled workflow using the verified session audience and identity."""

        service = request.app.state.chat_service
        if service is None:
            raise HTTPException(status_code=503, detail="chat service is not configured")
        try:
            return await run_ai(service.respond, principal, payload.message)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail="agent tool access denied") from error

    @application.post(
        "/v1/actions/{action_id}/confirm",
        response_model=EmailReceipt,
        tags=["chat"],
    )
    async def confirm_action(
        action_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> EmailReceipt:
        """Explicitly confirm and send one session-owned email draft through Mailpit."""

        service = request.app.state.chat_service
        if service is None:
            raise HTTPException(status_code=503, detail="chat service is not configured")
        try:
            return service.confirm_email(principal, action_id)
        except ActionAuthorizationError as error:
            raise HTTPException(status_code=404, detail="pending action unavailable") from error
        except EmailDeliveryError as error:
            raise HTTPException(
                status_code=503, detail="synthetic email delivery failed"
            ) from error

    @application.post("/v1/feedback", response_model=FeedbackResponse, tags=["chat"])
    async def feedback(
        payload: FeedbackRequest,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> FeedbackResponse:
        service = request.app.state.chat_service
        if service is None:
            raise HTTPException(status_code=503, detail="chat service is not configured")
        try:
            service.record_feedback(principal, payload.message_id, payload.helpful)
        except FeedbackAuthorizationError as error:
            raise HTTPException(status_code=404, detail="message unavailable") from error
        return FeedbackResponse()

    from ai_support.visitor.routes import register_visitor_routes

    register_visitor_routes(application, current_principal)
    return application


app = create_app()

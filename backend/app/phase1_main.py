"""Focused AgentOps Phase 1 control-plane API."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.phase1_database import async_session, engine, get_db, init_db
from app.phase1_events import event_notifier
from app.phase1_models import Run, RunEvent
from app.phase1_schemas import (
    ApiErrorPayload,
    AnalysisResponse,
    ClaimRequest,
    ClaimResponse,
    ClaimedRun,
    CompleteRequest,
    EventEnvelope,
    EventUploadRequest,
    EventUploadResponse,
    ExperimentCreate,
    ExperimentResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    PolicyResponse,
    RejectPolicyRequest,
    RunCreate,
    RunResponse,
    TERMINAL_RUN_STATUSES,
)
from app.phase1_service import (
    DomainError,
    activate_policy,
    cancel_run,
    claim_job,
    complete_job,
    create_baseline_run,
    create_experiment,
    experiment_response,
    get_analysis,
    heartbeat,
    list_experiments,
    next_event_sequence,
    persist_events,
    reject_policy,
    replay_policy,
    require_experiment,
    require_run,
    run_response,
)


def _error_payload(code: str, message: str, details: object | None = None) -> dict:
    return ApiErrorPayload(code=code, message=message, details=details).model_dump(mode="json")


def _event_payload(row: RunEvent) -> dict:
    return EventEnvelope(
        run_id=row.run_id,
        sequence=row.sequence,
        occurred_at=row.occurred_at,
        type=row.event_type,
        payload=row.payload,
    ).model_dump(mode="json")


def create_app(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    database_engine: AsyncEngine | None = None,
    initialize_database: bool = True,
) -> FastAPI:
    selected_factory = session_factory or async_session
    selected_engine = database_engine or engine
    owns_engine = database_engine is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        if initialize_database:
            await init_db(selected_engine)
        yield
        if owns_engine:
            await selected_engine.dispose()

    application = FastAPI(
        title="AgentOps Evaluation Workbench API",
        version="1.0.0",
        description="Control plane for deterministic agent evaluation and policy replay.",
        lifespan=lifespan,
    )
    application.state.session_factory = selected_factory
    application.state.runner_token = os.getenv("RUNNER_TOKEN", "development-runner-token")

    origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(",") if item.strip()]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @application.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, exc.details),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_payload("VALIDATION_ERROR", "Request validation failed", exc.errors()),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, str(exc.detail)),
        )

    router = APIRouter(prefix="/api")

    async def require_runner(
        request: Request, authorization: str | None = Header(default=None)
    ) -> None:
        expected = request.app.state.runner_token
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
            raise DomainError(401, "RUNNER_UNAUTHORIZED", "A valid runner bearer token is required")

    @router.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "protocol_version": 1}

    @router.get("/experiments", response_model=list[ExperimentResponse])
    async def experiments_list(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        db: AsyncSession = Depends(get_db),
    ) -> list[ExperimentResponse]:
        return await list_experiments(db, limit, offset)

    @router.post("/experiments", response_model=ExperimentResponse, status_code=201)
    async def experiments_create(
        body: ExperimentCreate, db: AsyncSession = Depends(get_db)
    ) -> ExperimentResponse:
        return await create_experiment(db, body)

    @router.get("/experiments/{experiment_id}", response_model=ExperimentResponse)
    async def experiments_get(
        experiment_id: str, db: AsyncSession = Depends(get_db)
    ) -> ExperimentResponse:
        experiment = await require_experiment(db, experiment_id)
        return await experiment_response(db, experiment)

    @router.post("/experiments/{experiment_id}/runs", response_model=RunResponse, status_code=201)
    async def runs_create(
        experiment_id: str,
        body: RunCreate | None = None,
        db: AsyncSession = Depends(get_db),
    ) -> RunResponse:
        return await create_baseline_run(db, experiment_id, (body or RunCreate()).seed)

    @router.get("/runs/{run_id}", response_model=RunResponse)
    async def runs_get(run_id: str, db: AsyncSession = Depends(get_db)) -> RunResponse:
        return run_response(await require_run(db, run_id))

    @router.post("/runs/{run_id}/cancel", response_model=RunResponse)
    async def runs_cancel(run_id: str, db: AsyncSession = Depends(get_db)) -> RunResponse:
        return await cancel_run(db, run_id)

    @router.get("/runs/{run_id}/analysis", response_model=AnalysisResponse)
    async def runs_analysis(
        run_id: str, db: AsyncSession = Depends(get_db)
    ) -> AnalysisResponse:
        return await get_analysis(db, run_id)

    @router.get("/runs/{run_id}/stream")
    async def runs_stream(
        run_id: str,
        after: int = Query(default=0, ge=0),
        db: AsyncSession = Depends(get_db),
    ) -> StreamingResponse:
        await require_run(db, run_id)

        async def generate() -> AsyncGenerator[str, None]:
            queue = event_notifier.subscribe(run_id)
            last_sequence = after
            idle_ticks = 0
            try:
                while True:
                    await db.rollback()
                    rows = list(
                        (
                            await db.execute(
                                select(RunEvent)
                                .where(
                                    RunEvent.run_id == run_id,
                                    RunEvent.sequence > last_sequence,
                                )
                                .order_by(RunEvent.sequence.asc())
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for row in rows:
                        payload = _event_payload(row)
                        last_sequence = row.sequence
                        yield (
                            f"id: {row.sequence}\n"
                            f"event: {row.event_type}\n"
                            f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                        )
                    status = (
                        await db.execute(select(Run.status).where(Run.id == run_id))
                    ).scalar_one_or_none()
                    if status in TERMINAL_RUN_STATUSES and not rows:
                        return
                    try:
                        await asyncio.wait_for(queue.get(), timeout=1.0)
                        idle_ticks = 0
                    except TimeoutError:
                        idle_ticks += 1
                        if idle_ticks >= 15:
                            idle_ticks = 0
                            yield ": keep-alive\n\n"
            finally:
                event_notifier.unsubscribe(run_id, queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/policies/{policy_id}/replay", response_model=PolicyResponse, status_code=201)
    async def policies_replay(
        policy_id: str, db: AsyncSession = Depends(get_db)
    ) -> PolicyResponse:
        return await replay_policy(db, policy_id)

    @router.post("/policies/{policy_id}/activate", response_model=PolicyResponse)
    async def policies_activate(
        policy_id: str, db: AsyncSession = Depends(get_db)
    ) -> PolicyResponse:
        return await activate_policy(db, policy_id)

    @router.post("/policies/{policy_id}/reject", response_model=PolicyResponse)
    async def policies_reject(
        policy_id: str,
        body: RejectPolicyRequest | None = None,
        db: AsyncSession = Depends(get_db),
    ) -> PolicyResponse:
        return await reject_policy(db, policy_id, body.reason if body else None)

    @router.post(
        "/internal/runner/jobs/claim",
        response_model=ClaimResponse,
        responses={204: {"description": "No queued work"}},
        dependencies=[Depends(require_runner)],
    )
    async def runner_claim(
        body: ClaimRequest, db: AsyncSession = Depends(get_db)
    ) -> ClaimResponse | Response:
        result = await claim_job(db, body.runner_id)
        if result is None:
            return Response(status_code=204)
        job, run = result
        return ClaimResponse(
            lease_id=job.lease_id or "",
            lease_expires_at=job.lease_expires_at,
            attempt=job.attempt,
            next_sequence=await next_event_sequence(db, run.id),
            recovery_reason=job.recovery_reason,
            run=ClaimedRun(
                run_id=run.id,
                evaluation_spec=run.evaluation_spec,
            ),
        )

    @router.post(
        "/internal/runner/jobs/{lease_id}/heartbeat",
        response_model=HeartbeatResponse,
        dependencies=[Depends(require_runner)],
    )
    async def runner_heartbeat(
        lease_id: str,
        body: HeartbeatRequest,
        db: AsyncSession = Depends(get_db),
    ) -> HeartbeatResponse:
        command, expires_at = await heartbeat(db, lease_id, body.runner_id)
        return HeartbeatResponse(command=command, lease_expires_at=expires_at)

    @router.post(
        "/internal/runner/runs/{run_id}/events",
        response_model=EventUploadResponse,
        dependencies=[Depends(require_runner)],
    )
    async def runner_events(
        run_id: str,
        body: EventUploadRequest,
        db: AsyncSession = Depends(get_db),
    ) -> EventUploadResponse:
        accepted, published = await persist_events(
            db,
            run_id=run_id,
            lease_id=body.lease_id,
            runner_id=body.runner_id,
            events=body.events,
        )
        if published:
            await event_notifier.publish(run_id, published)
        return EventUploadResponse(accepted_through=accepted)

    @router.post(
        "/internal/runner/jobs/{lease_id}/complete",
        response_model=RunResponse,
        dependencies=[Depends(require_runner)],
    )
    async def runner_complete(
        lease_id: str,
        body: CompleteRequest,
        db: AsyncSession = Depends(get_db),
    ) -> RunResponse:
        return await complete_job(
            db,
            lease_id=lease_id,
            runner_id=body.runner_id,
            status=body.status,
            error=body.error,
            metrics=body.metrics,
        )

    application.include_router(router)
    return application


app = create_app()

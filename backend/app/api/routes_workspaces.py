"""Workspace routes - the isolation boundary, exposed over HTTP."""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import CarinaaError
from app.core.logging import get_logger
from app.core.storage import remove_stored_files
from app.db.models import Chunk, Conversation, Document, Message, QueryLog, Workspace
from app.rag.vectorstore import get_vector_store
from app.schemas.core import (
    WorkspaceCreate,
    WorkspaceListOut,
    WorkspaceOut,
    WorkspaceStats,
    WorkspaceUpdate,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _workspace_stats(db: DbSession, workspace_id: int) -> WorkspaceStats:
    """Real counts for one workspace. Every number is a query result."""

    def count(model, *conditions) -> int:
        return int(
            db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )

    doc_counts = dict(
        db.execute(
            select(Document.status, func.count())
            .where(Document.workspace_id == workspace_id)
            .group_by(Document.status)
        ).all()
    )

    pages, tokens, chars = db.execute(
        select(
            func.coalesce(func.sum(Document.page_count), 0),
            func.coalesce(func.sum(Document.token_estimate), 0),
            func.coalesce(func.sum(Document.char_count), 0),
        ).where(Document.workspace_id == workspace_id)
    ).one()

    conversation_ids = select(Conversation.id).where(
        Conversation.workspace_id == workspace_id
    )

    return WorkspaceStats(
        documents=sum(doc_counts.values()),
        documents_ready=int(doc_counts.get("ready", 0)),
        documents_processing=int(doc_counts.get("processing", 0) + doc_counts.get("pending", 0)),
        documents_failed=int(doc_counts.get("failed", 0)),
        chunks=count(Chunk, Chunk.workspace_id == workspace_id),
        vectors=get_vector_store().count(workspace_id),
        conversations=count(Conversation, Conversation.workspace_id == workspace_id),
        messages=count(Message, Message.conversation_id.in_(conversation_ids)),
        queries=count(QueryLog, QueryLog.workspace_id == workspace_id),
        pages=int(pages),
        tokens_estimated=int(tokens),
        characters=int(chars),
    )


@router.get("", response_model=WorkspaceListOut)
def list_workspaces(user: CurrentUser, db: DbSession) -> WorkspaceListOut:
    """List the current user's workspaces. Never anyone else's."""
    rows = db.scalars(
        select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.updated_at.desc())
    ).all()

    out: list[WorkspaceOut] = []
    for row in rows:
        item = WorkspaceOut.model_validate(row)
        item.stats = _workspace_stats(db, row.id)
        out.append(item)

    return WorkspaceListOut(workspaces=out, total=len(out))


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, user: CurrentUser, db: DbSession) -> WorkspaceOut:
    existing = db.scalar(
        select(Workspace).where(Workspace.user_id == user.id, Workspace.name == payload.name.strip())
    )
    if existing is not None:
        raise CarinaaError(
            "You already have a workspace with that name.",
            code="duplicate_workspace",
            status_code=status.HTTP_409_CONFLICT,
        )

    total = db.scalar(
        select(func.count()).select_from(Workspace).where(Workspace.user_id == user.id)
    )
    if int(total or 0) >= settings.max_workspaces_per_user:
        raise CarinaaError(
            f"You have reached the limit of {settings.max_workspaces_per_user} workspaces.",
            code="workspace_limit",
            status_code=status.HTTP_409_CONFLICT,
        )

    workspace = Workspace(
        user_id=user.id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        color=payload.color,
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    item = WorkspaceOut.model_validate(workspace)
    item.stats = _workspace_stats(db, workspace.id)
    return item


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace: CurrentWorkspace, db: DbSession) -> WorkspaceOut:
    item = WorkspaceOut.model_validate(workspace)
    item.stats = _workspace_stats(db, workspace.id)
    return item


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    payload: WorkspaceUpdate, workspace: CurrentWorkspace, db: DbSession
) -> WorkspaceOut:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(workspace, key, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(workspace)

    item = WorkspaceOut.model_validate(workspace)
    item.stats = _workspace_stats(db, workspace.id)
    return item


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(workspace: CurrentWorkspace, db: DbSession) -> None:
    """Delete a workspace and everything in it.

    Order matters: remove the vectors first, then the relational rows. If we did it
    the other way round and the process died in between, we would leave orphaned
    vectors behind - retrievable data belonging to a workspace that no longer
    exists. This order can only ever leave us with fewer vectors than rows, which
    is the safe direction.
    """
    workspace_id = workspace.id
    user_id = workspace.user_id

    get_vector_store().delete_workspace(workspace_id)

    # Collect the filenames BEFORE deleting the rows, then remove the files AFTER
    # the commit. Same reasoning as `delete_document`: the database change is the
    # point of no return, and file removal is best-effort housekeeping that must
    # never fail the request.
    stored = list(
        db.scalars(
            select(Document.stored_filename).where(Document.workspace_id == workspace_id)
        ).all()
    )

    db.delete(workspace)
    db.commit()
    logger.info("Deleted workspace %s (user %s)", workspace_id, user_id)

    removed = remove_stored_files(stored, context=f"workspace {workspace_id}")
    if removed != len(stored):
        logger.warning(
            "Deleted workspace %s but %d of %d stored file(s) remain on disk.",
            workspace_id,
            len(stored) - removed,
            len(stored),
        )

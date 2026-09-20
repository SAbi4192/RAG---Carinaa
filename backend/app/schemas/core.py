"""Auth, workspace and document schemas."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ===========================================================================
# Auth
# ===========================================================================
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    created_at: dt.datetime
    preferences: dict[str, Any] = Field(default_factory=dict)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: dt.datetime
    user: UserOut


class PreferencesUpdate(BaseModel):
    theme: str | None = None
    language: str | None = None
    default_ai_mode: str | None = None
    default_top_k: int | None = Field(default=None, ge=1, le=50)
    default_rerank: bool | None = None
    learning_mode: bool | None = None
    developer_mode: bool | None = None

    @field_validator("theme")
    @classmethod
    def _theme(cls, value: str | None) -> str | None:
        if value is not None and value not in ("light", "dark", "system"):
            raise ValueError("theme must be light, dark or system")
        return value

    @field_validator("default_ai_mode")
    @classmethod
    def _mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("online", "offline"):
            raise ValueError("default_ai_mode must be online or offline")
        return value


# ===========================================================================
# Workspaces
# ===========================================================================
class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    color: str = Field(default="violet", max_length=20)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=20)


class WorkspaceStats(BaseModel):
    documents: int = 0
    documents_ready: int = 0
    documents_processing: int = 0
    documents_failed: int = 0
    chunks: int = 0
    vectors: int = 0
    conversations: int = 0
    messages: int = 0
    queries: int = 0
    pages: int = 0
    tokens_estimated: int = 0
    characters: int = 0


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    color: str
    created_at: dt.datetime
    updated_at: dt.datetime
    stats: WorkspaceStats | None = None


class WorkspaceListOut(BaseModel):
    workspaces: list[WorkspaceOut]
    total: int


# ===========================================================================
# Documents
# ===========================================================================
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    original_filename: str
    file_type: str
    size_bytes: int
    status: str
    stage: str
    progress: float
    error_message: str | None = None
    page_count: int
    chunk_count: int
    token_estimate: int
    char_count: int
    embedding_model: str
    embedding_dim: int
    doc_metadata: dict[str, Any] = Field(default_factory=dict)
    stage_timings: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime
    processed_at: dt.datetime | None = None


class DocumentListOut(BaseModel):
    documents: list[DocumentOut]
    total: int


class DocumentProgressOut(BaseModel):
    document_id: int
    status: str
    stage: str
    stage_label: str
    progress: float
    percent: float
    error: str | None = None
    chunk_count: int = 0
    stages: list[dict[str, Any]] = Field(default_factory=list)


class ChunkPreviewOut(BaseModel):
    id: int
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    token_estimate: int
    block_type: str
    doc_metadata: dict[str, Any] = Field(default_factory=dict)


class UploadResponse(BaseModel):
    document: DocumentOut
    message: str
    ingestion_started: bool


class ChunkPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)

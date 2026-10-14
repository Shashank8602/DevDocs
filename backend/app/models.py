"""ORM models. Tables are added phase by phase."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    # empty | processing | ready | failed | needs_reindex
    status: Mapped[str] = mapped_column(String(20), default="empty", server_default="empty")
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class File(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("project_id", "file_path", name="uq_files_project_path"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(Text)  # relative, POSIX separators
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))  # doc | source | other
    language: Mapped[str | None] = mapped_column(String(30))
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256, set on upload
    # pending | processing | ready | failed
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)  # internal only
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("file_id", "ordinal", name="uq_chunks_file_ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)  # position within the file
    content: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)  # 1-based, inclusive; code/text files
    line_end: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)  # 1-based; PDFs
    section: Mapped[str | None] = mapped_column(Text)  # heading path, or function/class name
    token_count: Mapped[int] = mapped_column(Integer)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100))  # derived from the first question, never changes
    # Conversation memory: older messages folded into `summary`; `summary_checkpoint_message_id` is the last
    # message it covers. `recent_token_estimate` counts the messages after it (the summarization trigger).
    summary: Mapped[str | None] = mapped_column(Text)
    summary_checkpoint_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    recent_token_estimate: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[list | None] = mapped_column(JSONB)  # assistant only; frozen citations of the answer
    # user messages only: pending | answered | failed | stopped | dismissed
    gen_state: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AppSettings(Base):
    """One row (id = 1): user-edited configuration as JSON. API keys inside are Fernet-encrypted."""
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

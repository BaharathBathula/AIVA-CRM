"""Documents, pinned records and team chat."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db.base import Base
from app.db.mixins import OrganizationOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.editions import _bool, _int, _json_list, _user_fk, _when

# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class DocumentFolder(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "document_folders"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_folders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class Document(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_org_folder", "organization_id", "folder_id"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_folders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    content_type: Mapped[str] = mapped_column(String(160), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    version: Mapped[int] = _int(1)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # organization: everyone with access to records; private: owner and admins only.
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="organization", server_default="organization")
    tags: Mapped[list[str]] = _json_list()
    owner_user_id: Mapped[uuid.UUID | None] = _user_fk()
    download_count: Mapped[int] = _int(0)


class DocumentVersion(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_document_versions_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    # The file itself; loaded only when downloading.
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))


class DocumentLink(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "document_links"
    __table_args__ = (
        UniqueConstraint("document_id", "record_type", "record_id", name="uq_document_links_record"),
        Index("ix_document_links_record", "organization_id", "record_type", "record_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


class Pin(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "user_pins"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "record_type", "record_id", name="uq_user_pins_record"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    href: Mapped[str] = mapped_column(String(500), nullable=False)
    position: Mapped[int] = _int(0)


# ---------------------------------------------------------------------------
# Team chat
# ---------------------------------------------------------------------------


class ChatChannel(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "chat_channels"
    __table_args__ = (
        UniqueConstraint("organization_id", "dm_key", name="uq_chat_channels_dm"),
        Index("ix_chat_channels_org_kind", "organization_id", "kind"),
    )

    # channel | direct
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="channel", server_default="channel")
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_private: Mapped[bool] = _bool(False)
    is_archived: Mapped[bool] = _bool(False)
    # Sorted member ids for direct conversations, so each pair has one conversation.
    dm_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    last_message_at: Mapped[datetime | None] = _when()


class ChatMember(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "chat_members"
    __table_args__ = (UniqueConstraint("channel_id", "user_id", name="uq_chat_members_user"),)

    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = _user_fk(nullable=False, ondelete="CASCADE")
    # owner | member
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member", server_default="member")
    last_read_at: Mapped[datetime | None] = _when()
    is_muted: Mapped[bool] = _bool(False)


class ChatMessage(UUIDPrimaryKeyMixin, OrganizationOwnedMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_channel_created", "channel_id", "created_at"),)

    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = _user_fk()
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mentions: Mapped[list[str]] = _json_list()
    # Optional link to a CRM record: {"type", "id", "label", "href"}.
    record: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    edited_at: Mapped[datetime | None] = _when()
    deleted_at: Mapped[datetime | None] = _when()

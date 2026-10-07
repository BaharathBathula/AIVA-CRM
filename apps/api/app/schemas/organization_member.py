import uuid

from pydantic import BaseModel


class OrganizationMemberResponse(BaseModel):
    user_id: uuid.UUID

    full_name: str
    email: str

    role: str

    is_active: bool
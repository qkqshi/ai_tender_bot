from pydantic import BaseModel, Field
from typing import List, Optional


class TenderBase(BaseModel):
    id: str
    title: str
    nmcc: float
    participant_count: int = Field(default=0)
    is_online: bool
    platform: str = Field(default="b2b-center")
    url: str
    deadline: Optional[str] = None
    raw_json: dict


class UserProfile(BaseModel):
    user_id: int
    target_participants: List[int] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)
    only_online: bool = False

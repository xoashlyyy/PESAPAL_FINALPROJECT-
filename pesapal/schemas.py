from typing import List, Literal

from pydantic import BaseModel, Field


PIN = Field(pattern=r"^\d{4,6}$")


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    email: str = Field(max_length=120)
    phone: str
    gender: str
    pin: str = PIN


class LoginRequest(BaseModel):
    email: str
    pin: str


class VerifyRequest(BaseModel):
    email: str
    code: str


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    email: str
    code: str
    new_pin: str = PIN


class PreviewRequest(BaseModel):
    amount: float
    member_ids: List[str]
    mode: Literal["equal", "percentage", "custom"]
    values: List[float] = []


class ExpenseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    category: str
    payer_id: str
    amount: float = Field(gt=0)
    member_ids: List[str]
    mode: Literal["equal", "percentage", "custom"]
    values: List[float] = []


class SettlementRequest(BaseModel):
    payee_id: str
    amount: float = Field(gt=0)
    phone: str


class ReminderRequest(BaseModel):
    user_id: str


class FeedbackRequest(BaseModel):
    clarity: int = Field(ge=1, le=5)
    trust: int = Field(ge=1, le=5)
    ease: int = Field(ge=1, le=5)

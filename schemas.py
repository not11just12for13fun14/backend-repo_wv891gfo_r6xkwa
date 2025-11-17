"""
All Assist Schemas

Each Pydantic model below maps to a MongoDB collection. The collection name is the lowercase of the class name.
This file is read by the built-in database viewer and used for validation.
"""
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, EmailStr

# Core identities
class User(BaseModel):
    role: Literal["motorist", "provider", "admin"] = Field("motorist", description="User role")
    name: str = Field(..., description="Full name")
    email: Optional[EmailStr] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number (E.164)")
    password_hash: Optional[str] = Field(None, description="Password hash, if password auth is used")
    is_verified: bool = Field(False, description="Email/phone verified")
    is_active: bool = Field(True, description="Whether the user is active")

class Providerapplication(BaseModel):
    user_id: str = Field(..., description="Reference to user")
    company_name: Optional[str] = Field(None, description="Business name")
    service_types: List[str] = Field(default_factory=list, description="Services offered")
    license_number: Optional[str] = Field(None, description="Driver license")
    insurance_policy: Optional[str] = Field(None, description="Insurance policy number")
    documents: List[str] = Field(default_factory=list, description="Uploaded doc URLs")
    status: Literal["pending", "approved", "rejected"] = Field("pending", description="Application status")
    notes: Optional[str] = Field(None, description="Admin notes")

class Providerprofile(BaseModel):
    user_id: str = Field(..., description="Reference to user")
    status: Literal["offline", "online", "busy"] = Field("offline", description="Availability")
    vehicle: Optional[str] = Field(None, description="Vehicle details")
    service_types: List[str] = Field(default_factory=list, description="Services offered")
    rating: float = Field(5.0, ge=0, le=5, description="Average rating")
    jobs_completed: int = Field(0, ge=0, description="Completed jobs count")
    lat: Optional[float] = Field(None, description="Current latitude")
    lng: Optional[float] = Field(None, description="Current longitude")

class Servicerequest(BaseModel):
    motorist_id: str = Field(..., description="User id of motorist")
    service_type: str = Field(..., description="Type of service requested")
    description: Optional[str] = Field(None, description="Additional details")
    pickup_lat: float = Field(..., description="Pickup latitude")
    pickup_lng: float = Field(..., description="Pickup longitude")
    provider_id: Optional[str] = Field(None, description="Assigned provider user id")
    status: Literal["pending", "assigned", "enroute", "in_progress", "completed", "cancelled"] = Field("pending", description="Request status")
    quoted_amount: Optional[float] = Field(None, description="Estimated amount")
    paid: bool = Field(False, description="Payment status")

class Payment(BaseModel):
    request_id: str = Field(..., description="Linked service request id")
    motorist_id: str = Field(...)
    provider_id: str = Field(...)
    amount: float = Field(..., ge=0)
    currency: str = Field("ZAR")
    status: Literal["initiated", "authorized", "captured", "failed", "refunded"] = Field("initiated")
    gateway: str = Field("peach", description="Payment gateway used")
    gateway_reference: Optional[str] = Field(None)

class Review(BaseModel):
    request_id: str
    motorist_id: str
    provider_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

class Dispute(BaseModel):
    request_id: str
    raised_by: Literal["motorist", "provider", "admin"]
    reason: str
    details: Optional[str] = None
    status: Literal["open", "in_review", "resolved", "dismissed"] = Field("open")

class Notificationtoken(BaseModel):
    user_id: str
    fcm_token: str
    platform: Literal["ios", "android", "web"] = "web"

class Earningspayout(BaseModel):
    provider_id: str
    amount: float
    period: str = Field(..., description="e.g., 2025-11")
    status: Literal["pending", "paid", "failed"] = "pending"

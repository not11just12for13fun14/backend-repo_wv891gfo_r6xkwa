import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Literal, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
from passlib.context import CryptContext
import jwt
import requests

from database import db, create_document, get_documents

# Env
JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change_me")
JWT_EXPIRES_IN = int(os.getenv("JWT_EXPIRES_IN", "3600"))
DATABASE_NAME = os.getenv("DATABASE_NAME", "allassist")
FCM_SERVER_KEY = os.getenv("FCM_SERVER_KEY", "")

# Auth helpers
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

app = FastAPI(title="All Assist API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ Models (lightweight for request bodies) ------------------
class RegisterDTO(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    role: Literal["motorist", "provider", "admin"] = "motorist"

class LoginDTO(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: Optional[str] = None

class LocationDTO(BaseModel):
    lat: float
    lng: float

class RequestCreateDTO(BaseModel):
    service_type: str
    description: Optional[str] = None
    pickup_lat: float
    pickup_lng: float

class ProviderStatusDTO(BaseModel):
    status: Literal["offline", "online", "busy"]
    lat: Optional[float] = None
    lng: Optional[float] = None

class ProviderApplyDTO(BaseModel):
    company_name: Optional[str] = None
    service_types: List[str] = []
    license_number: Optional[str] = None
    insurance_policy: Optional[str] = None

class PaymentIntentDTO(BaseModel):
    request_id: str
    amount: float
    currency: str = "ZAR"

class NotificationSendDTO(BaseModel):
    user_id: str
    title: str
    body: str
    data: Dict[str, Any] = {}

# ------------------ Utility functions ------------------

def create_token(user: dict) -> str:
    payload = {
        "sub": str(user.get("_id")),
        "role": user.get("role", "motorist"),
        "exp": datetime.now(timezone.utc) + timedelta(seconds=JWT_EXPIRES_IN),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = creds.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])  # type: ignore
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Look up by mirrored string id first
    user = db["user"].find_one({"id": user_id}) or db["user"].find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def distance_km(a: LocationDTO, b: LocationDTO) -> float:
    from math import radians, sin, cos, sqrt, atan2
    R = 6371.0
    lat1, lon1 = radians(a.lat), radians(a.lng)
    lat2, lon2 = radians(b.lat), radians(b.lng)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    aa = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(aa), sqrt(1-aa))
    return R * c

# ------------------ Basic routes ------------------
@app.get("/")
def root():
    return {"name": "All Assist API", "database": DATABASE_NAME}

@app.get("/schema")
def get_schema():
    # Expose schema models for the database viewer
    try:
        from schemas import (
            User, Providerapplication, Providerprofile, Servicerequest,
            Payment, Review, Dispute, Notificationtoken, Earningspayout
        )
        models = [User, Providerapplication, Providerprofile, Servicerequest,
                  Payment, Review, Dispute, Notificationtoken, Earningspayout]
        return {m.__name__: m.model_json_schema() for m in models}
    except Exception as e:
        return {"error": str(e)}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected & Working"
            response["database_url"] = "✅ Set"
            response["database_name"] = DATABASE_NAME
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()
            except Exception as e:
                response["database"] = f"⚠️ Connected but error: {str(e)[:60]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:60]}"
    return response

# ------------------ Auth ------------------
@app.post("/auth/register")
def register(payload: RegisterDTO):
    # Ensure unique email/phone
    if payload.email and db["user"].find_one({"email": payload.email}):
        raise HTTPException(400, "Email already registered")
    if payload.phone and db["user"].find_one({"phone": payload.phone}):
        raise HTTPException(400, "Phone already registered")

    doc = {
        "name": payload.name,
        "email": payload.email,
        "phone": payload.phone,
        "role": payload.role,
        "is_verified": False,
        "is_active": True,
    }
    if payload.password:
        doc["password_hash"] = pwd_context.hash(payload.password)
    user_id = create_document("user", doc)
    # Store mirror id to ease JWT lookups without ObjectId handling
    db["user"].update_one({"id": {"$exists": False}, "_id": user_id}, {"$set": {"id": user_id}})
    user = db["user"].find_one({"id": user_id})
    token = create_token({"_id": user_id, "role": user.get("role")})
    return {"token": token, "user": {"id": user_id, "name": user.get("name"), "role": user.get("role")}}

@app.post("/auth/login")
def login(payload: LoginDTO):
    q: Dict[str, Any] = {}
    if payload.email:
        q["email"] = payload.email
    if payload.phone:
        q["phone"] = payload.phone
    user = db["user"].find_one(q) if q else None
    if not user:
        raise HTTPException(401, "Invalid credentials")
    if payload.password and user.get("password_hash"):
        if not pwd_context.verify(payload.password, user.get("password_hash")):
            raise HTTPException(401, "Invalid credentials")
    token = create_token({"_id": user.get("id") or str(user.get("_id")), "role": user.get("role")})
    return {"token": token, "user": {"id": user.get("id") or str(user.get("_id")), "name": user.get("name"), "role": user.get("role")}}

# ------------------ Provider onboarding & presence ------------------
@app.post("/providers/apply")
def provider_apply(payload: ProviderApplyDTO, user=Depends(get_current_user)):
    if user.get("role") not in ("provider", "admin"):
        raise HTTPException(403, "Only providers can apply")
    application = {
        "user_id": user.get("id") or str(user.get("_id")),
        "company_name": payload.company_name,
        "service_types": payload.service_types,
        "license_number": payload.license_number,
        "insurance_policy": payload.insurance_policy,
        "documents": [],
        "status": "pending",
    }
    app_id = create_document("providerapplication", application)
    return {"application_id": app_id, "status": "pending"}

@app.post("/providers/status")
def provider_status(payload: ProviderStatusDTO, user=Depends(get_current_user)):
    if user.get("role") not in ("provider", "admin"):
        raise HTTPException(403, "Only providers can set status")
    profile = db["providerprofile"].find_one({"user_id": user.get("id") or str(user.get("_id"))})
    data = {"user_id": user.get("id") or str(user.get("_id")), "status": payload.status}
    if payload.lat is not None and payload.lng is not None:
        data.update({"lat": payload.lat, "lng": payload.lng})
    if profile:
        db["providerprofile"].update_one({"_id": profile["_id"]}, {"$set": data})
    else:
        create_document("providerprofile", data)
    return {"ok": True}

@app.get("/providers/nearby")
def providers_nearby(lat: float, lng: float, service_type: Optional[str] = None, radius_km: float = 30.0):
    origin = LocationDTO(lat=lat, lng=lng)
    results: List[Dict[str, Any]] = []
    for p in db["providerprofile"].find({"status": "online"}):
        if service_type and service_type not in (p.get("service_types") or []):
            continue
        if p.get("lat") is None or p.get("lng") is None:
            continue
        d = distance_km(origin, LocationDTO(lat=p["lat"], lng=p["lng"]))
        if d <= radius_km:
            results.append({"user_id": p.get("user_id"), "lat": p.get("lat"), "lng": p.get("lng"), "distance_km": round(d, 2)})
    results.sort(key=lambda x: x["distance_km"])  # nearest first
    return {"providers": results}

# ------------------ Requests & real-time matching (simplified) ------------------
@app.post("/requests")
def create_request(payload: RequestCreateDTO, user=Depends(get_current_user)):
    if user.get("role") != "motorist":
        raise HTTPException(403, "Only motorists can create requests")
    request_doc = {
        "motorist_id": user.get("id") or str(user.get("_id")),
        "service_type": payload.service_type,
        "description": payload.description,
        "pickup_lat": payload.pickup_lat,
        "pickup_lng": payload.pickup_lng,
        "status": "pending",
    }
    req_id = create_document("servicerequest", request_doc)
    # Auto-match nearest provider online
    nearby = providers_nearby(payload.pickup_lat, payload.pickup_lng, payload.service_type)["providers"]
    if nearby:
        chosen = nearby[0]
        db["servicerequest"].update_one({"id": req_id}, {"$set": {"provider_id": chosen["user_id"], "status": "assigned"}})
        db["providerprofile"].update_one({"user_id": chosen["user_id"]}, {"$set": {"status": "busy"}})
        match = {"provider_id": chosen["user_id"], "eta_min": max(3, int(chosen["distance_km"] / 0.6))}
    else:
        match = None
    return {"request_id": req_id, "match": match}

@app.get("/requests")
def list_requests(user=Depends(get_current_user)):
    role = user.get("role")
    if role == "motorist":
        items = list(db["servicerequest"].find({"motorist_id": user.get("id") or str(user.get("_id"))}).sort("created_at", -1))
    elif role == "provider":
        items = list(db["servicerequest"].find({"provider_id": user.get("id") or str(user.get("_id"))}).sort("created_at", -1))
    else:
        items = list(db["servicerequest"].find({}).sort("created_at", -1))
    for it in items:
        it["id"] = it.get("id") or str(it.get("_id"))
    return {"items": items}

@app.post("/requests/{request_id}/status")
def update_request_status(request_id: str, status: Literal["enroute", "in_progress", "completed", "cancelled"], user=Depends(get_current_user)):
    req = db["servicerequest"].find_one({"id": request_id}) or db["servicerequest"].find_one({"_id": request_id})
    if not req:
        raise HTTPException(404, "Request not found")
    role = user.get("role")
    if role == "provider" and req.get("provider_id") not in (user.get("id"), str(user.get("_id"))):
        raise HTTPException(403, "Not your job")
    if role == "motorist" and req.get("motorist_id") not in (user.get("id"), str(user.get("_id"))):
        raise HTTPException(403, "Not your request")
    db["servicerequest"].update_one({"id": request_id}, {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}})
    return {"ok": True}

# ------------------ Payments (Peach placeholder + webhook) ------------------
@app.post("/payments/intent")
def create_payment_intent(payload: PaymentIntentDTO, user=Depends(get_current_user)):
    # Placeholder integration – store intent and return a mock redirect url
    sr = db["servicerequest"].find_one({"id": payload.request_id})
    intent_id = create_document("payment", {
        "request_id": payload.request_id,
        "motorist_id": user.get("id") or str(user.get("_id")),
        "provider_id": sr.get("provider_id") if sr else None,
        "amount": payload.amount,
        "currency": payload.currency,
        "status": "initiated",
        "gateway": "peach",
    })
    return {"intent_id": intent_id, "redirect_url": f"https://sandbox.peachpayments.com/pay/{intent_id}"}

@app.post("/payments/webhook")
def payments_webhook(request: Request):
    # Minimal webhook to update payment status
    try:
        payload = {}
        try:
            payload = {}
        except Exception:
            payload = {}
        # In real integration, parse request body and verify signature
        intent_id = request.query_params.get("intent_id")
        status = request.query_params.get("status", "succeeded")
        if intent_id:
            db["payment"].update_one({"id": intent_id}, {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}})
        return {"ok": True}
    except Exception:
        return {"ok": False}

# ------------------ Notifications (FCM) ------------------
@app.post("/notifications/register")
def register_fcm_token(token: str = Body(..., embed=True), platform: str = Body("web", embed=True), user=Depends(get_current_user)):
    create_document("notificationtoken", {"user_id": user.get("id") or str(user.get("_id")), "fcm_token": token, "platform": platform})
    return {"ok": True}

@app.post("/notifications/send")
def send_notification(payload: NotificationSendDTO, user=Depends(get_current_user)):
    # Allow admin to send or user to send to self
    if user.get("role") != "admin" and user.get("id") != payload.user_id:
        raise HTTPException(403, "Not allowed")
    tokens = [t.get("fcm_token") for t in db["notificationtoken"].find({"user_id": payload.user_id}) if t.get("fcm_token")]
    if not tokens:
        return {"sent": 0, "message": "No tokens registered"}
    if not FCM_SERVER_KEY:
        return {"sent": 0, "message": "FCM_SERVER_KEY not set"}
    headers = {
        "Authorization": f"key={FCM_SERVER_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "registration_ids": tokens,
        "notification": {"title": payload.title, "body": payload.body},
        "data": payload.data,
        "priority": "high",
    }
    try:
        resp = requests.post("https://fcm.googleapis.com/fcm/send", json=body, headers=headers, timeout=5)
        ok = resp.status_code == 200
        return {"ok": ok, "status": resp.status_code, "response": resp.json() if ok else resp.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ------------------ Reviews & disputes ------------------
@app.post("/reviews")
def post_review(request_id: str = Body(...), provider_id: str = Body(...), rating: int = Body(..., ge=1, le=5), comment: Optional[str] = Body(None), user=Depends(get_current_user)):
    create_document("review", {
        "request_id": request_id,
        "motorist_id": user.get("id") or str(user.get("_id")),
        "provider_id": provider_id,
        "rating": rating,
        "comment": comment,
    })
    return {"ok": True}

@app.post("/disputes")
def raise_dispute(request_id: str = Body(...), reason: str = Body(...), details: Optional[str] = Body(None), user=Depends(get_current_user)):
    create_document("dispute", {"request_id": request_id, "raised_by": user.get("role"), "reason": reason, "details": details, "status": "open"})
    return {"ok": True}

# ------------------ Admin ------------------
@app.get("/admin/overview")
def admin_overview(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admins only")
    revenue = 0
    if db["payment"].count_documents({}):
        try:
            revenue = db["payment"].aggregate([{ "$group": {"_id": None, "sum": {"$sum": "$amount"}}}]).next().get("sum", 0)  # type: ignore
        except Exception:
            # Fallback: sum in python
            revenue = sum([p.get("amount", 0) for p in db["payment"].find({})])
    return {
        "users": db["user"].count_documents({}),
        "providers": db["providerprofile"].count_documents({}),
        "active_jobs": db["servicerequest"].count_documents({"status": {"$in": ["assigned", "enroute", "in_progress"]}}),
        "revenue": revenue,
    }

@app.get("/admin/applications")
def admin_list_applications(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admins only")
    items = list(db["providerapplication"].find({}).sort("created_at", -1))
    for it in items:
        it["id"] = it.get("id") or str(it.get("_id"))
    return {"items": items}

@app.post("/admin/applications/{app_id}/status")
def admin_set_application_status(app_id: str, status: Literal["approved", "rejected"], user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admins only")
    db["providerapplication"].update_one({"id": app_id}, {"$set": {"status": status}})
    # Auto-create provider profile if approved
    app_doc = db["providerapplication"].find_one({"id": app_id})
    if status == "approved" and app_doc:
        prof = db["providerprofile"].find_one({"user_id": app_doc.get("user_id")})
        if not prof:
            create_document("providerprofile", {"user_id": app_doc.get("user_id"), "status": "offline", "service_types": app_doc.get("service_types", [])})
    return {"ok": True}

# --------------- Run ---------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

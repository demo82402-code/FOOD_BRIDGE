import hashlib
from datetime import datetime, timedelta
import streamlit as st

AREAS = {
    "Pitampura, Delhi": (28.7005, 77.1315),
    "Rohini, Delhi": (28.7383, 77.0822),
    "Connaught Place, Delhi": (28.6315, 77.2167),
    "Dwarka, Delhi": (28.5921, 77.0460),
    "Sector 62, Noida": (28.6270, 77.3649),
    "Greater Noida West": (28.6050, 77.4290),
    "Knowledge Park III": (28.4740, 77.5030),
    "Indirapuram": (28.6420, 77.3690),
    "Gurugram Sector 29": (28.4595, 77.0266),
}

CATEGORIES = [
    "Meals",
    "Rice",
    "Bread",
    "Fruits & Vegetables",
    "Bakery",
    "Sweets",
    "Beverages",
    "Other",
]

STORAGE = [
    "Covered containers",
    "Refrigerated (< 5°C)",
    "Insulated hot-box",
    "Ambient controlled",
    "Other",
]

STEPS = ["Listed", "Verified", "Claimed", "Accepted", "Picked Up", "Completed"]

STATUS_CLASS = {
    "Available": "b-ok",
    "Completed": "b-ok",
    "Pending Verification": "b-warn",
    "Claim Requested": "b-warn",
    "Accepted": "b-info",
    "Picked Up": "b-info",
    "Sold Out": "b-info",
    "Rejected": "b-bad",
    "Expired": "b-bad",
    "Cancelled": "b-bad",
}

CO2_PER_SERVING = 1.0  # kg CO2e avoided per serving rescued


def hp(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()


@st.cache_resource
def get_db():
    now = datetime.now()

    def U(name, email, pw, role, phone, loc, verified=True, reg=""):
        return {
            "name": name,
            "email": email,
            "pw": hp(pw),
            "role": role,
            "phone": phone,
            "location": loc,
            "verified": verified,
            "reg": reg,
            "created": now,
        }

    def L(
        i,
        donor,
        venue,
        food,
        cat,
        qty,
        price,
        disc,
        mins_left,
        prep_ago_h,
        safe_h,
        area,
        addr,
        storage,
        veg=True,
        verified=True,
        status="Available",
        stage="Discount Active",
    ):
        lat, lon = AREAS[area]
        unit = round(price * (1 - disc / 100) / qty, 2)
        return {
            "id": f"FB{i:03d}",
            "donor": donor,
            "venue": venue,
            "food_type": food,
            "category": cat,
            "quantity": qty,
            "remaining": qty,
            "sold": 0,
            "donated": 0,
            "original_price": price,
            "discount_percent": disc,
            "unit_price": unit,
            "discount_minutes": 45,
            "discount_end": (now + timedelta(minutes=mins_left))
            if verified
            else None,
            "stage": stage,
            "status": status,
            "verified": verified,
            "prep_time": now - timedelta(hours=prep_ago_h),
            "deadline": now + timedelta(hours=safe_h),
            "area": area,
            "address": addr,
            "lat": lat,
            "lon": lon,
            "storage": storage,
            "veg": veg,
            "contact": "9810000001",
            "photo": None,
            "claimed_by": "",
            "claim_qty": 0,
            "claim_status": "",
            "pickup_eta": "",
            "beneficiaries": "",
            "watchers": [],
            "created": now,
            "notes": "",
        }

    users = [
        U(
            "Demo Donor",
            "donor@foodbridge.demo",
            "1234",
            "Donor",
            "9810000001",
            "Pitampura, Delhi",
        ),
        U(
            "Green Leaf Restaurant",
            "restaurant@foodbridge.demo",
            "1234",
            "Donor",
            "9810000002",
            "Greater Noida West",
        ),
        U(
            "Demo NGO",
            "ngo@foodbridge.demo",
            "1234",
            "NGO",
            "9810000003",
            "Rohini, Delhi",
            True,
            "NGO/DL/2019/4471",
        ),
        U(
            "Roti Bank India",
            "rotibank@foodbridge.demo",
            "1234",
            "NGO",
            "9810000004",
            "Dwarka, Delhi",
            False,
            "NGO/DL/2024/9920",
        ),
        U(
            "Demo Buyer",
            "buyer@foodbridge.demo",
            "1234",
            "Buyer",
            "9810000005",
            "Pitampura, Delhi",
        ),
        U(
            "Admin",
            "admin@foodbridge.demo",
            "admin123",
            "Admin",
            "9810000099",
            "Delhi",
        ),
    ]

    donations = [
        L(
            1,
            "Demo Donor",
            "Royal Celebration Hall",
            "Mixed Veg Banquet Platter",
            "Meals",
            120,
            6000,
            50,
            18,
            2,
            5,
            "Sector 62, Noida",
            "Royal Celebration Hall, Block C",
            "Covered containers",
        ),
        L(
            2,
            "Green Leaf Restaurant",
            "Green Leaf Restaurant",
            "Steamed Basmati & Dal Tadka",
            "Meals",
            80,
            4000,
            40,
            30,
            1,
            4,
            "Greater Noida West",
            "Gaur City Mall, Ground Floor",
            "Refrigerated (< 5°C)",
        ),
        L(
            3,
            "Demo Donor",
            "Apex Corporate Event",
            "Hyderabadi Veg Dum Biryani",
            "Rice",
            65,
            3500,
            50,
            -10,
            3,
            3,
            "Knowledge Park III",
            "Apex Tower, Gate 2",
            "Insulated hot-box",
            stage="Donation Priority",
        ),
        L(
            4,
            "Green Leaf Restaurant",
            "Sunrise Banquet",
            "Tandoori Roti & Paneer Masala",
            "Meals",
            100,
            5000,
            40,
            45,
            1,
            6,
            "Indirapuram",
            "Sunrise Banquet Hall",
            "Covered containers",
            verified=False,
            status="Pending Verification",
        ),
    ]

    return {
        "users": users,
        "donations": donations,
        "orders": [],
        "notifications": [],
        "messages": {},
        "activity": [
            {
                "time": now,
                "message": "SYS.INIT // FoodBridge network node initialized",
                "type": "system",
            }
        ],
    }
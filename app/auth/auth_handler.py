import time
from typing import Dict
import os
from dotenv import load_dotenv
import jwt
from datetime import date, datetime

load_dotenv()

JWT_SECRET = os.getenv("secret")
JWT_ALGORITHM = os.getenv("algorithm")


def token_response(token: str):
    return {
        "access_token": token
    }


def sign_jwt(contact_no) -> Dict[str, str]:
    now = time.time()
    end_of_day = datetime.combine(datetime.today(), datetime.max.time()).timestamp()
    expires_in = end_of_day - now

    payload = {
        "contact_no": contact_no,
        "expires": now + expires_in
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return token_response(token)


def decode_jwt(token: str) -> dict:
    try:
        decoded_token = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return decoded_token if decoded_token["expires"] >= time.time() else None
    except:
        return {}

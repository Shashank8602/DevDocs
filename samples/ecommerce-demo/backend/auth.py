from jwt import create_token, verify_token

USERS = {"alice": "s3cret"}


def login(username: str, password: str) -> str:
    """Return a JWT if the credentials are valid."""
    if USERS.get(username) != password:
        raise PermissionError("invalid credentials")
    return create_token(username)


def current_user(token: str) -> str:
    return verify_token(token)["sub"]

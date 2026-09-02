from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .database import AgentPrincipal
from .services import get_repository


bearer_scheme = HTTPBearer(auto_error=False)


def require_bridge_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> Optional[AgentPrincipal]:
    path = request.url.path
    if path in {"/healthz", "/readyz", "/agent-guide"} or path.startswith("/admin"):
        return None
    if request.method == "GET" and path.startswith("/wechat/callback"):
        return
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    principal = get_repository().authenticate_agent(credentials.credentials)
    if not principal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token",
        )
    request.state.agent_principal = principal
    return principal


def require_scope(request: Request, scope: str) -> AgentPrincipal:
    principal: Optional[AgentPrincipal] = getattr(request.state, "agent_principal", None)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing agent identity")
    if scope not in principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not have required scope: {scope}",
        )
    return principal

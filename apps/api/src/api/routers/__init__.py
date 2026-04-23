from .api_config import router as api_config_router
from .auth import router as auth_router
from .conversation import router as conversation_router
from .document import router as document_router
from .faq import router as faq_router
from .lead import router as lead_router
from .settings import router as settings_router

__all__ = [
    "api_config_router",
    "auth_router",
    "conversation_router",
    "document_router",
    "faq_router",
    "lead_router",
    "settings_router",
]

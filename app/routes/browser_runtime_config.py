from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from app.auth import AuthPrincipal, require_principal
from app.settings import Settings, get_settings


router = APIRouter()


class BrowserLaunchpadUrls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lingxi: str | None
    sop_assistant: str | None
    word_translate: str | None
    word_review: str | None


class BrowserRuntimeConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launchpad_urls: BrowserLaunchpadUrls


def build_browser_runtime_config(settings: Settings) -> BrowserRuntimeConfigResponse:
    """Project only explicitly browser-public settings into the ordinary UI."""

    return BrowserRuntimeConfigResponse(
        launchpad_urls=BrowserLaunchpadUrls(
            lingxi=settings.browser_public_launchpad_lingxi_url,
            sop_assistant=settings.browser_public_launchpad_sop_url,
            word_translate=settings.browser_public_launchpad_word_translate_url,
            word_review=settings.browser_public_launchpad_word_review_url,
        )
    )


@router.get(
    "/runtime-config/browser",
    response_model=BrowserRuntimeConfigResponse,
    response_model_exclude_none=False,
)
async def browser_runtime_config(
    response: Response,
    _principal: AuthPrincipal = Depends(require_principal),
) -> BrowserRuntimeConfigResponse:
    response.headers["Cache-Control"] = "no-store"
    return build_browser_runtime_config(get_settings())

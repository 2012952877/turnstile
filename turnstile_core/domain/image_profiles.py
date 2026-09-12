from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .models import StrictModel

IMAGE_RESPONSE_SAFETY_BYTES = 16 * 1024 * 1024


class ImageGenerationLimits(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_request_bytes: int = Field(strict=True, ge=1, le=IMAGE_RESPONSE_SAFETY_BYTES)
    max_response_bytes: int = Field(strict=True, ge=1, le=IMAGE_RESPONSE_SAFETY_BYTES)
    output_reservation_tokens: int = Field(strict=True, ge=1, le=2**31 - 1)
    timeout_seconds: int = Field(strict=True, ge=1, le=240)

    @model_validator(mode="after")
    def reservation_fits_counter(self) -> Self:
        if self.burst_reservation_tokens > 2**31 - 1:
            raise ValueError("Image reservation exceeds the protocol counter range")
        return self

    @property
    def burst_reservation_tokens(self) -> int:
        return self.max_request_bytes + self.output_reservation_tokens


class ImageGenerationProfile(ImageGenerationLimits):
    id: str = Field(min_length=1)
    version: Literal[4]
    display_name: str = Field(min_length=1)
    provider_kind: Literal["microsoft_foundry"]
    provider_model: str = Field(min_length=1)
    protocol: Literal["openai_images"]
    api_version: str = Field(min_length=1)
    backend_path: str = Field(pattern=r"^/")
    public_path: str = Field(pattern=r"^/")
    usage_format: Literal["text-input-image-output-v1"]

    @property
    def limits(self) -> ImageGenerationLimits:
        return ImageGenerationLimits.model_validate(
            self.model_dump(include=set(ImageGenerationLimits.model_fields))
        )

    @property
    def configuration(self) -> ImageGenerationLimits:
        return self.limits


def create_image_profile(
    configuration: ImageGenerationLimits, provider_model: str,
) -> ImageGenerationProfile:
    values = {
        **configuration.model_dump(mode="json", include=set(ImageGenerationLimits.model_fields)),
        "version": 4,
        "display_name": "OpenAI Images text to image",
        "provider_kind": "microsoft_foundry",
        "provider_model": provider_model,
        "protocol": "openai_images",
        "api_version": "preview",
        "backend_path": "/openai/v1/images/generations",
        "public_path": "/images/generations",
        "usage_format": "text-input-image-output-v1",
    }
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ImageGenerationProfile.model_validate({"id": "sha256:" + digest, **values})


def validate_image_profile(value: object) -> ImageGenerationProfile:
    if value is None:
        raise ValueError("The model needs an explicitly published image profile")
    profile = ImageGenerationProfile.model_validate(value)
    if profile != create_image_profile(profile.limits, profile.provider_model):
        raise ValueError("The published image profile does not match its immutable version")
    return profile